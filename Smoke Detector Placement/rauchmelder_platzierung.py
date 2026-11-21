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

    def werte(self, flaeche: float, hoehe: float) -> tuple[float, float]:
        """Liefert (A, DH) fuer einen Raum."""
        if flaeche <= self.grenzflaeche or hoehe > self.grenzhoehe:
            return self.a_gross, self.dh_gross
        return self.a_klein, self.dh_klein


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
    melder: np.ndarray = field(default_factory=lambda: np.zeros((0, 2)))
    a: float = 0.0
    dh: float = 0.0
    winkel: float = 0.0
    raster_n: tuple[int, int] = (0, 0)
    max_abstand: float = 0.0
    hinweise: list[str] = field(default_factory=list)

    @property
    def flaeche(self) -> float:
        return self.polygon.area

    @property
    def hoehe(self) -> float:
        return self.z_oben - self.z_unten


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


def platziere(raum: Raum, regeln: Regeln, achsen: Achsen) -> None:
    """Bestimmt die Melderpositionen eines Raums und traegt sie in `raum` ein."""
    raum.a, raum.dh = regeln.werte(raum.flaeche, raum.hoehe)
    if raum.hoehe > regeln.max_hoehe:
        raum.hinweise.append(
            f"Raumhoehe {raum.hoehe:.2f} m > {regeln.max_hoehe:.1f} m: "
            "punktfoermige Rauchmelder nicht geeignet")

    # Alles Weitere im gedrehten System (Hauptrichtung des Raums = u-Achse)
    raum.winkel = hauptwinkel(raum.polygon)
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


def platziere_alle(raeume: list[Raum], regeln: Regeln) -> Achsen:
    achsen = Achsen()
    # Grosse Raeume zuerst: sie geben die Achsen vor, kleine richten sich danach
    for raum in sorted(raeume, key=lambda r: -r.flaeche):
        if raum.flaeche < regeln.min_flaeche:
            raum.hinweise.append(
                f"Flaeche < {regeln.min_flaeche:.1f} m2: kein Melder vorgesehen")
            continue
        platziere(raum, regeln, achsen)
    return achsen


# --------------------------------------------------------------------------- #
# Ausgabe
# --------------------------------------------------------------------------- #
def schreibe_csv(raeume: list[Raum], pfad: Path) -> None:
    with pfad.open("w", newline="", encoding="utf-8-sig") as datei:
        w = csv.writer(datei, delimiter=";")
        w.writerow(["Geschoss", "Raumnummer", "Raumname", "Raum_GUID", "Melder",
                    "X_m", "Y_m", "Z_m", "Raumflaeche_m2", "Raumhoehe_m",
                    "A_m2", "DH_m", "max_Abstand_m", "Hinweise"])
        for raum in sorted(raeume, key=lambda r: (r.geschoss, r.nummer)):
            kopf = [raum.geschoss, raum.nummer, raum.name, raum.guid]
            fuss = [f"{raum.flaeche:.2f}", f"{raum.hoehe:.2f}", f"{raum.a:.0f}",
                    f"{raum.dh:.2f}", f"{raum.max_abstand:.2f}" if len(raum.melder) else "",
                    " | ".join(raum.hinweise)]
            if len(raum.melder) == 0:
                w.writerow(kopf + ["", "", "", ""] + fuss)
            for nr, (x, y) in enumerate(raum.melder, 1):
                w.writerow(kopf + [f"{raum.nummer or 'R'}-RM{nr:02d}",
                                   f"{x:.3f}", f"{y:.3f}", f"{raum.z_oben:.3f}"] + fuss)


