# -*- coding: utf-8 -*-
"""Revit-Adapter fuer die Datendosen-Platzierung (Dynamo-Python-Knoten).

Liest Raeume, Waende, Tueren/Fenster und Smartboards aus dem Revit-Projekt,
ruft den Platzierungs-Kern ``datendosen_core`` auf und setzt die Familien.

Aufruf aus dem Python-Knoten (siehe ``Datendosen Platzierung.dyn``):

    import datendosen_revit
    OUT = datendosen_revit.ausfuehren(ordner, config_pfad, platzieren)
"""
from __future__ import division, print_function, unicode_literals

import math
import os

import clr

clr.AddReference("RevitAPI")
clr.AddReference("RevitServices")

from Autodesk.Revit.DB import (  # noqa: E402
    BuiltInCategory, BuiltInParameter, ElementId, ElementTransformUtils, FamilyInstance,
    FamilyPlacementType, FamilySymbol, FilteredElementCollector, HostObjectUtils, Level, Line,
    RevitLinkInstance, ShellLayerType, SpatialElementBoundaryOptions, StorageType, Transform,
    Wall, WallFunction, WallKind, XYZ)
from Autodesk.Revit.DB.Structure import StructuralType  # noqa: E402
from RevitServices.Persistence import DocumentManager  # noqa: E402
from RevitServices.Transactions import TransactionManager  # noqa: E402

import datendosen_core as core  # noqa: E402

FUSS = 0.3048


# ---------------------------------------------------------------------------
# Kleine Helfer
# ---------------------------------------------------------------------------
def _id(element_id):
    try:
        return element_id.Value  # Revit 2024+
    except Exception:
        return element_id.IntegerValue


def _text(param):
    if param is None or not param.HasValue:
        return ""
    try:
        wert = param.AsString()
        if wert:
            return wert
        return param.AsValueString() or ""
    except Exception:
        return ""


def _bip_text(element, bip):
    try:
        return _text(element.get_Parameter(bip))
    except Exception:
        return ""


def _bip_laenge(element, bips):
    for bip in bips:
        try:
            p = element.get_Parameter(bip)
            if p is not None and p.HasValue and p.AsDouble() > 1e-6:
                return p.AsDouble() * FUSS
        except Exception:
            pass
    return None


def _typ(doc, element):
    try:
        return doc.GetElement(element.GetTypeId())
    except Exception:
        return None


def _name(doc, element):
    """Familien- und Typname eines Exemplars als ein Text."""
    typ = _typ(doc, element)
    teile = []
    if typ is not None:
        teile.append(_bip_text(typ, BuiltInParameter.SYMBOL_FAMILY_NAME_PARAM))
        teile.append(_bip_text(typ, BuiltInParameter.SYMBOL_NAME_PARAM))
    return " ".join(t for t in teile if t)


def _m(punkt, transform):
    p = transform.OfPoint(punkt)
    return (p.X * FUSS, p.Y * FUSS, p.Z * FUSS)


class Quelle(object):
    """Ein Dokument (Projekt oder Verknuepfung) mit Transformation ins Projekt."""

    def __init__(self, doc, transform, link=None):
        self.doc = doc
        self.transform = transform
        self.link = link
        self.schluessel = "host" if link is None else "link%s" % _id(link.Id)


def _quellen(doc, mit_links):
    quellen = [Quelle(doc, Transform.Identity)]
    if mit_links:
        for inst in FilteredElementCollector(doc).OfClass(RevitLinkInstance):
            ldoc = inst.GetLinkDocument()
            if ldoc is not None:
                quellen.append(Quelle(ldoc, inst.GetTotalTransform(), inst))
    return quellen


# ---------------------------------------------------------------------------
# Modell aus Revit lesen
# ---------------------------------------------------------------------------
def _raeume_sammeln(quellen):
    """Raeume des Projekts, sonst MEP-Raeume, sonst Raeume der Verknuepfungen."""
    for kategorien, auswahl in (
            ([BuiltInCategory.OST_Rooms], quellen[:1]),
            ([BuiltInCategory.OST_MEPSpaces], quellen[:1]),
            ([BuiltInCategory.OST_Rooms], quellen[1:])):
        gefunden = []
        for q in auswahl:
            for kat in kategorien:
                for r in FilteredElementCollector(q.doc).OfCategory(kat).WhereElementIsNotElementType():
                    try:
                        if r.Area > 1e-6:
                            gefunden.append((q, r))
                    except Exception:
                        pass
        if gefunden:
            return gefunden
    return []


