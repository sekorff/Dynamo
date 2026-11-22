#!/usr/bin/env python3
"""Prüft testmodell_AP.ifc und testmodell_UP.ifc gegen die Regeldatei."""
import itertools
import json
import math
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
BE = REGELN["bedienelemente"]
RADIUS = REGELN["leerrohr"]["bogen"]["innenradius"]
ERWARTET = {"T01": "neben der Tür", "T02": "neben der Tür", "T04": "Querwand"}  # T03 ohne Merkmal, T05 abgelehnt
fehler = []


def pruefe(ok, text):
    if not ok:
        fehler.append(text)


def netz(model, element):
    s = ifcopenshell.geom.settings()
    s.set("use-world-coords", True)
    return np.array(ifcopenshell.geom.create_shape(s, element).geometry.verts).reshape(-1, 3) * 1000.0


def achse(element):
    """Stützpunkte der Rohrachse in Weltkoordinaten (mm), direkt aus den IFC-Parametern."""
    m = ifcopenshell.util.placement.get_local_placement(element.ObjectPlacement)
    solid = element.Representation.Representations[0].Items[0]
    if solid.is_a("IfcExtrudedAreaSolid"):
        lokal = [(0, 0, t) for t in np.linspace(0, solid.Depth, max(2, int(solid.Depth / 4) + 1))]
    else:
        rc = solid.Axis.Location.Coordinates[0]
        winkel = solid.Angle if solid.Angle < 7 else math.radians(solid.Angle)
        lokal = [(rc - rc * math.cos(a), 0, rc * math.sin(a)) for a in np.linspace(0, winkel, 45)]
    return (m @ np.c_[np.array(lokal), np.ones(len(lokal))].T).T[:, :3]


def in_quader(p, box, rand=0.0):
    return bool(np.all(p >= box[0] - rand) and np.all(p <= box[1] + rand))


def gilt(regel, bedienung):
    return regel.get("nur_bei") in (None, bedienung)


