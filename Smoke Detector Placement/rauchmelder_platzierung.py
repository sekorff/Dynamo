#!/usr/bin/env python3
"""Automatische Platzierung von punktfoermigen Rauchmeldern auf Basis einer IFC-Datei.

Eingabe : IFC-Datei, deren Raeume als IfcSpace-Volumen vorliegen.
Ausgabe : CSV mit Melderkoordinaten, Uebersichtsplan (PNG) je Geschoss und
          optional eine IFC-Datei, in die die Melder als IfcSensor eingefuegt sind.

Vorgehen je Raum
  1. Grundriss und Hoehe aus dem Raumvolumen ableiten.
  2. Ueberwachungsflaeche A und max. Horizontalabstand DH aus Grundflaeche und
     Raumhoehe bestimmen (Projektierungswerte, siehe `Regeln`).
  3. Kleinstes regelmaessiges Raster (Reihen x Spalten) parallel zur
     Hauptrichtung des Raums suchen, das A, DH und den Wandabstand einhaelt.
  4. Das Raster innerhalb der Toleranzen so verschieben, dass seine Reihen auf
     Achsen bereits bearbeiteter Raeume (oder einem Deckenraster) liegen.

Zusaetzlich wird jeder Raum untersucht auf
  * Abhangdecken (IfcCovering CEILING): ist der Hohlraum zwischen Abhangdecke
    und Rohdecke hoeher als `zwischendecke_min` (0,29 m), erhaelt er eigene
    Zwischendeckenmelder.
  * Unterzuege (IfcBeam): trennt ein Unterzug die Decke, wird jedes Deckenfeld
    wie ein eigener Raum projektiert (gilt fuer Raum und Zwischendecke).

Die Normwerte sind Voreinstellungen und muessen vom Fachplaner gegen die
gueltige Ausgabe der DIN VDE 0833-2 / VdS 2095 geprueft werden.
"""
from __future__ import annotations

import argparse
import csv
import math
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import shapely
from shapely import affinity
from shapely.geometry import MultiPolygon, Polygon
from shapely.ops import unary_union

import ifcopenshell
import ifcopenshell.geom
import ifcopenshell.guid
import ifcopenshell.util.element
import ifcopenshell.util.placement
import ifcopenshell.util.unit


# --------------------------------------------------------------------------- #
# Projektierungsregeln
# --------------------------------------------------------------------------- #
@dataclass
class Regeln:
    """Projektierungswerte fuer punktfoermige Rauchmelder (flache Decke)."""

    # Raeume bis zu dieser Grundflaeche duerfen mit A_gross ueberwacht werden
    grenzflaeche: float = 80.0
    # Hoehengrenze, ab der bei grossen Raeumen wieder A_gross gilt
    grenzhoehe: float = 6.0
    # Maximale Raumhoehe fuer punktfoermige Rauchmelder
    max_hoehe: float = 12.0
    a_klein: float = 60.0   # m2 je Melder (Flaeche > Grenzflaeche, Hoehe <= Grenzhoehe)
    dh_klein: float = 5.7   # m  zugehoeriger max. Horizontalabstand
    a_gross: float = 80.0   # m2 je Melder
    dh_gross: float = 6.6   # m
    wandabstand: float = 0.5  # m Mindestabstand Melder - Wand
    # Schmale Gaenge: bis zu dieser Breite gilt der Melderabstand `gang_abstand`
    gang_breite: float = 3.0
    gang_abstand: float = 15.0
    # Achsausrichtung
    max_verschiebung: float = 1.5  # m, um die ein Raumraster verschoben werden darf
    raster: float = 0.0            # m, optionales Deckenraster (0 = aus)
    # Raeume unterhalb dieser Flaeche erhalten keinen Melder (0 = alle Raeume)
    min_flaeche: float = 0.0
    # Hohlraum ueber der Abhangdecke: oberhalb dieser Hoehe eigene Melder
    zwischendecke_min: float = 0.29
    # Unterzuege trennen die Decke ab dieser Hoehe unter der Decke (0 = immer)
    unterzug_min: float = 0.0
    # Deckenfelder unterhalb dieser Flaeche gelten als Restflaeche (kein Melder)
    min_feld: float = 0.5

    def werte(self, flaeche: float, hoehe: float) -> tuple[float, float]:
        """Liefert (A, DH) fuer einen Raum."""
        if flaeche <= self.grenzflaeche or hoehe > self.grenzhoehe:
            return self.a_gross, self.dh_gross
        return self.a_klein, self.dh_klein


@dataclass
class Bauteil:
    """Abhangdecke, Unterzug oder Rohdecke: Grundriss und Hoehenlage."""
    art: str
    name: str
    polygon: Polygon
    z_unten: float
    z_oben: float


@dataclass
class Bereich:
    """Ein zu ueberwachendes Deckenfeld: Raum bzw. Zwischendecke, ggf. durch
    Unterzuege begrenzt. `z_oben` ist die Montagehoehe der Melder."""
    art: str            # "Raum" oder "Zwischendecke"
    feld: int           # laufende Nummer des Deckenfelds im Raum
    polygon: Polygon
    z_unten: float
    z_oben: float
    winkel: float = 0.0
    melder: np.ndarray = field(default_factory=lambda: np.zeros((0, 2)))
    namen: list[str] = field(default_factory=list)
    a: float = 0.0
    dh: float = 0.0
    raster_n: tuple[int, int] = (0, 0)
    max_abstand: float = 0.0
    hinweise: list[str] = field(default_factory=list)

    @property
    def flaeche(self) -> float:
        return self.polygon.area

    @property
    def hoehe(self) -> float:
        return self.z_oben - self.z_unten