def _funktion(raum, cfg):
    for name in cfg["funktion_parameter"]:
        wert = _text(raum.LookupParameter(name)).strip()
        if wert:
            return wert
    return ""


def _wand_info(q, segment, cfg, waende):
    """Liefert (schluessel, fassade, bestueckbar) und merkt sich die Wand."""
    element = q.doc.GetElement(segment.ElementId)
    wq = q
    if isinstance(element, RevitLinkInstance):
        ldoc = element.GetLinkDocument()
        wq = Quelle(ldoc, element.GetTotalTransform(), element)
        element = ldoc.GetElement(segment.LinkElementId) if ldoc is not None else None
    if not isinstance(element, Wall):
        return None, False, False
    schluessel = "%s:%s" % (wq.schluessel, _id(element.Id))
    waende[schluessel] = (wq, element)
    wandtyp = element.WallType
    fassade = False
    vorhang = False
    try:
        fassade = wandtyp.Function == WallFunction.Exterior
        vorhang = wandtyp.Kind == WallKind.Curtain
    except Exception:
        pass
    typname = _bip_text(wandtyp, BuiltInParameter.SYMBOL_NAME_PARAM)
    if core._enthaelt(typname, cfg["fassade_wandtyp_enthaelt"]):
        fassade = True
    bestueckbar = (not vorhang) or cfg["vorhangfassade_bestueckbar"]
    return schluessel, fassade, bestueckbar


def _kontur(q, raum, cfg, waende):
    schleifen = raum.GetBoundarySegments(SpatialElementBoundaryOptions())
    beste = None
    for schleife in schleifen or []:
        kontur = []
        for seg in schleife:
            schluessel, fassade, bestueckbar = _wand_info(q, seg, cfg, waende)
            punkte = [_m(p, q.transform) for p in seg.GetCurve().Tessellate()]
            for a, b in zip(punkte[:-1], punkte[1:]):
                kontur.append({"p0": [a[0], a[1]], "p1": [b[0], b[1]], "fassade": fassade,
                               "bestueckbar": bestueckbar, "wand": schluessel})
        flaeche = abs(core.polygon_flaeche([k["p0"] for k in kontur]))
        if beste is None or flaeche > beste[0]:
            beste = (flaeche, kontur)
    return beste[1] if beste else []


def _oeffnungen(wand_schluessel, waende, raum_id, boden, cfg, cache):
    """Tueren und Fenster der Wand als gesperrte Bereiche (Hoehen ab OKFF des Raums)."""
    if wand_schluessel not in cache:
        wq, wand = waende[wand_schluessel]
        liste = []
        try:
            ids = wand.FindInserts(True, False, False, False)
        except Exception:
            ids = []
        for eid in ids:
            e = wq.doc.GetElement(eid)
            if not isinstance(e, FamilyInstance) or e.Category is None:
                continue
            kat = _id(e.Category.Id)
            if kat == _id(ElementId(BuiltInCategory.OST_Doors)):
                typ = "tuer"
            elif kat == _id(ElementId(BuiltInCategory.OST_Windows)):
                typ = "fenster"
            else:
                continue
            try:
                ort = e.Location.Point
            except Exception:
                continue
            bips = [BuiltInParameter.DOOR_WIDTH, BuiltInParameter.WINDOW_WIDTH,
                    BuiltInParameter.FAMILY_WIDTH_PARAM, BuiltInParameter.GENERIC_WIDTH]
            breite = _bip_laenge(e, bips) or _bip_laenge(_typ(wq.doc, e), bips) or 1.0
            p = _m(ort, wq.transform)
            uk_abs, ok_abs = p[2], p[2] + 2.1
            box = e.get_BoundingBox(None)
            if box is not None:
                uk_abs = _m(box.Min, wq.transform)[2]
                ok_abs = _m(box.Max, wq.transform)[2]
            andere = []
            for attr in ("FromRoom", "ToRoom"):
                try:
                    r = getattr(e, attr)
                    if r is not None:
                        andere.append(r)
                except Exception:
                    pass
            liste.append({"p": [p[0], p[1]], "breite": breite, "uk_abs": uk_abs, "ok_abs": ok_abs,
                          "typ": typ, "wand": wand_schluessel,
                          "kennung": _bip_text(e, BuiltInParameter.ALL_MODEL_MARK),
                          "_raeume": andere})
        cache[wand_schluessel] = liste
    ergebnis = []
    for o in cache[wand_schluessel]:
        eintrag = {k: v for k, v in o.items() if not k.startswith("_")}
        eintrag["uk"] = o["uk_abs"] - boden
        eintrag["ok"] = o["ok_abs"] - boden
        # Tuer zum Flur / oeffentlichen Bereich ist bevorzugter Startpunkt.
        prio = 2
        for r in o["_raeume"]:
            if _id(r.Id) == raum_id:
                continue
            nachbar = {"funktion": _funktion(r, cfg),
                       "name": _bip_text(r, BuiltInParameter.ROOM_NAME)}
            prio = 0 if (core.ist_ausgeschlossen(nachbar, cfg)
                         or core.wlan_klasse(nachbar, cfg) == 0) else 1
        eintrag["prio"] = prio
        ergebnis.append(eintrag)
    return ergebnis