def schreibe_plaene(raeume: list[Raum], achsen: Achsen, ordner: Path, stamm: str) -> list[Path]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    dateien = []
    je_geschoss: dict[str, list[Raum]] = defaultdict(list)
    for raum in raeume:
        je_geschoss[raum.geschoss].append(raum)

    for geschoss, liste in sorted(je_geschoss.items()):
        grenzen = unary_union([r.polygon for r in liste]).bounds
        bx, by = grenzen[2] - grenzen[0], grenzen[3] - grenzen[1]
        massstab = 14.0 / max(bx, by, 1.0)
        fig, ax = plt.subplots(figsize=(max(6, bx * massstab) + 1, max(4, by * massstab) + 1))

        # Achsen, auf denen in diesem Geschoss Melder liegen
        for (winkel, richtung), werte in achsen.alle():
            for wert in werte:
                belegt = [m for r in liste if abs(r.winkel - winkel) < 1e-6
                          for m in _drehen(r.melder, winkel) if abs(m[richtung] - wert) < 2e-3]
                if len(belegt) < 2:
                    continue
                andere = np.array([m[1 - richtung] for m in belegt])
                ende = np.array([[wert, andere.min() - 1.0], [wert, andere.max() + 1.0]])
                if richtung == 1:
                    ende = ende[:, ::-1]
                ende = _drehen(ende, -winkel)
                ax.plot(ende[:, 0], ende[:, 1], color="#c0392b", lw=0.6, ls="-.", zorder=2)

        for raum in liste:
            x, y = raum.polygon.exterior.xy
            flaeche, = ax.fill(x, y, facecolor="#f4f6f8", edgecolor="#2c3e50", lw=1.2, zorder=1)
            for ring in raum.polygon.interiors:
                ax.fill(*ring.xy, facecolor="white", edgecolor="#2c3e50", lw=1.0, zorder=1)
            for m in raum.melder:
                kreis = ax.add_patch(plt.Circle(m, raum.dh, facecolor="#2980b9", alpha=0.07,
                                                edgecolor="#2980b9", lw=0.4, zorder=1.5))
                kreis.set_clip_path(flaeche)  # Ueberwachungsbereich nur im eigenen Raum
            if len(raum.melder):
                ax.scatter(raum.melder[:, 0], raum.melder[:, 1], s=28, color="#c0392b",
                           edgecolor="white", lw=0.6, zorder=4)
            p = raum.polygon.representative_point()
            ax.annotate(f"{raum.nummer} {raum.name}\n{raum.flaeche:.1f} m² · h {raum.hoehe:.2f} m"
                        f"\n{len(raum.melder)} Melder", (p.x, p.y), ha="center", va="center",
                        fontsize=6.5, color="#2c3e50", zorder=5,
                        xytext=(0, -14), textcoords="offset points")
        ax.set_xlim(grenzen[0] - 1, grenzen[2] + 1)
        ax.set_ylim(grenzen[1] - 1, grenzen[3] + 1)
        ax.set_aspect("equal")
        ax.set_title(f"Rauchmelderplatzierung – {geschoss}  "
                     f"({sum(len(r.melder) for r in liste)} Melder)", fontsize=10)
        ax.set_xlabel("X [m]")
        ax.set_ylabel("Y [m]")
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
        for nr, (x, y) in enumerate(raum.melder, 1):
            lokal = invers @ np.array([x * faktor, y * faktor, raum.z_oben * faktor, 1.0])
            achse = invers[:3, :3] @ np.array([0.0, 0.0, 1.0])
            ref = invers[:3, :3] @ np.array([1.0, 0.0, 0.0])
            platzierung = modell.createIfcLocalPlacement(bezug, modell.createIfcAxis2Placement3D(
                modell.createIfcCartesianPoint(tuple(float(v) for v in lokal[:3])),
                modell.createIfcDirection(tuple(float(v) for v in achse)),
                modell.createIfcDirection(tuple(float(v) for v in ref))))
            form = (modell.createIfcProductDefinitionShape(None, None, [darstellung])
                    if darstellung is not None else None)
            name = f"{raum.nummer or 'R'}-RM{nr:02d}"
            attribute = dict(GlobalId=ifcopenshell.guid.new(), OwnerHistory=historie,
                             Name=name, Description=f"Punktfoermiger Rauchmelder, Raum {raum.nummer}",
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
    print(f"\n{'Geschoss':<14}{'Raum':<10}{'Name':<22}{'Flaeche':>9}{'Hoehe':>7}"
          f"{'A':>5}{'DH':>6}{'Raster':>8}{'Melder':>7}{'max.Abst.':>10}")
    print("-" * 98)
    for raum in sorted(raeume, key=lambda r: (r.geschoss, r.nummer)):
        n = len(raum.melder)
        print(f"{raum.geschoss[:13]:<14}{raum.nummer[:9]:<10}{raum.name[:21]:<22}"
              f"{raum.flaeche:>8.1f} {raum.hoehe:>6.2f}{raum.a:>5.0f}{raum.dh:>6.1f}"
              f"{'%dx%d' % raum.raster_n if n else '-':>8}{n:>7}"
              f"{raum.max_abstand if n else 0:>10.2f}")
        for hinweis in raum.hinweise:
            print(f"{'':14}! {hinweis}")
    print("-" * 98)
    print(f"Raeume: {len(raeume)}   Melder gesamt: {sum(len(r.melder) for r in raeume)}")


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

    achsen = platziere_alle(raeume, regeln)
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
