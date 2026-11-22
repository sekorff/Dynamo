#!/usr/bin/env python3
"""Erzeugt eine kleine Beispiel-IFC mit Raumvolumen (IfcSpace) zum Testen.

    python beispiel_ifc_erzeugen.py beispiel.ifc
"""
import sys

import ifcopenshell
import ifcopenshell.api

# (Nummer, Name, Grundriss-Polygon [m], lichte Hoehe [m], Hohlraum ueber Abhangdecke [m] oder None)
# Art der Abhangdecke je Raum: Name mit Rastermass, "PLATTEN" = einzeln modellierte Platten
DECKENART = {
    "01": "Rasterdecke Mineralfaser 625x625",
    "02": "Rasterdecke 600x600",
    "03": "Rasterdecke 625x625",
    "04": "Abhangdecke GK",
    "05": "PLATTEN",
    "08": "Abhangdecke GK",
}
PLATTE = 0.625
RAEUME_EG = [
    ("0.01", "Grossraumbuero", [(0, 0), (20, 0), (20, 12), (0, 12)], 3.0, 0.50),
    ("0.02", "Flur", [(0, 12.2), (32, 12.2), (32, 14.2), (0, 14.2)], 2.8, 0.70),
    ("0.03", "Buero", [(0, 14.4), (5, 14.4), (5, 19.4), (0, 19.4)], 3.0, 0.20),
    ("0.04", "Buero", [(5.2, 14.4), (10.2, 14.4), (10.2, 19.4), (5.2, 19.4)], 3.0, 0.29),
    ("0.05", "Besprechung", [(10.4, 14.4), (20, 14.4), (20, 21.4), (10.4, 21.4)], 3.0, 0.50),
    ("0.06", "Lager (L-Form)", [(20.2, 0), (32, 0), (32, 12), (26, 12), (26, 6), (20.2, 6)], 3.5, None),
    ("0.07", "Halle", [(20.2, 14.4), (32, 14.4), (32, 26), (20.2, 26)], 7.5, None),
    ("0.08", "WC", [(20.2, 6.2), (22.5, 6.2), (22.5, 9), (20.2, 9)], 2.6, 0.90),
    ("0.09", "Technik", [(22.7, 6.2), (25.8, 6.2), (25.8, 12), (20.2, 12), (20.2, 9.2), (22.7, 9.2)], 3.5, None),
]
DECKENSTAERKE = 0.02  # Abhangdecke

# Unterzuege: (Name, Rechteck x0, y0, x1, y1 [m], Unterkante, Oberkante [m ueber Geschoss])
# Treppenhaus am Ostende des Flurs (in jedem Geschoss)
TREPPENHAUS = ("0.10", "Treppenhaus", [(32.2, 10), (36, 10), (36, 16.2), (32.2, 16.2)], 3.5, None)

# Tueren: (Name, Rechteck x0, y0, x1, y1 [m], nur im Erdgeschoss?)
TUEREN = [
    ("T Flur-Treppenhaus", (32.0, 12.7, 32.2, 13.7), False),
    ("T Flur-Buero 0.03", (2.0, 14.2, 3.0, 14.4), False),
    ("T Flur-Grossraumbuero", (9.0, 12.0, 10.0, 12.2), False),
    ("T Haupteingang", (-0.2, 12.7, 0.0, 13.7), True),
    ("T Treppenhaus ins Freie", (36.0, 12.5, 36.2, 13.5), True),
    ("T Halle ins Freie", (25.0, 26.0, 27.0, 26.2), True),
]

# Brandabschnitte: (Name, Rechteck x0, y0, x1, y1 [m]) - in jedem Geschoss
BRANDABSCHNITTE = [
    ("BA 1", (-0.1, -0.1, 20.1, 26.1)),
    ("BA 2", (20.1, -0.1, 32.1, 26.1)),
    ("BA 3", (32.1, 9.9, 36.1, 16.3)),   # Treppenhaus
]

UNTERZUEGE_EG = [
    ("UZ Lager", (26.0, 5.85, 32.0, 6.15), 3.0, 3.5),           # sichtbar: teilt den Raum
    ("UZ Halle", (20.2, 20.0, 32.0, 20.4), 6.8, 7.5),           # sichtbar: teilt die Halle
    ("UZ Grossraumbuero", (9.85, 0.0, 10.15, 12.0), 3.02, 3.52),  # im Hohlraum: teilt die Zwischendecke
]


def _koerper(modell, polygon, z0, hoehe):
    """Extrusionskoerper aus Grundriss [m] in Millimetern."""
    punkte = [modell.createIfcCartesianPoint((x * 1000.0, y * 1000.0)) for x, y in polygon]
    profil = modell.createIfcArbitraryClosedProfileDef(
        "AREA", None, modell.createIfcPolyline(punkte + [punkte[0]]))
    lage = modell.createIfcAxis2Placement3D(modell.createIfcCartesianPoint((0.0, 0.0, z0 * 1000.0)), None, None)
    return modell.createIfcExtrudedAreaSolid(
        profil, lage, modell.createIfcDirection((0.0, 0.0, 1.0)), hoehe * 1000.0)


