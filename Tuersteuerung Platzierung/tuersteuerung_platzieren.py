#!/usr/bin/env python3
"""Platziert die Bauteile der Türsteuerung an Türen eines IFC-Modells.

Die Regeln (Bauteile, Außenmaße, IFC-Klassen, Positionen, Leerrohre) stehen in
platzierung.json. Das Skript liest ein IFC-Modell, wählt Türen aus und setzt je
Tür die Bauteile als Quader und die Leerrohre als Rohrsegmente, wahlweise in
der Variante Aufputz (AP) oder Unterputz (UP).

Beispiele:
    python tuersteuerung_platzieren.py modell.ifc -o modell_AP.ifc --variante AP
    python tuersteuerung_platzieren.py modell.ifc -o out.ifc --variante UP --tuer 2O2Fr$t4X7Zf8NOew3FLOH
    python tuersteuerung_platzieren.py modell.ifc -o out.ifc --name "T0[12]" --variante AP
"""
from __future__ import annotations

import argparse
import json
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
# Türgeometrie
# --------------------------------------------------------------------------
class Tuer:
    """Maße und Lage einer Tür in Millimetern.

    Lokales System der Tür: x entlang der Breite, y durch die Wand, z nach oben.
    Nach IFC öffnet die Tür in Richtung +y, die Bandseite liegt also bei +y.
    Regelkoordinaten: u ab Bandkante, v ab Wandfläche der Bandgegenseite
    (v < 0 Raumseite Bandgegenseite, 0..t in der Wand, v > t Bandseite).
    """

    def __init__(self, model, door, regeln, mm):
        self.door = door
        self.mm = mm  # Projekteinheit je Millimeter
        self.W = door.OverallWidth / mm
        self.H = door.OverallHeight / mm
        self.matrix = ifcopenshell.util.placement.get_local_placement(door.ObjectPlacement)
        self.band = self._bandseite()
        self.y0, self.t = self._wand(model, regeln)

    def _bandseite(self):
        werte = ifcopenshell.util.element.get_psets(self.door).get(PSET, {})
        if werte.get("Bandseite"):
            return str(werte["Bandseite"]).lower()
        typ = ifcopenshell.util.element.get_type(self.door)
        op = getattr(self.door, "OperationType", None) or getattr(typ, "OperationType", None) or ""
        return "rechts" if "RIGHT" in op else "links"

    def _wand(self, model, regeln):
        """Lage der Wandflächen in Tür-y aus der Geometrie der Öffnung."""
        for rel in getattr(self.door, "FillsVoids", []) or []:
            oeffnung = rel.RelatingOpeningElement
            try:
                s = ifcopenshell.geom.settings()
                s.set("use-world-coords", True)
                shape = ifcopenshell.geom.create_shape(s, oeffnung)
                v = np.array(shape.geometry.verts).reshape(-1, 3)
                si = ifcopenshell.util.unit.calculate_unit_scale(model)
                v = v / si  # Meter -> Projekteinheit
                lokal = (np.linalg.inv(self.matrix) @ np.c_[v, np.ones(len(v))].T).T
                y = lokal[:, 1] / self.mm
                return float(y.min()), float(y.max() - y.min())
            except Exception as e:  # Geometrie nicht auswertbar
                print(f"  Hinweis: Öffnung von {self.door.Name} nicht auswertbar ({e}), nutze Fallback.")
        return 0.0, float(regeln["wand"]["dicke_fallback"])

    def lokal(self, u, v, z):
        x = u if self.band == "links" else self.W - u
        return np.array([x, self.y0 + v, z])

    def welt(self, p_lokal_mm):
        p = np.append(np.asarray(p_lokal_mm) * self.mm, 1.0)
        return (self.matrix @ p)[:3]


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
            None, self._achse(tuer.welt(ursprung_lokal_mm), r @ np.array(z_lokal, float), r @ np.array(x_lokal, float)))

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
        """Quader zwischen zwei Ecken in Regelkoordinaten (u, v, z). Gibt (Platzierung, Körper) zurück."""
        a, b = tuer.lokal(*lo), tuer.lokal(*hi)
        mn, mx = np.minimum(a, b), np.maximum(a, b)
        d = (mx - mn) * self.mm
        m = self.m
        profil = m.createIfcRectangleProfileDef(
            "AREA", None,
            m.createIfcAxis2Placement2D(m.createIfcCartesianPoint([float(d[0] / 2), float(d[1] / 2)]), None),
            float(d[0]), float(d[1]))
        solid = m.createIfcExtrudedAreaSolid(profil, None, m.createIfcDirection([0.0, 0.0, 1.0]), float(d[2]))
        return self._platzierung(tuer, mn), solid

    def rohr(self, tuer, p, q, d_aussen, wand):
        """Rohrsegment von p nach q (Regelkoordinaten u, v, z), achsparallel."""
        a, b = tuer.lokal(*p), tuer.lokal(*q)
        vek = b - a
        laenge = float(np.linalg.norm(vek))
        z = vek / laenge
        x = np.array([0.0, 0.0, 1.0]) if abs(z[2]) < 0.9 else np.array([1.0, 0.0, 0.0])
        m = self.m
        r = d_aussen / 2 * self.mm
        profil = m.createIfcCircleHollowProfileDef("AREA", None, None, float(r), float(wand * self.mm))
        solid = m.createIfcExtrudedAreaSolid(profil, None, m.createIfcDirection([0.0, 0.0, 1.0]), laenge * self.mm)
        return self._platzierung(tuer, a, z, x), solid, laenge

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
def geraete_berechnen(tuer, regeln, variante):
    """Berechnet je Bauteil Sichtkörper, Einbaukörper und Anschlusskörper in (u, v, z)."""
    ns = {"W": tuer.W, "H": tuer.H}
    geraete = {}
    for g in regeln["bauteile"]:
        var = g.get("varianten", {}).get(variante, g)
        masse = {k: rechne(val, ns) for k, val in var["masse"].items()}
        lok = dict(ns, **masse)
        u0 = rechne(g["position"]["u"], lok)
        z0 = rechne(g["position"]["z"], lok)
        b, h, t = masse["b"], masse["h"], masse["t"]
        bg = g["seite"] == "BG"
        # v-Bereich des sichtbaren Körpers vor der Wandfläche
        v_sicht = (-t, 0.0) if bg else (tuer.t, tuer.t + t)
        koerper = [((u0, v_sicht[0], z0), (u0 + b, v_sicht[1], z0 + h))]
        anschluss = SimpleNamespace(u0=u0, u1=u0 + b, z0=z0, z1=z0 + h)
        einbau = var.get("einbau")
        if einbau:
            eb, eh, et = (rechne(einbau[k], ns) for k in ("b", "h", "t"))
            eu0, ez0 = u0 + (b - eb) / 2, z0 + (h - eh) / 2
            v_ein = (0.0, et) if bg else (tuer.t - et, tuer.t)
            koerper.append(((eu0, v_ein[0], ez0), (eu0 + eb, v_ein[1], ez0 + eh)))
            anschluss = SimpleNamespace(u0=eu0, u1=eu0 + eb, z0=ez0, z1=ez0 + eh)
        anschluss.um = (anschluss.u0 + anschluss.u1) / 2
        anschluss.zm = (anschluss.z0 + anschluss.z1) / 2
        geraete[g["bmk"]] = SimpleNamespace(regel=g, koerper=koerper, a=anschluss, einbau=bool(einbau),
                                            masse=masse, element=None)
        ns[g["bmk"].lstrip("-")] = anschluss
    return geraete, ns


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
    """Liefert die Stützpunkte eines Leerrohrs in (u, v, z)."""
    d = regeln["leerrohr"]["aussendurchmesser"][lr["groesse"]]
    up = variante == "UP"
    v_ebene = regeln["leerrohr"]["UP"]["tiefe_achse"] if up else -d / 2
    gv, gn = geraete[lr["von"]["bmk"]], geraete[lr["nach"]["bmk"]]
    s, e = anschlusspunkt(gv, lr["von"], ns), anschlusspunkt(gn, lr["nach"], ns)

    def hinter_geraet(g, ende, p):
        # Unterputz an einem Aufputzgerät: Rohr endet hinter dem Gerät bzw. knapp darüber
        if up and not g.einbau and ende["flaeche"] in ("oben", "unten"):
            if g.regel.get("wand_hinter_geraet"):
                return (p[0], g.a.zm)
            return (p[0], g.a.z1 + 15 if ende["flaeche"] == "oben" else g.a.z0 - 15)
        return p

    s, e = hinter_geraet(gv, lr["von"], s), hinter_geraet(gn, lr["nach"], e)
    if "bahn_z" in lr:
        z = rechne(lr["bahn_z"], ns)
        uz = [s, (s[0], z), (e[0], z), e]
    elif lr["nach"]["flaeche"] in ("band", "schloss"):
        uz = [s, (s[0], e[1]), e]
    else:
        if abs(s[0] - e[0]) > 0.01 and abs(s[1] - e[1]) > 0.01:
            raise ValueError(f"{lr['id']}: ohne bahn_z müssen die Anschlüsse fluchten")
        uz = [s, e]
    pts = [(u, v_ebene, z) for u, z in uz]

    def stich(g, vorn):
        """Zusätzlicher Punkt quer zur Wand am Rohrende."""
        bg = g.regel["seite"] == "BG"
        p = pts[0] if vorn else pts[-1]
        if not bg:  # Gerät auf der Bandseite: Wanddurchführung
            ziel = g.koerper[1][0][1] if g.einbau else tuer.t
        elif up and not g.einbau:  # Unterputzrohr tritt an der Wandfläche aus
            ziel = 0.0
        else:
            return
        if abs(ziel - p[1]) > 0.5:
            neu = (p[0], ziel, p[2])
            pts.insert(0, neu) if vorn else pts.append(neu)

    stich(gv, True)
    stich(gn, False)
    sauber = [pts[0]]
    for p in pts[1:]:
        if np.linalg.norm(np.subtract(p, sauber[-1])) > 0.5:
            sauber.append(p)
    return sauber, d