@dataclass
class Raum:
    guid: str
    nummer: str
    name: str
    geschoss: str
    polygon: Polygon
    z_unten: float
    z_oben: float
    element: object = None
    bereiche: list[Bereich] = field(default_factory=list)
    unterzuege: list[Polygon] = field(default_factory=list)
    hinweise: list[str] = field(default_factory=list)

    @property
    def flaeche(self) -> float:
        return self.polygon.area

    @property
    def hoehe(self) -> float:
        return self.z_oben - self.z_unten

    def anzahl(self, art: str) -> int:
        return sum(len(b.melder) for b in self.bereiche if b.art == art)


# --------------------------------------------------------------------------- #
# IFC einlesen
# --------------------------------------------------------------------------- #
def _grundriss(verts: np.ndarray, faces: np.ndarray) -> Polygon | None:
    """Projiziert ein Dreiecksnetz in die XY-Ebene und vereinigt es zum Grundriss."""
    tri = verts[faces][:, :, :2]
    a, b, c = tri[:, 0], tri[:, 1], tri[:, 2]
    flaeche2 = np.abs((b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1])
                      - (b[:, 1] - a[:, 1]) * (c[:, 0] - a[:, 0]))
    tri = tri[flaeche2 > 1e-8]  # senkrechte Flaechen entfallen
    if len(tri) == 0:
        return None
    eps = 1e-4
    geom = unary_union(shapely.polygons(tri))
    geom = geom.buffer(eps, join_style="mitre").buffer(-eps, join_style="mitre")
    if isinstance(geom, MultiPolygon):
        geom = max(geom.geoms, key=lambda g: g.area)
    if geom.is_empty or not isinstance(geom, Polygon):
        return None
    return geom.simplify(1e-3)


def lade_raeume(modell: ifcopenshell.file) -> list[Raum]:
    """Liest alle IfcSpace mit Geometrie; Koordinaten in Metern (Weltkoordinaten)."""
    einstellungen = ifcopenshell.geom.settings()
    einstellungen.set("use-world-coords", True)
    raeume: list[Raum] = []
    for space in modell.by_type("IfcSpace"):
        bez = space.Name or space.GlobalId
        if space.Representation is None:
            print(f"  ! Raum {bez}: keine Geometrie, uebersprungen", file=sys.stderr)
            continue
        try:
            shape = ifcopenshell.geom.create_shape(einstellungen, space)
        except Exception as fehler:  # defekte Geometrie soll den Lauf nicht abbrechen
            print(f"  ! Raum {bez}: Geometriefehler ({fehler}), uebersprungen", file=sys.stderr)
            continue
        verts = np.asarray(shape.geometry.verts, dtype=float).reshape(-1, 3)
        faces = np.asarray(shape.geometry.faces, dtype=int).reshape(-1, 3)
        polygon = _grundriss(verts, faces)
        if polygon is None:
            print(f"  ! Raum {bez}: kein Grundriss ableitbar, uebersprungen", file=sys.stderr)
            continue
        geschoss = _geschoss(space)
        raeume.append(Raum(
            guid=space.GlobalId,
            nummer=space.Name or "",
            name=space.LongName or "",
            geschoss=(geschoss.Name or geschoss.GlobalId) if geschoss else "ohne Geschoss",
            polygon=polygon,
            z_unten=float(verts[:, 2].min()),
            z_oben=float(verts[:, 2].max()),
            element=space,
        ))
    return raeume


def lade_bauteile(modell: ifcopenshell.file) -> dict[str, list[Bauteil]]:
    """Liest Abhangdecken, Unterzuege und Rohdecken (Meter, Weltkoordinaten)."""
    einstellungen = ifcopenshell.geom.settings()
    einstellungen.set("use-world-coords", True)
    ergebnis: dict[str, list[Bauteil]] = {"Abhangdecke": [], "Unterzug": [], "Rohdecke": []}
    quellen = [("Abhangdecke", "IfcCovering"), ("Unterzug", "IfcBeam"),
               ("Rohdecke", "IfcSlab"), ("Rohdecke", "IfcRoof")]
    for art, klasse in quellen:
        for element in modell.by_type(klasse):
            if art == "Abhangdecke" and \
                    ifcopenshell.util.element.get_predefined_type(element) != "CEILING":
                continue
            if element.Representation is None:
                continue
            try:
                shape = ifcopenshell.geom.create_shape(einstellungen, element)
            except Exception:
                continue
            verts = np.asarray(shape.geometry.verts, dtype=float).reshape(-1, 3)
            faces = np.asarray(shape.geometry.faces, dtype=int).reshape(-1, 3)
            polygon = _grundriss(verts, faces)
            if polygon is None:
                continue
            ergebnis[art].append(Bauteil(art, element.Name or element.GlobalId, polygon,
                                         float(verts[:, 2].min()), float(verts[:, 2].max())))
    return ergebnis


def _polygone(geom) -> list[Polygon]:
    if geom.is_empty:
        return []
    if isinstance(geom, Polygon):
        return [geom]
    return [g for g in getattr(geom, "geoms", []) if isinstance(g, Polygon)]


