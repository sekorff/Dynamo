#!/usr/bin/env python3
"""Platziert die Bauteile der Türsteuerung an Türen eines IFC-Modells.

Die Regeln (Bauteile, Außenmaße, IFC-Klassen, Positionen, Leerrohre) stehen in
platzierung.json. Das Skript liest ein IFC-Modell, wählt Türen aus und setzt je
Tür die Bauteile als Quader und die Leerrohre als Rohrsegmente mit Bögen,
wahlweise in der Variante Aufputz (AP) oder Unterputz (UP).

Vor dem Setzen prüft es, ob an der Schlossseite ab Türrahmen genug Wand frei
ist. Fehlt der Platz, kommen die Bedienelemente auf die Querwand.

Beispiele:
    python tuersteuerung_platzieren.py modell.ifc -o modell_AP.ifc --variante AP
    python tuersteuerung_platzieren.py modell.ifc -o out.ifc --variante UP --tuer 2O2Fr$t4X7Zf8NOew3FLOH
    python tuersteuerung_platzieren.py modell.ifc -o out.ifc --name "T0[12]" --variante AP
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np

import ifcopenshell
import ifcopenshell.api
import ifcopenshell.geom
import ifcopenshell.guid
import ifcopenshell.util.element
import ifcopenshell.util.placement
import ifcopenshell.util.unit

PSET = "Tuersteuerung"
EXPR_OK = re.compile(r"^[\w\s.+\-*/()]+$")
INF = float("inf")


class NichtPlatzierbar(Exception):
    """Die Regel lässt sich an dieser Tür nicht umsetzen."""


def api(model, fn, **kw):
    return ifcopenshell.api.run(fn, model, **kw)


def rechne(ausdruck, ns):
    """Wertet einen Positionsausdruck aus (Zahlen, W, H, b, h, t, A1.um ...)."""
    if isinstance(ausdruck, (int, float)):
        return float(ausdruck)
    if not EXPR_OK.match(ausdruck):
        raise ValueError(f"Unzulässiger Ausdruck: {ausdruck!r}")
    return float(eval(ausdruck, {"__builtins__": {}}, ns))  # noqa: S307 - Zeichensatz geprüft


# --------------------------------------------------------------------------
# Umgebung: Wände, Stützen und Öffnungen als Punktwolken in mm
# --------------------------------------------------------------------------
class Umgebung:
    def __init__(self, model):
        self.model = model
        self.si = ifcopenshell.util.unit.calculate_unit_scale(model)
        self.punkte = {}
        s = ifcopenshell.geom.settings()
        s.set("use-world-coords", True)
        for klasse in ("IfcWall", "IfcColumn", "IfcOpeningElement"):
            for e in model.by_type(klasse):
                try:
                    v = np.array(ifcopenshell.geom.create_shape(s, e).geometry.verts).reshape(-1, 3)
                    self.punkte[e.id()] = (e, v * 1000.0)
                except Exception:  # Element ohne auswertbare Geometrie
                    pass


# --------------------------------------------------------------------------
# Türgeometrie
# --------------------------------------------------------------------------
class Tuer:
    """Maße und Lage einer Tür in Millimetern.

    Lokales System der Tür: x entlang der Breite, y durch die Wand, z nach oben.
    Nach IFC öffnet die Tür in Richtung +y, die Bandseite liegt also bei +y.
    Regelkoordinaten: u ab Bandkante Richtung Schlossseite, v ab Wandfläche der
    Bandgegenseite (v < 0 Raum Bandgegenseite, 0..t in der Wand, v > t Bandseite).
    """

    def __init__(self, umgebung, door, regeln, mm):
        self.door = door
        self.mm = mm  # Projekteinheit je Millimeter
        self.W = door.OverallWidth / mm
        self.H = door.OverallHeight / mm
        self.matrix = ifcopenshell.util.placement.get_local_placement(door.ObjectPlacement)
        self.M = self.matrix.copy()
        self.M[:3, 3] /= mm  # Tür-lokal (mm) -> Welt (mm)
        self.Minv = np.linalg.inv(self.M)
        self.band = self._bandseite()
        self.oeffnung = next((r.RelatingOpeningElement for r in getattr(door, "FillsVoids", []) or []), None)
        self.wand = None
        if self.oeffnung is not None and self.oeffnung.VoidsElements:
            self.wand = self.oeffnung.VoidsElements[0].RelatingBuildingElement
        self.y0, self.t = 0.0, float(regeln["wand"]["dicke_fallback"])
        if self.oeffnung is not None and self.oeffnung.id() in umgebung.punkte:
            y = self._lokal(umgebung.punkte[self.oeffnung.id()][1])[:, 1]
            self.y0, self.t = float(y.min()), float(y.max() - y.min())
        self.uq = INF  # Lage der Querwand, falls die Bedienelemente dorthin ausweichen

    def _bandseite(self):
        werte = ifcopenshell.util.element.get_psets(self.door).get(PSET, {})
        if werte.get("Bandseite"):
            return str(werte["Bandseite"]).lower()
        typ = ifcopenshell.util.element.get_type(self.door)
        op = getattr(self.door, "OperationType", None) or getattr(typ, "OperationType", None) or ""
        return "rechts" if "RIGHT" in op else "links"

    def _lokal(self, welt_mm):
        return (self.Minv @ np.c_[welt_mm, np.ones(len(welt_mm))].T).T[:, :3]

    def uvz(self, welt_mm):
        """Weltpunkte -> Regelkoordinaten (u, v, z)."""
        p = self._lokal(welt_mm)
        u = p[:, 0] if self.band == "links" else self.W - p[:, 0]
        return np.c_[u, p[:, 1] - self.y0, p[:, 2]]

    def lokal(self, u, v, z):
        x = u if self.band == "links" else self.W - u
        return np.array([x, self.y0 + v, z])

    def welt(self, p_lokal_mm):
        return (self.M @ np.append(np.asarray(p_lokal_mm, float), 1.0))[:3]

    def punkt(self, s, d, z, seite="BG"):
        """Abgewickelte Koordinaten -> (u, v, z).

        s läuft entlang der Wand, d ist die Tiefe zur Wandoberfläche (d < 0 im
        Raum, d > 0 in der Wand). Hinter der Raumecke (s > uq) liegt der Punkt
        auf der Querwand.
        """
        if seite == "B":
            return (s, self.t - d, z)
        if s <= self.uq:
            return (s, d, z)
        return (self.uq + d, -(s - self.uq), z)

    # ---- Platzprüfung an der Schlossseite --------------------------------
    def platz(self, umgebung, regeln, seite):
        """Freie Wandlänge ab Türrahmen an der Schlossseite.

        Gibt (frei_mm, hindernis, querwand) zurück. querwand ist gesetzt, wenn
        das nächste Hindernis eine Wand ist, die quer zur Türwand steht.
        """
        zone = regeln["bedienelemente"]["pruefzone"]
        tief = zone["tiefe_vor_wand"]
        v_zone = (-tief, -5.0) if seite == "BG" else (self.t + 5.0, self.t + tief)
        naechstes = (INF, "frei", None)
        for e, welt in umgebung.punkte.values():
            if e == self.oeffnung:
                continue
            p = self.uvz(welt)
            mn, mx = p.min(axis=0), p.max(axis=0)
            if mx[2] < zone["z_von"] or mn[2] > zone["z_bis"]:
                continue
            if e == self.wand:
                kandidat = (mx[0], "Wandende", None)
            elif e.is_a("IfcOpeningElement"):
                gleiche_wand = e.VoidsElements and e.VoidsElements[0].RelatingBuildingElement == self.wand
                if not gleiche_wand or mx[0] <= self.W:
                    continue
                kandidat = (max(mn[0], self.W), f"Öffnung {e.Name or ''}".strip(), None)
            else:
                if mx[0] <= self.W or mx[1] < v_zone[0] or mn[1] > v_zone[1]:
                    continue
                quer = SimpleNamespace(element=e, u=max(mn[0], self.W), dicke=mx[0] - mn[0],
                                       reicht_bis=-mn[1] if seite == "BG" else mx[1] - self.t)
                kandidat = (quer.u, e.Name or e.is_a(), quer if e.is_a("IfcWall") else None)
            # bei gleichem Abstand hat die Querwand Vorrang vor dem Wandende
            if kandidat[0] < naechstes[0] - 1.0 or (abs(kandidat[0] - naechstes[0]) <= 1.0 and kandidat[2]):
                naechstes = kandidat
        return naechstes[0] - self.W, naechstes[1], naechstes[2]


# --------------------------------------------------------------------------
# IFC-Bausteine
# --------------------------------------------------------------------------
class Bauer:
    def __init__(self, model, regeln, variante):
        self.m = model
        self.regeln = regeln
        self.variante = variante
        self.mm = 0.001 / ifcopenshell.util.unit.calculate_unit_scale(model)
        self.body = self._kontext()
        self.typen = {}
        self.history = next(iter(model.by_type("IfcOwnerHistory")), None)
        winkel = ifcopenshell.util.unit.get_project_unit(model, "PLANEANGLEUNIT")
        self.viertelkreis = 90.0 if winkel is not None and winkel.is_a("IfcConversionBasedUnit") else math.pi / 2

    def _kontext(self):
        for c in self.m.by_type("IfcGeometricRepresentationSubContext"):
            if c.ContextIdentifier == "Body":
                return c
        haupt = next((c for c in self.m.by_type("IfcGeometricRepresentationContext", include_subtypes=False)
                      if c.ContextType == "Model"), None)
        if haupt is None:
            haupt = api(self.m, "context.add_context", context_type="Model")
        return api(self.m, "context.add_context", context_type="Model", context_identifier="Body",
                   target_view="MODEL_VIEW", parent=haupt)

    def _achse(self, ursprung, z, x):
        m = self.m
        return m.createIfcAxis2Placement3D(
            m.createIfcCartesianPoint([float(c) for c in ursprung]),
            m.createIfcDirection([float(c) for c in z]),
            m.createIfcDirection([float(c) for c in x]))

    def _platzierung(self, tuer, ursprung_lokal_mm, z_lokal=(0, 0, 1), x_lokal=(1, 0, 0)):
        r = tuer.matrix[:3, :3]
        return self.m.createIfcLocalPlacement(
            None, self._achse(tuer.welt(ursprung_lokal_mm) * self.mm,
                              r @ np.array(z_lokal, float), r @ np.array(x_lokal, float)))

    def _darstellung(self, koerper):
        rep = self.m.createIfcShapeRepresentation(self.body, "Body", "SweptSolid", koerper)
        return self.m.createIfcProductDefinitionShape(None, None, [rep])

    def typ(self, klasse, name, predefined, object_type=None):
        key = (klasse, name)
        if key not in self.typen:
            vorhanden = [t for t in self.m.by_type(klasse + "Type") if t.Name == name]
            if vorhanden:
                self.typen[key] = vorhanden[0]
            else:
                t = api(self.m, "root.create_entity", ifc_class=klasse + "Type", name=name, predefined_type=predefined)
                if object_type and hasattr(t, "ElementType"):
                    t.ElementType = object_type
                self.typen[key] = t
        return self.typen[key]

    def element(self, klasse, name, predefined, object_type=None):
        e = api(self.m, "root.create_entity", ifc_class=klasse, name=name, predefined_type=predefined)
        if object_type:
            e.ObjectType = object_type
        return e

    def quader(self, tuer, lo, hi):
        """Quader zwischen zwei Ecken in (u, v, z). Gibt (Platzierung, Körper, untere Ecke) zurück."""
        a, b = tuer.lokal(*lo), tuer.lokal(*hi)
        mn, mx = np.minimum(a, b), np.maximum(a, b)
        d = (mx - mn) * self.mm
        m = self.m
        profil = m.createIfcRectangleProfileDef(
            "AREA", None,
            m.createIfcAxis2Placement2D(m.createIfcCartesianPoint([float(d[0] / 2), float(d[1] / 2)]), None),
            float(d[0]), float(d[1]))
        solid = m.createIfcExtrudedAreaSolid(profil, None, m.createIfcDirection([0.0, 0.0, 1.0]), float(d[2]))
        return self._platzierung(tuer, mn), solid, mn

    def _rohrprofil(self, d_aussen, wand):
        return self.m.createIfcCircleHollowProfileDef("AREA", None, None, float(d_aussen / 2 * self.mm),
                                                      float(wand * self.mm))

    def rohr(self, tuer, p, q, d_aussen, wand):
        """Gerades Rohrsegment von p nach q in (u, v, z)."""
        a, b = tuer.lokal(*p), tuer.lokal(*q)
        vek = b - a
        laenge = float(np.linalg.norm(vek))
        z = vek / laenge
        x = np.array([0.0, 0.0, 1.0]) if abs(z[2]) < 0.9 else np.array([1.0, 0.0, 0.0])
        solid = self.m.createIfcExtrudedAreaSolid(self._rohrprofil(d_aussen, wand), None,
                                                  self.m.createIfcDirection([0.0, 0.0, 1.0]), laenge * self.mm)
        return self._platzierung(tuer, a, z, x), solid, laenge

    def bogen(self, tuer, start, ecke, ende, d_aussen, wand):
        """90°-Bogen von start über die Ecke nach ende in (u, v, z)."""
        a, e, b = tuer.lokal(*start), tuer.lokal(*ecke), tuer.lokal(*ende)
        rein, raus = (e - a), (b - e)
        rc = float(np.linalg.norm(rein))
        rein, raus = rein / rc, raus / rc
        m = self.m
        achse = m.createIfcAxis1Placement(m.createIfcCartesianPoint([rc * self.mm, 0.0, 0.0]),
                                          m.createIfcDirection([0.0, -1.0, 0.0]))
        solid = m.createIfcRevolvedAreaSolid(self._rohrprofil(d_aussen, wand), None, achse, self.viertelkreis)
        return self._platzierung(tuer, a, rein, raus), solid, rc * math.pi / 2

    def fertig(self, e, platzierung, koerper, typ, pset):
        e.ObjectPlacement = platzierung
        e.Representation = self._darstellung(koerper)
        api(self.m, "type.assign_type", related_objects=[e], relating_type=typ)
        ps = api(self.m, "pset.add_pset", product=e, name=PSET)
        api(self.m, "pset.edit_pset", pset=ps, properties=pset)
        return e

    def verbinde(self, a, b):
        self.m.createIfcRelConnectsElements(ifcopenshell.guid.new(), self.history, None, None, None, a, b)


# --------------------------------------------------------------------------
# Platzierung je Tür
# --------------------------------------------------------------------------
def lage_bestimmen(tuer, umgebung, regeln, variante, bedienung):
    """Platzprüfung: liefert Bezugspunkte der Bedienelemente und Meldungen."""
    be = regeln["bedienelemente"]
    meldungen, ohne = [], set()
    frei, hindernis, quer = tuer.platz(umgebung, regeln, "BG")
    lage = "neben der Tür"
    bezug = tuer.W + be["abstand_neben_tuer"]
    if frei < be["platz_min"]:
        if quer is None:
            raise NichtPlatzierbar(f"an der Schlossseite nur {frei:.0f} mm frei ({hindernis}) und keine Querwand")
        if quer.reicht_bis < be["abstand_querwand"] + 500:
            raise NichtPlatzierbar(f"Querwand {quer.element.Name} ist für {be['abstand_querwand']} mm Abstand zu kurz")
        if variante == "UP" and quer.dicke < regeln["wand"]["min_dicke_up_querwand"]:
            raise NichtPlatzierbar(f"Querwand {quer.element.Name} ist für Unterputz zu dünn")
        tuer.uq = quer.u
        bezug = quer.u + be["abstand_querwand"]
        lage = f"Querwand {quer.element.Name or ''}".strip()
        meldungen.append(f"nur {frei:.0f} mm frei bis {hindernis}, Bedienelemente auf der Querwand, "
                         f"{be['abstand_querwand']} mm von der Türwand")
    frei_b, hindernis_b, _ = tuer.platz(umgebung, regeln, "B")
    if frei_b < be["platz_min"]:
        ohne = {g["bmk"] for g in regeln["bauteile"]
                if g.get("bedienelement") and g["seite"] == "B" and gilt(g, bedienung)}
        meldungen.append(f"Bandseite nur {frei_b:.0f} mm frei ({hindernis_b}): "
                         f"{', '.join(sorted(ohne))} nicht gesetzt, bitte von Hand planen")
    ns = {"W": tuer.W, "H": tuer.H, "OK": be["oberkante"], "BE": bezug,
          "BEB": tuer.W + be["abstand_neben_tuer"]}
    return ns, lage, ohne, meldungen


def gilt(regel, bedienung):
    """Regeln mit nur_bei gelten nur in der genannten Anordnung der Bedienelemente."""
    return regel.get("nur_bei") in (None, bedienung)


def geraete_berechnen(tuer, regeln, variante, ns, ohne, bedienung):
    """Berechnet je Bauteil die Körper in (u, v, z) und den Anschlusskörper in (s, z)."""
    geraete = {}
    for g in regeln["bauteile"]:
        if g["bmk"] in ohne or not gilt(g, bedienung):
            continue
        var = g.get("varianten", {}).get(variante, g)
        masse = {k: rechne(val, ns) for k, val in var["masse"].items()}
        lok = dict(ns, **masse)
        s0, z0 = rechne(g["position"]["u"], lok), rechne(g["position"]["z"], lok)
        b, h, t = masse["b"], masse["h"], masse["t"]
        seite = g["seite"]
        if seite == "BG" and s0 < tuer.uq < s0 + b:
            raise NichtPlatzierbar(f"{g['bmk']} läge über der Raumecke")

        def kasten(sa, sb, da, db, za, zb):
            return tuer.punkt(sa, da, za, seite), tuer.punkt(sb, db, zb, seite)

        koerper = [kasten(s0, s0 + b, -t, 0.0, z0, z0 + h)]
        a = SimpleNamespace(u0=s0, u1=s0 + b, z0=z0, z1=z0 + h)
        einbau = var.get("einbau")
        tiefe = 0.0
        if einbau:
            eb, eh, tiefe = (rechne(einbau[k], ns) for k in ("b", "h", "t"))
            es0, ez0 = s0 + (b - eb) / 2, z0 + (h - eh) / 2
            koerper.append(kasten(es0, es0 + eb, 0.0, tiefe, ez0, ez0 + eh))
            a = SimpleNamespace(u0=es0, u1=es0 + eb, z0=ez0, z1=ez0 + eh)
        a.um, a.zm = (a.u0 + a.u1) / 2, (a.z0 + a.z1) / 2
        geraete[g["bmk"]] = SimpleNamespace(regel=g, koerper=koerper, a=a, einbau=bool(einbau), tiefe=tiefe,
                                            masse=masse, element=None)
        ns[g["bmk"].lstrip("-")] = a
    return geraete


def anschlusspunkt(geraet, ende, ns):
    a = geraet.a
    versatz = rechne(ende.get("versatz", 0), ns)
    return {
        "oben": (a.um + versatz, a.z1),
        "unten": (a.um + versatz, a.z0),
        "band": (a.u0, a.zm + versatz),
        "schloss": (a.u1, a.zm + versatz),
        "hinten": (a.um + versatz, a.zm),
    }[ende["flaeche"]]


def trasse(tuer, regeln, variante, lr, geraete, ns):
    """Liefert die Eckpunkte eines Leerrohrs in (u, v, z) und den Außendurchmesser."""
    d = regeln["leerrohr"]["aussendurchmesser"][lr["groesse"]]
    up = variante == "UP"
    gv, gn = geraete[lr["von"]["bmk"]], geraete[lr["nach"]["bmk"]]
    s, e = anschlusspunkt(gv, lr["von"], ns), anschlusspunkt(gn, lr["nach"], ns)

    if lr.get("ebene") == "B":
        # Start an der Rückseite eines Geräts der Bandgegenseite, durch die Wand, weiter auf der Bandseite
        tiefe = gn.tiefe / 2 if (up and gn.einbau) else -d / 2
        z = rechne(lr["bahn_z"], ns)
        return [(s[0], 0.0, s[1]), tuer.punkt(s[0], tiefe, s[1], "B"), tuer.punkt(s[0], tiefe, z, "B"),
                tuer.punkt(e[0], tiefe, z, "B"), tuer.punkt(e[0], tiefe, e[1], "B")], d

    tiefe = regeln["leerrohr"]["UP"]["tiefe_achse"] if up else -d / 2

    def hinter_geraet(g, ende, p):
        # Unterputz an einem Aufputzgerät: Rohr endet als Wandauslass hinter dem Gerät bzw. knapp darüber
        if up and not g.einbau and ende["flaeche"] in ("oben", "unten"):
            if g.regel.get("wand_hinter_geraet"):
                return (p[0], g.a.zm)
            return (p[0], g.a.z1 + 15 if ende["flaeche"] == "oben" else g.a.z0 - 15)
        return p

    s, e = hinter_geraet(gv, lr["von"], s), hinter_geraet(gn, lr["nach"], e)
    if "bahn_z" in lr:
        z = rechne(lr["bahn_z"], ns)
        sz = [s, (s[0], z), (e[0], z), e]
    elif lr["nach"]["flaeche"] in ("band", "schloss"):
        sz = [s, (s[0], e[1]), e]
    else:
        if abs(s[0] - e[0]) > 0.01 and abs(s[1] - e[1]) > 0.01:
            raise ValueError(f"{lr['id']}: ohne bahn_z müssen die Anschlüsse fluchten")
        sz = [s, e]
    pts = []
    for i, (si, zi) in enumerate(sz):
        if i and (sz[i - 1][0] - tuer.uq) * (si - tuer.uq) < 0:  # Lauf geht um die Raumecke
            pts.append((tuer.uq + tiefe, tiefe, zi))
        pts.append(tuer.punkt(si, tiefe, zi))
    return pts, d


def rohrstuecke(pts, d, innenradius, rohr_id):
    """Zerlegt den Eckpunktzug in gerade Stücke und 90°-Bögen."""
    sauber = [np.array(pts[0], float)]
    for p in pts[1:]:
        if np.linalg.norm(np.subtract(p, sauber[-1])) > 0.5:
            sauber.append(np.array(p, float))
    rc = innenradius + d / 2
    n = len(sauber)
    stuecke, anfang = [], sauber[0]
    for i in range(1, n):
        a, b = sauber[i - 1], sauber[i]
        laenge = float(np.linalg.norm(b - a))
        noetig = (rc if i > 1 else 0) + (rc if i < n - 1 else 0)
        if laenge < noetig - 0.5:
            raise NichtPlatzierbar(f"Leerrohr {rohr_id}: Abschnitt {laenge:.0f} mm ist für Bögen mit "
                                   f"Innenradius {innenradius:.0f} mm zu kurz (nötig {noetig:.0f} mm)")
        ri = (b - a) / laenge
        ende = b - ri * rc if i < n - 1 else b
        if np.linalg.norm(ende - anfang) > 0.5:
            stuecke.append(("gerade", anfang, ende))
        if i < n - 1:
            nach = (sauber[i + 1] - b) / np.linalg.norm(sauber[i + 1] - b)
            if abs(float(ri @ nach)) > 1e-6:
                raise ValueError(f"Leerrohr {rohr_id}: Richtungswechsel ist nicht rechtwinklig")
            anfang = b + nach * rc
            stuecke.append(("bogen", ende, b, anfang))
    return stuecke


def tuer_bestuecken(model, bauer, umgebung, door, regeln, variante, bedienung):
    tuer = Tuer(umgebung, door, regeln, bauer.mm)
    eigen = ifcopenshell.util.element.get_psets(door).get(PSET, {}).get("Bedienung")
    if eigen in regeln["bedienelemente"]["anordnung"]["werte"]:
        bedienung = eigen
    if variante == "UP" and tuer.t < regeln["wand"]["min_dicke_up"]:
        raise NichtPlatzierbar(f"Wand {tuer.t:.0f} mm ist für Unterputz zu dünn")
    ns, lage, ohne, meldungen = lage_bestimmen(tuer, umgebung, regeln, variante, bedienung)
    geraete = geraete_berechnen(tuer, regeln, variante, ns, ohne, bedienung)
    radius = regeln["leerrohr"]["bogen"]["innenradius"]
    laeufe = []
    for lr in regeln["leerrohre"]:
        if lr["von"]["bmk"] in ohne or lr["nach"]["bmk"] in ohne or not gilt(lr, bedienung):
            continue
        pts, d = trasse(tuer, regeln, variante, lr, geraete, ns)
        laeufe.append((lr, d, rohrstuecke(pts, d, radius, lr["id"])))

    # erst ab hier wird das Modell verändert
    geschoss = ifcopenshell.util.element.get_container(door)
    neu = []
    for bmk, g in geraete.items():
        r = g.regel
        teile = [bauer.quader(tuer, lo, hi) for lo, hi in g.koerper]
        koerper = [teile[0][1]]
        for _, solid, ecke in teile[1:]:
            solid.Position = bauer._achse((ecke - teile[0][2]) * bauer.mm, (0, 0, 1), (1, 0, 0))
            koerper.append(solid)
        typname = f"{r['name']} ({variante})" if "varianten" in r else r["name"]
        typ = bauer.typ(r["ifc_klasse"], typname, r["predefined_type"], r.get("object_type"))
        e = bauer.element(r["ifc_klasse"], f"{bmk} {r['name']}", r["predefined_type"], r.get("object_type"))
        m = g.masse
        eigenschaften = {
            "BMK": bmk, "Variante": variante, "Bedienung": bedienung, "TuerGlobalId": door.GlobalId, "TuerName": door.Name or "",
            "Seite": "Bandgegenseite" if r["seite"] == "BG" else "Bandseite",
            "Breite_mm": m["b"], "Hoehe_mm": m["h"], "Tiefe_mm": m["t"],
            "QuelleMasse": r.get("quelle_masse", ""),
        }
        if r.get("bedienelement"):
            eigenschaften["Lage"] = lage if r["seite"] == "BG" else "neben der Tür"
            eigenschaften["Oberkante_mm"] = ns["OK"]
        g.element = bauer.fertig(e, teile[0][0], koerper, typ, eigenschaften)
        neu.append(g.element)

    for lr, d, stuecke in laeufe:
        cfg = regeln["leerrohr"][variante]
        typ_rohr = bauer.typ("IfcCableCarrierSegment", f"{cfg['typname']} {lr['groesse']}", "CONDUITSEGMENT")
        typ_bogen = bauer.typ("IfcCableCarrierFitting",
                              f"{regeln['leerrohr']['bogen']['typname']} {lr['groesse']}, Innenradius {radius:g} mm", "BEND")
        fertige, gesamt = [], 0.0
        for i, st in enumerate(stuecke, 1):
            if st[0] == "gerade":
                platz, solid, laenge = bauer.rohr(tuer, st[1], st[2], d, cfg["wandstaerke"])
                e = bauer.element("IfcCableCarrierSegment", f"{lr['id']}.{i} Leerrohr {lr['groesse']}", "CONDUITSEGMENT")
                typ = typ_rohr
            else:
                platz, solid, laenge = bauer.bogen(tuer, st[1], st[2], st[3], d, cfg["wandstaerke"])
                e = bauer.element("IfcCableCarrierFitting", f"{lr['id']}.{i} Bogen {lr['groesse']}", "BEND")
                typ = typ_bogen
            gesamt += laenge
            fertige.append((e, platz, solid, typ, laenge, st[0]))
        vorher = geraete[lr["von"]["bmk"]].element
        for e, platz, solid, typ, laenge, art in fertige:
            eigenschaften = {
                "BMK": lr["id"], "Variante": variante, "Bedienung": bedienung, "TuerGlobalId": door.GlobalId, "TuerName": door.Name or "",
                "Kabel": ", ".join(lr["kabel"] + lr.get("kabel_zusatz", {}).get(bedienung, [])), "Nenngroesse": lr["groesse"], "Aussendurchmesser_mm": d,
                "Von": lr["von"]["bmk"], "Nach": lr["nach"]["bmk"],
                "Laenge_mm": round(laenge), "Gesamtlaenge_mm": round(gesamt),
            }
            if art == "bogen":
                eigenschaften["Innenradius_mm"] = radius
            bauer.fertig(e, platz, [solid], typ, eigenschaften)
            bauer.verbinde(vorher, e)
            vorher = e
            neu.append(e)
        bauer.verbinde(vorher, geraete[lr["nach"]["bmk"]].element)
        for bmk in lr.get("auch_verbunden", []):
            if bmk in geraete:
                bauer.verbinde(vorher, geraete[bmk].element)

    if geschoss:
        api(model, "spatial.assign_container", products=neu, relating_structure=geschoss)
    system = api(model, "system.add_system", ifc_class="IfcDistributionSystem")
    system.Name = f"Türsteuerung {door.Name or door.GlobalId} ({variante}, {bedienung})"
    system.Description = door.GlobalId
    api(model, "system.assign_system", products=neu, system=system)
    return neu, lage, meldungen, bedienung


def vorhandene_entfernen(model, door):
    """Entfernt eine frühere Bestückung derselben Tür."""
    n = 0
    for e in list(model.by_type("IfcDistributionElement")):
        if ifcopenshell.util.element.get_psets(e).get(PSET, {}).get("TuerGlobalId") == door.GlobalId:
            api(model, "root.remove_product", product=e)
            n += 1
    for s in list(model.by_type("IfcDistributionSystem")):
        if s.Description == door.GlobalId and (s.Name or "").startswith("Türsteuerung"):
            api(model, "system.remove_system", system=s)
    return n


def tueren_waehlen(model, regeln, args):
    tueren = model.by_type("IfcDoor")
    if args.tuer:
        ids = set(args.tuer)
        return [d for d in tueren if d.GlobalId in ids]
    if args.name:
        muster = re.compile(args.name)
        return [d for d in tueren if muster.search(d.Name or "")]
    if args.alle:
        return tueren
    a = regeln["ausloeser"]
    return [d for d in tueren
            if ifcopenshell.util.element.get_psets(d).get(a["pset"], {}).get(a["eigenschaft"]) == a["wert"]]


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("ifc", help="Eingabemodell (IFC4 oder IFC4X3)")
    p.add_argument("-o", "--out", required=True, help="Ausgabemodell")
    p.add_argument("--variante", choices=["AP", "UP"], required=True, help="Aufputz oder Unterputz")
    p.add_argument("--bedienung", choices=["einseitig", "beidseitig"],
                   help="Bedienelemente nur auf der Fluchtseite oder auf beiden Wandseiten (Standard aus der Regeldatei)")
    p.add_argument("--regeln", default=str(Path(__file__).with_name("platzierung.json")))
    wahl = p.add_argument_group("Türauswahl (ohne Angabe: Türen mit dem Auslöser-Merkmal aus der Regeldatei)")
    wahl.add_argument("--tuer", action="append", help="GlobalId einer Tür, mehrfach möglich")
    wahl.add_argument("--name", help="Regulärer Ausdruck auf den Türnamen")
    wahl.add_argument("--alle", action="store_true", help="alle Türen")
    args = p.parse_args(argv)

    regeln = json.loads(Path(args.regeln).read_text(encoding="utf-8"))
    model = ifcopenshell.open(args.ifc)
    if model.schema == "IFC2X3":
        sys.exit("IFC2X3 kennt die benötigten Klassen nicht. Bitte als IFC4 oder IFC4X3 exportieren.")
    tueren = tueren_waehlen(model, regeln, args)
    if not tueren:
        sys.exit("Keine passende Tür gefunden.")
    bedienung = args.bedienung or regeln["bedienelemente"]["anordnung"]["standard"]
    bauer = Bauer(model, regeln, args.variante)
    umgebung = Umgebung(model)
    nicht = 0
    for d in tueren:
        name = d.Name or d.GlobalId
        weg = vorhandene_entfernen(model, d)
        try:
            neu, lage, meldungen, bed = tuer_bestuecken(model, bauer, umgebung, d, regeln, args.variante, bedienung)
        except NichtPlatzierbar as grund:
            nicht += 1
            print(f"  {name}: NICHT bestückt – {grund}")
            continue
        ersetzt = f", {weg} alte ersetzt" if weg else ""
        print(f"  {name}: {len(neu)} Elemente ({args.variante}, {bed}), Bedienelemente: {lage}{ersetzt}")
        for m in meldungen:
            print(f"      Hinweis: {m}")
    model.write(args.out)
    print(f"Geschrieben: {args.out}" + (f" – {nicht} Tür(en) nicht bestückt" if nicht else ""))


if __name__ == "__main__":
    main()
