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
  * Druckknopfmelder: an jedem Ausgang in ein Treppenhaus und an jedem Ausgang
    ins Freie im Erdgeschoss (IfcDoor), dazu in Verkehrswegen alle 25 m.
  * Meldegruppen: alle Melder werden nach einer Strategie Meldegruppen
    zugeordnet. Die Zuordnung steht in einer Tabelle (CSV), die nachtraeglich
    bearbeitet werden kann; beim naechsten Lauf wird sie uebernommen.
  * Brandabschnitte: als Raumpolygone vorhandene Brandabschnitte werden
    eingelesen; Meldegruppen ueberschreiten sie nicht. Grosse Brandabschnitte
    werden in virtuelle Brandabschnitte aus benachbarten Raeumen geteilt.
  * Unterzuege (IfcBeam): trennt ein Unterzug die Decke, wird jedes Deckenfeld
    wie ein eigener Raum projektiert (gilt fuer Raum und Zwischendecke).

Die Normwerte sind Voreinstellungen und muessen vom Fachplaner gegen die
gueltige Ausgabe der DIN VDE 0833-2 / VdS 2095 geprueft werden.
"""
from __future__ import annotations

import argparse
import csv
import heapq
import math
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import shapely
from shapely import affinity
from shapely.geometry import MultiPoint, MultiPolygon, Point, Polygon
from shapely.ops import nearest_points, unary_union

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
    # Druckknopfmelder (Handfeuermelder)
    ohne_dkm: bool = False
    dkm_abstand: float = 25.0     # m, Abstand der Melder entlang eines Verkehrswegs
    dkm_hoehe: float = 1.4        # m ueber Fussboden
    dkm_tuerabstand: float = 0.3  # m seitlich neben der Tueroeffnung
    tuer_toleranz: float = 0.5    # m, max. Abstand Tuer - Raum (halbe Wanddicke)
    muster_treppenhaus: str = r"trepp|\btrh\b|stair"
    muster_verkehrsweg: str = r"flur|gang|korridor|corridor|verkehr|foyer|lobby|windfang|diele|hallway"
    erdgeschoss: str = ""         # Name des Erdgeschosses ("" = automatisch)
    # Brandabschnitte
    muster_brandabschnitt: str = r"brandabschnitt|\bBA[\s_.-]*\d|\bBRA\b|fire\s*compartment"
    vba_max_flaeche: float = 1600.0  # m2 je virtuellem Brandabschnitt
    # Meldegruppen
    mg_max_melder: int = 32        # automatische Melder je Meldegruppe
    mg_max_dkm: int = 10           # Druckknopfmelder je Meldegruppe
    mg_max_raeume: int = 5         # Raeume je Meldegruppe (mehrere Raeume)
    mg_max_flaeche: float = 400.0  # m2 je Meldegruppe bei mehreren Raeumen
    mg_max_gesamt: float = 1600.0  # m2 je Meldegruppe insgesamt
    mg_start: int = 1              # erste Meldegruppennummer
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
class Brandabschnitt:
    name: str
    polygon: Polygon
    z_unten: float
    z_oben: float


@dataclass
class Tuer:
    name: str
    polygon: Polygon
    z_unten: float
    extern: bool | None   # Pset_DoorCommon.IsExternal, falls vorhanden


@dataclass
class Druckknopf:
    x: float
    y: float
    z: float
    grund: str
    name: str = ""


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
    treppenhaus: bool = False
    verkehrsweg: bool = False
    tueren: list[Polygon] = field(default_factory=list)
    druckknopf: list[Druckknopf] = field(default_factory=list)
    ba: str = ""    # Brandabschnitt
    vba: str = ""   # virtueller Brandabschnitt
    mg: dict[str, str] = field(default_factory=dict)       # Art (RM/ZD/DKM) -> Meldegruppe
    adresse: dict[str, tuple[str, int]] = field(default_factory=dict)  # Melder -> (MG, Nr.)

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


def _ist_brandabschnitt(element, muster: str | None) -> bool:
    if not muster:
        return False
    if element.is_a("IfcSpatialZone") and getattr(element, "PredefinedType", None) == "FIRESAFETY":
        return True
    text = " ".join(t for t in (element.Name, getattr(element, "LongName", None),
                                getattr(element, "ObjectType", None)) if t)
    return bool(re.search(muster, text, re.IGNORECASE))


def lade_raeume(modell: ifcopenshell.file, muster_brandabschnitt: str | None = None) -> list[Raum]:
    """Liest alle IfcSpace mit Geometrie; Koordinaten in Metern (Weltkoordinaten).
    Raumpolygone, die Brandabschnitte darstellen, sind keine Raeume."""
    einstellungen = ifcopenshell.geom.settings()
    einstellungen.set("use-world-coords", True)
    raeume: list[Raum] = []
    for space in modell.by_type("IfcSpace"):
        if _ist_brandabschnitt(space, muster_brandabschnitt):
            continue
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
# Druckknopfmelder
# --------------------------------------------------------------------------- #
def lade_tueren(modell: ifcopenshell.file) -> list[Tuer]:
    einstellungen = ifcopenshell.geom.settings()
    einstellungen.set("use-world-coords", True)
    tueren = []
    for element in modell.by_type("IfcDoor"):
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
        try:
            extern = ifcopenshell.util.element.get_psets(element).get(
                "Pset_DoorCommon", {}).get("IsExternal")
        except Exception:
            extern = None
        tueren.append(Tuer(element.Name or element.GlobalId, polygon,
                           float(verts[:, 2].min()), extern if isinstance(extern, bool) else None))
    return tueren


def _erdgeschoss(modell: ifcopenshell.file, regeln: Regeln) -> str | None:
    """Name des Erdgeschosses: Vorgabe, sonst ueblicher Name, sonst Hoehe naechst 0."""
    geschosse = modell.by_type("IfcBuildingStorey")
    if not geschosse:
        return None
    name = lambda g: g.Name or g.GlobalId  # noqa: E731
    if regeln.erdgeschoss:
        for g in geschosse:
            if name(g).lower() == regeln.erdgeschoss.lower():
                return name(g)
        print(f"  ! Geschoss '{regeln.erdgeschoss}' nicht gefunden - Erdgeschoss automatisch bestimmt",
              file=sys.stderr)
    ueblich = re.compile(r"^(eg|e0|e00|00|0|erdgeschoss|ground( floor)?|gf|l0|level 0)$", re.IGNORECASE)
    for g in geschosse:
        if ueblich.match(name(g).strip()):
            return name(g)
    faktor = ifcopenshell.util.unit.calculate_unit_scale(modell)
    return name(min(geschosse, key=lambda g: abs((g.Elevation or 0.0) * faktor)))


def _an_tuer(raum: Raum, tuer: Tuer, regeln: Regeln) -> tuple[float, float]:
    """Wandpunkt im Raum seitlich neben der Tuer."""
    rand = raum.polygon.exterior
    s = rand.project(tuer.polygon.centroid)
    mitte = rand.interpolate(s)
    ecken = np.asarray(tuer.polygon.minimum_rotated_rectangle.exterior.coords)
    breite = float(np.hypot(*np.diff(ecken, axis=0).T).max())
    versatz = breite / 2 + regeln.dkm_tuerabstand
    punkt = None
    for richtung in (1, -1):
        kandidat = rand.interpolate((s + richtung * versatz) % rand.length)
        if punkt is None:
            punkt = kandidat
        if kandidat.distance(mitte) >= versatz - 0.05:  # gleiche Wand, nicht um die Ecke
            punkt = kandidat
            break
    return punkt.x, punkt.y


def _mittellinie(polygon: Polygon):
    """Naeherung der Mittelachse eines Raums als Graph (Knoten, Nachbarlisten)."""
    rand = shapely.segmentize(polygon.boundary, 0.5)
    punkte = MultiPoint(shapely.get_coordinates(rand))
    kanten = shapely.voronoi_polygons(punkte, only_edges=True)
    abstand_max = shapely.maximum_inscribed_circle(polygon).length
    grenze = min(0.4, 0.5 * abstand_max)
    knoten: dict[tuple[float, float], int] = {}
    nachbarn: list[list[tuple[int, float]]] = []

    def index(p) -> int:
        schluessel = (round(p[0], 3), round(p[1], 3))
        if schluessel not in knoten:
            knoten[schluessel] = len(nachbarn)
            nachbarn.append([])
        return knoten[schluessel]

    for linie in shapely.get_parts(kanten):
        xy = shapely.get_coordinates(linie)
        for a, b in zip(xy[:-1], xy[1:]):
            strecke = shapely.LineString([a, b])
            if not polygon.contains(strecke):
                continue
            if min(polygon.boundary.distance(Point(a)), polygon.boundary.distance(Point(b))) < grenze:
                continue  # Aeste in die Raumecken abschneiden
            i, j = index(a), index(b)
            laenge = float(np.hypot(*(b - a)))
            nachbarn[i].append((j, laenge))
            nachbarn[j].append((i, laenge))
    return np.array(list(knoten.keys())).reshape(-1, 2), nachbarn


def _wege(nachbarn, quellen: list[int]):
    """Kuerzeste Wege von mehreren Quellen: Abstand, Vorgaenger, naechste Quelle."""
    n = len(nachbarn)
    abstand, vor, quelle = [math.inf] * n, [-1] * n, [-1] * n
    halde = []
    for q in quellen:
        abstand[q], quelle[q] = 0.0, q
        heapq.heappush(halde, (0.0, q))
    while halde:
        d, i = heapq.heappop(halde)
        if d > abstand[i]:
            continue
        for j, laenge in nachbarn[i]:
            if d + laenge < abstand[j]:
                abstand[j], vor[j], quelle[j] = d + laenge, i, quelle[i]
                heapq.heappush(halde, (d + laenge, j))
    return abstand, vor, quelle


def _verkehrsweg(raum: Raum, regeln: Regeln) -> None:
    """Setzt entlang der Mittelachse des Verkehrswegs alle `dkm_abstand` einen Melder."""
    knoten, nachbarn = _mittellinie(raum.polygon)
    if len(knoten) < 2:
        return
    z = raum.z_unten + regeln.dkm_hoehe
    grenzwert = regeln.dkm_abstand

    def naechster(x: float, y: float) -> int:
        return int(np.hypot(knoten[:, 0] - x, knoten[:, 1] - y).argmin())

    quellen = [naechster(d.x, d.y) for d in raum.druckknopf]
    neue: list[int] = []
    if not quellen:  # kein Ausgang im Raum: am Ende des Verkehrswegs beginnen
        abstand, _, _ = _wege(nachbarn, [0])
        erreichbar = [i for i, d in enumerate(abstand) if d < math.inf]
        start = max(erreichbar, key=lambda i: abstand[i])
        quellen.append(start)
        neue.append(start)

    for _ in range(500):
        abstand, vor, quelle = _wege(nachbarn, quellen)
        # 1. Zwischen zwei Meldern: Luecke groesser als der Abstand gleichmaessig teilen
        luecke = None
        for i, liste in enumerate(nachbarn):
            for j, laenge in liste:
                if i < j and quelle[i] != quelle[j] and quelle[i] >= 0 and quelle[j] >= 0:
                    gesamt = abstand[i] + laenge + abstand[j]
                    if gesamt > grenzwert + 0.01 and (luecke is None or gesamt > luecke[0]):
                        luecke = (gesamt, i, j)
        if luecke is not None:
            gesamt, i, j = luecke
            ziel = gesamt / math.ceil(gesamt / grenzwert - 1e-9)
            if ziel > abstand[i]:   # Zielpunkt liegt auf der Seite von j
                i, ziel = j, gesamt - ziel
            while vor[i] >= 0 and abstand[i] > ziel:
                i = vor[i]
            if abstand[i] < 1.0:    # zu nah am vorhandenen Melder: Rasterweite der Achse erreicht
                break
            quellen.append(i)
            neue.append(i)
            continue
        # 2. Ende des Wegs: nach jeweils `dkm_abstand` ein weiterer Melder
        fern = [i for i, d in enumerate(abstand) if d >= grenzwert]
        if not fern:
            break
        i = min(fern, key=lambda k: (abstand[k] == math.inf, abstand[k]))
        if abstand[i] > grenzwert and vor[i] >= 0 and abstand[vor[i]] > 1.0:
            i = vor[i]  # letzter Knoten innerhalb des Abstands
        quellen.append(i)
        neue.append(i)

    rand = raum.polygon.exterior
    for i in neue:
        wand = nearest_points(rand, Point(knoten[i]))[0]
        s = rand.project(wand)
        for versatz in (0.0, 0.8, -0.8, 1.6, -1.6):  # nicht in eine Tueroeffnung setzen
            punkt = rand.interpolate((s + versatz) % rand.length)
            if all(t.distance(punkt) > regeln.tuer_toleranz + 0.3 for t in raum.tueren):
                break
        raum.druckknopf.append(Druckknopf(punkt.x, punkt.y, z, f"Verkehrsweg, alle {grenzwert:.0f} m"))


def klassifiziere_raeume(modell: ifcopenshell.file, raeume: list[Raum], regeln: Regeln) -> None:
    """Kennzeichnet Treppenhaeuser und Verkehrswege (Raumname bzw. IfcStair im Raum)."""
    treppe = re.compile(regeln.muster_treppenhaus, re.IGNORECASE)
    weg = re.compile(regeln.muster_verkehrsweg, re.IGNORECASE)

    # Treppen im Modell kennzeichnen den Raum ebenfalls als Treppenhaus
    einstellungen = ifcopenshell.geom.settings()
    einstellungen.set("use-world-coords", True)
    treppen = []
    for element in modell.by_type("IfcStair") + modell.by_type("IfcStairFlight"):
        if element.Representation is None:
            continue
        try:
            verts = np.asarray(ifcopenshell.geom.create_shape(einstellungen, element).geometry.verts
                               ).reshape(-1, 3)
        except Exception:
            continue
        treppen.append((Point(verts[:, 0].mean(), verts[:, 1].mean()), verts[:, 2].min(), verts[:, 2].max()))

    for raum in raeume:
        text = " ".join(t for t in (raum.nummer, raum.name,
                                    getattr(raum.element, "ObjectType", None)) if t)
        raum.treppenhaus = bool(treppe.search(text)) or any(
            raum.polygon.contains(p) and unten < raum.z_oben and oben > raum.z_unten
            for p, unten, oben in treppen)
        raum.verkehrsweg = not raum.treppenhaus and bool(weg.search(text))


def platziere_druckknopfmelder(modell: ifcopenshell.file, raeume: list[Raum], regeln: Regeln) -> int:
    """Druckknopfmelder an Ausgaengen (Treppenhaus, ins Freie im EG) und in Verkehrswegen.
    Rueckgabe: Anzahl der ausgewerteten Tueren."""
    eg = _erdgeschoss(modell, regeln)
    klassifiziere_raeume(modell, raeume, regeln)
    for raum in raeume:
        raum.druckknopf, raum.tueren = [], []

    def setzen(raum: Raum, tuer: Tuer, grund: str) -> None:
        x, y = _an_tuer(raum, tuer, regeln)
        if any(math.hypot(d.x - x, d.y - y) < 3.0 for d in raum.druckknopf):
            return  # benachbarte Tueren desselben Ausgangs: ein Melder genuegt
        raum.druckknopf.append(Druckknopf(x, y, raum.z_unten + regeln.dkm_hoehe, grund))

    tueren = lade_tueren(modell)
    for tuer in tueren:
        nah = sorted(((raum.polygon.distance(tuer.polygon), k) for k, raum in enumerate(raeume)
                      if raum.z_unten - 0.3 <= tuer.z_unten <= raum.z_oben - 0.5
                      and raum.polygon.distance(tuer.polygon) <= regeln.tuer_toleranz))[:2]
        anliegend = [raeume[k] for _, k in nah]
        for raum in anliegend:
            raum.tueren.append(tuer.polygon)
        if len(anliegend) == 2:
            a, b = anliegend
            if a.treppenhaus != b.treppenhaus:
                setzen(b if a.treppenhaus else a, tuer, "Ausgang ins Treppenhaus")
        elif len(anliegend) == 1 and tuer.extern is not False and anliegend[0].geschoss == eg:
            setzen(anliegend[0], tuer, "Ausgang ins Freie (EG)")

    for raum in raeume:
        if raum.verkehrsweg:
            _verkehrsweg(raum, regeln)
        for nr, melder in enumerate(raum.druckknopf, 1):
            melder.name = f"{raum.nummer or 'R'}-DKM{nr:02d}"
    return len(tueren)


def schreibe_csv_dkm(raeume: list[Raum], pfad: Path) -> None:
    zeilen = [[*raum.adresse.get(d.name, ("", "")), d.name,
               raum.geschoss, raum.nummer, raum.name, f"{d.x:.3f}", f"{d.y:.3f}", f"{d.z:.3f}",
               d.grund, raum.guid]
              for raum in sorted(raeume, key=lambda r: (r.geschoss, r.nummer)) for d in raum.druckknopf]
    zeilen.sort(key=_nach_meldegruppe)
    with pfad.open("w", newline="", encoding="utf-8-sig") as datei:
        w = csv.writer(datei, delimiter=";")
        # Meldegruppe und Meldernummer getrennt: "3/1" wuerde Excel als Datum lesen
        w.writerow(["Meldegruppe", "Meldernummer", "Kennung", "Geschoss", "Raumnummer",
                    "Raumname", "X_m", "Y_m", "Z_m", "Grund", "Raum_GUID"])
        w.writerows(zeilen)


def bericht_dkm(raeume: list[Raum]) -> None:
    print(f"\n{'Geschoss':<10}{'Raum':<8}{'Name':<20}{'Druckknopfmelder':<18}{'X':>8}{'Y':>8}  Grund")
    print("-" * 105)
    for raum in sorted(raeume, key=lambda r: (r.geschoss, r.nummer)):
        for d in raum.druckknopf:
            print(f"{raum.geschoss[:9]:<10}{raum.nummer[:7]:<8}{raum.name[:19]:<20}{melder_bezeichnung(raum, d.name):<18}"
                  f"{d.x:>8.2f}{d.y:>8.2f}  {d.grund}")
    print("-" * 105)
    treppen = sorted(r.nummer for r in raeume if r.treppenhaus)
    wege = sorted(r.nummer for r in raeume if r.verkehrsweg)
    print(f"Druckknopfmelder: {sum(len(r.druckknopf) for r in raeume)}   "
          f"Treppenhaeuser: {', '.join(treppen) or '-'}   Verkehrswege: {', '.join(wege) or '-'}")


# --------------------------------------------------------------------------- #
# Brandabschnitte
# --------------------------------------------------------------------------- #
def lade_brandabschnitte(modell: ifcopenshell.file, regeln: Regeln) -> list[Brandabschnitt]:
    """Brandabschnitte als Raumpolygone: IfcSpace bzw. IfcSpatialZone, deren
    Name/Typ auf `muster_brandabschnitt` passt (oder Typ FIRESAFETY)."""
    einstellungen = ifcopenshell.geom.settings()
    einstellungen.set("use-world-coords", True)
    elemente = list(modell.by_type("IfcSpace"))
    try:
        elemente += modell.by_type("IfcSpatialZone")
    except RuntimeError:
        pass  # IFC2X3 kennt IfcSpatialZone nicht
    abschnitte = []
    for element in elemente:
        if not _ist_brandabschnitt(element, regeln.muster_brandabschnitt):
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
        if polygon is not None:
            name = element.Name or getattr(element, "LongName", None) or element.GlobalId
            abschnitte.append(Brandabschnitt(name, polygon, float(verts[:, 2].min()),
                                             float(verts[:, 2].max())))
    return abschnitte


def ordne_brandabschnitte(modell: ifcopenshell.file, raeume: list[Raum],
                          abschnitte: list[Brandabschnitt], regeln: Regeln) -> None:
    """Ordnet jeden Raum seinem Brandabschnitt zu und teilt Brandabschnitte ueber
    `vba_max_flaeche` in virtuelle Brandabschnitte aus benachbarten Raeumen."""
    # Zuordnung ueber IfcZone (Raumgruppe ohne eigene Geometrie)
    aus_zone: dict[str, str] = {}
    for zone in modell.by_type("IfcZone"):
        if not _ist_brandabschnitt(zone, regeln.muster_brandabschnitt):
            continue
        for beziehung in zone.IsGroupedBy or []:
            for objekt in beziehung.RelatedObjects:
                aus_zone[objekt.GlobalId] = zone.Name or zone.GlobalId

    vorhanden = bool(abschnitte or aus_zone)
    for raum in raeume:
        raum.ba = aus_zone.get(raum.guid, "")
        if not raum.ba and abschnitte:
            anteile = sorted(((a.polygon.intersection(raum.polygon).area, a.name) for a in abschnitte
                              if a.z_unten < raum.z_oben - 0.1 and a.z_oben > raum.z_unten + 0.1
                              and a.polygon.intersects(raum.polygon)), reverse=True)
            if anteile and anteile[0][0] > 0.01:
                raum.ba = anteile[0][1]
                if len(anteile) > 1 and anteile[1][1] != raum.ba and anteile[1][0] > 0.1 * raum.flaeche:
                    raum.hinweise.append(f"Raum liegt in mehreren Brandabschnitten "
                                         f"({raum.ba}, {anteile[1][1]}) - groesserem Anteil zugeordnet")
        if not raum.ba and vorhanden:
            raum.ba = "ohne BA"
            raum.hinweise.append("Raum liegt in keinem Brandabschnitt")

    # Virtuelle Brandabschnitte je Brandabschnitt und Geschoss
    gruppen: dict[tuple[str, str], list[Raum]] = defaultdict(list)
    for raum in raeume:
        gruppen[(raum.ba, raum.geschoss)].append(raum)
    for (ba, geschoss), mitglieder in gruppen.items():
        basis = f"{ba} {geschoss}" if ba else geschoss
        gesamt = sum(r.flaeche for r in mitglieder)
        teile = max(1, math.ceil(gesamt / regeln.vba_max_flaeche - 1e-9))
        if teile == 1:
            for raum in mitglieder:
                raum.vba = basis
            continue
        # Zusammenhaengende Raumgruppen entlang der Laengsachse des Brandabschnitts bilden
        umriss = unary_union([r.polygon for r in mitglieder])
        winkel = hauptwinkel(umriss.convex_hull if umriss.geom_type != "Polygon" else umriss)
        u0, v0, u1, v1 = affinity.rotate(umriss, -winkel, origin=(0, 0)).bounds
        achse = 0 if (u1 - u0) >= (v1 - v0) else 1
        lage = {r.guid: float(_drehen(np.array([[r.polygon.centroid.x, r.polygon.centroid.y]]),
                                      winkel)[0, achse]) for r in mitglieder}

        def nachbarn(raum: Raum, gruppe: list[Raum]) -> bool:
            return any(raum.polygon.distance(g.polygon) <= 0.6 for g in gruppe)

        ziel = gesamt / teile
        rest = sorted(mitglieder, key=lambda r: lage[r.guid])
        gruppen_vba: list[list[Raum]] = []
        while rest:
            gruppe = [rest.pop(0)]
            flaeche = gruppe[0].flaeche
            while True:
                kandidaten = [r for r in rest if nachbarn(r, gruppe)]
                if not kandidaten:
                    break
                naechster = min(kandidaten, key=lambda r: lage[r.guid])
                if flaeche + naechster.flaeche / 2 > ziel or \
                        flaeche + naechster.flaeche > regeln.vba_max_flaeche:
                    break
                gruppe.append(naechster)
                rest.remove(naechster)
                flaeche += naechster.flaeche
            gruppen_vba.append(gruppe)
        # Kleine Restgruppen an eine benachbarte Gruppe anschliessen, wenn die Flaeche es zulaesst
        geaendert = True
        while geaendert:
            geaendert = False
            for klein in sorted(gruppen_vba, key=lambda g: sum(r.flaeche for r in g)):
                flaeche = sum(r.flaeche for r in klein)
                if flaeche >= 0.5 * ziel:
                    break
                ziele = [g for g in gruppen_vba if g is not klein
                         and any(nachbarn(r, g) for r in klein)
                         and flaeche + sum(r.flaeche for r in g) <= regeln.vba_max_flaeche]
                if ziele:
                    min(ziele, key=lambda g: sum(r.flaeche for r in g)).extend(klein)
                    gruppen_vba = [g for g in gruppen_vba if g is not klein]
                    geaendert = True
                    break
        gruppen_vba.sort(key=lambda g: min(lage[r.guid] for r in g))
        for k, gruppe in enumerate(gruppen_vba, 1):
            for raum in gruppe:
                raum.vba = f"{basis}.{k}" if len(gruppen_vba) > 1 else basis


# --------------------------------------------------------------------------- #
# Meldegruppen
# --------------------------------------------------------------------------- #
MG_ARTEN = {"RM": "Rauchmelder Raum", "ZD": "Rauchmelder Zwischendecke", "DKM": "Druckknopfmelder"}
MG_SPALTEN = ["Meldegruppe", "Meldegruppen_Text", "Art", "Geschoss", "Brandabschnitt",
              "Virt_Brandabschnitt", "Raumnummer", "Raumname", "Melderanzahl", "Flaeche_m2",
              "Status", "Pruefung", "Bemerkung", "Raum_GUID"]


def _melder_der_art(raum: Raum, art: str) -> list[str]:
    """Bezeichnungen der Melder eines Raums je Art."""
    if art == "DKM":
        return [d.name for d in raum.druckknopf]
    bereich = "Raum" if art == "RM" else "Zwischendecke"
    return [n for b in raum.bereiche if b.art == bereich for n in b.namen]


def _mg_schluessel(mg: str):
    """Sortierung: Zahlen numerisch, alles andere dahinter alphabetisch."""
    try:
        return (0, float(mg.replace(",", ".")), "")
    except ValueError:
        return (1, 0.0, mg)


def lese_meldegruppen(pfad: Path) -> dict[tuple[str, str], dict[str, str]]:
    """Liest die (bearbeitete) Meldegruppentabelle: (Raum_GUID, Art) -> Zeile."""
    try:
        text = pfad.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        text = pfad.read_text(encoding="cp1252")  # von Excel als ANSI gespeichert
    kopf = text.splitlines()[0] if text.strip() else ""
    trenner = ";" if kopf.count(";") >= kopf.count(",") else ","
    vorgabe = {}
    for zeile in csv.DictReader(text.splitlines(), delimiter=trenner):
        zeile = {(k or "").strip(): (v or "").strip() for k, v in zeile.items()}
        if zeile.get("Raum_GUID") and zeile.get("Art") in MG_ARTEN:
            vorgabe[(zeile["Raum_GUID"], zeile["Art"])] = zeile
    return vorgabe


def bilde_meldegruppen(raeume: list[Raum], regeln: Regeln,
                       vorgabe: dict[tuple[str, str], dict[str, str]] | None = None) -> list[dict]:
    """Ordnet alle Melder Meldegruppen zu und liefert die Zeilen der Tabelle.

    Zuordnungen aus `vorgabe` (bearbeitete Tabelle) haben Vorrang. Alles Uebrige
    wird automatisch gruppiert:
      * getrennte Gruppen fuer Raummelder, Zwischendeckenmelder und Druckknopfmelder
      * je Geschoss eigene Gruppen; Treppenhaeuser geschossuebergreifend als eine Gruppe
      * Verkehrswege getrennt von den uebrigen Raeumen
      * benachbarte Raeume gemeinsam, solange Raumzahl, Flaeche und Melderzahl
        die Grenzwerte einhalten
    """
    vorgabe = vorgabe or {}
    hoehe = {}
    for raum in raeume:
        hoehe[raum.geschoss] = min(hoehe.get(raum.geschoss, math.inf), raum.z_unten)
    ordnung = lambda r: (hoehe[r.geschoss], r.geschoss, r.nummer, r.guid)  # noqa: E731

    einheiten = [(raum, art) for raum in sorted(raeume, key=ordnung) for art in MG_ARTEN
                 if _melder_der_art(raum, art)]
    texte: dict[str, str] = {}
    status: dict[tuple[str, str], str] = {}
    bemerkung: dict[tuple[str, str], str] = {}
    for raum in raeume:
        raum.mg, raum.adresse = {}, {}
        # in der Tabelle geaenderter virtueller Brandabschnitt hat Vorrang
        for art in MG_ARTEN:
            wert = vorgabe.get((raum.guid, art), {}).get("Virt_Brandabschnitt")
            if wert:
                raum.vba = wert
                break
    offen = []
    for raum, art in einheiten:
        zeile = vorgabe.get((raum.guid, art))
        if zeile:
            bemerkung[(raum.guid, art)] = zeile.get("Bemerkung", "")
        if zeile and zeile.get("Meldegruppe"):
            mg = zeile["Meldegruppe"]
            raum.mg[art] = mg
            status[(raum.guid, art)] = "Tabelle"
            if zeile.get("Meldegruppen_Text"):
                texte.setdefault(mg, zeile["Meldegruppen_Text"])
        else:
            offen.append((raum, art))
            status[(raum.guid, art)] = "neu" if vorgabe else "auto"

    # Naechste freie Nummer hinter den vorhandenen
    belegt = [k[1] for k in (_mg_schluessel(r.mg[a]) for r, a in einheiten if a in r.mg) if k[0] == 0]
    naechste = max([regeln.mg_start - 1] + [int(b) for b in belegt]) + 1

    def neue_gruppe(mitglieder: list[Raum], art: str, bezeichnung: str) -> None:
        nonlocal naechste
        mg = str(naechste)
        naechste += 1
        for raum in mitglieder:
            raum.mg[art] = mg
        nummern = ", ".join(r.nummer or r.name or "?" for r in mitglieder[:4])
        if len(mitglieder) > 4:
            nummern += f" +{len(mitglieder) - 4}"
        texte[mg] = f"{bezeichnung} {nummern}".strip()

    def benachbart(liste: list[Raum], art: str, grenze_melder: int) -> list[list[Raum]]:
        """Fasst benachbarte Raeume innerhalb der Grenzwerte zusammen."""
        gruppen, rest = [], list(liste)
        while rest:
            gruppe = [rest.pop(0)]
            melder = len(_melder_der_art(gruppe[0], art))
            flaeche = gruppe[0].flaeche
            gewachsen = True
            while gewachsen:
                gewachsen = False
                for raum in list(rest):
                    n = len(_melder_der_art(raum, art))
                    if (len(gruppe) < regeln.mg_max_raeume
                            and flaeche + raum.flaeche <= regeln.mg_max_flaeche
                            and melder + n <= grenze_melder
                            and any(raum.polygon.distance(g.polygon) <= 0.6 for g in gruppe)):
                        gruppe.append(raum)
                        rest.remove(raum)
                        melder, flaeche, gewachsen = melder + n, flaeche + raum.flaeche, True
            gruppen.append(gruppe)
        return gruppen

    # Treppenhaeuser: uebereinander liegende Raeume bilden eine Gruppe (Raummelder)
    treppen = [r for r, a in offen if a == "RM" and r.treppenhaus]
    while treppen:
        kern = [treppen.pop(0)]
        for raum in list(treppen):
            if any(raum.polygon.intersection(k.polygon).area > 0.5 * min(raum.flaeche, k.flaeche)
                   for k in kern):
                kern.append(raum)
                treppen.remove(raum)
        neue_gruppe(kern, "RM", "Treppenhaus")

    for geschoss in sorted(hoehe, key=lambda g: (hoehe[g], g)):
        im_geschoss = [(r, a) for r, a in offen if r.geschoss == geschoss and a not in r.mg]
        # Meldegruppen bleiben innerhalb eines (virtuellen) Brandabschnitts
        for vba in sorted({r.vba for r, _ in im_geschoss}):
            hier = [(r, a) for r, a in im_geschoss if r.vba == vba]
            ort = vba or geschoss
            for art, name in (("RM", "RM"), ("ZD", "Zwischendecke")):
                wege = [r for r, a in hier if a == art and r.verkehrsweg]
                sonst = [r for r, a in hier if a == art and not r.verkehrsweg]
                for liste, zusatz in ((wege, " Verkehrsweg"), (sonst, "")):
                    for gruppe in benachbart(liste, art, regeln.mg_max_melder):
                        neue_gruppe(gruppe, art, f"{ort} {name}{zusatz}")
            # Druckknopfmelder: hoechstens `mg_max_dkm` je Gruppe
            gruppe, anzahl = [], 0
            for raum in [r for r, a in hier if a == "DKM"]:
                n = len(raum.druckknopf)
                if gruppe and anzahl + n > regeln.mg_max_dkm:
                    neue_gruppe(gruppe, "DKM", f"{ort} Druckknopfmelder")
                    gruppe, anzahl = [], 0
                gruppe.append(raum)
                anzahl += n
            if gruppe:
                neue_gruppe(gruppe, "DKM", f"{ort} Druckknopfmelder")

    # Meldernummern innerhalb der Gruppe fortlaufend
    zaehler: dict[str, int] = defaultdict(int)
    for raum, art in sorted(einheiten, key=lambda e: (_mg_schluessel(e[0].mg[e[1]]), ordnung(e[0]))):
        for name in _melder_der_art(raum, art):
            zaehler[raum.mg[art]] += 1
            raum.adresse[name] = (raum.mg[art], zaehler[raum.mg[art]])

    pruefung = pruefe_meldegruppen(einheiten, regeln)
    zeilen = []
    for raum, art in sorted(einheiten, key=lambda e: (_mg_schluessel(e[0].mg[e[1]]), ordnung(e[0]))):
        mg = raum.mg[art]
        zeilen.append({
            "Meldegruppe": mg, "Meldegruppen_Text": texte.get(mg, ""), "Art": art,
            "Geschoss": raum.geschoss, "Brandabschnitt": raum.ba,
            "Virt_Brandabschnitt": raum.vba, "Raumnummer": raum.nummer, "Raumname": raum.name,
            "Melderanzahl": len(_melder_der_art(raum, art)), "Flaeche_m2": f"{raum.flaeche:.1f}",
            "Status": status[(raum.guid, art)], "Pruefung": " | ".join(pruefung.get(mg, [])),
            "Bemerkung": bemerkung.get((raum.guid, art), ""), "Raum_GUID": raum.guid})
    return zeilen


def pruefe_meldegruppen(einheiten: list[tuple[Raum, str]], regeln: Regeln) -> dict[str, list[str]]:
    """Prueft jede Meldegruppe gegen die Grenzwerte; Rueckgabe: Meldegruppe -> Hinweise."""
    gruppen: dict[str, list[tuple[Raum, str]]] = defaultdict(list)
    for raum, art in einheiten:
        gruppen[raum.mg[art]].append((raum, art))
    ergebnis = {}
    for mg, mitglieder in gruppen.items():
        hinweise = []
        arten = {a for _, a in mitglieder}
        raeume = list({r.guid: r for r, _ in mitglieder}.values())
        automatisch = sum(len(_melder_der_art(r, a)) for r, a in mitglieder if a != "DKM")
        dkm = sum(len(_melder_der_art(r, a)) for r, a in mitglieder if a == "DKM")
        flaeche = sum(r.flaeche for r in raeume)
        if "DKM" in arten and len(arten) > 1:
            hinweise.append("Druckknopfmelder und automatische Melder gemischt")
        if {"RM", "ZD"} <= arten:
            hinweise.append("Raum- und Zwischendeckenmelder gemischt")
        if automatisch > regeln.mg_max_melder:
            hinweise.append(f"{automatisch} automatische Melder > {regeln.mg_max_melder}")
        if dkm > regeln.mg_max_dkm:
            hinweise.append(f"{dkm} Druckknopfmelder > {regeln.mg_max_dkm}")
        if not all(r.treppenhaus for r in raeume):
            if len({r.ba for r in raeume}) > 1:
                hinweise.append("mehrere Brandabschnitte in einer Meldegruppe")
            elif len({r.vba for r in raeume}) > 1:
                hinweise.append("mehrere virtuelle Brandabschnitte in einer Meldegruppe")
        if len({r.geschoss for r in raeume}) > 1 and not all(r.treppenhaus for r in raeume):
            hinweise.append("mehrere Geschosse in einer Meldegruppe")
        if arten != {"DKM"}:
            if flaeche > regeln.mg_max_gesamt:
                hinweise.append(f"Flaeche {flaeche:.0f} m2 > {regeln.mg_max_gesamt:.0f} m2")
            elif len(raeume) > 1 and not all(r.treppenhaus for r in raeume):
                if len(raeume) > regeln.mg_max_raeume:
                    hinweise.append(f"{len(raeume)} Raeume > {regeln.mg_max_raeume}")
                if flaeche > regeln.mg_max_flaeche:
                    hinweise.append(f"mehrere Raeume mit {flaeche:.0f} m2 > {regeln.mg_max_flaeche:.0f} m2")
        if hinweise:
            ergebnis[mg] = hinweise
    return ergebnis


def schreibe_meldegruppen(zeilen: list[dict], pfad: Path) -> None:
    if pfad.exists():  # bearbeitete Tabelle nie ohne Sicherung ueberschreiben
        pfad.with_suffix(pfad.suffix + ".bak").write_bytes(pfad.read_bytes())
    with pfad.open("w", newline="", encoding="utf-8-sig") as datei:
        w = csv.DictWriter(datei, fieldnames=MG_SPALTEN, delimiter=";")
        w.writeheader()
        w.writerows(zeilen)


def bericht_meldegruppen(zeilen: list[dict]) -> None:
    print(f"\n{'MG':>4}  {'Text':<34}{'Art':<8}{'Raeume':<31}{'Melder':>7}{'Flaeche':>9}  Status")
    print("-" * 105)
    gruppen: dict[str, list[dict]] = defaultdict(list)
    for zeile in zeilen:
        gruppen[zeile["Meldegruppe"]].append(zeile)
    for mg, liste in gruppen.items():
        raeume = ", ".join(dict.fromkeys(f"{z['Geschoss']} {z['Raumnummer']}" for z in liste))
        flaeche = sum({z["Raum_GUID"]: float(z["Flaeche_m2"]) for z in liste}.values())
        print(f"{mg:>4}  {liste[0]['Meldegruppen_Text'][:33]:<34}"
              f"{'/'.join(dict.fromkeys(z['Art'] for z in liste)):<8}"
              f"{raeume[:30]:<31}{sum(z['Melderanzahl'] for z in liste):>7}{flaeche:>9.1f}"
              f"  {'/'.join(dict.fromkeys(z['Status'] for z in liste))}")
        if liste[0]["Pruefung"]:
            print(f"      ! {liste[0]['Pruefung']}")
    print("-" * 105)
    print(f"Meldegruppen: {len(gruppen)}   mit Hinweis: "
          f"{sum(1 for l in gruppen.values() if l[0]['Pruefung'])}")


# --------------------------------------------------------------------------- #
# Ausgabe
# --------------------------------------------------------------------------- #
def melder_bezeichnung(raum: Raum, kennung: str) -> str:
    """Bezeichnung eines Melders: Meldegruppe/Meldernummer, z.B. "3/1"."""
    if kennung in raum.adresse:
        return "{}/{}".format(*raum.adresse[kennung])
    return kennung


def _nach_meldegruppe(zeile: list) -> tuple:
    """Sortierung der Melderlisten: Meldegruppe, dann Meldernummer; Zeilen ohne Melder zuletzt."""
    mg, nr = zeile[0], zeile[1]
    return (mg == "", _mg_schluessel(str(mg)), nr if isinstance(nr, int) else 0)


def schreibe_csv(raeume: list[Raum], pfad: Path) -> None:
    zeilen = []
    for raum in sorted(raeume, key=lambda r: (r.geschoss, r.nummer)):
        ort = [raum.geschoss, raum.nummer, raum.name]
        if not raum.bereiche:
            zeilen.append(["", "", ""] + ort + [""] * 10 + [" | ".join(raum.hinweise), raum.guid])
        for b in raum.bereiche:
            hinweise = " | ".join(raum.hinweise + b.hinweise)
            fuss = [f"{b.flaeche:.2f}", f"{b.hoehe:.2f}", f"{b.a:.0f}", f"{b.dh:.2f}",
                    f"{b.max_abstand:.2f}" if len(b.melder) else "", hinweise, raum.guid]
            if len(b.melder) == 0:
                zeilen.append(["", "", ""] + ort + [b.art, b.feld, "", "", ""] + fuss)
            for name, (x, y) in zip(b.namen, b.melder):
                zeilen.append([*raum.adresse.get(name, ("", "")), name]
                              + ort + [b.art, b.feld, f"{x:.3f}", f"{y:.3f}", f"{b.z_oben:.3f}"] + fuss)
    zeilen.sort(key=_nach_meldegruppe)
    with pfad.open("w", newline="", encoding="utf-8-sig") as datei:
        w = csv.writer(datei, delimiter=";")
        # Meldegruppe und Meldernummer getrennt: "3/1" wuerde Excel als Datum lesen
        w.writerow(["Meldegruppe", "Meldernummer", "Kennung", "Geschoss", "Raumnummer",
                    "Raumname", "Bereich", "Deckenfeld", "X_m", "Y_m", "Z_m", "Feldflaeche_m2",
                    "Hoehe_m", "A_m2", "DH_m", "max_Abstand_m", "Hinweise", "Raum_GUID"])
        w.writerows(zeilen)


def schreibe_plaene(raeume: list[Raum], achsen: Achsen, ordner: Path, stamm: str,
                    abschnitte: list[Brandabschnitt] | None = None) -> list[Path]:
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

        # Raeume je virtuellem Brandabschnitt einfaerben, Brandabschnitte umranden
        palette = ["#f4f6f8", "#fdf2e3", "#eaf5ea", "#f3eaf7", "#fdeeee", "#e8f4f6"]
        farbe_vba = {v: palette[i % len(palette)]
                     for i, v in enumerate(sorted({r.vba for r in liste}))}
        z0, z1 = min(r.z_unten for r in liste), max(r.z_oben for r in liste)
        for abschnitt in abschnitte or []:
            if abschnitt.z_unten < z1 - 0.1 and abschnitt.z_oben > z0 + 0.1:
                ax.plot(*abschnitt.polygon.exterior.xy, color="#8e44ad", lw=2.2, ls="--", zorder=6.5)
                bx0, _, _, by1 = abschnitt.polygon.bounds
                ax.annotate(abschnitt.name, (bx0, by1), xytext=(4, 4), textcoords="offset points",
                            fontsize=7.5, color="#8e44ad", weight="bold", zorder=7)
        for raum in liste:
            x, y = raum.polygon.exterior.xy
            ax.fill(x, y, facecolor=farbe_vba[raum.vba], edgecolor="#2c3e50", lw=1.2, zorder=1)
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
            for b in raum.bereiche:  # Beschriftung Meldegruppe/Meldernummer
                rm = b.art == "Raum"
                for name, m in zip(b.namen, b.melder):
                    ax.annotate(melder_bezeichnung(raum, name), m, fontsize=5, zorder=8,
                                color="#922b21" if rm else "#1f618d",
                                xytext=(4, 4) if rm else (4, -9), textcoords="offset points")
            for d in raum.druckknopf:
                ax.annotate(melder_bezeichnung(raum, d.name), (d.x, d.y), fontsize=5, zorder=8,
                            color="#1e8449", xytext=(5, 4), textcoords="offset points")
            for tuer in raum.tueren:
                ax.fill(*tuer.exterior.xy, facecolor="#e67e22", edgecolor="#a04000", lw=0.5, zorder=3.5)
            if raum.druckknopf:
                ax.scatter([d.x for d in raum.druckknopf], [d.y for d in raum.druckknopf], s=70,
                           marker="D", color="#1e8449", edgecolor="white", lw=0.8, zorder=7)
            p = raum.polygon.representative_point()
            zd = raum.anzahl("Zwischendecke")
            hoehe = raum.bereiche[0].hoehe if raum.bereiche else raum.hoehe
            ax.annotate(f"{raum.nummer} {raum.name}\n{raum.flaeche:.1f} m² · h {hoehe:.2f} m"
                        f"\n{raum.anzahl('Raum')} Melder" + (f" + {zd} ZD" if zd else "")
                        + ("\nMG " + " · ".join(f"{a} {m}" for a, m in raum.mg.items()) if raum.mg else ""),
                        (p.x, p.y), ha="center", va="center", fontsize=6.5, color="#2c3e50",
                        zorder=6, xytext=(0, -14), textcoords="offset points")
        griffe = [
            Line2D([], [], marker="o", ls="", color="#c0392b", markersize=6, label="Raummelder"),
            Line2D([], [], marker="s", ls="", markerfacecolor="none", markeredgecolor="#1f618d",
                   markersize=9, label="Zwischendeckenmelder"),
            Line2D([], [], marker="D", ls="", color="#1e8449", markersize=7, label="Druckknopfmelder"),
            Patch(facecolor="#e67e22", edgecolor="#a04000", label="Tür"),
            Patch(facecolor="#95a5a6", edgecolor="#566573", hatch="////", label="Unterzug"),
            Line2D([], [], color="#aab7c4", lw=0.6, label="Rasterdecke"),
            Line2D([], [], color="#c0392b", lw=0.8, ls="-.", label="Melderachse"),
                    Line2D([], [], color="#8e44ad", lw=2, ls="--", label="Brandabschnitt"),
        ]
        if len(farbe_vba) > 1:
            griffe += [Patch(facecolor=f, edgecolor="#2c3e50", label=f"VBA {v}")
                       for v, f in farbe_vba.items()]
        ax.set_xlim(grenzen[0] - 1, grenzen[2] + 1)
        ax.set_ylim(grenzen[1] - 1, grenzen[3] + 1)
        ax.set_aspect("equal")
        n_rm = sum(r.anzahl("Raum") for r in liste)
        n_zd = sum(r.anzahl("Zwischendecke") for r in liste)
        ax.set_title(f"Rauchmelderplatzierung – {geschoss}  "
                     f"({n_rm} Raummelder, {n_zd} Zwischendeckenmelder, "
                     f"{sum(len(r.druckknopf) for r in liste)} Druckknopfmelder)", fontsize=10)
        ax.set_xlabel("X [m]")
        ax.set_ylabel("Y [m]")
        ax.legend(handles=griffe, loc="upper left", bbox_to_anchor=(1.01, 1.0), fontsize=7, frameon=False)
        sicher = "".join(c if c.isalnum() or c in "-_" else "_" for c in geschoss)
        pfad = ordner / f"{stamm}_{sicher}.png"
        fig.savefig(pfad, dpi=160, bbox_inches="tight")
        plt.close(fig)
        dateien.append(pfad)
    return dateien


def schreibe_ifc(modell: ifcopenshell.file, raeume: list[Raum], pfad: Path) -> None:
    """Fuegt Rauchmelder (IfcSensor SMOKESENSOR) und Druckknopfmelder (IfcAlarm
    MANUALPULLBOX) in das Modell ein und speichert es."""
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
    darstellung = darstellung_dkm = None
    if kontext is not None:
        profil = modell.createIfcCircleProfileDef(
            "AREA", None, modell.createIfcAxis2Placement2D(
                modell.createIfcCartesianPoint((0.0, 0.0)), None), 0.05 * faktor)
        koerper = modell.createIfcExtrudedAreaSolid(
            profil, modell.createIfcAxis2Placement3D(
                modell.createIfcCartesianPoint((0.0, 0.0, -0.05 * faktor)), richtung_z, richtung_x),
            richtung_z, 0.05 * faktor)
        darstellung = modell.createIfcShapeRepresentation(kontext, "Body", "SweptSolid", [koerper])
        # Druckknopfmelder: Kasten 120 x 120 x 120 mm
        kasten = modell.createIfcExtrudedAreaSolid(
            modell.createIfcRectangleProfileDef("AREA", None, modell.createIfcAxis2Placement2D(
                modell.createIfcCartesianPoint((0.0, 0.0)), None), 0.12 * faktor, 0.12 * faktor),
            modell.createIfcAxis2Placement3D(
                modell.createIfcCartesianPoint((0.0, 0.0, -0.06 * faktor)), richtung_z, richtung_x),
            richtung_z, 0.12 * faktor)
        darstellung_dkm = modell.createIfcShapeRepresentation(kontext, "Body", "SweptSolid", [kasten])

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
        eintraege = [(n, m[0], m[1], b.z_oben, darstellung, "IfcSensor", "SMOKESENSOR",
                      "Punktfoermiger Rauchmelder, "
                      + ("Zwischendecke" if b.art == "Zwischendecke" else "Raum") + f" {raum.nummer}")
                     for b in raum.bereiche for n, m in zip(b.namen, b.melder)]
        eintraege += [(d.name, d.x, d.y, d.z, darstellung_dkm if darstellung is not None else None,
                       "IfcAlarm", "MANUALPULLBOX", f"Druckknopfmelder, {d.grund}")
                      for d in raum.druckknopf]
        for name, x, y, z, geometrie, klasse, typ, beschreibung in eintraege:
            lokal = invers @ np.array([x * faktor, y * faktor, z * faktor, 1.0])
            achse = invers[:3, :3] @ np.array([0.0, 0.0, 1.0])
            ref = invers[:3, :3] @ np.array([1.0, 0.0, 0.0])
            platzierung = modell.createIfcLocalPlacement(bezug, modell.createIfcAxis2Placement3D(
                modell.createIfcCartesianPoint(tuple(float(v) for v in lokal[:3])),
                modell.createIfcDirection(tuple(float(v) for v in achse)),
                modell.createIfcDirection(tuple(float(v) for v in ref))))
            form = (modell.createIfcProductDefinitionShape(None, None, [geometrie])
                    if geometrie is not None else None)
            if name in raum.adresse:
                beschreibung += ", Meldegruppe {} Melder {}".format(*raum.adresse[name])
            attribute = dict(GlobalId=ifcopenshell.guid.new(), OwnerHistory=historie,
                             Name=melder_bezeichnung(raum, name), Description=beschreibung,
                             ObjectPlacement=platzierung, Representation=form)
            if ifc2x3:
                melder = modell.create_entity("IfcDistributionControlElement",
                                              ObjectType=typ, **attribute)
            else:
                melder = modell.create_entity(klasse, PredefinedType=typ, **attribute)
            je_geschoss[geschoss].append(melder)

    for geschoss, melder in je_geschoss.items():
        if geschoss is None:
            continue
        modell.create_entity("IfcRelContainedInSpatialStructure",
                             GlobalId=ifcopenshell.guid.new(), OwnerHistory=historie,
                             Name="Brandmelder", RelatedElements=melder,
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
    k = p.add_argument_group("Druckknopfmelder")
    k.add_argument("--ohne-dkm", action="store_true", help="keine Druckknopfmelder setzen")
    k.add_argument("--dkm-abstand", type=float, default=d.dkm_abstand,
                   help="Abstand der Druckknopfmelder in Verkehrswegen [m] (%(default)s)")
    k.add_argument("--dkm-hoehe", type=float, default=d.dkm_hoehe, help="Montagehoehe [m] (%(default)s)")
    k.add_argument("--dkm-tuerabstand", type=float, default=d.dkm_tuerabstand,
                   help="seitlicher Abstand zur Tueroeffnung [m] (%(default)s)")
    k.add_argument("--tuer-toleranz", type=float, default=d.tuer_toleranz,
                   help="max. Abstand Tuer - Raum fuer die Zuordnung [m] (%(default)s)")
    k.add_argument("--muster-treppenhaus", default=d.muster_treppenhaus,
                   help="Regulaerer Ausdruck fuer Treppenhaus-Raeume (%(default)s)")
    k.add_argument("--muster-verkehrsweg", default=d.muster_verkehrsweg,
                   help="Regulaerer Ausdruck fuer Verkehrswege (%(default)s)")
    k.add_argument("--erdgeschoss", default=d.erdgeschoss,
                   help="Name des Erdgeschosses (Standard: automatisch)")
    b = p.add_argument_group("Brandabschnitte")
    b.add_argument("--muster-brandabschnitt", default=d.muster_brandabschnitt,
                   help="Regulaerer Ausdruck fuer Raumpolygone, die Brandabschnitte sind (%(default)s)")
    b.add_argument("--vba-max-flaeche", type=float, default=d.vba_max_flaeche,
                   help="max. Flaeche eines virtuellen Brandabschnitts [m2] (%(default)s)")
    m = p.add_argument_group("Meldegruppen")
    m.add_argument("--meldegruppen", type=Path,
                   help="Meldegruppentabelle (CSV); Standard: <IFC-Name>_meldegruppen.csv im Ausgabeordner. "
                        "Vorhandene Zuordnungen werden uebernommen")
    m.add_argument("--meldegruppen-neu", action="store_true",
                   help="vorhandene Tabelle ignorieren und alle Meldegruppen neu bilden")
    m.add_argument("--mg-max-melder", type=int, default=d.mg_max_melder, help="automatische Melder je Gruppe (%(default)s)")
    m.add_argument("--mg-max-dkm", type=int, default=d.mg_max_dkm, help="Druckknopfmelder je Gruppe (%(default)s)")
    m.add_argument("--mg-max-raeume", type=int, default=d.mg_max_raeume, help="Raeume je Gruppe (%(default)s)")
    m.add_argument("--mg-max-flaeche", type=float, default=d.mg_max_flaeche, help="[m2] bei mehreren Raeumen (%(default)s)")
    m.add_argument("--mg-max-gesamt", type=float, default=d.mg_max_gesamt, help="[m2] je Gruppe insgesamt (%(default)s)")
    m.add_argument("--mg-start", type=int, default=d.mg_start, help="erste Meldegruppennummer (%(default)s)")
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
    raeume = lade_raeume(modell, regeln.muster_brandabschnitt)
    abschnitte = lade_brandabschnitte(modell, regeln)
    if not raeume:
        print("Keine Raeume (IfcSpace) mit Geometrie gefunden.", file=sys.stderr)
        return 1

    bauteile = lade_bauteile(modell)
    print("Gefunden: {} Raeume, {} Abhangdecken, {} Unterzuege, {} Rohdecken".format(
        len(raeume), *(len(bauteile[k]) for k in ("Abhangdecke", "Unterzug", "Rohdecke"))))
    achsen = platziere_alle(raeume, regeln, bauteile)
    bericht(raeume)
    if not regeln.ohne_dkm:
        anzahl = platziere_druckknopfmelder(modell, raeume, regeln)
        print(f"\nGefunden: {anzahl} Tueren, Erdgeschoss: {_erdgeschoss(modell, regeln)}")
    else:
        klassifiziere_raeume(modell, raeume, regeln)

    ordne_brandabschnitte(modell, raeume, abschnitte, regeln)
    if any(r.ba for r in raeume):
        print(f"\nBrandabschnitte: {', '.join(sorted({r.ba for r in raeume}))}"
              f"   virtuelle Brandabschnitte: {len({r.vba for r in raeume})}")
        for vba in sorted({r.vba for r in raeume}):
            mitglieder = [r for r in raeume if r.vba == vba]
            print(f"  {vba:<28}{len(mitglieder):>3} Raeume {sum(r.flaeche for r in mitglieder):>8.1f} m2")
    else:
        print("\nKeine Brandabschnitte im Modell gefunden (--muster-brandabschnitt pruefen)")

    mg_pfad = args.meldegruppen or ordner / f"{args.ifc.stem}_meldegruppen.csv"
    vorgabe = {}
    if mg_pfad.is_file() and not args.meldegruppen_neu:
        vorgabe = lese_meldegruppen(mg_pfad)
        print(f"\nMeldegruppentabelle uebernommen: {mg_pfad} ({len(vorgabe)} Zeilen)")
    elif args.meldegruppen and not args.meldegruppen_neu:
        print(f"  ! {mg_pfad} nicht gefunden - Meldegruppen werden neu gebildet", file=sys.stderr)
    zeilen = bilde_meldegruppen(raeume, regeln, vorgabe)
    if not regeln.ohne_dkm:
        bericht_dkm(raeume)
    bericht_meldegruppen(zeilen)
    entfallen = len(set(vorgabe) - {(z["Raum_GUID"], z["Art"]) for z in zeilen})
    if entfallen:
        print(f"{entfallen} Zeile(n) der Tabelle ohne Melder im Modell - entfallen")
    schreibe_meldegruppen(zeilen, mg_pfad)

    csv_pfad = ordner / f"{stamm}.csv"
    schreibe_csv(raeume, csv_pfad)
    print(f"\nCSV : {csv_pfad}")
    if not regeln.ohne_dkm:
        dkm_pfad = ordner / f"{args.ifc.stem}_druckknopfmelder.csv"
        schreibe_csv_dkm(raeume, dkm_pfad)
        print(f"CSV : {dkm_pfad}")
    print(f"MG  : {mg_pfad}  (bearbeitbar; wird beim naechsten Lauf uebernommen)")
    if not args.kein_plan:
        for pfad in schreibe_plaene(raeume, achsen, ordner, stamm, abschnitte):
            print(f"Plan: {pfad}")
    if args.ifc_ausgabe:
        ifc_pfad = ordner / f"{stamm}.ifc"
        schreibe_ifc(modell, raeume, ifc_pfad)
        print(f"IFC : {ifc_pfad}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