def _deckenfelder(flaeche, z_boden: float, z_decke: float, unterzuege: list[Bauteil],
                  regeln: Regeln) -> tuple[list[Polygon], list[Polygon], int]:
    """Teilt eine Deckenflaeche an den Unterzuegen, die unter die Decke ragen.

    Rueckgabe: (Deckenfelder, Grundrisse der wirksamen Unterzuege, Anzahl Restflaechen)
    """
    wirksam = [u.polygon for u in unterzuege
               if u.z_unten < z_decke - max(regeln.unterzug_min, 0.01)  # ragt unter die Decke
               and u.z_oben > z_decke - 0.05                            # reicht bis zur Decke
               and u.z_oben > z_boden
               and u.polygon.intersects(flaeche)
               and u.polygon.intersection(flaeche).area > 0.01]
    if not wirksam:
        return _polygone(flaeche), [], 0
    teile = _polygone(flaeche.difference(unary_union(wirksam)))
    felder = [t for t in teile if t.area >= regeln.min_feld]
    felder.sort(key=lambda t: (round(t.bounds[1], 2), round(t.bounds[0], 2)))
    schnitt = [g for u in wirksam for g in _polygone(u.intersection(flaeche))]
    return felder, schnitt, len(teile) - len(felder)


def bilde_bereiche(raum: Raum, bauteile: dict[str, list[Bauteil]], regeln: Regeln) -> None:
    """Untersucht den Raum auf Abhangdecken und Unterzuege und legt die
    zu ueberwachenden Bereiche (Deckenfelder, Zwischendecken) an."""
    winkel = hauptwinkel(raum.polygon)
    raum.bereiche, raum.unterzuege = [], []

    # Abhangdecken im oberen Raumbereich bzw. direkt ueber dem Raumvolumen
    decken = []
    for decke in bauteile["Abhangdecke"]:
        if not (raum.z_unten + 0.5 * raum.hoehe < decke.z_unten <= raum.z_oben + 0.10):
            continue
        if not decke.polygon.intersects(raum.polygon):
            continue
        flaeche = decke.polygon.intersection(raum.polygon)
        if flaeche.area > 0.25:
            decken.append((decke, flaeche))

    # Deckenhoehe des Raums: Unterkante Abhangdecke, wenn sie den Raum ueberwiegend bedeckt
    z_decke = raum.z_oben
    if decken and sum(f.area for _, f in decken) >= 0.5 * raum.flaeche:
        z_decke = min(raum.z_oben, max(decken, key=lambda d: d[1].area)[0].z_unten)

    felder, schnitt, reste = _deckenfelder(raum.polygon, raum.z_unten, z_decke,
                                           bauteile["Unterzug"], regeln)
    raum.unterzuege += schnitt
    if len(felder) > 1:
        raum.hinweise.append(f"Unterzug teilt den Raum in {len(felder)} Deckenfelder")
    if reste:
        raum.hinweise.append(f"{reste} Restflaeche(n) < {regeln.min_feld} m2 ohne Melder")
    for nr, feld in enumerate(felder, 1):
        raum.bereiche.append(Bereich("Raum", nr, feld, raum.z_unten, z_decke, winkel))

    # Zwischendecken
    nr = 0
    for decke, flaeche in decken:
        kandidaten = [r.z_unten for r in bauteile["Rohdecke"]
                      if decke.z_oben - 0.02 <= r.z_unten <= decke.z_oben + 5.0
                      and r.polygon.intersects(flaeche)
                      and r.polygon.intersection(flaeche).area > 0.25]
        if raum.z_oben > decke.z_oben + 0.01:
            kandidaten.append(raum.z_oben)  # Raumvolumen reicht bis zur Rohdecke
        if not kandidaten:
            raum.hinweise.append("Abhangdecke ohne erkennbare Rohdecke - Hohlraum manuell pruefen")
            continue
        z_roh = min(kandidaten)
        hohlraum = z_roh - decke.z_oben
        if hohlraum <= regeln.zwischendecke_min + 1e-6:
            raum.hinweise.append(f"Abhangdecke, Hohlraum {hohlraum:.2f} m: kein Zwischendeckenmelder")
            continue
        raum.hinweise.append(f"Abhangdecke, Hohlraum {hohlraum:.2f} m > "
                             f"{regeln.zwischendecke_min:.2f} m: Zwischendeckenmelder")
        felder, schnitt, reste = _deckenfelder(flaeche, decke.z_oben, z_roh,
                                               bauteile["Unterzug"], regeln)
        raum.unterzuege += schnitt
        if len(felder) > 1:
            raum.hinweise.append(f"Unterzug teilt die Zwischendecke in {len(felder)} Deckenfelder")
        if reste:
            raum.hinweise.append(
                f"{reste} Restflaeche(n) < {regeln.min_feld} m2 in der Zwischendecke ohne Melder")
        for feld in felder:
            nr += 1
            raum.bereiche.append(Bereich("Zwischendecke", nr, feld, decke.z_oben, z_roh, winkel))


def _geschoss(element):
    """Sucht das IfcBuildingStorey oberhalb eines Raums."""
    aktuell = element
    while aktuell is not None:
        if aktuell.is_a("IfcBuildingStorey"):
            return aktuell
        aktuell = (ifcopenshell.util.element.get_aggregate(aktuell)
                   or ifcopenshell.util.element.get_container(aktuell))
    return None


# --------------------------------------------------------------------------- #
# Geometrie-Hilfen
# --------------------------------------------------------------------------- #
def hauptwinkel(polygon: Polygon) -> float:
    """Dominante Wandrichtung in Grad, normiert auf [0, 90)."""
    xy = np.asarray(polygon.exterior.coords)
    d = np.diff(xy, axis=0)
    laenge = np.hypot(d[:, 0], d[:, 1])
    winkel = np.degrees(np.arctan2(d[:, 1], d[:, 0])) % 90.0
    # laengengewichtetes Kreismittel mit Periode 90 Grad
    phase = np.radians(winkel * 4.0)
    mittel = math.atan2((laenge * np.sin(phase)).sum(), (laenge * np.cos(phase)).sum())
    ergebnis = (math.degrees(mittel) / 4.0) % 90.0
    ergebnis = round(ergebnis, 2)
    return 0.0 if ergebnis >= 90.0 else ergebnis


