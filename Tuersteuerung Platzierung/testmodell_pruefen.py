#!/usr/bin/env python3
"""Prüft testmodell_AP.ifc und testmodell_UP.ifc gegen die Regeldatei."""
import itertools
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

import ifcopenshell
import ifcopenshell.geom
import ifcopenshell.util.element
import ifcopenshell.util.placement

HIER = Path(__file__).parent
REGELN = json.loads((HIER / "platzierung.json").read_text(encoding="utf-8"))
fehler = []


def pruefe(ok, text):
    if not ok:
        fehler.append(text)


def punkte_lokal(model, element, tuer):
    """Eckpunkte eines Elements im lokalen System der Tür, in mm."""
    s = ifcopenshell.geom.settings()
    s.set("use-world-coords", True)
    v = np.array(ifcopenshell.geom.create_shape(s, element).geometry.verts).reshape(-1, 3) * 1000.0
    inv = np.linalg.inv(ifcopenshell.util.placement.get_local_placement(tuer.ObjectPlacement))
    return (inv @ np.c_[v, np.ones(len(v))].T).T[:, :3]


def ueberlappt(a, b, tol=1.0):
    return all(a[0][i] + tol < b[1][i] and b[0][i] + tol < a[1][i] for i in range(3))


def pruefe_modell(variante):
    model = ifcopenshell.open(str(HIER / f"testmodell_{variante}.ifc"))
    tueren = {d.Name: d for d in model.by_type("IfcDoor")}
    je_tuer = defaultdict(list)
    for e in model.by_type("IfcDistributionElement"):
        ps = ifcopenshell.util.element.get_psets(e).get("Tuersteuerung")
        if ps:
            je_tuer[ps["TuerName"]].append((e, ps))
    pruefe(set(je_tuer) == {"T01", "T02"}, f"{variante}: bestückte Türen {sorted(je_tuer)}, erwartet T01 und T02")
    dicke = {"T01": 240.0, "T02": 175.0}

    for name, eintraege in je_tuer.items():
        tuer = tueren[name]
        box, geraete, rohre = {}, {}, defaultdict(list)
        for e, ps in eintraege:
            p = punkte_lokal(model, e, tuer)
            box[e.id()] = (p.min(axis=0), p.max(axis=0))
            (rohre[ps["BMK"]].append(e) if e.is_a("IfcCableCarrierSegment") else geraete.__setitem__(ps["BMK"], e))

        # Bauteile: vollständig, richtige Klasse, Typ, Außenmaße
        for r in REGELN["bauteile"]:
            e = geraete.get(r["bmk"])
            pruefe(e is not None, f"{variante} {name}: {r['bmk']} fehlt")
            if e is None:
                continue
            pruefe(e.is_a() == r["ifc_klasse"], f"{variante} {name}: {r['bmk']} ist {e.is_a()}")
            pt = ifcopenshell.util.element.get_predefined_type(e)
            soll_pt = r.get("object_type") or r["predefined_type"]
            pruefe(pt == soll_pt, f"{variante} {name}: {r['bmk']} PredefinedType {pt}")
            typ = ifcopenshell.util.element.get_type(e)
            pruefe(typ is not None and typ.is_a() == r["ifc_klasse"] + "Type", f"{variante} {name}: {r['bmk']} ohne Typ")
            ps = ifcopenshell.util.element.get_psets(e)["Tuersteuerung"]
            mn, mx = box[e.id()]
            ist = sorted(mx - mn)
            einbau = r.get("varianten", {}).get(variante, {}).get("einbau")
            if not einbau:
                soll = sorted([ps["Breite_mm"], ps["Hoehe_mm"], ps["Tiefe_mm"]])
                pruefe(np.allclose(ist, soll, atol=0.5), f"{variante} {name}: {r['bmk']} Maße {ist} statt {soll}")
            else:
                pruefe(mn[1] < 0 < mx[1] or mn[1] < dicke[name] < mx[1],
                       f"{variante} {name}: {r['bmk']} Einbaukörper liegt nicht in der Wand")

        # Leerrohre: vollständig, Lage zur Wand, keine Durchdringung
        t = dicke[name]
        for lr in REGELN["leerrohre"]:
            segs = rohre.get(lr["id"], [])
            pruefe(bool(segs), f"{variante} {name}: Leerrohr {lr['id']} fehlt")
            for e in segs:
                pruefe(ifcopenshell.util.element.get_predefined_type(e) == "CONDUITSEGMENT", f"{variante} {name}: {e.Name} falscher Typ")
                mn, mx = box[e.id()]
                if variante == "UP":
                    pruefe(mn[1] > -0.5 and mx[1] < t + 0.5, f"{variante} {name}: {e.Name} ragt aus der Wand ({mn[1]:.0f}..{mx[1]:.0f})")
                elif lr["id"] != "R8":
                    pruefe(mx[1] < 0.5, f"{variante} {name}: {e.Name} liegt nicht auf der Wand")
        alle_rohre = [(rid, e) for rid, segs in rohre.items() for e in segs]
        for (ra, a), (rb, b) in itertools.combinations(alle_rohre, 2):
            if ra != rb:
                pruefe(not ueberlappt(box[a.id()], box[b.id()]), f"{variante} {name}: {a.Name} kreuzt {b.Name}")
        enden = {lr["id"]: {lr["von"]["bmk"], lr["nach"]["bmk"], *lr.get("auch_verbunden", [])} for lr in REGELN["leerrohre"]}
        for rid, e in alle_rohre:
            for bmk, g in geraete.items():
                if bmk not in enden[rid]:
                    pruefe(not ueberlappt(box[e.id()], box[g.id()]), f"{variante} {name}: {e.Name} durchdringt {bmk}")
        for (ba, a), (bb, b) in itertools.combinations(geraete.items(), 2):
            pruefe(not ueberlappt(box[a.id()], box[b.id()]), f"{variante} {name}: {ba} überlappt {bb}")

        # Verbindung: von jedem Startgerät über die Rohrsegmente zum Zielgerät
        nachbarn = defaultdict(set)
        for rel in model.by_type("IfcRelConnectsElements"):
            nachbarn[rel.RelatingElement.id()].add(rel.RelatedElement.id())
            nachbarn[rel.RelatedElement.id()].add(rel.RelatingElement.id())
        for lr in REGELN["leerrohre"]:
            erlaubt = {e.id() for e in rohre[lr["id"]]}
            start, ziel = geraete[lr["von"]["bmk"]].id(), geraete[lr["nach"]["bmk"]].id()
            offen, gesehen = [start], {start}
            while offen:
                for n in nachbarn[offen.pop()]:
                    if n not in gesehen and (n in erlaubt or n == ziel):
                        gesehen.add(n)
                        offen.append(n)
            pruefe(ziel in gesehen and erlaubt <= gesehen, f"{variante} {name}: {lr['id']} verbindet {lr['von']['bmk']} und {lr['nach']['bmk']} nicht durchgehend")
        systeme = [s for s in model.by_type("IfcDistributionSystem") if s.Description == tuer.GlobalId]
        pruefe(len(systeme) == 1, f"{variante} {name}: {len(systeme)} Systeme")
        print(f"  {variante} {name}: {len(geraete)} Bauteile, {len(alle_rohre)} Rohrsegmente in {len(rohre)} Leerrohren")


if __name__ == "__main__":
    for v in ("AP", "UP"):
        pruefe_modell(v)
    if fehler:
        print(f"\n{len(fehler)} Fehler:")
        print("\n".join("  - " + f for f in fehler))
        sys.exit(1)
    print("Alle Prüfungen bestanden.")
