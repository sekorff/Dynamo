#!/usr/bin/env python3
"""Erzeugt das Testmodell testmodell.ifc und daraus die Varianten AP und UP.

Inhalt: ein Geschoss, drei Wände, fünf Türen.
  T01  1000 x 2100, Band links,  Wand 240 mm, 1400 mm frei -> Bedienelemente neben der Tür
  T02  1100 x 2100, Band rechts, Wand 175 mm, um 90° gedreht -> neben der Tür
  T03   900 x 2100, Band links,  ohne Auslöser-Merkmal -> bleibt unbestückt
  T04  1000 x 2100, Band rechts, 325 mm bis zur Querwand -> Bedienelemente auf der Querwand
  T05  1000 x 2100, Band links,  400 mm bis zum Wandende, keine Querwand -> wird abgelehnt
"""
from pathlib import Path

import numpy as np

import ifcopenshell
import ifcopenshell.api
import ifcopenshell.guid

import tuersteuerung_platzieren as tp

HIER = Path(__file__).parent


def api(m, fn, **kw):
    return ifcopenshell.api.run(fn, m, **kw)


def quader(m, body, dx, dy, dz):
    profil = m.createIfcRectangleProfileDef(
        "AREA", None, m.createIfcAxis2Placement2D(m.createIfcCartesianPoint([dx / 2, dy / 2]), None), dx, dy)
    solid = m.createIfcExtrudedAreaSolid(profil, None, m.createIfcDirection([0.0, 0.0, 1.0]), dz)
    return m.createIfcProductDefinitionShape(None, None, [m.createIfcShapeRepresentation(body, "Body", "SweptSolid", [solid])])


def matrix(x, y, z=0.0, winkel=0.0):
    c, s = np.cos(np.radians(winkel)), np.sin(np.radians(winkel))
    return np.array([[c, -s, 0, x], [s, c, 0, y], [0, 0, 1, z], [0, 0, 0, 1.0]])


def bauen():
    m = api(None, "project.create_file", version="IFC4")
    projekt = api(m, "root.create_entity", ifc_class="IfcProject", name="Testmodell Türsteuerung")
    api(m, "unit.assign_unit", length={"is_metric": True, "raw": "MILLIMETERS"})
    modell = api(m, "context.add_context", context_type="Model")
    body = api(m, "context.add_context", context_type="Model", context_identifier="Body",
               target_view="MODEL_VIEW", parent=modell)
    gelaende = api(m, "root.create_entity", ifc_class="IfcSite", name="Gelände")
    gebaeude = api(m, "root.create_entity", ifc_class="IfcBuilding", name="Testgebäude")
    geschoss = api(m, "root.create_entity", ifc_class="IfcBuildingStorey", name="EG")
    api(m, "aggregate.assign_object", products=[gelaende], relating_object=projekt)
    api(m, "aggregate.assign_object", products=[gebaeude], relating_object=gelaende)
    api(m, "aggregate.assign_object", products=[geschoss], relating_object=gebaeude)

    def setze(element, mat, form):
        api(m, "geometry.edit_object_placement", product=element, matrix=mat.copy(), is_si=False)
        element.Representation = form
        return element

    tuertypen = {}

    def tuer(wand, wandmatrix, dicke, name, x, breite, hoehe, band, ausloeser):
        mat = wandmatrix @ matrix(x, 0)
        oeffnung = setze(api(m, "root.create_entity", ifc_class="IfcOpeningElement", name=f"Öffnung {name}"),
                         mat, quader(m, body, breite, dicke, hoehe))
        m.createIfcRelVoidsElement(ifcopenshell.guid.new(), None, None, None, wand, oeffnung)
        t = api(m, "root.create_entity", ifc_class="IfcDoor", name=name)
        t.OverallWidth, t.OverallHeight = float(breite), float(hoehe)
        # Türblatt 40 mm, bündig zur Bandseite; Ursprung der Tür auf der Wandfläche der Bandgegenseite
        api(m, "geometry.edit_object_placement", product=t, matrix=mat.copy(), is_si=False)
        blatt = quader(m, body, breite, 40.0, hoehe)
        blatt.Representations[0].Items[0].Position = m.createIfcAxis2Placement3D(
            m.createIfcCartesianPoint([0.0, dicke - 40.0, 0.0]), None, None)
        t.Representation = blatt
        m.createIfcRelFillsElement(ifcopenshell.guid.new(), None, None, None, oeffnung, t)
        op = "SINGLE_SWING_LEFT" if band == "links" else "SINGLE_SWING_RIGHT"
        if op not in tuertypen:
            typ = api(m, "root.create_entity", ifc_class="IfcDoorType", name=f"Drehtür {band}", predefined_type="DOOR")
            typ.OperationType = op
            tuertypen[op] = typ
        api(m, "type.assign_type", related_objects=[t], relating_type=tuertypen[op])
        ps = api(m, "pset.add_pset", product=t, name="Tuersteuerung")
        api(m, "pset.edit_pset", pset=ps, properties={"Fluchttuersteuerung": bool(ausloeser)})
        api(m, "spatial.assign_container", products=[t], relating_structure=geschoss)
        return t

    def wand(name, mat, laenge, dicke, hoehe=3000.0):
        w = setze(api(m, "root.create_entity", ifc_class="IfcWall", name=name), mat, quader(m, body, laenge, dicke, hoehe))
        api(m, "spatial.assign_container", products=[w], relating_structure=geschoss)
        return w

    m1 = matrix(0, 0)
    w1 = wand("Wand 1 (240 mm)", m1, 9000.0, 240.0)
    tuer(w1, m1, 240.0, "T01", 600.0, 1000.0, 2100.0, "links", True)
    tuer(w1, m1, 240.0, "T04", 3500.0, 1000.0, 2100.0, "rechts", True)
    tuer(w1, m1, 240.0, "T03", 6000.0, 900.0, 2100.0, "links", False)
    tuer(w1, m1, 240.0, "T05", 7600.0, 1000.0, 2100.0, "links", True)
    # Querwand auf der Bandgegenseite: 325 mm neben der Schlossseite von T04
    wand("Wand 3 (Querwand, 175 mm)", matrix(3000.0, 0.0, 0, -90.0), 4000.0, 175.0)
    m2 = matrix(9000.0, 240.0, 0, 90.0)
    w2 = wand("Wand 2 (175 mm)", m2, 5000.0, 175.0)
    tuer(w2, m2, 175.0, "T02", 2400.0, 1100.0, 2100.0, "rechts", True)
    return m


if __name__ == "__main__":
    basis = HIER / "testmodell.ifc"
    bauen().write(str(basis))
    print(f"Geschrieben: {basis}")
    for v in ("AP", "UP"):
        tp.main([str(basis), "-o", str(HIER / f"testmodell_{v}.ifc"), "--variante", v])