def _drehen(punkte: np.ndarray, grad: float) -> np.ndarray:
    w = math.radians(grad)
    c, s = math.cos(w), math.sin(w)
    return punkte @ np.array([[c, s], [-s, c]])


def _pruefpunkte(polygon: Polygon) -> np.ndarray:
    """Stichpunkte im Raum (Raster + Eckpunkte) fuer die Abstandspruefung."""
    schritt = max(0.2, math.sqrt(polygon.area / 20000.0))
    x0, y0, x1, y1 = polygon.bounds
    xs = np.arange(x0 + schritt / 2, x1, schritt)
    ys = np.arange(y0 + schritt / 2, y1, schritt)
    gx, gy = np.meshgrid(xs, ys)
    gx, gy = gx.ravel(), gy.ravel()
    innen = shapely.contains_xy(polygon, gx, gy)
    ecken = [np.asarray(polygon.exterior.coords)]
    ecken += [np.asarray(ring.coords) for ring in polygon.interiors]
    return np.vstack([np.column_stack([gx[innen], gy[innen]])] + ecken)


def _max_abstand(pruef: np.ndarray, melder: np.ndarray) -> float:
    """Groesster Abstand eines Pruefpunkts zum jeweils naechsten Melder."""
    if len(melder) == 0:
        return math.inf
    kleinste = np.full(len(pruef), np.inf)
    for m in melder:
        np.minimum(kleinste, np.hypot(pruef[:, 0] - m[0], pruef[:, 1] - m[1]), out=kleinste)
    return float(kleinste.max())


# --------------------------------------------------------------------------- #
# Platzierung
# --------------------------------------------------------------------------- #
class Achsen:
    """Sammelt die Melderachsen aller Raeume, getrennt nach Rasterrichtung."""

    def __init__(self) -> None:
        self._achsen: dict[tuple[float, int], list[float]] = defaultdict(list)

    def liste(self, winkel: float, richtung: int) -> list[float]:
        return self._achsen[(winkel, richtung)]

    def merken(self, winkel: float, richtung: int, werte) -> None:
        vorhandene = self._achsen[(winkel, richtung)]
        for wert in werte:
            if not any(abs(wert - v) < 1e-3 for v in vorhandene):
                vorhandene.append(float(wert))

    def alle(self):
        return self._achsen.items()


def platziere(raum: Bereich, regeln: Regeln, achsen: Achsen) -> None:
    """Bestimmt die Melderpositionen eines Bereichs und traegt sie dort ein."""
    raum.a, raum.dh = regeln.werte(raum.flaeche, raum.hoehe)
    if raum.art == "Raum" and raum.hoehe > regeln.max_hoehe:
        raum.hinweise.append(
            f"Raumhoehe {raum.hoehe:.2f} m > {regeln.max_hoehe:.1f} m: "
            "punktfoermige Rauchmelder nicht geeignet")

    # Alles Weitere im gedrehten System (Hauptrichtung des Raums = u-Achse)
    poly = affinity.rotate(raum.polygon, -raum.winkel, origin=(0, 0))
    u0, v0, u1, v1 = poly.bounds
    laenge, breite = u1 - u0, v1 - v0

    innen = poly.buffer(-regeln.wandabstand)
    if innen.is_empty:
        innen = None
        raum.hinweise.append(
            f"Raum zu schmal fuer Wandabstand {regeln.wandabstand:.2f} m")

    pruef = _pruefpunkte(poly)
    mindestzahl = max(1, math.ceil(raum.flaeche / raum.a - 1e-9))
    dh = raum.dh

    # Schmaler Gang: Abstand der Melder untereinander bis `gang_abstand`
    gang = min(laenge, breite) < regeln.gang_breite and regeln.gang_abstand > 0
    if gang:
        halb = regeln.gang_abstand / 2.0
        dh = max(dh, math.hypot(halb, min(laenge, breite) / 2.0))
        mindestzahl = 1

    def gueltig(punkte: np.ndarray) -> float | None:
        """Gibt den max. Abstand zurueck, wenn alle Regeln eingehalten sind."""
        if len(punkte) < mindestzahl:
            return None
        abstand = _max_abstand(pruef, punkte)
        return abstand if abstand <= dh + 1e-6 else None

    def raster(nu: int, nv: int) -> np.ndarray:
        us = u0 + (np.arange(nu) + 0.5) * laenge / nu
        vs = v0 + (np.arange(nv) + 0.5) * breite / nv
        gu, gv = np.meshgrid(us, vs)
        punkte = np.column_stack([gu.ravel(), gv.ravel()])
        if innen is not None:
            punkte = punkte[shapely.contains_xy(innen, punkte[:, 0], punkte[:, 1])]
        else:
            punkte = punkte[shapely.contains_xy(poly, punkte[:, 0], punkte[:, 1])]
        return punkte

    # Kandidaten nach Melderzahl, dann nach moeglichst quadratischen Feldern
    max_n = 80
    kandidaten = [
        (nu * nv, abs(math.log((laenge / nu) / (breite / nv))), nu, nv)
        for nu in range(1, max_n + 1) for nv in range(1, max_n + 1)
        if laenge / nu >= 0.5 and breite / nv >= 0.5 or (nu == 1 or nv == 1)
    ]
    kandidaten.sort()

    beste = None  # (anzahl, formabweichung, punkte, nu, nv, abstand)
    grenze = None
    for produkt, form, nu, nv in kandidaten:
        if grenze is not None and produkt > grenze:
            break
        if produkt < mindestzahl:
            continue
        punkte = raster(nu, nv)
        abstand = gueltig(punkte)
        if abstand is None:
            continue
        schluessel = (len(punkte), form)
        if beste is None or schluessel < beste[:2]:
            beste = (len(punkte), form, punkte, nu, nv, abstand)
        if grenze is None:
            # verwinkelte Raeume: groebere Raster koennen weniger Melder ergeben
            grenze = produkt * 1.5

    if beste is None:
        # Notloesung: ein Melder im Punkt mit groesstem Wandabstand
        kreis = shapely.maximum_inscribed_circle(poly)
        punkte = np.asarray(kreis.coords)[:1]
        raum.raster_n = (1, 1)
        raum.max_abstand = _max_abstand(pruef, punkte)
        if gueltig(punkte) is None:
            raum.hinweise.append("kein regelkonformes Raster gefunden - manuell pruefen")
    else:
        _, _, punkte, nu, nv, abstand = beste
        raum.raster_n = (nu, nv)
        raum.max_abstand = abstand
        punkte = _ausrichten(punkte, raum, regeln, achsen,
                             innen if innen is not None else poly, gueltig)
        raum.max_abstand = _max_abstand(pruef, punkte)

    for richtung in (0, 1):
        achsen.merken(raum.winkel, richtung, np.unique(np.round(punkte[:, richtung], 4)))
    raum.melder = _drehen(punkte, -raum.winkel)