def erzeugen(pfad: str) -> None:
    api = ifcopenshell.api.run
    modell = api("project.create_file", version="IFC4")
    projekt = api("root.create_entity", modell, ifc_class="IfcProject", name="Beispiel Rauchmelder")
    api("unit.assign_unit", modell)  # Millimeter
    kontext = api("context.add_context", modell, context_type="Model")
    body = api("context.add_context", modell, context_type="Model", context_identifier="Body",
               target_view="MODEL_VIEW", parent=kontext)
    grundstueck = api("root.create_entity", modell, ifc_class="IfcSite", name="Grundstueck")
    gebaeude = api("root.create_entity", modell, ifc_class="IfcBuilding", name="Gebaeude")
    api("aggregate.assign_object", modell, products=[grundstueck], relating_object=projekt)
    api("aggregate.assign_object", modell, products=[gebaeude], relating_object=grundstueck)

    def bauteil(klasse, name, geschoss, hoehe_ok, polygon, z0, hoehe, typ=None):
        element = api("root.create_entity", modell, ifc_class=klasse, name=name, predefined_type=typ)
        if klasse == "IfcSpace":
            api("aggregate.assign_object", modell, products=[element], relating_object=geschoss)
        else:
            api("spatial.assign_container", modell, products=[element], relating_structure=geschoss)
        api("geometry.edit_object_placement", modell, product=element,
            matrix=[[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, hoehe_ok], [0, 0, 0, 1]])
        darstellung = modell.createIfcShapeRepresentation(
            body, "Body", "SweptSolid", [_koerper(modell, polygon, z0, hoehe)])
        api("geometry.assign_representation", modell, product=element, representation=darstellung)
        return element

    for geschossname, hoehe_ok, raeume, unterzuege in (
            ("EG", 0.0, RAEUME_EG + [TREPPENHAUS], UNTERZUEGE_EG),
            ("OG1", 8.0, RAEUME_EG[:6] + [TREPPENHAUS], UNTERZUEGE_EG[:1])):
        geschoss = api("root.create_entity", modell, ifc_class="IfcBuildingStorey", name=geschossname)
        api("aggregate.assign_object", modell, products=[geschoss], relating_object=gebaeude)
        api("geometry.edit_object_placement", modell, product=geschoss,
            matrix=[[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, hoehe_ok], [0, 0, 0, 1]])
        for nummer, name, polygon, hoehe, hohlraum in raeume:
            if geschossname != "EG":
                nummer = nummer.replace("0.", "1.", 1)
            raum = bauteil("IfcSpace", nummer, geschoss, hoehe_ok, polygon, 0.0, hoehe)
            raum.LongName = name
            rohdecke = hoehe
            if hohlraum is not None:  # Raumvolumen endet an der Abhangdecke
                art = DECKENART[nummer[-2:]]
                if art == "PLATTEN":  # jede Platte ein eigenes Bauteil, Rest als Randzuschnitt
                    xs = [p[0] for p in polygon]
                    ys = [p[1] for p in polygon]
                    x = min(xs)
                    while x < max(xs) - 1e-6:
                        x1, y = min(x + PLATTE, max(xs)), min(ys)
                        while y < max(ys) - 1e-6:
                            y1 = min(y + PLATTE, max(ys))
                            bauteil("IfcCovering", "Deckenplatte", geschoss, hoehe_ok,
                                    [(x, y), (x1, y), (x1, y1), (x, y1)], hoehe, DECKENSTAERKE,
                                    typ="CEILING")
                            y = y1
                        x = x1
                else:
                    bauteil("IfcCovering", f"{art} {nummer}", geschoss, hoehe_ok, polygon,
                            hoehe, DECKENSTAERKE, typ="CEILING")
                rohdecke = hoehe + DECKENSTAERKE + hohlraum
            bauteil("IfcSlab", f"Decke ueber {nummer}", geschoss, hoehe_ok, polygon,
                    rohdecke, 0.25, typ="FLOOR")
        # Brandabschnitte als Raumpolygone
        for name, (x0, y0, x1, y1) in BRANDABSCHNITTE:
            abschnitt = bauteil("IfcSpace", name, geschoss, hoehe_ok,
                                [(x0, y0), (x1, y0), (x1, y1), (x0, y1)], 0.0, 7.5)
            abschnitt.LongName = "Brandabschnitt"
        for name, (x0, y0, x1, y1), nur_eg in TUEREN:
            if nur_eg and geschossname != "EG":
                continue
            bauteil("IfcDoor", name, geschoss, hoehe_ok,
                    [(x0, y0), (x1, y0), (x1, y1), (x0, y1)], 0.0, 2.1, typ="DOOR")
        for name, (x0, y0, x1, y1), uk, ok in unterzuege:
            bauteil("IfcBeam", name, geschoss, hoehe_ok,
                    [(x0, y0), (x1, y0), (x1, y1), (x0, y1)], uk, ok - uk, typ="BEAM")
    modell.write(pfad)
    print(f"geschrieben: {pfad}")


if __name__ == "__main__":
    erzeugen(sys.argv[1] if len(sys.argv) > 1 else "beispiel.ifc")
