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
  * Rasterdecken: liegt eine Rasterdecke vor, sitzt jeder Melder mittig auf
    einer vollen Rasterplatte; Zwischendeckenmelder werden gegenueber dem
    Raummelder um eine Rasterplatte versetzt (gerade oder diagonal).
  * Unterzuege (IfcBeam): trennt ein Unterzug die Decke, wird jedes Deckenfeld
    wie ein eigener Raum projektiert (gilt fuer Raum und Zwischendecke).

Die Normwerte sind Voreinstellungen und muessen vom Fachplaner gegen die
gueltige Ausgabe der DIN VDE 0833-2 / VdS 2095 geprueft werden.
"""
from __future__ import annotations

import argparse
import csv
import math
import re
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
    # Rasterdecke: Rastermass [m] fuer als Rasterdecke erkannte Abhangdecken ohne
    # Massangabe im Modell (0 = keine Annahme)
    rasterdecke: float = 0.0
    # jede Abhangdecke ohne Massangabe als Rasterdecke mit `rasterdecke` behandeln
    rasterdecke_alle: bool = False
    # Lage des Rasters, wenn im Modell nicht definiert: "mitte" (symmetrisch) oder "ecke"
    raster_ursprung: str = "mitte"
    # Versatz Zwischendeckenmelder zum Raummelder: "auto" (erst gerade, dann
    # diagonal), "gerade" oder "diagonal"
    zd_versatz: str = "auto"
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
class Raster:
    """Plattenraster einer Rasterdecke im um `winkel` gedrehten System."""
    ru: float
    rv: float
    winkel: float | None = None                  # None = Hauptrichtung des Raums
    ursprung: tuple[float, float] | None = None  # eine Plattenecke; None = nicht definiert
    quelle: str = ""


@dataclass
class Bauteil:
    """Abhangdecke, Unterzug oder Rohdecke: Grundriss und Hoehenlage."""
    art: str
    name: str
    polygon: Polygon
    z_unten: float
    z_oben: float
    raster: Raster | None = None   # nur Abhangdecke: erkanntes Plattenraster
    ist_raster: bool = False       # als Rasterdecke bezeichnet, Mass unbekannt


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
    raster: Raster | None = None    # Rasterdecke: Melder nur mittig auf vollen Platten
    decke: Polygon | None = None    # Flaeche der Rasterdecke
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
    raster: list[tuple[Raster, Polygon]] = field(default_factory=list)  # fuer den Plan
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
            bauteil = Bauteil(art, element.Name or element.GlobalId, polygon,
                              float(verts[:, 2].min()), float(verts[:, 2].max()))
            if art == "Abhangdecke":
                bauteil.raster, bauteil.ist_raster = _raster_aus_angaben(element)
            ergebnis[art].append(bauteil)
    ergebnis["Abhangdecke"] = _platten_zusammenfassen(ergebnis["Abhangdecke"])
    return ergebnis


_RASTERMASS = re.compile(r"(?<![\d.,])(\d{3,4})\s*(?:mm)?\s*[x×]\s*(\d{3,4})(?![\d.,]*\s*[x×])")
_RASTERWORT = re.compile(r"raster|kassette|lay-?in|grid|tile", re.IGNORECASE)


def _raster_aus_angaben(element) -> tuple[Raster | None, bool]:
    """Sucht ein Rastermass wie "625x625" in Name, Typ, Material und Eigenschaften."""
    texte = [element.Name, getattr(element, "ObjectType", None), element.Description]
    typ = ifcopenshell.util.element.get_type(element)
    if typ is not None:
        texte += [typ.Name, typ.Description]
    try:
        texte += [m.Name for m in ifcopenshell.util.element.get_materials(element)]
    except Exception:
        pass
    try:
        psets = ifcopenshell.util.element.get_psets(element)
    except Exception:
        psets = {}
    for pset in psets.values():
        for schluessel, wert in pset.items():
            if isinstance(wert, str):
                texte.append(wert)
            elif isinstance(wert, (int, float)) and not isinstance(wert, bool) \
                    and re.search(r"raster|grid|modul", schluessel, re.IGNORECASE):
                mass = wert / 1000.0 if wert > 10 else float(wert)  # mm oder m
                if 0.3 <= mass <= 1.25:
                    return Raster(mass, mass, quelle=f"Eigenschaft {schluessel}"), True
    text = " | ".join(t for t in texte if t)
    for treffer in _RASTERMASS.finditer(text):
        a, b = (int(g) / 1000.0 for g in treffer.groups())
        if 0.3 <= a <= 1.25 and 0.3 <= b <= 1.25:
            return Raster(a, b, quelle=f'Angabe "{treffer.group(0)}"'), True
    return None, bool(_RASTERWORT.search(text))


def _platten_zusammenfassen(decken: list[Bauteil]) -> list[Bauteil]:
    """Einzeln modellierte Deckenplatten zu einer Rasterdecke zusammenfassen;
    Rastermass und -lage ergeben sich dann aus den Platten selbst."""
    def rechteckig(d: Bauteil) -> bool:
        return d.polygon.area <= 2.5 and \
            d.polygon.area >= 0.95 * d.polygon.minimum_rotated_rectangle.area

    platten = [d for d in decken if rechteckig(d)]
    rest = [d for d in decken if not rechteckig(d)]
    je_hoehe: dict[float, list[Bauteil]] = defaultdict(list)
    for platte in platten:
        je_hoehe[round(platte.z_unten, 2)].append(platte)
    for gruppe in je_hoehe.values():
        fugen = 0.03  # Platten ueber Tragschienen hinweg verbinden
        gesamt = unary_union([p.polygon.buffer(fugen, join_style="mitre") for p in gruppe])
        for teil in _polygone(gesamt):
            mitglieder = [p for p in gruppe if teil.contains(p.polygon.representative_point())]
            if len(mitglieder) < 4:
                rest += mitglieder
                continue
            flaeche = teil.buffer(-fugen, join_style="mitre")
            muster = sorted(mitglieder, key=lambda p: p.polygon.area)[len(mitglieder) // 2]
            winkel = hauptwinkel(muster.polygon)
            grenzen = np.array([affinity.rotate(p.polygon, -winkel, origin=(0, 0)).bounds
                                for p in mitglieder])
            masse = []
            for a, b in ((0, 2), (1, 3)):
                platte = float(np.median(grenzen[:, b] - grenzen[:, a]))
                kanten = np.unique(np.round(grenzen[:, a], 3))
                schritte = np.diff(kanten)
                schritte = schritte[(schritte > 0.8 * platte) & (schritte < 1.5 * platte)]
                masse.append(float(np.median(schritte)) if len(schritte) else platte)
            # Bezugsplatte: eine volle Platte (nicht der Randzuschnitt)
            voll = grenzen[np.argmax((grenzen[:, 2] - grenzen[:, 0]) * (grenzen[:, 3] - grenzen[:, 1]))]
            mitte = ((voll[0] + voll[2]) / 2, (voll[1] + voll[3]) / 2)
            raster = Raster(masse[0], masse[1], winkel,
                            (mitte[0] - masse[0] / 2, mitte[1] - masse[1] / 2),
                            f"{len(mitglieder)} modellierte Platten")
            rest.append(Bauteil("Abhangdecke", "Rasterdecke", flaeche,
                                min(p.z_unten for p in mitglieder),
                                max(p.z_oben for p in mitglieder), raster, True))
    return rest


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
    raum.bereiche, raum.unterzuege, raum.raster = [], [], []

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

    # Rasterdecken: Mass und Lage des Plattenrasters festlegen
    raster_je_decke: dict[int, Raster] = {}
    for decke, flaeche in decken:
        raster = decke.raster
        if raster is None and regeln.rasterdecke > 0 and (decke.ist_raster or regeln.rasterdecke_alle):
            raster = Raster(regeln.rasterdecke, regeln.rasterdecke, quelle="Vorgabe --rasterdecke")
        if raster is None:
            if decke.ist_raster:
                raum.hinweise.append("Rasterdecke ohne Rastermass im Modell - "
                                     "--rasterdecke angeben, Raster nicht beruecksichtigt")
            continue
        w = winkel if raster.winkel is None else raster.winkel
        ursprung, lage = raster.ursprung, "aus Modell"
        if ursprung is None:
            # Lage im Modell nicht definiert: Annahme laut `raster_ursprung`
            u0, v0, u1, v1 = affinity.rotate(flaeche, -w, origin=(0, 0)).bounds
            ursprung = []
            for anfang, ende, mass in ((u0, u1, raster.ru), (v0, v1, raster.rv)):
                rest = (ende - anfang) % mass
                if regeln.raster_ursprung == "ecke" or min(rest, mass - rest) < 0.005:
                    ursprung.append(anfang)                     # volle Platten ab der Wand
                else:
                    ursprung.append(anfang + (rest - mass) / 2)  # Platte mittig, gleiche Randzuschnitte
            lage = "Lage angenommen: " + ("ab Raumecke" if regeln.raster_ursprung == "ecke"
                                          else "mittig im Raum")
        fertig = Raster(raster.ru, raster.rv, w, tuple(ursprung), raster.quelle)
        raster_je_decke[id(decke)] = fertig
        raum.raster.append((fertig, flaeche))
        raum.hinweise.append(f"Rasterdecke {raster.ru * 1000:.0f} x {raster.rv * 1000:.0f} mm "
                             f"({raster.quelle}; {lage})")

    # Deckenhoehe des Raums: Unterkante Abhangdecke, wenn sie den Raum ueberwiegend bedeckt
    z_decke = raum.z_oben
    raum_raster, raum_decke = None, None
    if decken and sum(f.area for _, f in decken) >= 0.5 * raum.flaeche:
        haupt, raum_decke = max(decken, key=lambda d: d[1].area)
        z_decke = min(raum.z_oben, haupt.z_unten)
        raum_raster = raster_je_decke.get(id(haupt))

    felder, schnitt, reste = _deckenfelder(raum.polygon, raum.z_unten, z_decke,
                                           bauteile["Unterzug"], regeln)
    raum.unterzuege += schnitt
    if len(felder) > 1:
        raum.hinweise.append(f"Unterzug teilt den Raum in {len(felder)} Deckenfelder")
    if reste:
        raum.hinweise.append(f"{reste} Restflaeche(n) < {regeln.min_feld} m2 ohne Melder")
    for nr, feld in enumerate(felder, 1):
        if raum_raster is not None:
            raum.bereiche.append(Bereich("Raum", nr, feld, raum.z_unten, z_decke,
                                         raum_raster.winkel, raum_raster, raum_decke))
        else:
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
            raster = raster_je_decke.get(id(decke))
            raum.bereiche.append(Bereich("Zwischendecke", nr, feld, decke.z_oben, z_roh,
                                         winkel if raster is None else raster.winkel,
                                         raster, flaeche if raster is not None else None))


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


class _Feld:
    """Geometrie und Regelpruefung eines Bereichs im gedrehten System
    (Hauptrichtung bzw. Rasterrichtung = u-Achse)."""

    def __init__(self, b: Bereich, regeln: Regeln) -> None:
        self.b, self.regeln = b, regeln
        b.a, b.dh = regeln.werte(b.flaeche, b.hoehe)
        if b.art == "Raum" and b.hoehe > regeln.max_hoehe:
            b.hinweise.append(f"Raumhoehe {b.hoehe:.2f} m > {regeln.max_hoehe:.1f} m: "
                              "punktfoermige Rauchmelder nicht geeignet")
        self.poly = affinity.rotate(b.polygon, -b.winkel, origin=(0, 0))
        self.u0, self.v0, u1, v1 = self.poly.bounds
        self.laenge, self.breite = u1 - self.u0, v1 - self.v0

        self.zone = self.poly.buffer(-regeln.wandabstand)  # zulaessige Melderflaeche
        if self.zone.is_empty:
            self.zone = self.poly
            b.hinweise.append(f"Flaeche zu schmal fuer Wandabstand {regeln.wandabstand:.2f} m")

        self.pruef = _pruefpunkte(self.poly)
        self.mindestzahl = max(1, math.ceil(b.flaeche / b.a - 1e-9))
        self.dh = b.dh
        # Schmaler Gang: Abstand der Melder untereinander bis `gang_abstand`
        schmal = min(self.laenge, self.breite)
        if schmal < regeln.gang_breite and regeln.gang_abstand > 0:
            self.dh = max(self.dh, math.hypot(regeln.gang_abstand / 2.0, schmal / 2.0))
            self.mindestzahl = 1

        # Rasterdecke: zulaessig sind nur die Mitten voller Platten
        self.platten = None   # Kennzahlen der zulaessigen Platten
        self.us = self.vs = None
        if b.raster is not None:
            self._raster_vorbereiten()

    def _raster_vorbereiten(self) -> None:
        r = self.b.raster
        ou, ov = r.ursprung
        u0, v0, u1, v1 = self.poly.bounds
        i = np.arange(math.floor((u0 - ou) / r.ru), math.ceil((u1 - ou) / r.ru) + 1)
        j = np.arange(math.floor((v0 - ov) / r.rv), math.ceil((v1 - ov) / r.rv) + 1)
        gi, gj = (g.ravel() for g in np.meshgrid(i, j))
        decke = affinity.rotate(self.b.decke, -self.b.winkel, origin=(0, 0))
        decke = decke.intersection(self.poly).buffer(0.005)
        zellen = shapely.box(ou + gi * r.ru, ov + gj * r.rv,
                             ou + (gi + 1) * r.ru, ov + (gj + 1) * r.rv)
        cu, cv = ou + (gi + 0.5) * r.ru, ov + (gj + 0.5) * r.rv
        gut = shapely.contains(decke, zellen) & shapely.contains_xy(self.zone, cu, cv)
        if not gut.any():
            self.b.hinweise.append("keine volle Rasterplatte mit Wandabstand - "
                                   "Melder ohne Rasterbezug gesetzt")
            return
        self.platten = self._kennzahl(gi[gut], gj[gut])
        self.us, self.vs = np.unique(cu[gut]), np.unique(cv[gut])

    @staticmethod
    def _kennzahl(i, j):
        return np.asarray(i, dtype=np.int64) * 1_000_003 + np.asarray(j, dtype=np.int64)

    def platte(self, punkte: np.ndarray):
        """Plattenindex (i, j) und ob der Punkt mittig auf der Platte liegt."""
        r = self.b.raster
        fu = (punkte[:, 0] - r.ursprung[0]) / r.ru - 0.5
        fv = (punkte[:, 1] - r.ursprung[1]) / r.rv - 0.5
        i, j = np.rint(fu), np.rint(fv)
        mittig = (np.abs(fu - i) * r.ru < 1e-3) & (np.abs(fv - j) * r.rv < 1e-3)
        return i.astype(np.int64), j.astype(np.int64), mittig

    def zulaessig(self, punkte: np.ndarray) -> np.ndarray:
        """Maske: Wandabstand eingehalten und (bei Rasterdecke) mittig auf voller Platte."""
        maske = shapely.contains_xy(self.zone, punkte[:, 0], punkte[:, 1])
        if self.platten is not None:
            i, j, mittig = self.platte(punkte)
            maske &= mittig & np.isin(self._kennzahl(i, j), self.platten)
        return maske

    def gueltig(self, punkte: np.ndarray) -> float | None:
        """Gibt den max. Abstand zurueck, wenn A und DH eingehalten sind."""
        if len(punkte) < self.mindestzahl:
            return None
        abstand = _max_abstand(self.pruef, punkte)
        return abstand if abstand <= self.dh + 1e-6 else None

    def gitter(self, nu: int, nv: int) -> np.ndarray:
        us = self.u0 + (np.arange(nu) + 0.5) * self.laenge / nu
        vs = self.v0 + (np.arange(nv) + 0.5) * self.breite / nv
        if self.platten is not None:  # Reihen auf die naechste Plattenmitte legen
            us = np.unique(self.us[np.abs(self.us[None, :] - us[:, None]).argmin(axis=1)])
            vs = np.unique(self.vs[np.abs(self.vs[None, :] - vs[:, None]).argmin(axis=1)])
        gu, gv = np.meshgrid(us, vs)
        punkte = np.column_stack([gu.ravel(), gv.ravel()])
        return punkte[self.zulaessig(punkte)]

    def suche(self) -> np.ndarray | None:
        """Kleinstes regelkonformes Raster (Punkte im gedrehten System)."""
        b, max_n = self.b, 80
        # Kandidaten nach Melderzahl, dann nach moeglichst quadratischen Feldern
        kandidaten = [
            (nu * nv, abs(math.log((self.laenge / nu) / (self.breite / nv))), nu, nv)
            for nu in range(1, max_n + 1) for nv in range(1, max_n + 1)
            if self.laenge / nu >= 0.5 and self.breite / nv >= 0.5 or (nu == 1 or nv == 1)
        ]
        kandidaten.sort()
        beste, grenze = None, None
        for produkt, form, nu, nv in kandidaten:
            if grenze is not None and produkt > grenze:
                break
            if produkt < self.mindestzahl:
                continue
            punkte = self.gitter(nu, nv)
            if self.gueltig(punkte) is None:
                continue
            if beste is None or (len(punkte), form) < beste[:2]:
                beste = (len(punkte), form, punkte, nu, nv)
            if grenze is None:
                # verwinkelte Flaechen: groebere Raster koennen weniger Melder ergeben
                grenze = produkt * 1.5
        if beste is not None:
            b.raster_n = beste[3:]
            return beste[2]
        # Notloesung: ein Melder im Punkt mit groesstem Wandabstand
        punkte = np.asarray(shapely.maximum_inscribed_circle(self.poly).coords)[:1]
        if self.platten is not None:
            gu, gv = np.meshgrid(self.us, self.vs)
            alle = np.column_stack([gu.ravel(), gv.ravel()])
            alle = alle[self.zulaessig(alle)]
            punkte = alle[[np.hypot(*(alle - punkte[0]).T).argmin()]]
        b.raster_n = (1, 1)
        if self.gueltig(punkte) is None:
            b.hinweise.append("kein regelkonformes Raster gefunden - manuell pruefen")
        return punkte

    def ausrichten(self, punkte: np.ndarray, achsen: Achsen) -> np.ndarray:
        """Verschiebt das Raster auf vorhandene Achsen bzw. das Deckenraster.
        Bei einer Rasterdecke nur um ganze Platten."""
        regeln, b = self.regeln, self.b
        if regeln.max_verschiebung <= 0:
            return punkte
        punkte = punkte.copy()
        for richtung in (0, 1):
            eigene = np.unique(np.round(punkte[:, richtung], 4))
            ziele = list(achsen.liste(b.winkel, richtung))
            schritt = None
            if self.platten is not None:
                schritt = (b.raster.ru, b.raster.rv)[richtung]
            elif regeln.raster > 0:
                ziele += [round(e / regeln.raster) * regeln.raster for e in eigene]
            deltas = {round(z - e, 4) for z in ziele for e in eigene
                      if abs(z - e) <= regeln.max_verschiebung}
            if schritt:
                deltas = {d for d in deltas if abs(d / schritt - round(d / schritt)) * schritt < 1e-3}
            if not deltas:
                continue

            def treffer(delta: float) -> int:
                return sum(any(abs(e + delta - z) < 1e-3 for z in ziele) for e in eigene)

            for delta in sorted(deltas, key=lambda d: (-treffer(d), abs(d))):
                if abs(delta) < 1e-6:
                    break  # liegt bereits auf einer Achse
                versuch = punkte.copy()
                versuch[:, richtung] += delta
                if not self.zulaessig(versuch).all() or self.gueltig(versuch) is None:
                    continue
                punkte = versuch
                break
        return punkte

    def eintragen(self, punkte: np.ndarray, achsen: Achsen) -> None:
        b = self.b
        b.max_abstand = _max_abstand(self.pruef, punkte)
        for richtung in (0, 1):
            achsen.merken(b.winkel, richtung, np.unique(np.round(punkte[:, richtung], 4)))
        b.melder = _drehen(punkte, -b.winkel)


def platziere(bereich: Bereich, regeln: Regeln, achsen: Achsen) -> None:
    """Bestimmt die Melderpositionen eines Bereichs und traegt sie dort ein."""
    feld = _Feld(bereich, regeln)
    punkte = feld.suche()
    if not bereich.hinweise or "manuell" not in bereich.hinweise[-1]:
        punkte = feld.ausrichten(punkte, achsen)
    feld.eintragen(punkte, achsen)


def platziere_zwischendecke(bereich: Bereich, raum: Raum, regeln: Regeln, achsen: Achsen) -> None:
    """Zwischendeckenmelder ueber einer Rasterdecke: gegenueber dem Raummelder
    um eine Rasterplatte versetzt (gerade oder diagonal)."""
    feld = _Feld(bereich, regeln)
    if feld.platten is None:
        return platziere(bereich, regeln, achsen)
    r = bereich.raster
    gerade = [(1, 0), (-1, 0), (0, 1), (0, -1)]
    diagonal = [(1, 1), (-1, 1), (1, -1), (-1, -1)]
    versatz = {"gerade": gerade, "diagonal": diagonal}.get(regeln.zd_versatz, gerade + diagonal)
    schritte = [np.array([di * r.ru, dj * r.rv]) for di, dj in versatz]

    # Raummelder unter derselben Rasterdecke, im gedrehten System
    rm = [_drehen(b.melder, bereich.winkel) for b in raum.bereiche
          if b.art == "Raum" and b.raster is bereich.raster and len(b.melder)]
    rm = np.vstack(rm) if rm else np.zeros((0, 2))
    belegt = feld._kennzahl(*feld.platte(rm)[:2]) if len(rm) else np.array([], dtype=np.int64)

    def frei(punkte: np.ndarray) -> bool:
        """Kein Zwischendeckenmelder auf der Platte eines Raummelders."""
        return not np.isin(feld._kennzahl(*feld.platte(punkte)[:2]), belegt).any()

    def versetzt(basis: np.ndarray, auswahl) -> np.ndarray | None:
        for schritt in auswahl:
            versuch = basis + schritt
            if feld.zulaessig(versuch).all() and feld.gueltig(versuch) is not None and frei(versuch):
                return versuch
        return None

    alle = [np.array([di * r.ru, dj * r.rv]) for di, dj in gerade + diagonal]
    # 1. Wahl: jeder Raummelder in diesem Feld bekommt seinen versetzten Partner
    im_feld = rm[shapely.contains_xy(feld.poly, rm[:, 0], rm[:, 1])] if len(rm) else rm
    if len(im_feld):
        punkte = versetzt(im_feld, schritte)
        if punkte is not None:
            bereich.raster_n = (0, 0)  # Positionen aus den Raummeldern abgeleitet
            return feld.eintragen(punkte, achsen)

    # 2. Wahl: eigenes Raster fuer dieses Feld, bei Bedarf um eine Platte verschoben
    punkte = feld.ausrichten(feld.suche(), achsen)
    if not frei(punkte):
        neu = versetzt(punkte, schritte)
        if neu is None and len(schritte) < len(alle):
            neu = versetzt(punkte, alle)
            if neu is not None:
                bereich.hinweise.append(f"Versatz '{regeln.zd_versatz}' nicht regelkonform moeglich - "
                                        "andere Nachbarplatte gewaehlt")
        if neu is None:
            bereich.hinweise.append("Versatz zum Raummelder um eine Rasterplatte nicht moeglich - "
                                    "manuell pruefen")
        else:
            punkte = neu
    feld.eintragen(punkte, achsen)


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
    for bereich in sorted((b for b in bereiche if b.art == "Raum"), key=lambda b: -b.flaeche):
        platziere(bereich, regeln, achsen)
    zwischendecken = [(b, raum) for raum in raeume for b in raum.bereiche if b.art != "Raum"]
    for bereich, raum in sorted(zwischendecken, key=lambda z: -z[0].flaeche):
        platziere_zwischendecke(bereich, raum, regeln, achsen)
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
            for raster, decke in raum.raster:  # Plattenraster der Rasterdecke
                rand, = ax.fill(*decke.exterior.xy, facecolor="none", edgecolor="none")
                u0, v0, u1, v1 = affinity.rotate(decke, -raster.winkel, origin=(0, 0)).bounds
                for achse, (anfang, ende, mass, start) in enumerate((
                        (u0, u1, raster.ru, raster.ursprung[0]),
                        (v0, v1, raster.rv, raster.ursprung[1]))):
                    k = np.arange(math.ceil((anfang - start) / mass), math.floor((ende - start) / mass) + 1)
                    for wert in start + k * mass:
                        linie = np.array([[wert, v0], [wert, v1]] if achse == 0
                                         else [[u0, wert], [u1, wert]])
                        linie = _drehen(linie, -raster.winkel)
                        strich, = ax.plot(linie[:, 0], linie[:, 1], color="#aab7c4", lw=0.3, zorder=1.2)
                        strich.set_clip_path(rand)
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
            Line2D([], [], color="#aab7c4", lw=0.6, label="Rasterdecke"),
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
                  f"{b.a:>5.0f}{b.dh:>6.1f}{('%dx%d' % b.raster_n if b.raster_n != (0, 0) else 'RM+1') if n else '-':>8}{n:>7}"
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
    g.add_argument("--rasterdecke", type=float, default=d.rasterdecke,
                   help="Rastermass [m] fuer Rasterdecken ohne Massangabe im Modell, z.B. 0.625 (%(default)s)")
    g.add_argument("--rasterdecke-alle", action="store_true",
                   help="jede Abhangdecke ohne Massangabe als Rasterdecke mit --rasterdecke behandeln")
    g.add_argument("--raster-ursprung", choices=["mitte", "ecke"], default=d.raster_ursprung,
                   help="Lage des Plattenrasters, wenn im Modell nicht definiert (%(default)s)")
    g.add_argument("--zd-versatz", choices=["auto", "gerade", "diagonal"], default=d.zd_versatz,
                   help="Versatz Zwischendeckenmelder zum Raummelder um eine Rasterplatte (%(default)s)")
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