def _ausrichten(punkte, raum, regeln, achsen, bereich, gueltig) -> np.ndarray:
    """Verschiebt das Raumraster auf vorhandene Achsen bzw. das Deckenraster."""
    if regeln.max_verschiebung <= 0:
        return punkte
    punkte = punkte.copy()
    for richtung in (0, 1):
        eigene = np.unique(np.round(punkte[:, richtung], 4))
        ziele = list(achsen.liste(raum.winkel, richtung))
        if regeln.raster > 0:
            ziele += [round(e / regeln.raster) * regeln.raster for e in eigene]
        deltas = {round(z - e, 4) for z in ziele for e in eigene
                  if abs(z - e) <= regeln.max_verschiebung}
        if not deltas:
            continue

        def treffer(delta: float) -> int:
            return sum(any(abs(e + delta - z) < 1e-3 for z in ziele) for e in eigene)

        for delta in sorted(deltas, key=lambda d: (-treffer(d), abs(d))):
            if abs(delta) < 1e-6:
                break  # liegt bereits auf einer Achse
            versuch = punkte.copy()
            versuch[:, richtung] += delta
            if not shapely.contains_xy(bereich, versuch[:, 0], versuch[:, 1]).all():
                continue
            if gueltig(versuch) is None:
                continue
            punkte = versuch
            break
    return punkte


def platziere_alle(raeume: list[Raum], regeln: Regeln,
                   bauteile: dict[str, list[Bauteil]] | None = None) -> Achsen:
    bauteile = bauteile or {"Abhangdecke": [], "Unterzug": [], "Rohdecke": []}
    achsen = Achsen()
    bereiche: list[Bereich] = []
    for raum in raeume:
        if raum.flaeche < regeln.min_flaeche:
            raum.hinweise.append(
                f"Flaeche < {regeln.min_flaeche:.1f} m2: kein Melder vorgesehen")
            continue
        bilde_bereiche(raum, bauteile, regeln)
        bereiche += raum.bereiche
    # Raeume vor Zwischendecken, grosse Flaechen zuerst: sie geben die Achsen vor
    for bereich in sorted(bereiche, key=lambda b: (b.art != "Raum", -b.flaeche)):
        platziere(bereich, regeln, achsen)
    # Melder je Raum durchnummerieren: RM = Raummelder, ZD = Zwischendeckenmelder
    for raum in raeume:
        for art, kuerzel in (("Raum", "RM"), ("Zwischendecke", "ZD")):
            nr = 0
            for bereich in raum.bereiche:
                if bereich.art != art:
                    continue
                bereich.namen = []
                for _ in bereich.melder:
                    nr += 1
                    bereich.namen.append(f"{raum.nummer or 'R'}-{kuerzel}{nr:02d}")
    return achsen


# --------------------------------------------------------------------------- #
# Ausgabe
# --------------------------------------------------------------------------- #
def schreibe_csv(raeume: list[Raum], pfad: Path) -> None:
    with pfad.open("w", newline="", encoding="utf-8-sig") as datei:
        w = csv.writer(datei, delimiter=";")
        w.writerow(["Geschoss", "Raumnummer", "Raumname", "Raum_GUID", "Bereich", "Deckenfeld",
                    "Melder", "X_m", "Y_m", "Z_m", "Feldflaeche_m2", "Hoehe_m",
                    "A_m2", "DH_m", "max_Abstand_m", "Hinweise"])
        for raum in sorted(raeume, key=lambda r: (r.geschoss, r.nummer)):
            kopf = [raum.geschoss, raum.nummer, raum.name, raum.guid]
            if not raum.bereiche:
                w.writerow(kopf + [""] * 11 + [" | ".join(raum.hinweise)])
            for b in raum.bereiche:
                hinweise = " | ".join(raum.hinweise + b.hinweise)
                fuss = [f"{b.flaeche:.2f}", f"{b.hoehe:.2f}", f"{b.a:.0f}", f"{b.dh:.2f}",
                        f"{b.max_abstand:.2f}" if len(b.melder) else "", hinweise]
                if len(b.melder) == 0:
                    w.writerow(kopf + [b.art, b.feld, "", "", "", ""] + fuss)
                for name, (x, y) in zip(b.namen, b.melder):
                    w.writerow(kopf + [b.art, b.feld, name,
                                       f"{x:.3f}", f"{y:.3f}", f"{b.z_oben:.3f}"] + fuss)