def _smartboards(quellen, cfg):
    sbc = cfg["smartboard"]
    liste = []
    for q in quellen:
        for e in FilteredElementCollector(q.doc).OfClass(FamilyInstance):
            if not core._enthaelt(_name(q.doc, e), sbc["erkennung"]):
                continue
            box = e.get_BoundingBox(None)
            if box is None:
                continue
            a = _m(box.Min, q.transform)
            b = _m(box.Max, q.transform)
            breite = None
            for name in sbc["breite_parameter"]:
                for traeger in (e, _typ(q.doc, e)):
                    try:
                        p = traeger.LookupParameter(name)
                        if p is not None and p.StorageType == StorageType.Double and p.AsDouble() > 1e-6:
                            breite = p.AsDouble() * FUSS
                    except Exception:
                        pass
                    if breite:
                        break
                if breite:
                    break
            if not breite:
                # Duenne Tafel: Diagonale des Grundrisses entspricht etwa der Breite.
                breite = math.hypot(b[0] - a[0], b[1] - a[1])
            liste.append({"p": [(a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0], "breite": breite,
                          "uk_abs": min(a[2], b[2]), "ok_abs": max(a[2], b[2])})
    return liste


def modell_lesen(doc, cfg):
    """Erzeugt das neutrale Modell fuer ``datendosen_core.platziere``."""
    quellen = _quellen(doc, cfg["revit"]["verknuepfte_modelle"])
    waende = {}
    cache = {}
    raeume = []
    for q, r in _raeume_sammeln(quellen):
        ebene = r.Level
        if ebene is None:
            continue
        boden = ebene.ProjectElevation * FUSS + q.transform.Origin.Z * FUSS
        hoehe = None
        try:
            hoehe = r.UnboundedHeight * FUSS
        except Exception:
            pass
        kontur = _kontur(q, r, cfg, waende)
        oeffnungen = []
        for schluessel in sorted(set(k["wand"] for k in kontur if k["wand"])):
            oeffnungen.extend(_oeffnungen(schluessel, waende, _id(r.Id), boden, cfg, cache))
        raeume.append({
            "id": "%s:%s" % (q.schluessel, _id(r.Id)),
            "nummer": _bip_text(r, BuiltInParameter.ROOM_NUMBER),
            "name": _bip_text(r, BuiltInParameter.ROOM_NAME),
            "funktion": _funktion(r, cfg),
            "geschoss": ebene.Name, "geschoss_hoehe": boden,
            "raumhoehe": hoehe if hoehe and hoehe > 0.5 else None,
            "flaeche": r.Area * FUSS * FUSS,
            "kontur": kontur, "oeffnungen": oeffnungen})
    return {"raeume": raeume, "smartboards": _smartboards(quellen, cfg)}, waende


# ---------------------------------------------------------------------------
# Familien setzen
# ---------------------------------------------------------------------------
def _symbol(doc, eintrag):
    familie = (eintrag.get("familie") or "").strip().lower()
    typ = (eintrag.get("typ") or "").strip().lower()
    for s in FilteredElementCollector(doc).OfClass(FamilySymbol):
        if _bip_text(s, BuiltInParameter.SYMBOL_FAMILY_NAME_PARAM).strip().lower() != familie:
            continue
        if typ and _bip_text(s, BuiltInParameter.SYMBOL_NAME_PARAM).strip().lower() != typ:
            continue
        return s
    return None


def _ebene(doc, hoehe_m):
    ebenen = list(FilteredElementCollector(doc).OfClass(Level))
    return min(ebenen, key=lambda e: abs(e.ProjectElevation * FUSS - hoehe_m)) if ebenen else None


def _wandflaeche(wq, wand, punkt):
    """Referenz der Wandseite, die dem Punkt am naechsten liegt."""
    lokal = wq.transform.Inverse.OfPoint(punkt)
    beste = None
    for seite in (ShellLayerType.Interior, ShellLayerType.Exterior):
        for ref in HostObjectUtils.GetSideFaces(wand, seite):
            flaeche = wand.GetGeometryObjectFromReference(ref)
            treffer = flaeche.Project(lokal) if flaeche is not None else None
            if treffer is not None and (beste is None or treffer.Distance < beste[0]):
                beste = (treffer.Distance, ref)
    if beste is None:
        return None
    return beste[1].CreateLinkReference(wq.link) if wq.link is not None else beste[1]


def _setze(element, name, wert):
    if not name:
        return True
    p = element.LookupParameter(name)
    if p is None or p.IsReadOnly:
        return False
    try:
        if p.StorageType == StorageType.Integer:
            p.Set(int(wert))
        elif p.StorageType == StorageType.Double:
            p.Set(float(wert))
        else:
            p.Set(core.text_type(wert))
        return True
    except Exception:
        return False


def _vorherige_loeschen(doc, markierung):
    ids = []
    for e in FilteredElementCollector(doc).OfClass(FamilyInstance):
        if _bip_text(e, BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS).startswith(markierung):
            ids.append(e.Id)
    for eid in ids:
        doc.Delete(eid)
    return len(ids)


def _erzeuge(doc, dose, symbol, waende, drehung):
    boden = dose["z_abs"] - dose["z"]
    ebene = _ebene(doc, boden)
    punkt = XYZ(dose["x"] / FUSS, dose["y"] / FUSS, dose["z_abs"] / FUSS)
    art = symbol.Family.FamilyPlacementType
    wand = waende.get(dose.get("wand"))
    richtung = XYZ(-dose["ny"], dose["nx"], 0.0)  # waagerecht entlang der Wand
    if dose["montage"] == "wand" and wand is not None:
        if art == FamilyPlacementType.WorkPlaneBased:
            ref = _wandflaeche(wand[0], wand[1], punkt)
            if ref is not None:
                return doc.Create.NewFamilyInstance(ref, punkt, richtung, symbol), True
        elif art == FamilyPlacementType.OneLevelBasedHosted and wand[0].link is None:
            return doc.Create.NewFamilyInstance(punkt, symbol, wand[1], ebene,
                                                StructuralType.NonStructural), True
    # Frei auf der Ebene: Hoehe als Versatz, danach zur Raumseite drehen.
    versatz = XYZ(punkt.X, punkt.Y, punkt.Z - ebene.ProjectElevation)
    inst = doc.Create.NewFamilyInstance(versatz, symbol, ebene, StructuralType.NonStructural)
    if dose["montage"] == "wand":
        # Annahme: Vorderseite der Familie zeigt im Grundriss nach -Y.
        winkel = math.atan2(dose["ny"], dose["nx"]) + math.pi / 2.0 + math.radians(drehung)
        achse = Line.CreateBound(punkt, XYZ(punkt.X, punkt.Y, punkt.Z + 1.0))
        ElementTransformUtils.RotateElement(doc, inst.Id, achse, winkel)
    return inst, False


def platzieren(doc, dosen, waende, cfg):
    rc = cfg["revit"]
    meldungen = []
    symbole = {"daten": _symbol(doc, rc["familie_datendose"]), "wlan": _symbol(doc, rc["familie_wlan"])}
    if symbole["daten"] is None:
        return [], ["Familie '%s' nicht geladen - nichts platziert."
                    % rc["familie_datendose"].get("familie")]
    if symbole["wlan"] is None:
        symbole["wlan"] = symbole["daten"]
        meldungen.append("WLAN-Familie nicht gefunden, Datendosen-Familie wird verwendet.")

    TransactionManager.Instance.EnsureInTransaction(doc)
    if rc["vorherige_loeschen"]:
        n = _vorherige_loeschen(doc, rc["markierung"])
        if n:
            meldungen.append("%d zuvor automatisch platzierte Dosen geloescht." % n)
    for s in set(symbole.values()):
        if not s.IsActive:
            s.Activate()
    doc.Regenerate()

    erzeugt = []
    for d in dosen:
        symbol = symbole["wlan"] if d["typ"] == core.TYP_WLAN else symbole["daten"]
        try:
            inst, gehostet = _erzeuge(doc, d, symbol, waende, rc["familien_drehung_grad"])
        except Exception as fehler:
            meldungen.append("%s: Platzierung fehlgeschlagen (%s)." % (d["nummer"], fehler))
            continue
        erzeugt.append((d, inst, gehostet))
    doc.Regenerate()

    fehlende = set()
    param = rc["parameter"]
    for d, inst, gehostet in erzeugt:
        # Hoehe pruefen und korrigieren (Familien werten den Versatz unterschiedlich aus).
        try:
            ist = inst.Location.Point.Z
            soll = d["z_abs"] / FUSS
            if abs(ist - soll) > 0.003:
                ElementTransformUtils.MoveElement(doc, inst.Id, XYZ(0, 0, soll - ist))
        except Exception:
            pass
        kommentar = inst.get_Parameter(BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
        if kommentar is not None and not kommentar.IsReadOnly:
            kommentar.Set("%s | %s | %s" % (rc["markierung"], d["nummer"], d["patch"]))
        if param.get("nummer"):
            if not _setze(inst, param["nummer"], d["nummer"]):
                fehlende.add(param["nummer"])
        else:
            kennzeichen = inst.get_Parameter(BuiltInParameter.ALL_MODEL_MARK)
            if kennzeichen is not None and not kennzeichen.IsReadOnly:
                kennzeichen.Set(d["nummer"])
        for key, wert in (("patchfeld", d["patchfeld"]),
                          ("port", "%02d-%02d" % (d["port_a"], d["port_b"])),
                          ("raumnummer", d["raum_nummer"]), ("dosentyp", d["typ"])):
            if not _setze(inst, param.get(key), wert):
                fehlende.add(param.get(key))
        d["element_id"] = _id(inst.Id)
    TransactionManager.Instance.TransactionTaskDone()
    if fehlende:
        meldungen.append("Parameter nicht vorhanden/schreibbar: %s (Werte stehen im Kommentar)."
                         % ", ".join(sorted(fehlende)))
    return [inst for _, inst, _ in erzeugt], meldungen


# ---------------------------------------------------------------------------
# Einstieg fuer den Dynamo-Knoten
# ---------------------------------------------------------------------------
def ausfuehren(ordner, config_pfad=None, platzieren_aktiv=False):
    """Rueckgabe: [Zusammenfassung, Tabelle, Warnungen, Element-Ids]."""
    doc = DocumentManager.Instance.CurrentDBDocument
    if not config_pfad:
        standard = os.path.join(ordner, "config.json")
        config_pfad = standard if os.path.exists(standard) else None
    cfg = core.config_laden(config_pfad)
    modell, waende = modell_lesen(doc, cfg)
    ergebnis = core.platziere(modell, cfg)
    dosen = ergebnis["dosen"]
    warnungen = list(ergebnis["warnungen"])
    ids = []
    if platzieren_aktiv:
        instanzen, meldungen = platzieren(doc, dosen, waende, cfg)
        warnungen.extend(meldungen)
        ids = [_id(i.Id) for i in instanzen]
    csv_pfad = cfg["revit"]["csv_pfad"]
    if csv_pfad:
        try:
            core.csv_schreiben(csv_pfad, dosen)
        except Exception as fehler:
            warnungen.append("CSV konnte nicht geschrieben werden: %s" % fehler)
    zusammenfassung = "%d Raeume gelesen, %d Dosen berechnet (%d WLAN), %d platziert, %d Hinweise" % (
        len(modell["raeume"]), len(dosen),
        len([d for d in dosen if d["typ"] == core.TYP_WLAN]), len(ids), len(warnungen))
    tabelle = [core.CSV_SPALTEN] + [[d.get(s) for s in core.CSV_SPALTEN] for d in dosen]
    return [zusammenfassung, tabelle, warnungen, ids]