def pruefe_modell(variante, bedienung):
    datei = f"testmodell_{variante}" + ("_beidseitig" if bedienung == "beidseitig" else "") + ".ifc"
    model = ifcopenshell.open(str(HIER / datei))
    bauteile = [r for r in REGELN["bauteile"] if gilt(r, bedienung)]
    leerrohre = [r for r in REGELN["leerrohre"] if gilt(r, bedienung)]
    tueren = {d.Name: d for d in model.by_type("IfcDoor")}
    waende = {w.Name: netz(model, w) for w in model.by_type("IfcWall")}
    wandbox = {n: (v.min(axis=0), v.max(axis=0)) for n, v in waende.items()}
    je_tuer = defaultdict(list)
    for e in model.by_type("IfcDistributionElement"):
        ps = ifcopenshell.util.element.get_psets(e).get("Tuersteuerung")
        if ps:
            je_tuer[ps["TuerName"]].append((e, ps))
    pruefe(set(je_tuer) == set(ERWARTET), f"{variante}: bestückte Türen {sorted(je_tuer)}, erwartet {sorted(ERWARTET)}")
    nachbarn = defaultdict(set)
    for rel in model.by_type("IfcRelConnectsElements"):
        nachbarn[rel.RelatingElement.id()].add(rel.RelatedElement.id())
        nachbarn[rel.RelatedElement.id()].add(rel.RelatingElement.id())

    for name, eintraege in sorted(je_tuer.items()):
        wo = f"{variante} {bedienung} {name}"
        tuer = tueren[name]
        boden = ifcopenshell.util.placement.get_local_placement(tuer.ObjectPlacement)[2, 3]
        box, geraete, rohre, daten = {}, {}, defaultdict(list), {}
        for e, ps in eintraege:
            daten[e.id()] = ps
            v = netz(model, e)
            box[e.id()] = (v.min(axis=0), v.max(axis=0))
            if e.is_a("IfcCableCarrierSegment") or e.is_a("IfcCableCarrierFitting"):
                rohre[ps["BMK"]].append(e)
            else:
                geraete[ps["BMK"]] = e

        # Bauteile: vollständig, Klasse, Typ, Außenmaße, Oberkante, Lage
        pruefe(set(geraete) == {r["bmk"] for r in bauteile}, f"{wo}: Bauteile {sorted(geraete)}")
        pruefe(set(rohre) == {r["id"] for r in leerrohre}, f"{wo}: Leerrohre {sorted(rohre)}")
        for r in bauteile:
            e = geraete.get(r["bmk"])
            pruefe(e is not None, f"{wo}: {r['bmk']} fehlt")
            if e is None:
                continue
            pruefe(e.is_a() == r["ifc_klasse"], f"{wo}: {r['bmk']} ist {e.is_a()}")
            pt = ifcopenshell.util.element.get_predefined_type(e)
            pruefe(pt == (r.get("object_type") or r["predefined_type"]), f"{wo}: {r['bmk']} PredefinedType {pt}")
            typ = ifcopenshell.util.element.get_type(e)
            pruefe(typ is not None and typ.is_a() == r["ifc_klasse"] + "Type", f"{wo}: {r['bmk']} ohne Typ")
            ps = daten[e.id()]
            mn, mx = box[e.id()]
            if not r.get("varianten", {}).get(variante, {}).get("einbau"):
                soll = sorted([ps["Breite_mm"], ps["Hoehe_mm"], ps["Tiefe_mm"]])
                pruefe(np.allclose(sorted(mx - mn), soll, atol=0.5), f"{wo}: {r['bmk']} Maße {sorted(mx - mn)} statt {soll}")
            if r.get("bedienelement"):
                pruefe(abs(mx[2] - boden - BE["oberkante"]) < 0.5,
                       f"{wo}: {r['bmk']} Oberkante {mx[2] - boden:.1f} statt {BE['oberkante']}")
                soll_lage = ERWARTET[name] if r["seite"] == "BG" else "neben der Tür"
                pruefe(ps["Lage"].startswith(soll_lage), f"{wo}: {r['bmk']} Lage {ps['Lage']!r}")
        if name == "T04":
            # Türwand-Fläche der Bandgegenseite liegt bei y = 0, Querwand-Fläche bei x = 3175
            mn, mx = box[geraete["-A1"].id()]
            pruefe(abs((mn[1] + mx[1]) / 2 + BE["abstand_querwand"]) < 0.5, f"{wo}: -A1 nicht {BE['abstand_querwand']} mm von der Türwand")
            pruefe(mn[0] <= 3175 + 0.5 <= mx[0] + 1 and mx[0] > 3175, f"{wo}: -A1 sitzt nicht auf der Querwand")

        # Leerrohre: Reihenfolge, Bögen, Lage, Abstände
        punkte, durchmesser = {}, {}
        for lr in leerrohre:
            teile = sorted(rohre.get(lr["id"], []), key=lambda e: int(e.Name.split()[0].split(".")[1]))
            pruefe(bool(teile), f"{wo}: Leerrohr {lr['id']} fehlt")
            if not teile:
                continue
            d = daten[teile[0].id()]["Aussendurchmesser_mm"]
            durchmesser[lr["id"]] = d
            linien = [achse(e) for e in teile]
            punkte[lr["id"]] = np.vstack(linien)
            for e, vor, nach in zip(teile[1:], linien, linien[1:]):
                # lückenlos und knickfrei: Ende des Vorgängers = Anfang, gleiche Richtung
                pruefe(np.linalg.norm(vor[-1] - nach[0]) < 0.5, f"{wo}: {e.Name} schließt nicht an")
                r1 = (vor[-1] - vor[-2]) / np.linalg.norm(vor[-1] - vor[-2])
                r2 = (nach[1] - nach[0]) / np.linalg.norm(nach[1] - nach[0])
                pruefe(float(r1 @ r2) > 0.995, f"{wo}: Knick ohne Bogen vor {e.Name}")
            for e, linie in zip(teile, linien):
                if e.is_a("IfcCableCarrierFitting"):
                    solid = e.Representation.Representations[0].Items[0]
                    innen = solid.Axis.Location.Coordinates[0] - d / 2
                    pruefe(abs(innen - RADIUS) < 0.5, f"{wo}: {e.Name} Innenradius {innen:.1f}")
                    pruefe(ifcopenshell.util.element.get_predefined_type(e) == "BEND", f"{wo}: {e.Name} kein BEND")
                    # Netz und berechnete Achse müssen zusammenpassen
                    mn, mx = box[e.id()]
                    pruefe(np.all(linie.min(axis=0) > mn - 1) and np.all(linie.max(axis=0) < mx + 1), f"{wo}: {e.Name} Geometrie weicht ab")
                else:
                    pruefe(ifcopenshell.util.element.get_predefined_type(e) == "CONDUITSEGMENT", f"{wo}: {e.Name} falscher Typ")
            in_wand = np.array([any(in_quader(p, b) for b in wandbox.values()) for p in punkte[lr["id"]]])
            if variante == "UP":
                pruefe(in_wand.all(), f"{wo}: {lr['id']} verlässt die Wand")
            elif lr.get("ebene") != "B":
                pruefe(not in_wand.any(), f"{wo}: {lr['id']} liegt nicht auf der Wand")

            # durchgehend verbunden vom Start- zum Zielgerät
            erlaubt = {e.id() for e in teile}
            start, ziel = geraete[lr["von"]["bmk"]].id(), geraete[lr["nach"]["bmk"]].id()
            offen, gesehen = [start], {start}
            while offen:
                for n in nachbarn[offen.pop()]:
                    if n not in gesehen and (n in erlaubt or n == ziel):
                        gesehen.add(n)
                        offen.append(n)
            pruefe(ziel in gesehen and erlaubt <= gesehen, f"{wo}: {lr['id']} nicht durchgehend verbunden")

        for a, b in itertools.combinations(punkte, 2):
            diff = punkte[a][:, None, :] - punkte[b][None, :, :]
            abstand = float(np.sqrt((diff ** 2).sum(axis=2)).min())
            pruefe(abstand >= (durchmesser[a] + durchmesser[b]) / 2 - 1.0, f"{wo}: {a} und {b} nur {abstand:.0f} mm Achsabstand")
        enden = {lr["id"]: {lr["von"]["bmk"], lr["nach"]["bmk"], *lr.get("auch_verbunden", [])} for lr in leerrohre}
        for rid, pts in punkte.items():
            for bmk, g in geraete.items():
                if bmk not in enden[rid]:
                    rand = durchmesser[rid] / 2 - 1.0
                    pruefe(not any(in_quader(p, box[g.id()], rand) for p in pts), f"{wo}: {rid} durchdringt {bmk}")
        for (ba, a), (bb, b) in itertools.combinations(geraete.items(), 2):
            (amn, amx), (bmn, bmx) = box[a.id()], box[b.id()]
            pruefe(not all(amn[i] + 1 < bmx[i] and bmn[i] + 1 < amx[i] for i in range(3)), f"{wo}: {ba} überlappt {bb}")
        pruefe(len([s for s in model.by_type("IfcDistributionSystem") if s.Description == tuer.GlobalId]) == 1, f"{wo}: System fehlt")
        boegen = sum(e.is_a("IfcCableCarrierFitting") for t in rohre.values() for e in t)
        print(f"  {wo}: {len(geraete)} Bauteile, {len(rohre)} Leerrohre, {boegen} Bögen, Bedienelemente {ERWARTET[name]}")


if __name__ == "__main__":
    for v in ("AP", "UP"):
        for b in ("einseitig", "beidseitig"):
            pruefe_modell(v, b)
    if fehler:
        print(f"\n{len(fehler)} Fehler:")
        print("\n".join("  - " + f for f in fehler))
        sys.exit(1)
    print("Alle Prüfungen bestanden.")