def schreibe_plaene(raeume: list[Raum], achsen: Achsen, ordner: Path, stamm: str) -> list[Path]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch

    dateien = []
    je_geschoss: dict[str, list[Raum]] = defaultdict(list)
    for raum in raeume:
        je_geschoss[raum.geschoss].append(raum)

    for geschoss, liste in sorted(je_geschoss.items()):
        grenzen = unary_union([r.polygon for r in liste]).bounds
        bx, by = grenzen[2] - grenzen[0], grenzen[3] - grenzen[1]
        massstab = 14.0 / max(bx, by, 1.0)
        fig, ax = plt.subplots(figsize=(max(6, bx * massstab) + 1, max(4, by * massstab) + 1))
        bereiche = [b for r in liste for b in r.bereiche]

        # Achsen, auf denen in diesem Geschoss mehrere Melder liegen
        for (winkel, richtung), werte in achsen.alle():
            gedreht = [_drehen(b.melder, winkel) for b in bereiche
                       if abs(b.winkel - winkel) < 1e-6 and len(b.melder)]
            if not gedreht:
                continue
            alle = np.unique(np.round(np.vstack(gedreht), 3), axis=0)
            for wert in werte:
                belegt = alle[np.abs(alle[:, richtung] - wert) < 2e-3]
                if len(belegt) < 2:
                    continue
                andere = belegt[:, 1 - richtung]
                ende = np.array([[wert, andere.min() - 1.0], [wert, andere.max() + 1.0]])
                if richtung == 1:
                    ende = ende[:, ::-1]
                ende = _drehen(ende, -winkel)
                ax.plot(ende[:, 0], ende[:, 1], color="#c0392b", lw=0.6, ls="-.", zorder=2)

        for raum in liste:
            x, y = raum.polygon.exterior.xy
            ax.fill(x, y, facecolor="#f4f6f8", edgecolor="#2c3e50", lw=1.2, zorder=1)
            for ring in raum.polygon.interiors:
                ax.fill(*ring.xy, facecolor="white", edgecolor="#2c3e50", lw=1.0, zorder=1)
            for unterzug in raum.unterzuege:
                ax.fill(*unterzug.exterior.xy, facecolor="#95a5a6", edgecolor="#566573",
                        lw=0.6, hatch="////", zorder=3)
            for b in raum.bereiche:
                if len(b.melder) == 0:
                    continue
                if b.art == "Raum":
                    feld, = ax.fill(*b.polygon.exterior.xy, facecolor="none", edgecolor="none")
                    for m in b.melder:
                        kreis = ax.add_patch(plt.Circle(m, b.dh, facecolor="#2980b9", alpha=0.07,
                                                        edgecolor="#2980b9", lw=0.4, zorder=1.5))
                        kreis.set_clip_path(feld)  # Ueberwachungsbereich nur im eigenen Feld
                    ax.scatter(b.melder[:, 0], b.melder[:, 1], s=28, color="#c0392b",
                               edgecolor="white", lw=0.6, zorder=5)
                else:
                    ax.scatter(b.melder[:, 0], b.melder[:, 1], s=110, marker="s",
                               facecolor="none", edgecolor="#1f618d", lw=1.3, zorder=4)
            p = raum.polygon.representative_point()
            zd = raum.anzahl("Zwischendecke")
            hoehe = raum.bereiche[0].hoehe if raum.bereiche else raum.hoehe
            ax.annotate(f"{raum.nummer} {raum.name}\n{raum.flaeche:.1f} m² · h {hoehe:.2f} m"
                        f"\n{raum.anzahl('Raum')} Melder" + (f" + {zd} ZD" if zd else ""),
                        (p.x, p.y), ha="center", va="center", fontsize=6.5, color="#2c3e50",
                        zorder=6, xytext=(0, -14), textcoords="offset points")
        ax.set_xlim(grenzen[0] - 1, grenzen[2] + 1)
        ax.set_ylim(grenzen[1] - 1, grenzen[3] + 1)
        ax.set_aspect("equal")
        n_rm = sum(r.anzahl("Raum") for r in liste)
        n_zd = sum(r.anzahl("Zwischendecke") for r in liste)
        ax.set_title(f"Rauchmelderplatzierung – {geschoss}  "
                     f"({n_rm} Raummelder, {n_zd} Zwischendeckenmelder)", fontsize=10)
        ax.set_xlabel("X [m]")
        ax.set_ylabel("Y [m]")
        ax.legend(handles=[
            Line2D([], [], marker="o", ls="", color="#c0392b", markersize=6, label="Raummelder"),
            Line2D([], [], marker="s", ls="", markerfacecolor="none", markeredgecolor="#1f618d",
                   markersize=9, label="Zwischendeckenmelder"),
            Patch(facecolor="#95a5a6", edgecolor="#566573", hatch="////", label="Unterzug"),
            Line2D([], [], color="#c0392b", lw=0.8, ls="-.", label="Melderachse"),
        ], loc="upper left", bbox_to_anchor=(1.01, 1.0), fontsize=7, frameon=False)
        sicher = "".join(c if c.isalnum() or c in "-_" else "_" for c in geschoss)
        pfad = ordner / f"{stamm}_{sicher}.png"
        fig.savefig(pfad, dpi=160, bbox_inches="tight")
        plt.close(fig)
        dateien.append(pfad)
    return dateien