def tuer_bestuecken(model, bauer, door, regeln, variante):
    tuer = Tuer(model, door, regeln, bauer.mm)
    if variante == "UP" and tuer.t < regeln["wand"]["min_dicke_up"]:
        print(f"  {door.Name}: Wand {tuer.t:.0f} mm ist für Unterputz zu dünn, übersprungen.")
        return None
    geraete, ns = geraete_berechnen(tuer, regeln, variante)
    geschoss = ifcopenshell.util.element.get_container(door)
    neu = []

    for bmk, g in geraete.items():
        r = g.regel
        teile = [bauer.quader(tuer, lo, hi) for lo, hi in g.koerper]
        platz = teile[0][0]
        # weitere Körper relativ zum ersten ausdrücken: gleiche Achsen, daher Versatz im lokalen System
        koerper = [teile[0][1]]
        if len(teile) > 1:
            a0 = np.minimum(tuer.lokal(*g.koerper[0][0]), tuer.lokal(*g.koerper[0][1]))
            a1 = np.minimum(tuer.lokal(*g.koerper[1][0]), tuer.lokal(*g.koerper[1][1]))
            teile[1][1].Position = bauer._achse((a1 - a0) * bauer.mm, (0, 0, 1), (1, 0, 0))
            koerper.append(teile[1][1])
        typname = f"{r['name']} ({variante})" if "varianten" in r else r["name"]
        typ = bauer.typ(r["ifc_klasse"], typname, r["predefined_type"], r.get("object_type"))
        e = bauer.element(r["ifc_klasse"], f"{bmk} {r['name']}", r["predefined_type"], r.get("object_type"))
        m = g.masse
        bauer.fertig(e, platz, koerper, typ, {
            "BMK": bmk, "Variante": variante, "TuerGlobalId": door.GlobalId, "TuerName": door.Name or "",
            "Seite": "Bandgegenseite" if r["seite"] == "BG" else "Bandseite",
            "Breite_mm": m["b"], "Hoehe_mm": m["h"], "Tiefe_mm": m["t"],
            "QuelleMasse": r.get("quelle_masse", ""),
        })
        g.element = e
        neu.append(e)

    for lr in regeln["leerrohre"]:
        pts, d = trasse(tuer, regeln, variante, lr, geraete, ns)
        cfg = regeln["leerrohr"][variante]
        typ = bauer.typ("IfcCableCarrierSegment", f"{cfg['typname']} {lr['groesse']}", "CONDUITSEGMENT")
        gesamt = sum(float(np.linalg.norm(np.subtract(b, a))) for a, b in zip(pts, pts[1:]))
        vorher = geraete[lr["von"]["bmk"]].element
        for i, (p, q) in enumerate(zip(pts, pts[1:]), 1):
            # an inneren Ecken um den Radius verlängern, damit die Ecke geschlossen ist
            ri = np.subtract(q, p) / np.linalg.norm(np.subtract(q, p))
            p2 = np.array(p) - ri * (d / 2 if i > 1 else 0)
            q2 = np.array(q) + ri * (d / 2 if i < len(pts) - 1 else 0)
            platz, solid, laenge = bauer.rohr(tuer, p2, q2, d, cfg["wandstaerke"])
            e = bauer.element("IfcCableCarrierSegment", f"{lr['id']}.{i} Leerrohr {lr['groesse']}", "CONDUITSEGMENT")
            bauer.fertig(e, platz, [solid], typ, {
                "BMK": lr["id"], "Variante": variante, "TuerGlobalId": door.GlobalId, "TuerName": door.Name or "",
                "Kabel": ", ".join(lr["kabel"]), "Nenngroesse": lr["groesse"],
                "Von": lr["von"]["bmk"], "Nach": lr["nach"]["bmk"],
                "Segmentlaenge_mm": round(laenge), "Gesamtlaenge_mm": round(gesamt),
            })
            bauer.verbinde(vorher, e)
            vorher = e
            neu.append(e)
        bauer.verbinde(vorher, geraete[lr["nach"]["bmk"]].element)
        for bmk in lr.get("auch_verbunden", []):
            bauer.verbinde(vorher, geraete[bmk].element)

    if geschoss:
        api(model, "spatial.assign_container", products=neu, relating_structure=geschoss)
    system = api(model, "system.add_system", ifc_class="IfcDistributionSystem")
    system.Name = f"Türsteuerung {door.Name or door.GlobalId} ({variante})"
    system.Description = door.GlobalId
    api(model, "system.assign_system", products=neu, system=system)
    return neu


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
    bauer = Bauer(model, regeln, args.variante)
    for d in tueren:
        weg = vorhandene_entfernen(model, d)
        neu = tuer_bestuecken(model, bauer, d, regeln, args.variante)
        if neu is not None:
            ersetzt = f", {weg} alte ersetzt" if weg else ""
            print(f"  {d.Name or d.GlobalId}: {len(neu)} Elemente platziert ({args.variante}){ersetzt}")
    model.write(args.out)
    print(f"Geschrieben: {args.out}")


if __name__ == "__main__":
    main()
