#!/usr/bin/env python3
"""Erzeugt eine kleine Beispiel-IFC mit Raumvolumen (IfcSpace) zum Testen.

    python beispiel_ifc_erzeugen.py beispiel.ifc
"""
import sys

import ifcopenshell
import ifcopenshell.api

# (Nummer, Name, Grundriss-Polygon [m], Hoehe [m])
RAEUME_EG = [
    ("0.01", "Grossraumbuero", [(0, 0), (20, 0), (20, 12), (0, 12)], 3.0),
    ("0.02", "Flur", [(0, 12.2), (32, 12.2), (32, 14.2), (0, 14.2)], 2.8),
    ("0.03", "Buero", [(0, 14.4), (5, 14.4), (5, 19.4), (0, 19.4)], 3.0),
    ("0.04", "Buero", [(5.2, 14.4), (10.2, 14.4), (10.2, 19.4), (5.2, 19.4)], 3.0),
    ("0.05", "Besprechung", [(10.4, 14.4), (20, 14.4), (20, 21.4), (10.4, 21.4)], 3.0),
    ("0.06", "Lager (L-Form)", [(20.2, 0), (32, 0), (32, 12), (26, 12), (26, 6), (20.2, 6)], 3.0),
    ("0.07", "Halle", [(20.2, 14.4), (32, 14.4), (32, 26), (20.2, 26)], 7.5),
    ("0.08", "WC", [(20.2, 6.2), (22.5, 6.2), (22.5, 9), (20.2, 9)], 2.6),
    ("0.09", "Technik", [(22.7, 6.2), (25.8, 6.2), (25.8, 12), (20.2, 12), (20.2, 9.2), (22.7, 9.2)], 3.0),
]


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

    for geschossname, hoehe_ok, raeume in (("EG", 0.0, RAEUME_EG), ("OG1", 8.0, RAEUME_EG[:6])):
        geschoss = api("root.create_entity", modell, ifc_class="IfcBuildingStorey", name=geschossname)
        api("aggregate.assign_object", modell, products=[geschoss], relating_object=gebaeude)
        api("geometry.edit_object_placement", modell, product=geschoss,
            matrix=[[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, hoehe_ok], [0, 0, 0, 1]])
        for nummer, name, polygon, hoehe in raeume:
            raum = api("root.create_entity", modell, ifc_class="IfcSpace",
                       name=nummer.replace("0.", "1.") if geschossname != "EG" else nummer)
            raum.LongName = name
            api("aggregate.assign_object", modell, products=[raum], relating_object=geschoss)
            api("geometry.edit_object_placement", modell, product=raum,
                matrix=[[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, hoehe_ok], [0, 0, 0, 1]])
            punkte = [modell.createIfcCartesianPoint((x * 1000.0, y * 1000.0)) for x, y in polygon]
            profil = modell.createIfcArbitraryClosedProfileDef(
                "AREA", None, modell.createIfcPolyline(punkte + [punkte[0]]))
            koerper = modell.createIfcExtrudedAreaSolid(
                profil, None, modell.createIfcDirection((0.0, 0.0, 1.0)), hoehe * 1000.0)
            darstellung = modell.createIfcShapeRepresentation(body, "Body", "SweptSolid", [koerper])
            api("geometry.assign_representation", modell, product=raum, representation=darstellung)
    modell.write(pfad)
    print(f"geschrieben: {pfad}")


if __name__ == "__main__":
    erzeugen(sys.argv[1] if len(sys.argv) > 1 else "beispiel.ifc")