def schreibe_ifc(modell: ifcopenshell.file, raeume: list[Raum], pfad: Path) -> None:
    """Fuegt die Melder als IfcSensor (SMOKESENSOR) in das Modell ein und speichert es."""
    faktor = 1.0 / ifcopenshell.util.unit.calculate_unit_scale(modell)  # m -> Modelleinheit
    ifc2x3 = modell.schema == "IFC2X3"
    kontext = next((k for k in modell.by_type("IfcGeometricRepresentationContext")
                    if k.ContextType == "Model" and not k.is_a("IfcGeometricRepresentationSubContext")),
                   None)
    historie = next(iter(modell.by_type("IfcOwnerHistory")), None)

    # Gemeinsame Geometrie: flacher Zylinder d=100 mm, h=50 mm unter der Decke
    richtung_z = modell.createIfcDirection((0.0, 0.0, 1.0))
    richtung_x = modell.createIfcDirection((1.0, 0.0, 0.0))
    ursprung = modell.createIfcCartesianPoint((0.0, 0.0, 0.0))
    darstellung = None
    if kontext is not None:
        profil = modell.createIfcCircleProfileDef(
            "AREA", None, modell.createIfcAxis2Placement2D(
                modell.createIfcCartesianPoint((0.0, 0.0)), None), 0.05 * faktor)
        koerper = modell.createIfcExtrudedAreaSolid(
            profil, modell.createIfcAxis2Placement3D(
                modell.createIfcCartesianPoint((0.0, 0.0, -0.05 * faktor)), richtung_z, richtung_x),
            richtung_z, 0.05 * faktor)
        darstellung = modell.createIfcShapeRepresentation(kontext, "Body", "SweptSolid", [koerper])

    je_geschoss = defaultdict(list)
    for raum in raeume:
        geschoss = _geschoss(raum.element)
        # Geschossplatzierung in Weltkoordinaten (Modelleinheit)
        if geschoss is not None and geschoss.ObjectPlacement is not None:
            matrix = ifcopenshell.util.placement.get_local_placement(geschoss.ObjectPlacement)
            invers = np.linalg.inv(matrix)
            bezug = geschoss.ObjectPlacement
        else:
            invers, bezug = np.eye(4), None
        for bereich, name, x, y in ((b, n, m[0], m[1]) for b in raum.bereiche
                                    for n, m in zip(b.namen, b.melder)):
            lokal = invers @ np.array([x * faktor, y * faktor, bereich.z_oben * faktor, 1.0])
            achse = invers[:3, :3] @ np.array([0.0, 0.0, 1.0])
            ref = invers[:3, :3] @ np.array([1.0, 0.0, 0.0])
            platzierung = modell.createIfcLocalPlacement(bezug, modell.createIfcAxis2Placement3D(
                modell.createIfcCartesianPoint(tuple(float(v) for v in lokal[:3])),
                modell.createIfcDirection(tuple(float(v) for v in achse)),
                modell.createIfcDirection(tuple(float(v) for v in ref))))
            form = (modell.createIfcProductDefinitionShape(None, None, [darstellung])
                    if darstellung is not None else None)
            ort = "Zwischendecke" if bereich.art == "Zwischendecke" else "Raum"
            attribute = dict(GlobalId=ifcopenshell.guid.new(), OwnerHistory=historie,
                             Name=name, Description=f"Punktfoermiger Rauchmelder, {ort} {raum.nummer}",
                             ObjectPlacement=platzierung, Representation=form)
            if ifc2x3:
                melder = modell.create_entity("IfcDistributionControlElement",
                                              ObjectType="SMOKESENSOR", **attribute)
            else:
                melder = modell.create_entity("IfcSensor", PredefinedType="SMOKESENSOR", **attribute)
            je_geschoss[geschoss].append(melder)

    for geschoss, melder in je_geschoss.items():
        if geschoss is None:
            continue
        modell.create_entity("IfcRelContainedInSpatialStructure",
                             GlobalId=ifcopenshell.guid.new(), OwnerHistory=historie,
                             Name="Rauchmelder", RelatedElements=melder,
                             RelatingStructure=geschoss)
    modell.write(str(pfad))


def bericht(raeume: list[Raum]) -> None:
    print(f"\n{'Geschoss':<10}{'Raum':<8}{'Name':<20}{'Bereich':<16}{'Flaeche':>8}{'Hoehe':>7}"
          f"{'A':>5}{'DH':>6}{'Raster':>8}{'Melder':>7}{'max.Abst.':>10}")
    print("-" * 105)
    for raum in sorted(raeume, key=lambda r: (r.geschoss, r.nummer)):
        kopf = f"{raum.geschoss[:9]:<10}{raum.nummer[:7]:<8}{raum.name[:19]:<20}"
        if not raum.bereiche:
            print(kopf + "-")
        mehrere = {art: sum(b.art == art for b in raum.bereiche) > 1
                   for art in ("Raum", "Zwischendecke")}
        for i, b in enumerate(raum.bereiche):
            n = len(b.melder)
            art = b.art + (f" F{b.feld}" if mehrere[b.art] else "")
            print(f"{kopf if i == 0 else '':<38}{art:<16}{b.flaeche:>7.1f} {b.hoehe:>6.2f}"
                  f"{b.a:>5.0f}{b.dh:>6.1f}{'%dx%d' % b.raster_n if n else '-':>8}{n:>7}"
                  f"{b.max_abstand if n else 0:>10.2f}")
            for hinweis in b.hinweise:
                print(f"{'':38}! {hinweis}")
        for hinweis in raum.hinweise:
            print(f"{'':38}> {hinweis}")
    print("-" * 105)
    print(f"Raeume: {len(raeume)}   Raummelder: {sum(r.anzahl('Raum') for r in raeume)}   "
          f"Zwischendeckenmelder: {sum(r.anzahl('Zwischendecke') for r in raeume)}")


# --------------------------------------------------------------------------- #
# Kommandozeile
# --------------------------------------------------------------------------- #
def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="Platziert punktfoermige Rauchmelder in den Raeumen (IfcSpace) einer IFC-Datei.")
    p.add_argument("ifc", type=Path, help="Eingabe-IFC mit Raumvolumen (IfcSpace)")
    p.add_argument("-o", "--ausgabe", type=Path, help="Ausgabeordner (Standard: neben der IFC-Datei)")
    p.add_argument("--ifc-ausgabe", action="store_true",
                   help="zusaetzlich IFC-Datei mit eingefuegten Meldern schreiben")
    p.add_argument("--kein-plan", action="store_true", help="keine PNG-Plaene erzeugen")
    d = Regeln()
    g = p.add_argument_group("Projektierungswerte")
    g.add_argument("--a-klein", type=float, default=d.a_klein, help="A [m2] fuer Raeume > Grenzflaeche und <= Grenzhoehe (%(default)s)")
    g.add_argument("--dh-klein", type=float, default=d.dh_klein, help="zugehoeriges DH [m] (%(default)s)")
    g.add_argument("--a-gross", type=float, default=d.a_gross, help="A [m2] sonst (%(default)s)")
    g.add_argument("--dh-gross", type=float, default=d.dh_gross, help="zugehoeriges DH [m] (%(default)s)")
    g.add_argument("--grenzflaeche", type=float, default=d.grenzflaeche, help="[m2] (%(default)s)")
    g.add_argument("--grenzhoehe", type=float, default=d.grenzhoehe, help="[m] (%(default)s)")
    g.add_argument("--max-hoehe", type=float, default=d.max_hoehe, help="[m] (%(default)s)")
    g.add_argument("--wandabstand", type=float, default=d.wandabstand, help="[m] (%(default)s)")
    g.add_argument("--gang-breite", type=float, default=d.gang_breite, help="Gangregel bis Breite [m] (%(default)s)")
    g.add_argument("--gang-abstand", type=float, default=d.gang_abstand, help="Melderabstand im Gang [m], 0 = Gangregel aus (%(default)s)")
    g.add_argument("--min-flaeche", type=float, default=d.min_flaeche, help="Raeume darunter ohne Melder [m2] (%(default)s)")
    g.add_argument("--zwischendecke-min", type=float, default=d.zwischendecke_min,
                   help="Hohlraumhoehe ueber Abhangdecke [m], ab der Zwischendeckenmelder gesetzt werden (%(default)s)")
    g.add_argument("--unterzug-min", type=float, default=d.unterzug_min,
                   help="Unterzughoehe unter der Decke [m], ab der die Decke geteilt wird; 0 = immer (%(default)s)")
    g.add_argument("--min-feld", type=float, default=d.min_feld,
                   help="Deckenfelder unterhalb dieser Flaeche [m2] bleiben ohne Melder (%(default)s)")
    a = p.add_argument_group("Achsausrichtung")
    a.add_argument("--max-verschiebung", type=float, default=d.max_verschiebung,
                   help="max. Verschiebung eines Raumrasters auf gemeinsame Achsen [m], 0 = aus (%(default)s)")
    a.add_argument("--raster", type=float, default=d.raster,
                   help="Deckenraster [m], z.B. 0.625; 0 = aus (%(default)s)")
    args = p.parse_args(argv)

    if not args.ifc.is_file():
        p.error(f"Datei nicht gefunden: {args.ifc}")
    regeln = Regeln(**{f: getattr(args, f) for f in Regeln.__dataclass_fields__})
    ordner = args.ausgabe or args.ifc.parent
    ordner.mkdir(parents=True, exist_ok=True)
    stamm = args.ifc.stem + "_rauchmelder"

    print(f"Lese {args.ifc} ...")
    modell = ifcopenshell.open(str(args.ifc))
    raeume = lade_raeume(modell)
    if not raeume:
        print("Keine Raeume (IfcSpace) mit Geometrie gefunden.", file=sys.stderr)
        return 1

    bauteile = lade_bauteile(modell)
    print("Gefunden: {} Raeume, {} Abhangdecken, {} Unterzuege, {} Rohdecken".format(
        len(raeume), *(len(bauteile[k]) for k in ("Abhangdecke", "Unterzug", "Rohdecke"))))
    achsen = platziere_alle(raeume, regeln, bauteile)
    bericht(raeume)

    csv_pfad = ordner / f"{stamm}.csv"
    schreibe_csv(raeume, csv_pfad)
    print(f"\nCSV : {csv_pfad}")
    if not args.kein_plan:
        for pfad in schreibe_plaene(raeume, achsen, ordner, stamm):
            print(f"Plan: {pfad}")
    if args.ifc_ausgabe:
        ifc_pfad = ordner / f"{stamm}.ifc"
        schreibe_ifc(modell, raeume, ifc_pfad)
        print(f"IFC : {ifc_pfad}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
