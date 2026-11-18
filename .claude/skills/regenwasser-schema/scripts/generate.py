#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministischer Generator fuer ein Regenwasseranlagen-Schema (PDF, DIN A3 quer).

Aufruf:
    python3 generate.py KONFIG.json [-o AUSGABE.pdf] [--check]

Gleiche Konfiguration -> byte-identische PDF-Datei (keine Zeitstempel, keine
Zufallswerte, feste Layoutregeln). Einzige Abhaengigkeit: reportlab.

Exit-Codes: 0 = ok, 2 = Konfiguration ungueltig / Versorgervorgaben verletzt.
"""
import argparse
import json
import math
import os
import sys

from reportlab.pdfgen import canvas
from reportlab.lib.units import mm

# --------------------------------------------------------------------------
# Konfiguration: Schema, Defaults, Pruefung
# --------------------------------------------------------------------------

EINLEITSTELLEN = {
    "regenwasserkanal": "öffentl. Regenwasserkanal",
    "mischwasserkanal": "öffentl. Mischwasserkanal",
    "gewaesser": "Gewässer (Vorflut)",
    "versickerung": "Versickerung auf dem Grundstück",
}
DACHTYPEN = {
    "standard": "Standarddach",
    "schwammdach": "Schwammdach",
    "retentionsdach": "Retentionsdach",
}
BEGRUENUNG = {"keine": "ohne Begrünung", "extensiv": "extensiv begrünt",
              "intensiv": "intensiv begrünt"}
BAUARTEN = {"unterirdisch": "unterirdisch", "offen": "offenes Becken"}
MAX_DAECHER = 4
DACHFLAECHE_M2 = 1000   # feste Angabe je Dachflaeche (nicht konfigurierbar)

# key -> (typ, default); typ: str | num | num? | bool | enum-dict | list
SCHEMA = {
    "projekt": {
        "name": ("str", ""), "standort": ("str", ""), "plan_nr": ("str", ""),
        "datum": ("str", ""), "bearbeiter": ("str", ""), "index": ("str", ""),
    },
    "versorger": {
        "name": ("str", ""),
        "einleitstelle": (EINLEITSTELLEN, "regenwasserkanal"),
        "einleitbeschraenkung_l_s": ("num?", None),
        "rueckhaltevolumen_min_m3": ("num?", None),
        "uebergabeschacht_gefordert": ("bool", False),
        "regenwassernutzung_zulaessig": ("bool", True),
        "rueckstauebene": ("str", ""),
        "bemerkung": ("str", ""),
    },
    "rinne": {
        "vorhanden": ("bool", False), "ueberdimensioniert": ("bool", False),
        "bezeichnung": ("str", ""), "volumen_m3": ("num?", None),
    },
    "stauraumkanal": {
        "aktiv": ("bool", False), "dn": ("num?", None), "laenge_m": ("num?", None),
    },
    "rueckhaltebecken": {
        "aktiv": ("bool", False), "volumen_m3": ("num?", None),
        "bauart": (BAUARTEN, "unterirdisch"),
    },
    "drosselschacht": {
        "aktiv": ("bool", False), "drosselabfluss_l_s": ("num?", None),
    },
    "uebergabeschacht": {"aktiv": ("bool", False)},
    "ueberflutung": {
        "befestigte_flaeche_m2": ("num?", 0.0),
        "befestigte_flaeche_cs": ("num?", 1.0),
        "schadlos_ueberflutbar_m3": ("num?", 0.0),
        "wiederkehrzeit_a": ("num?", 30),
    },
    "dachablauf": {
        "abflussvermoegen_l_s": ("num?", 4.5),
        "regenspende_r5_5_l_s_ha": ("num?", None),
    },
    "regenwassernutzung": {
        "aktiv": ("bool", False), "zisterne_m3": ("num?", None),
        "verwendung": ("list", []),
    },
}
DACH_SCHEMA = {
    "name": ("str", ""),
    "typ": (DACHTYPEN, "standard"),
    "begruenung": (BEGRUENUNG, "keine"),
    "pv": ("bool", False),
    "retentionsvolumen_m3": ("num?", None),
    "abflussbeiwert": ("num?", None),
}
# Dachablauf: Nennweite fest DN 100. Standardwerte: Abflussvermoegen 4,5 l/s
# (Mindestwert DIN EN 1253-1 fuer DN 100), Spitzenabflussbeiwerte Cs nach
# DIN 1986-100 Tab. 9 (unbegruent 1,0 / extensiv 0,5 / intensiv 0,2).
DACHABLAUF_DN = 100
CS_STANDARD = {"keine": 1.0, "extensiv": 0.5, "intensiv": 0.2}


def dachablauf_cs(d):
    return d["abflussbeiwert"] if d["abflussbeiwert"] is not None \
        else CS_STANDARD[d["begruenung"]]


def dachablauf(cfg, d):
    """(Cs, max. Flaeche je Ablauf DN 100 in m2, Anzahl Ablaeufe) - fest gerundet."""
    a = cfg["dachablauf"]
    cs = d["abflussbeiwert"] if d["abflussbeiwert"] is not None \
        else CS_STANDARD[d["begruenung"]]
    amax = int(math.floor(a["abflussvermoegen_l_s"] * 10000.0
                          / (a["regenspende_r5_5_l_s_ha"] * cs) + 1e-9))
    return cs, amax, int(math.ceil(DACHFLAECHE_M2 / float(amax) - 1e-9))
TOP_KEYS = set(SCHEMA) | {"daecher", "notueberlauf", "kostra", "geraete_zusatz",
                             "schnittstellen_zusatz"}
KOSTRA_T = (1, 2, 3, 5, 10, 20, 30, 50, 100)      # Wiederkehrzeiten [a]
KOSTRA_D_MAX = 6                                  # max. Dauerstufen in der Tabelle
NACHWEIS_D = (5, 10, 15)                          # Dauerstufen Ueberflutungsnachweis
R55_STANDARD = 300.0


def check_kostra(raw, errors):
    """kostra: {datensatz, rasterfeld, regenspenden_l_s_ha: {"D": {"T": r}}}."""
    out = {"datensatz": "KOSTRA-DWD-2020", "rasterfeld": "", "r": {}}
    if raw is None:
        return out
    if not isinstance(raw, dict):
        errors.append("kostra: Objekt erwartet")
        return out
    for k in sorted(set(raw) - {"datensatz", "rasterfeld", "regenspenden_l_s_ha"}):
        errors.append("kostra.%s: unbekannter Schlüssel" % k)
    for k in ("datensatz", "rasterfeld"):
        if k in raw:
            if isinstance(raw[k], str):
                out[k] = raw[k]
            else:
                errors.append("kostra.%s: Text erwartet" % k)
    tab = raw.get("regenspenden_l_s_ha", {})
    if not isinstance(tab, dict):
        errors.append("kostra.regenspenden_l_s_ha: Objekt {Dauer: {Wiederkehrzeit: r}} erwartet")
        return out
    for dk in sorted(tab, key=str):
        row = tab[dk]
        if not str(dk).isdigit() or int(dk) <= 0 or not isinstance(row, dict):
            errors.append("kostra.regenspenden_l_s_ha.%s: Dauerstufe in Minuten mit "
                          "Objekt {T: r} erwartet" % dk)
            continue
        for tk in sorted(row, key=str):
            if not str(tk).isdigit() or int(tk) not in KOSTRA_T:
                errors.append("kostra.regenspenden_l_s_ha.%s.%s: Wiederkehrzeit muss eine "
                              "von %s sein" % (dk, tk, ", ".join(map(str, KOSTRA_T))))
            elif not _is_num(row[tk]) or row[tk] <= 0:
                errors.append("kostra.regenspenden_l_s_ha.%s.%s: Zahl > 0 erwartet" % (dk, tk))
            else:
                out["r"].setdefault(int(dk), {})[int(tk)] = float(row[tk])
    if len(out["r"]) > KOSTRA_D_MAX:
        errors.append("kostra: höchstens %d Dauerstufen" % KOSTRA_D_MAX)
    # Plausibilitaet: r steigt mit T und faellt mit D
    ds = sorted(out["r"])
    for d in ds:
        ts = sorted(out["r"][d])
        for a, b in zip(ts, ts[1:]):
            if out["r"][d][b] < out["r"][d][a]:
                errors.append("kostra: r(%d,%d) < r(%d,%d) - Tabellenwerte prüfen" % (d, b, d, a))
    for a, b in zip(ds, ds[1:]):
        for t in sorted(set(out["r"][a]) & set(out["r"][b])):
            if out["r"][b][t] > out["r"][a][t]:
                errors.append("kostra: r(%d,%d) > r(%d,%d) - Tabellenwerte prüfen" % (b, t, a, t))
    return out


def ueberflutung(cfg):
    """Ueberflutungsnachweis in Anlehnung an DIN 1986-100 (Gl. 20/21).

    V(D,T) = (r(D,T) * A_ges / 10000 - Q_ab) * D * 60 / 1000   [m3]
    Q_ab = Drosselabfluss, sonst Summe r(D,2) * A_i * Cs_i / 10000.
    """
    u, r = cfg["ueberflutung"], cfg["kostra"]["r"]
    a_dach = DACHFLAECHE_M2 * len(cfg["daecher"])
    a_ges = a_dach + u["befestigte_flaeche_m2"]
    dr = cfg["drosselschacht"]
    rows = []
    for d in NACHWEIS_D:
        rd = r.get(d, {})
        if dr["aktiv"] and dr["drosselabfluss_l_s"]:
            q = dr["drosselabfluss_l_s"]
        elif 2 in rd:
            q = sum(rd[2] * DACHFLAECHE_M2 * dachablauf_cs(x) for x in cfg["daecher"])
            q = (q + rd[2] * u["befestigte_flaeche_m2"] * u["befestigte_flaeche_cs"]) / 10000.0
        else:
            q = None
        vol = {}
        for t in (30, 100):
            if t in rd and q is not None:
                vol[t] = max(0.0, (rd[t] * a_ges / 10000.0 - q) * d * 60.0 / 1000.0)
        rows.append({"d": d, "r": rd, "q": q, "v": vol})
    v_vorh = rueckhaltevolumen(cfg) + u["schadlos_ueberflutbar_m3"]
    res = {}
    for t in (30, 100):
        if all(t in row["v"] for row in rows):
            best = max(rows, key=lambda row: (row["v"][t], -row["d"]))
            res[t] = {"v": best["v"][t], "d": best["d"],
                      "ok": best["v"][t] <= v_vorh + 1e-9}
    return {"a_ges": a_ges, "rows": rows, "v_vorh": v_vorh, "res": res,
            "gedrosselt": bool(dr["aktiv"] and dr["drosselabfluss_l_s"])}



def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _check_section(raw, schema, pfad, errors):
    out = {}
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        errors.append("%s: Objekt erwartet" % pfad)
        raw = {}
    for k in sorted(set(raw) - set(schema)):
        errors.append("%s.%s: unbekannter Schlüssel" % (pfad, k))
    for key in schema:
        typ, default = schema[key]
        v = raw.get(key, default)
        p = "%s.%s" % (pfad, key)
        if isinstance(typ, dict):
            if v not in typ:
                errors.append("%s: '%s' ungültig, erlaubt: %s" % (p, v, ", ".join(typ)))
                v = default
        elif typ == "str":
            if not isinstance(v, str):
                errors.append("%s: Text erwartet" % p)
                v = default
        elif typ == "bool":
            if not isinstance(v, bool):
                errors.append("%s: true/false erwartet" % p)
                v = default
        elif typ == "num?":
            if v is not None and (not _is_num(v) or v < 0):
                errors.append("%s: Zahl >= 0 oder null erwartet" % p)
                v = default
        elif typ == "list":
            if not isinstance(v, list) or not all(isinstance(i, str) for i in v):
                errors.append("%s: Liste von Texten erwartet" % p)
                v = list(default)
        out[key] = v
    return out


def stauraum_volumen(cfg):
    s = cfg["stauraumkanal"]
    if s["aktiv"] and s["dn"] and s["laenge_m"]:
        return math.pi * (s["dn"] / 1000.0) ** 2 / 4.0 * s["laenge_m"]
    return 0.0


def rueckhaltevolumen(cfg):
    """Angerechnetes Rueckhaltevolumen in m3 (Zisterne zaehlt nicht)."""
    v = stauraum_volumen(cfg)
    if cfg["rueckhaltebecken"]["aktiv"]:
        v += cfg["rueckhaltebecken"]["volumen_m3"] or 0.0
    r = cfg["rinne"]
    if r["vorhanden"] and r["ueberdimensioniert"]:
        v += r["volumen_m3"] or 0.0
    for d in cfg["daecher"]:
        if d["typ"] != "standard":
            v += d["retentionsvolumen_m3"] or 0.0
    return v


def hat_rueckhalt(cfg):
    return (cfg["stauraumkanal"]["aktiv"] or cfg["rueckhaltebecken"]["aktiv"]
            or (cfg["rinne"]["vorhanden"] and cfg["rinne"]["ueberdimensioniert"])
            or any(d["typ"] != "standard" for d in cfg["daecher"]))


def normalize(raw):
    """Gibt (cfg, errors, warnings) zurueck. Regeln sind fest und reihenfolgestabil."""
    errors, warnings = [], []
    if not isinstance(raw, dict):
        return None, ["Konfiguration: JSON-Objekt erwartet"], []
    for k in sorted(set(raw) - TOP_KEYS):
        errors.append("%s: unbekannter Schlüssel" % k)
    cfg = {name: _check_section(raw.get(name), SCHEMA[name], name, errors)
           for name in SCHEMA}

    nue = raw.get("notueberlauf", True)
    if not isinstance(nue, bool):
        errors.append("notueberlauf: true/false erwartet")
        nue = True
    cfg["notueberlauf"] = nue
    cfg["geraete_zusatz"] = check_zusatz(raw.get("geraete_zusatz"), "geraete_zusatz",
                                         GERAETE_KEYS, errors)
    cfg["schnittstellen_zusatz"] = check_zusatz(raw.get("schnittstellen_zusatz"),
                                                "schnittstellen_zusatz", SCHNITT_KEYS, errors)

    cfg["kostra"] = check_kostra(raw.get("kostra"), errors)
    da = cfg["dachablauf"]
    if not da["abflussvermoegen_l_s"]:
        errors.append("dachablauf.abflussvermoegen_l_s: Zahl > 0 erwartet")
        da["abflussvermoegen_l_s"] = 4.5
    r55_kostra = cfg["kostra"]["r"].get(5, {}).get(5)
    if da["regenspende_r5_5_l_s_ha"] is None:
        if r55_kostra is not None:
            da["regenspende_r5_5_l_s_ha"] = r55_kostra
        else:
            da["regenspende_r5_5_l_s_ha"] = R55_STANDARD
            warnings.append("Regenspende r(5,5) nicht angegeben - Standardwert 300 l/(s·ha) "
                            "angesetzt; Standortwert (KOSTRA-DWD) einsetzen.")
    elif da["regenspende_r5_5_l_s_ha"] <= 0:
        errors.append("dachablauf.regenspende_r5_5_l_s_ha: Zahl > 0 erwartet")
        da["regenspende_r5_5_l_s_ha"] = R55_STANDARD
    elif r55_kostra is not None and abs(r55_kostra - da["regenspende_r5_5_l_s_ha"]) > 1e-9:
        errors.append("dachablauf.regenspende_r5_5_l_s_ha widerspricht kostra r(5,5) = %s"
                      % de(r55_kostra))
    u = cfg["ueberflutung"]
    if u["wiederkehrzeit_a"] not in (30, 100):
        errors.append("ueberflutung.wiederkehrzeit_a: 30 oder 100 erwartet")
        u["wiederkehrzeit_a"] = 30
    if u["befestigte_flaeche_cs"] is None or not 0 < u["befestigte_flaeche_cs"] <= 1:
        errors.append("ueberflutung.befestigte_flaeche_cs: Wert > 0 und <= 1 erwartet")
        u["befestigte_flaeche_cs"] = 1.0
    for k in ("befestigte_flaeche_m2", "schadlos_ueberflutbar_m3"):
        if u[k] is None:
            u[k] = 0.0
    daecher = raw.get("daecher")
    if not isinstance(daecher, list) or not 1 <= len(daecher) <= MAX_DAECHER:
        errors.append("daecher: Liste mit 1 bis %d Dachflächen erwartet" % MAX_DAECHER)
        daecher = []
    cfg["daecher"] = []
    for i, d in enumerate(daecher):
        dd = _check_section(d, DACH_SCHEMA, "daecher[%d]" % i, errors)
        if not dd["name"]:
            dd["name"] = "Dach %d" % (i + 1)
        if dd["typ"] != "standard" and dd["begruenung"] == "keine":
            errors.append("daecher[%d]: %s erfordert begruenung 'extensiv' oder 'intensiv'"
                          % (i, DACHTYPEN[dd["typ"]]))
        if dd["abflussbeiwert"] is not None and not 0 < dd["abflussbeiwert"] <= 1:
            errors.append("daecher[%d].abflussbeiwert: Wert > 0 und <= 1 erwartet" % i)
            dd["abflussbeiwert"] = None
        if dd["pv"] and dd["begruenung"] == "intensiv":
            warnings.append("%s: PV auf Intensivbegrünung - Verschattung, Aufständerung "
                            "und Pflegewege abstimmen." % dd["name"])
        cfg["daecher"].append(dd)

    v = cfg["versorger"]
    dr = cfg["drosselschacht"]
    if dr["aktiv"] and not dr["drosselabfluss_l_s"]:
        errors.append("drosselschacht.drosselabfluss_l_s: bei aktivem Drosselschacht "
                      "erforderlich (> 0)")
    if cfg["stauraumkanal"]["aktiv"] and not (cfg["stauraumkanal"]["dn"]
                                              and cfg["stauraumkanal"]["laenge_m"]):
        errors.append("stauraumkanal: dn und laenge_m erforderlich")
    if cfg["rueckhaltebecken"]["aktiv"] and not cfg["rueckhaltebecken"]["volumen_m3"]:
        errors.append("rueckhaltebecken.volumen_m3: bei aktivem Becken erforderlich")
    if cfg["regenwassernutzung"]["aktiv"] and not cfg["regenwassernutzung"]["zisterne_m3"]:
        errors.append("regenwassernutzung.zisterne_m3: bei aktiver Nutzung erforderlich")
    if cfg["rinne"]["ueberdimensioniert"] and not cfg["rinne"]["vorhanden"]:
        errors.append("rinne: ueberdimensioniert erfordert vorhanden = true")

    # --- Vorgaben des Versorgers -------------------------------------------
    lim = v["einleitbeschraenkung_l_s"]
    if v["uebergabeschacht_gefordert"] and not cfg["uebergabeschacht"]["aktiv"]:
        errors.append("Versorgervorgabe: Übergabeschacht gefordert, aber nicht aktiv")
    if lim is not None and v["einleitstelle"] != "versickerung":
        if not dr["aktiv"]:
            errors.append("Versorgervorgabe: Einleitbeschränkung %s l/s erfordert "
                          "einen Drosselschacht" % de(lim))
        elif dr["drosselabfluss_l_s"] and dr["drosselabfluss_l_s"] > lim:
            errors.append("Versorgervorgabe: Drosselabfluss %s l/s überschreitet "
                          "Einleitbeschränkung %s l/s" % (de(dr["drosselabfluss_l_s"]), de(lim)))
    vmin = v["rueckhaltevolumen_min_m3"]
    if vmin is not None and not errors:
        vist = rueckhaltevolumen(cfg)
        if vist + 1e-9 < vmin:
            errors.append("Versorgervorgabe: Rückhaltevolumen %s m³ < gefordert %s m³"
                          % (de(vist), de(vmin)))
    if cfg["regenwassernutzung"]["aktiv"] and not v["regenwassernutzung_zulaessig"]:
        errors.append("Versorgervorgabe: Regenwassernutzung nicht zulässig, aber aktiv")

    if dr["aktiv"] and not hat_rueckhalt(cfg):
        warnings.append("Drosselschacht ohne Rückhalteraum (Becken, Stauraumkanal, "
                        "Rinne oder Dach) - Rückstau prüfen.")
    if hat_rueckhalt(cfg) and not dr["aktiv"] and \
            (cfg["stauraumkanal"]["aktiv"] or cfg["rueckhaltebecken"]["aktiv"]):
        warnings.append("Rückhalteraum ohne Drosselschacht - Einstau nicht gesichert.")
    if v["einleitstelle"] == "versickerung" and cfg["uebergabeschacht"]["aktiv"]:
        warnings.append("Übergabeschacht bei Versickerung auf dem Grundstück "
                        "i. d. R. nicht erforderlich.")
    if v["einleitstelle"] == "mischwasserkanal":
        warnings.append("Einleitung in Mischwasserkanal: Rückstausicherung gemäß "
                        "Rückstauebene vorsehen.")
    if not errors:
        n = ueberflutung(cfg)
        t = cfg["ueberflutung"]["wiederkehrzeit_a"]
        if not cfg["kostra"]["r"]:
            warnings.append("KOSTRA-Regenspenden fehlen - Überflutungsnachweis nicht geführt.")
        elif t not in n["res"]:
            warnings.append("Überflutungsnachweis T = %d a unvollständig: r(D,2) und r(D,%d) "
                            "für D = 5, 10, 15 min erforderlich." % (t, t))
        elif not n["res"][t]["ok"]:
            warnings.append("Überflutungsnachweis T = %d a nicht erfüllt: erforderlich %s m³, "
                            "vorhanden %s m³." % (t, de(n["res"][t]["v"]), de(n["v_vorh"])))
    return cfg, errors, warnings


def de(x, nd=1):
    """Zahl deutsch formatiert (Komma), deterministisch."""
    if x is None:
        return "-"
    if float(x) == int(x) and nd <= 1 and abs(x) >= 100:
        return str(int(x))
    return ("%.*f" % (nd, x)).replace(".", ",")


def de_int(x):
    return str(int(round(x))) if x is not None else "-"


# --------------------------------------------------------------------------
# Zeichnen (alle Koordinaten in mm; Ursprung links unten)
# --------------------------------------------------------------------------

W, H = 420.0, 297.0
F, FB = "Helvetica", "Helvetica-Bold"

BLACK = (0, 0, 0)
GREY = (0.45, 0.45, 0.45)
LGREY = (0.80, 0.80, 0.80)
BLUE = (0.08, 0.33, 0.72)
WATER = (0.72, 0.86, 0.97)
SPONGE = (0.82, 0.88, 0.92)
SOIL = (0.96, 0.93, 0.87)
SUBSTRAT = (0.70, 0.56, 0.40)
VEG = (0.20, 0.52, 0.16)
BW = (0.00, 0.55, 0.55)       # Betriebswasser
RED = (0.82, 0.10, 0.10)
PV = (0.10, 0.14, 0.38)
WHITE = (1, 1, 1)

Y_GOK = 150.0      # Gelaendeoberkante
Y_PIPE = 120.0     # Achse Grund-/Sammelleitung
Y_ROOF = 225.0     # OK Rohdecke Dach
BX0, BX1 = 42.0, 182.0     # Gebaeude
CH0, CH1 = 210.0, 366.0    # Bereich der Stationen
X_GRENZE = 374.0
STRIP = 70.0       # OK Fusszeile


class Z(object):
    """Duenne Huelle um den reportlab-Canvas mit mm-Koordinaten."""

    def __init__(self, c):
        self.c = c

    def style(self, stroke=BLACK, fill=None, lw=0.25, dash=None):
        self.c.setStrokeColorRGB(*stroke)
        if fill is not None:
            self.c.setFillColorRGB(*fill)
        self.c.setLineWidth(lw)
        if dash:
            self.c.setDash(list(dash))
        else:
            self.c.setDash()
        self.c.setLineCap(0)
        self.c.setLineJoin(0)

    def line(self, x1, y1, x2, y2, stroke=BLACK, lw=0.25, dash=None):
        self.style(stroke, None, lw, dash)
        self.c.line(x1, y1, x2, y2)

    def poly(self, pts, stroke=BLACK, lw=0.25, dash=None):
        self.style(stroke, None, lw, dash)
        p = self.c.beginPath()
        p.moveTo(*pts[0])
        for q in pts[1:]:
            p.lineTo(*q)
        self.c.drawPath(p, stroke=1, fill=0)

    def area(self, pts, fill, stroke=None, lw=0.25):
        self.style(stroke or BLACK, fill, lw)
        p = self.c.beginPath()
        p.moveTo(*pts[0])
        for q in pts[1:]:
            p.lineTo(*q)
        p.close()
        self.c.drawPath(p, stroke=1 if stroke else 0, fill=1)

    def rect(self, x, y, w, h, stroke=BLACK, fill=None, lw=0.25, dash=None):
        self.style(stroke or BLACK, fill, lw, dash)
        self.c.rect(x, y, w, h, stroke=1 if stroke else 0, fill=1 if fill else 0)

    def circle(self, x, y, r, stroke=BLACK, fill=None, lw=0.25):
        self.style(stroke or BLACK, fill, lw)
        self.c.circle(x, y, r, stroke=1 if stroke else 0, fill=1 if fill else 0)

    def width(self, s, size, font=F):
        return self.c.stringWidth(s, font, size)

    def text(self, x, y, s, size=2.6, font=F, color=BLACK, align="l", maxw=None):
        if maxw is not None:
            s, size = self.fit(s, size, font, maxw)
        self.c.setFillColorRGB(*color)
        self.c.setFont(font, size)
        if align == "c":
            self.c.drawCentredString(x, y, s)
        elif align == "r":
            self.c.drawRightString(x, y, s)
        else:
            self.c.drawString(x, y, s)

    def fit(self, s, size, font, maxw):
        """Schrift bis 65 % verkleinern, danach kuerzen."""
        w = self.width(s, size, font)
        if w <= maxw:
            return s, size
        size2 = max(size * 0.65, size * maxw / w)
        while len(s) > 1 and self.width(s, size2, font) > maxw:
            s = s[:-4] + "..." if len(s) > 4 else s[:-1]
        return s, size2

    def wrap(self, s, size, font, maxw):
        lines, cur = [], ""
        for word in s.split():
            t = (cur + " " + word).strip()
            if self.width(t, size, font) <= maxw or not cur:
                cur = t
            else:
                lines.append(cur)
                cur = word
        if cur:
            lines.append(cur)
        return lines

    def label(self, x, y, lines, size=2.6, align="c", color=BLACK, bold_first=True):
        for i, s in enumerate(lines):
            self.text(x, y - i * size * 1.3, s, size,
                      FB if (i == 0 and bold_first) else F, color, align)


# ---- Symbole -------------------------------------------------------------

def sym_arrow(z, x, y, s=1.6, color=BLUE, direction="r"):
    if direction == "r":
        pts = [(x - s, y + s * 0.6), (x + s, y), (x - s, y - s * 0.6)]
    elif direction == "l":
        pts = [(x + s, y + s * 0.6), (x - s, y), (x + s, y - s * 0.6)]
    elif direction == "d":
        pts = [(x - s * 0.6, y + s), (x, y - s), (x + s * 0.6, y + s)]
    else:
        pts = [(x - s * 0.6, y - s), (x, y + s), (x + s * 0.6, y - s)]
    z.area(pts, color)


def sym_drossel(z, x, y, s=2.0):
    z.area([(x - s, y + s * 0.7), (x, y), (x - s, y - s * 0.7)], RED, BLACK, 0.2)
    z.area([(x + s, y + s * 0.7), (x, y), (x + s, y - s * 0.7)], RED, BLACK, 0.2)


def sym_pumpe(z, x, y, r=1.8):
    z.circle(x, y, r, BLACK, WHITE, 0.25)
    z.area([(x - r * 0.6, y - r * 0.5), (x, y + r * 0.8), (x + r * 0.6, y - r * 0.5)], BW)


def sym_filter(z, x, y, s=2.4):
    z.rect(x - s, y - s, 2 * s, 2 * s, BLACK, WHITE, 0.25)
    for k in (-1, 0, 1):
        z.line(x - s + (k + 1) * s * 0.66, y - s, x - s + (k + 1) * s * 0.66 + s * 0.66,
               y + s, GREY, 0.15)


def sym_tuft(z, x, y, h=1.8):
    for dx in (-0.6, 0.0, 0.6):
        z.line(x, y, x + dx, y + h, VEG, 0.25)


def sym_shrub(z, x, y, h=4.5):
    z.line(x, y, x, y + h * 0.55, SUBSTRAT, 0.3)
    z.circle(x, y + h * 0.7, h * 0.32, VEG, (0.62, 0.82, 0.55), 0.25)


def sym_pv(z, x, y, w=7.0, h=3.6):
    z.line(x + w * 0.15, y, x + w * 0.15, y + h * 0.25, GREY, 0.2)
    z.line(x + w * 0.85, y, x + w * 0.85, y + h * 0.85, GREY, 0.2)
    z.line(x, y + h * 0.1, x + w, y + h, PV, 0.9)


def boxes(z, x0, y0, w, h):
    z.rect(x0, y0, w, h, BLUE, WATER, 0.2)
    n = max(1, int(w // 3.0))
    for i in range(1, n):
        z.line(x0 + i * w / n, y0, x0 + i * w / n, y0 + h, BLUE, 0.15)


def sponge(z, x0, y0, w, h):
    z.rect(x0, y0, w, h, GREY, SPONGE, 0.2)
    nx = max(1, int(w // 1.6))
    for i in range(nx):
        for j in range(3):
            xx = x0 + (i + 0.5 + (0.25 if j % 2 else -0.25)) * w / nx
            z.circle(xx, y0 + (j + 0.5) * h / 3.0, 0.22, None, BLUE)


# ---- Gebaeude und Daecher -------------------------------------------------

def draw_site(z):
    z.rect(20, STRIP + 2, 390, Y_GOK - STRIP - 2, None, SOIL)
    z.line(20, Y_GOK, 410, Y_GOK, BLACK, 0.5)
    for i in range(0, 39):                       # Gelaendeschraffur
        x = 20 + i * 10.0 + 2
        if BX0 - 3 < x < BX1 + 1:
            continue
        z.line(x, Y_GOK, x + 2, Y_GOK - 2, GREY, 0.15)
    z.text(22, Y_GOK + 1.2, "GOK", 2.2, F, GREY)


def draw_building(z, cfg):
    z.rect(BX0, Y_GOK, BX1 - BX0, Y_ROOF - Y_GOK, BLACK, WHITE, 0.5)
    for y in (175.0, 200.0):
        z.line(BX0, y, BX1, y, LGREY, 0.2)
    z.text(BX0 + 3, Y_GOK + 3, "Gebäude", 2.6, F, GREY)
    # Attika
    z.rect(BX0, Y_ROOF, 1.6, 14, BLACK, LGREY, 0.3)
    z.rect(BX1 - 1.6, Y_ROOF, 1.6, 14, BLACK, LGREY, 0.3)
    if cfg["notueberlauf"]:
        y = Y_ROOF + 9.5
        z.line(BX0 + 1.6, y, BX0 - 4, y, RED, 0.5, (1.5, 0.8))
        z.line(BX0 - 4, y, BX0 - 4, y - 6, RED, 0.5, (1.5, 0.8))
        sym_arrow(z, BX0 - 4, y - 7, 1.4, RED, "d")
        z.label(22, y + 6, ["Not-", "überlauf"], 2.2, "l", RED, False)


def draw_roofs(z, cfg):
    n = len(cfg["daecher"])
    x_in0, x_in1 = BX0 + 1.6, BX1 - 1.6
    w = (x_in1 - x_in0) / n
    drains = []
    for i, d in enumerate(cfg["daecher"]):
        x0, x1 = x_in0 + i * w, x_in0 + (i + 1) * w
        cx = (x0 + x1) / 2.0
        y = Y_ROOF
        if d["typ"] == "retentionsdach":
            boxes(z, x0, y, w, 4.5)
            y += 4.5
        elif d["typ"] == "schwammdach":
            sponge(z, x0, y, w, 4.5)
            y += 4.5
        veg_h = 0.0
        if d["begruenung"] == "keine":
            z.rect(x0, y, w, 1.2, GREY, LGREY, 0.15)      # Kies / Schutzlage
            y += 1.2
        else:
            hs = 2.5 if d["begruenung"] == "extensiv" else 5.5
            z.rect(x0, y, w, hs, GREY, SUBSTRAT, 0.15)
            y += hs
            if d["begruenung"] == "extensiv":
                k = max(1, int(w // 2.4))
                for j in range(k):
                    sym_tuft(z, x0 + (j + 0.5) * w / k, y)
                veg_h = 1.8
            else:
                k = max(1, int(w // 4.2))
                for j in range(k):
                    xx = x0 + (j + 0.5) * w / k
                    if j % 2 == 0:
                        sym_shrub(z, xx, y)
                    else:
                        sym_tuft(z, xx, y, 2.4)
                veg_h = 4.6
        z.line(x0, Y_ROOF, x1, Y_ROOF, BLACK, 0.6)        # Abdichtung
        if d["pv"]:
            k = max(1, int((w - 4) // 9.5))
            for j in range(k):
                sym_pv(z, x0 + 2 + j * (w - 4) / k + ((w - 4) / k - 7.0) / 2.0,
                       y + veg_h + 0.8)
        if i > 0:
            z.line(x0, Y_ROOF, x0, y + 2.5, BLACK, 0.4)   # Trennung der Dachflaechen
        # Dachablauf
        z.area([(cx - 1.8, Y_ROOF), (cx + 1.8, Y_ROOF), (cx + 0.5, Y_ROOF - 3),
                (cx - 0.5, Y_ROOF - 3)], BLUE)
        if d["typ"] == "retentionsdach":
            sym_drossel(z, cx, Y_ROOF - 7.5, 1.6)
            z.text(cx + 2.6, Y_ROOF - 8.3, "Ablaufdrossel", 2.0, F, RED,
                   maxw=w / 2.0 - 3.2)
        drains.append(cx)
        # Beschriftung
        lines = [d["name"], DACHTYPEN[d["typ"]], BEGRUENUNG[d["begruenung"]],
                 "mit PV" if d["pv"] else "ohne PV"]
        cs, amax, anz = dachablauf(cfg, d)
        lines.append("A = %s m², Cs = %s" % (de_int(DACHFLAECHE_M2), de(cs)))
        lines.append("%d x Dachablauf DN %d" % (anz, DACHABLAUF_DN))
        lines.append("je max. %d m²" % amax)
        if d["typ"] != "standard" and d["retentionsvolumen_m3"]:
            lines.append("V = %s m³" % de(d["retentionsvolumen_m3"]))
        for j, s in enumerate(lines):
            z.text(cx, 273.0 - j * 3.6, s, 2.6, FB if j == 0 else F, BLACK, "c",
                   maxw=w - 2.0)
    return drains


# ---- Stationen der Entwaesserungskette ------------------------------------

def shaft_cover(z, x0, x1):
    z.line(x0 - 0.8, Y_GOK, x1 + 0.8, Y_GOK, BLACK, 1.0)


def st_zisterne(z, cfg, x0, w):
    n = cfg["regenwassernutzung"]
    sym_filter(z, x0 + 2.4, Y_PIPE)
    tx0, tx1 = x0 + 7.0, x0 + w
    z.rect(tx0, 96, tx1 - tx0, 30, BLACK, WHITE, 0.4)
    z.rect(tx0 + 0.2, 96.2, tx1 - tx0 - 0.4, 22.3, None, WATER)
    z.line(tx0, 118.5, tx1, 118.5, BLUE, 0.2)
    z.line(x0 + 4.8, Y_PIPE, tx0, Y_PIPE, BLUE, 1.0)
    dcx = (tx0 + tx1) / 2.0
    z.rect(dcx - 2.5, 126, 5.0, Y_GOK - 126, BLACK, WHITE, 0.3)
    shaft_cover(z, dcx - 2.5, dcx + 2.5)
    # Betriebswasser: Pumpe -> Dom -> Gebaeude
    xb = BX1 - 11.0
    z.poly([(dcx, 101.8), (dcx, 141.0), (xb, 141.0), (xb, Y_GOK + 3.0)], BW, 0.6,
           (2.0, 1.0))
    sym_pumpe(z, dcx, 100.0)
    z.text(tx1 - 1.0, Y_PIPE + 2.6, "Überlauf", 2.0, F, BLUE, "r")
    z.label(x0 + w / 2.0, 91.0, ["Zisterne V = %s m³" % de(n["zisterne_m3"]),
                                 "mit Filter, Pumpe", "und Überlauf"])
    # Zentrale im Gebaeude
    z.rect(xb - 7.0, Y_GOK + 3.0, 14.0, 13.0, BW, WHITE, 0.4)
    z.label(xb, Y_GOK + 11.5, ["Betriebs-", "wasser-", "zentrale"], 2.1, "c", BW, False)
    z.poly([(xb, Y_GOK + 16.0), (xb, 205.0), (BX1 + 5.0, 205.0)], BW, 0.6, (2.0, 1.0))
    sym_arrow(z, BX1 + 6.0, 205.0, 1.4, BW, "r")
    lines = ["Regenwassernutzung", "Betriebswasser für:"]
    verw = n["verwendung"] or ["Bewässerung"]
    lines += ["- " + s for s in verw[:5]]
    lines += ["Trinkwassernachspeisung nur über", "freien Auslauf (DIN EN 1717, Typ AA)"]
    bx, by, bh = BX1 + 8.0, 207.5, len(lines) * 3.4 + 2.5
    z.rect(bx, by - bh, 52.0, bh, BW, WHITE, 0.3)
    for j, s in enumerate(lines):
        z.text(bx + 1.5, by - 3.4 - j * 3.4, s, 2.4, FB if j == 0 else F, BLACK,
               maxw=49.0)
    return x0 + w


def st_stauraum(z, cfg, x0, w):
    s = cfg["stauraumkanal"]
    yb, yt = Y_PIPE - 1.4, Y_PIPE + 9.0
    pts = [(x0, Y_PIPE - 0.5), (x0 + 3, yb), (x0 + w - 3, yb), (x0 + w, Y_PIPE - 0.5),
           (x0 + w, Y_PIPE + 0.5), (x0 + w - 3, yt), (x0 + 3, yt), (x0, Y_PIPE + 0.5)]
    z.area(pts, WHITE, BLUE, 0.5)
    z.rect(x0 + 3, yb + 0.25, w - 6, 6.6, None, WATER)
    z.line(x0 + 3, yb + 6.85, x0 + w - 3, yb + 6.85, BLUE, 0.2)
    z.label(x0 + w / 2.0, 113.5, ["Stauraumkanal DN %s" % de_int(s["dn"]),
                                  "L = %s m, V = %s m³" % (de(s["laenge_m"]),
                                                           de(stauraum_volumen(cfg))),
                                  "(überdimensionierte Leitung)"])
    return x0 + w


def st_rrb(z, cfg, x0, w):
    r = cfg["rueckhaltebecken"]
    yb = 117.0
    if r["bauart"] == "offen":
        yw, sl = 141.0, 7.0
        f = (yw - yb) / (Y_GOK - yb)
        z.area([(x0, Y_GOK), (x0 + sl, yb), (x0 + w - sl, yb), (x0 + w, Y_GOK)], WHITE)
        z.area([(x0 + sl * (1 - f), yw), (x0 + sl, yb), (x0 + w - sl, yb),
                (x0 + w - sl * (1 - f), yw)], WATER)
        z.poly([(x0, Y_GOK), (x0 + sl, yb), (x0 + w - sl, yb), (x0 + w, Y_GOK)],
               BLACK, 0.5)
        z.line(x0 + sl * (1 - f), yw, x0 + w - sl * (1 - f), yw, BLUE, 0.2)
        z.line(x0 - 1, Y_GOK, x0 + w + 1, Y_GOK, SOIL, 0.9)
        # Zu- und Ablauf bis zur Boeschung
        d = sl * (Y_GOK - Y_PIPE) / (Y_GOK - yb)
        z.line(x0, Y_PIPE, x0 + d, Y_PIPE, BLUE, 1.0)
        z.line(x0 + w - d, Y_PIPE, x0 + w, Y_PIPE, BLUE, 1.0)
    else:
        yw = 136.0
        z.rect(x0, yb, w, 23.0, BLACK, WHITE, 0.4)
        z.rect(x0 + 0.2, yb + 0.2, w - 0.4, yw - yb - 0.2, None, WATER)
        z.line(x0, yw, x0 + w, yw, BLUE, 0.2)
        cx = x0 + w / 2.0
        z.rect(cx - 2.5, 140, 5.0, Y_GOK - 140, BLACK, WHITE, 0.3)
        shaft_cover(z, cx - 2.5, cx + 2.5)
    xm = x0 + w * 0.72
    z.area([(xm - 1.2, yw + 2.0), (xm + 1.2, yw + 2.0), (xm, yw)], BLUE)
    z.text(xm - 1.8, yw + 0.6, "Stauziel", 2.0, F, BLUE, "r")
    z.label(x0 + w / 2.0, 112.0, ["Regenrückhaltebecken",
                                  "V = %s m³" % de(r["volumen_m3"]),
                                  "(%s)" % BAUARTEN[r["bauart"]]])
    return x0 + w


def st_drossel(z, cfg, x0, w):
    z.rect(x0, 113, w, Y_GOK - 113, BLACK, WHITE, 0.4)
    shaft_cover(z, x0 + 2.5, x0 + w - 2.5)
    z.line(x0, Y_PIPE, x0 + w, Y_PIPE, BLUE, 1.0)
    sym_drossel(z, x0 + w / 2.0, Y_PIPE, 2.4)
    z.label(x0 + w / 2.0, Y_GOK + 8.0,
            ["Drosselschacht",
             "Qdr = %s l/s" % de(cfg["drosselschacht"]["drosselabfluss_l_s"])])
    z.line(x0 + w / 2.0, Y_GOK + 1.0, x0 + w / 2.0, Y_GOK + 3.6, GREY, 0.15)
    return x0 + w


def st_uebergabe(z, cfg, x0, w):
    z.rect(x0, 113, w, Y_GOK - 113, BLACK, WHITE, 0.4)
    shaft_cover(z, x0 + 2.5, x0 + w - 2.5)
    z.line(x0, Y_PIPE, x0 + w, Y_PIPE, BLUE, 1.0)
    z.rect(x0 + 1.5, Y_PIPE - 1.6, w - 3.0, 1.0, None, WATER)
    z.label(x0 + w / 2.0, 108.0, ["Übergabeschacht", "(Kontrolle /", "Probenahme)"])
    return x0 + w


def draw_rinne(z, cfg):
    r = cfg["rinne"]
    x0, x1 = 187.0, 203.0
    depth = 6.0 if r["ueberdimensioniert"] else 2.2
    z.rect(x0, Y_GOK - depth, x1 - x0, depth, BLACK, WHITE, 0.4)
    if r["ueberdimensioniert"]:
        z.rect(x0 + 0.2, Y_GOK - depth + 0.2, x1 - x0 - 0.4, depth - 1.6, None, WATER)
    z.line(x0, Y_GOK, x1, Y_GOK, BLACK, 0.9, (1.0, 0.6))          # Rost
    cx = (x0 + x1) / 2.0
    z.line(cx, Y_GOK - depth, cx, Y_PIPE, BLUE, 1.0)
    z.circle(cx, Y_PIPE, 0.9, None, BLUE)
    lines = ["überdimensionierte Rinne", "(Rückhalt / Einstau)"] if r["ueberdimensioniert"] \
        else ["Entwässerungsrinne"]
    if r["bezeichnung"]:
        lines.append(r["bezeichnung"])
    if r["ueberdimensioniert"] and r["volumen_m3"]:
        lines.append("V = %s m³" % de(r["volumen_m3"]))
    for j, s in enumerate(reversed(lines)):
        z.text(x0 - 1.0, Y_GOK + 2.5 + j * 3.4, s, 2.6,
               FB if j == len(lines) - 1 else F, BLACK, maxw=44.0)


def draw_endpoint(z, cfg, x_from):
    e = cfg["versorger"]["einleitstelle"]
    if e == "versickerung":
        z.line(x_from, Y_PIPE, 380.0, Y_PIPE, BLUE, 1.0)
        z.rect(380.0, 108.0, 26.0, 16.0, BLACK, WHITE, 0.4, (1.5, 1.0))
        for i in range(8):
            for j in range(4):
                z.circle(381.8 + i * 3.2 + (1.6 if j % 2 else 0) * 0.5, 110.0 + j * 4.0,
                         0.7, GREY, None, 0.15)
        for i in range(3):
            sym_arrow(z, 386.0 + i * 7.0, 104.5, 1.4, BLUE, "d")
        z.label(393.0, 98.0, ["Versickerung", "(Rigole)"])
        return
    z.line(X_GRENZE, 84.0, X_GRENZE, 190.0, BLACK, 0.35, (5.0, 1.2, 0.8, 1.2))
    z.text(X_GRENZE - 1.2, 187.0, "Grundstücksgrenze", 2.4, F, BLACK, "r")
    if e == "gewaesser":
        z.line(x_from, Y_PIPE, 387.0, Y_PIPE, BLUE, 1.0)
        z.area([(383.0, Y_GOK), (389.0, 112.0), (401.0, 112.0), (407.0, Y_GOK)], WHITE)
        z.area([(387.5, 122.0), (389.0, 112.0), (401.0, 112.0), (402.5, 122.0)], WATER)
        z.poly([(383.0, Y_GOK), (389.0, 112.0), (401.0, 112.0), (407.0, Y_GOK)],
               BLACK, 0.5)
        z.line(387.5, 122.0, 402.5, 122.0, BLUE, 0.2)
        z.line(382.5, Y_GOK, 407.5, Y_GOK, SOIL, 0.9)
        z.label(395.0, 107.0, ["Gewässer", "(Vorflut)"])
    else:
        z.line(x_from, Y_PIPE, 389.2, Y_PIPE, BLUE, 1.0)
        z.circle(394.5, 118.0, 5.6, BLUE, WHITE, 0.6)
        z.rect(390.6, 113.6, 7.8, 2.6, None, WATER)
        z.label(394.5, 108.0, ["öffentlicher",
                               "Regenwasserkanal" if e == "regenwasserkanal"
                               else "Mischwasserkanal"])


def draw_chain(z, cfg, drains):
    stations = []
    if cfg["regenwassernutzung"]["aktiv"]:
        stations.append((st_zisterne, 28.0))
    if cfg["stauraumkanal"]["aktiv"]:
        stations.append((st_stauraum, 32.0))
    if cfg["rueckhaltebecken"]["aktiv"]:
        stations.append((st_rrb, 32.0))
    if cfg["drosselschacht"]["aktiv"]:
        stations.append((st_drossel, 12.0))
    if cfg["uebergabeschacht"]["aktiv"]:
        stations.append((st_uebergabe, 12.0))
    gap = (CH1 - CH0 - sum(w for _, w in stations)) / (len(stations) + 1.0)

    # Fallleitungen und Sammelleitung
    for cx in drains:
        z.line(cx, Y_ROOF - 3, cx, Y_PIPE, BLUE, 1.0)
        z.circle(cx, Y_PIPE, 0.9, None, BLUE)
    z.text(drains[0] + 1.5, Y_GOK - 8.0, "Fallleitung", 2.2, F, BLUE)
    z.text(BX0 + 3, Y_PIPE - 4.0, "Grund- / Sammelleitung", 2.2, F, BLUE)
    if cfg["rinne"]["vorhanden"]:
        draw_rinne(z, cfg)

    x = drains[0]
    arrows = []
    for idx, (fn, w) in enumerate(stations):
        x0 = (CH0 if idx == 0 else x) + gap
        z.line(x, Y_PIPE, x0, Y_PIPE, BLUE, 1.0)
        arrows.append((max(x, 203.0) + x0) / 2.0)
        x = fn(z, cfg, x0, w)
    arrows.append((x + X_GRENZE) / 2.0 if stations else 290.0)
    draw_endpoint(z, cfg, x)
    for ax in arrows:
        sym_arrow(z, ax, Y_PIPE, 1.7, BLUE, "r")


# ---- Fusszeile: Legende, Vorgaben, Hinweise, Schriftfeld ------------------

def draw_legend(z):
    x0, x1 = 20.0, 140.0
    z.rect(x0, 10, x1 - x0, STRIP - 10, BLACK, WHITE, 0.35)
    z.text(x0 + 2, 65.0, "Legende", 3.0, FB)
    items = [
        ("Regenwasserleitung", lambda x, y: z.line(x, y, x + 10, y, BLUE, 1.0)),
        ("Betriebswasser (Regenwassernutzung)",
         lambda x, y: z.line(x, y, x + 10, y, BW, 0.6, (2.0, 1.0))),
        ("Notüberlauf", lambda x, y: z.line(x, y, x + 10, y, RED, 0.5, (1.5, 0.8))),
        ("Grundstücksgrenze",
         lambda x, y: z.line(x, y, x + 10, y, BLACK, 0.35, (5.0, 1.2, 0.8, 1.2))),
        ("Rückhalte- / Stauraum", lambda x, y: z.rect(x, y - 1.5, 10, 3, BLUE, WATER, 0.2)),
        ("Fließrichtung", lambda x, y: sym_arrow(z, x + 5, y, 1.7)),
        ("Drossel", lambda x, y: sym_drossel(z, x + 5, y, 2.0)),
        ("Pumpe", lambda x, y: sym_pumpe(z, x + 5, y, 1.8)),
        ("Filter", lambda x, y: sym_filter(z, x + 5, y, 1.8)),
        ("Retentionsboxen (Retentionsdach)", lambda x, y: boxes(z, x, y - 1.5, 10, 3)),
        ("Speicherschicht (Schwammdach)", lambda x, y: sponge(z, x, y - 1.5, 10, 3)),
        ("Begrünung extensiv",
         lambda x, y: [sym_tuft(z, x + 2 + k * 3, y - 1.0) for k in range(3)]),
        ("Begrünung intensiv",
         lambda x, y: (sym_shrub(z, x + 3, y - 1.8, 4.0), sym_tuft(z, x + 7, y - 1.8, 2.2))),
        ("PV-Modul", lambda x, y: sym_pv(z, x + 1.5, y - 1.8)),
    ]
    for i, (name, fn) in enumerate(items):
        col, row = divmod(i, 7)
        x = x0 + 3 + col * 60.0
        y = 58.0 - row * 7.0
        fn(x, y)
        z.text(x + 13, y - 0.9, name, 2.4, F, BLACK, maxw=43.0)


def draw_vorgaben(z, cfg):
    x0, x1 = 140.0, 215.0
    v = cfg["versorger"]
    z.rect(x0, 10, x1 - x0, STRIP - 10, BLACK, WHITE, 0.35)
    z.text(x0 + 2, 65.0, "Vorgaben Versorger / Anlagenkenndaten", 3.0, FB)
    n = cfg["regenwassernutzung"]
    dr = cfg["drosselschacht"]
    rows = [
        ("Versorger", v["name"] or "-"),
        ("Einleitstelle", EINLEITSTELLEN[v["einleitstelle"]]),
        ("Einleitbeschränkung", "%s l/s" % de(v["einleitbeschraenkung_l_s"])
         if v["einleitbeschraenkung_l_s"] is not None else "keine"),
        ("Rückhaltevolumen min.", "%s m³" % de(v["rueckhaltevolumen_min_m3"])
         if v["rueckhaltevolumen_min_m3"] is not None else "keine Vorgabe"),
        ("Übergabeschacht", "gefordert" if v["uebergabeschacht_gefordert"]
         else "nicht gefordert"),
        ("Rückstauebene", v["rueckstauebene"] or "-"),
        ("Dachfläche gesamt", "%s m²" % de_int(DACHFLAECHE_M2 * len(cfg["daecher"]))),
        ("Dachablauf DN %d" % DACHABLAUF_DN, "%s l/s bei r(5,5) = %s l/(s·ha)" % (
            de(cfg["dachablauf"]["abflussvermoegen_l_s"]),
            de_int(cfg["dachablauf"]["regenspende_r5_5_l_s_ha"]))),
        ("Rückhaltevolumen vorh.", "%s m³" % de(rueckhaltevolumen(cfg))),
        ("Drosselabfluss", "%s l/s" % de(dr["drosselabfluss_l_s"]) if dr["aktiv"]
         else "ungedrosselt"),
        ("Regenwassernutzung", "Zisterne %s m³" % de(n["zisterne_m3"]) if n["aktiv"]
         else "keine"),
    ]
    if v["bemerkung"]:
        rows.append(("Bemerkung", v["bemerkung"]))
    for i, (k, val) in enumerate(rows):
        y = 59.5 - i * 4.1
        if i == 6:
            z.line(x0 + 2, y + 3.3, x1 - 2, y + 3.3, LGREY, 0.2)
        z.text(x0 + 2, y, k + ":", 2.5, F, GREY)
        z.text(x0 + 37, y, val, 2.5, F, BLACK, maxw=x1 - x0 - 39)


def draw_hinweise(z, warnings):
    x0, x1 = 215.0, 290.0
    z.rect(x0, 10, x1 - x0, STRIP - 10, BLACK, WHITE, 0.35)
    z.text(x0 + 2, 65.0, "Hinweise", 3.0, FB)
    notes = list(warnings) + [
        "Prinzipschema ohne Maßstab; ersetzt keine hydraulische Bemessung "
        "(DIN 1986-100, DIN EN 12056-3, DWA-A 117 / DWA-A 138).",
        "Überflutungsnachweis: Überschlag in Anlehnung an DIN 1986-100 mit den "
        "eingetragenen KOSTRA-Werten; durch Fachplaner zu prüfen.",
        "Vorgaben des Versorgers / der Entwässerungssatzung sind vor Ausführung "
        "zu bestätigen.",
    ]
    y, size = 60.0, 2.4
    for k, note in enumerate(notes):
        lines = z.wrap(note, size, F, x1 - x0 - 8)
        if y - (len(lines) - 1) * 3.2 < 12.0:
            z.text(x0 + 2, y, "(weitere Hinweise siehe Konsolenausgabe)", size, F, GREY)
            break
        z.text(x0 + 2, y, "%d." % (k + 1), size, FB, RED if k < len(warnings) else BLACK)
        for s in lines:
            z.text(x0 + 6, y, s, size, F, RED if k < len(warnings) else BLACK)
            y -= 3.2
        y -= 1.2


def draw_titleblock(z, cfg):
    x0, x1 = 290.0, 410.0
    p = cfg["projekt"]
    z.rect(x0, 10, x1 - x0, STRIP - 10, BLACK, WHITE, 0.5)
    for y in (52.0, 38.0, 24.0):
        z.line(x0, y, x1, y, BLACK, 0.25)
    z.line(x0 + 60, 10, x0 + 60, 38, BLACK, 0.25)

    def cell(x, y, k, val, w, size=3.2, bold=False):
        z.text(x + 1.5, y + 10.2, k, 1.9, F, GREY)
        z.text(x + 1.5, y + 3.2, val or "-", size, FB if bold else F, BLACK, maxw=w - 3)

    cell(x0, 52, "Projekt", p["name"], 120, 4.6, True)
    cell(x0, 38, "Planinhalt", "Regenwasseranlage - Prinzipschema", 120, 4.0, True)
    cell(x0, 24, "Standort", p["standort"], 60)
    cell(x0 + 60, 24, "Versorger / Entwässerungsbetrieb", cfg["versorger"]["name"], 60)
    cell(x0, 10, "Plan-Nr. / Index", (p["plan_nr"] + (" / " + p["index"] if p["index"] else "")),
         60)
    z.line(x0 + 90, 10, x0 + 90, 24, BLACK, 0.25)
    cell(x0 + 60, 10, "Datum", p["datum"], 30)
    cell(x0 + 90, 10, "Bearbeiter / Maßstab", (p["bearbeiter"] + "  o. M.").strip(), 30)


GREEN_OK = (0.05, 0.50, 0.15)


def draw_nachweis(z, cfg):
    """Regenspendentabelle (KOSTRA) und Ueberflutungsnachweis, oben rechts."""
    k, n, u = cfg["kostra"], ueberflutung(cfg), cfg["ueberflutung"]
    x0, x1, y = 246.0, 408.0, 279.5
    w = x1 - x0
    titel = "Regenspenden r(D,T) in l/(s·ha) - %s" % k["datensatz"]
    if k["rasterfeld"]:
        titel += ", Rasterfeld %s" % k["rasterfeld"]
    z.text(x0, y, titel, 2.8, FB, BLACK, maxw=w)
    rh, c0 = 4.2, 26.0
    if not k["r"]:
        z.text(x0, y - 5.0, "Keine KOSTRA-Werte angegeben - Tabelle und Überflutungsnachweis "
               "nicht geführt.", 2.5, F, RED, maxw=w)
        return

    def table(ytop, head, rows, bold_cols=()):
        cw = (w - c0) / (len(head) - 1)
        z.rect(x0, ytop - rh, w, rh, None, (0.90, 0.92, 0.95))
        for i, row in enumerate([head] + rows):
            yb = ytop - (i + 1) * rh
            for j, cell in enumerate(row):
                cx = x0 + c0 / 2.0 if j == 0 else x0 + c0 + (j - 0.5) * cw
                z.text(cx, yb + 1.2, cell, 2.4, FB if (i == 0 or j == 0 or j in bold_cols)
                       else F, BLACK, "c", maxw=(c0 if j == 0 else cw) - 1.0)
            z.line(x0, yb, x1, yb, LGREY, 0.15)
        z.rect(x0, ytop - (len(rows) + 1) * rh, w, (len(rows) + 1) * rh, BLACK, None, 0.3)
        z.line(x0 + c0, ytop, x0 + c0, ytop - (len(rows) + 1) * rh, BLACK, 0.2)
        return ytop - (len(rows) + 1) * rh

    head = ["D [min] \\ T [a]"] + [str(t) for t in KOSTRA_T]
    rows = [[str(d)] + [de(k["r"][d][t]) if t in k["r"][d] else "-" for t in KOSTRA_T]
            for d in sorted(k["r"])]
    y = table(y - 2.0, head, rows) - 6.5

    z.text(x0, y, "Überflutungsnachweis (in Anlehnung an DIN 1986-100)", 2.8, FB)
    y -= 4.0
    z.text(x0, y, "V = (r(D,T) · A_ges / 10000 - Q_ab) · D · 60 / 1000;  A_ges = %s m²;  Q_ab = %s"
           % (de_int(n["a_ges"]), "Drosselabfluss" if n["gedrosselt"]
              else "Summe r(D,2) · A · Cs / 10000"), 2.4, F, BLACK, maxw=w)
    head = ["D [min]", "r(D,2)", "Q_ab [l/s]", "r(D,30)", "V30 [m³]", "r(D,100)", "V100 [m³]"]
    rows = []
    for row in n["rows"]:
        rd = row["r"]
        rows.append([str(row["d"]), de(rd.get(2)), de(row["q"]), de(rd.get(30)),
                     de(row["v"].get(30)), de(rd.get(100)), de(row["v"].get(100))])
    y = table(y - 2.0, head, rows, (4, 6)) - 4.5
    z.text(x0, y, "V_vorh = %s m³ (Rückhalteräume %s m³ + schadlos überflutbare Fläche %s m³)"
           % (de(n["v_vorh"]), de(rueckhaltevolumen(cfg)), de(u["schadlos_ueberflutbar_m3"])),
           2.4, F, BLACK, maxw=w)
    for t in (30, 100):
        y -= 3.8
        massg = " (maßgebend)" if t == u["wiederkehrzeit_a"] else ""
        if t not in n["res"]:
            z.text(x0, y, "T = %d a%s: nicht geführt - KOSTRA-Werte unvollständig" % (t, massg),
                   2.5, FB, GREY, maxw=w)
            continue
        e = n["res"][t]
        z.text(x0, y, "T = %d a%s: V_erf = %s m³ (D = %d min) %s V_vorh = %s m³ - %s"
               % (t, massg, de(e["v"]), e["d"], "<=" if e["ok"] else ">", de(n["v_vorh"]),
                  "erfüllt" if e["ok"] else "NICHT erfüllt"),
               2.5, FB, GREEN_OK if e["ok"] else RED, maxw=w)


def draw_ueberflutungsflaeche(z, cfg):
    v = cfg["ueberflutung"]["schadlos_ueberflutbar_m3"]
    if not v:
        return
    xr = 406.0 if cfg["versorger"]["einleitstelle"] == "versickerung" else X_GRENZE - 2.0
    z.rect(206.0, Y_GOK + 0.3, xr - 206.0, 1.6, None, WATER)
    z.line(206.0, Y_GOK + 1.9, xr, Y_GOK + 1.9, BLUE, 0.2, (1.2, 0.8))
    z.text(xr, Y_GOK + 13.5, "Überflutungsfläche (schadlos), V = %s m³" % de(v), 2.4, F,
           BLUE, "r")


# --------------------------------------------------------------------------
# Seite 2 ff.: Geraetetabelle und Schnittstellenliste (DIN 276)
# --------------------------------------------------------------------------

GERAETE_KEYS = ("bezeichnung", "anzahl", "kenndaten", "kg", "bemerkung")
SCHNITT_KEYS = ("schnittstelle", "kg", "gegengewerk", "koordination", "anschluss",
                "inbetriebnahme")


def check_zusatz(raw, name, keys, errors):
    out = []
    if raw is None:
        return out
    if not isinstance(raw, list):
        errors.append("%s: Liste erwartet" % name)
        return out
    for i, item in enumerate(raw):
        if not isinstance(item, dict) or set(item) - set(keys) or \
                not all(isinstance(v, str) for v in item.values()):
            errors.append("%s[%d]: Objekt mit Textfeldern %s erwartet"
                          % (name, i, ", ".join(keys)))
            continue
        out.append([item.get(k, "") for k in keys])
    return out


def geraete_rows(cfg):
    """Feste Reihenfolge: Dach -> Gebaeude -> Grundstueck. Spalten wie GERAETE_KEYS."""
    rows = []
    a = cfg["dachablauf"]
    for d in cfg["daecher"]:
        cs, amax, anz = dachablauf(cfg, d)
        rows.append(["Dachablauf DN %d - %s" % (DACHABLAUF_DN, d["name"]), str(anz),
                     "Q = %s l/s je Ablauf; max. %d m² je Ablauf; Cs = %s; r(5,5) = %s l/(s·ha)"
                     % (de(a["abflussvermoegen_l_s"]), amax, de(cs),
                        de_int(a["regenspende_r5_5_l_s_ha"])),
                     "411", "mit Ablaufdrossel (Retentionsdach)"
                     if d["typ"] == "retentionsdach" else ""])
        if d["typ"] != "standard":
            rows.append(["%s - %s" % ("Retentionsdach-Aufbau (Retentionsboxen)"
                                      if d["typ"] == "retentionsdach"
                                      else "Schwammdach-Aufbau (Speicherschicht)", d["name"]),
                         "1", "A = %d m²; %s%s" % (
                             DACHFLAECHE_M2, BEGRUENUNG[d["begruenung"]],
                             "; V = %s m³" % de(d["retentionsvolumen_m3"])
                             if d["retentionsvolumen_m3"] else ""),
                         "363", "Leistung Dachabdichtung / Dachbegrünung"])
    rows.append(["Fallleitung Regenwasser", str(len(cfg["daecher"])),
                 "je Dachfläche ein Strang (Schema); Dimension nach Bemessung", "411", ""])
    if cfg["notueberlauf"]:
        rows.append(["Notüberlauf über Attika", "n. Bem.",
                     "freier Auslauf auf schadlos überflutbare Fläche", "411", ""])
    r = cfg["rinne"]
    if r["vorhanden"]:
        rows.append(["Entwässerungsrinne" + (", überdimensioniert (Rückhalt)"
                                             if r["ueberdimensioniert"] else ""), "1",
                     "; ".join(x for x in [r["bezeichnung"],
                                           "V = %s m³" % de(r["volumen_m3"])
                                           if r["ueberdimensioniert"] and r["volumen_m3"]
                                           else ""] if x) or "-", "551", ""])
    n = cfg["regenwassernutzung"]
    if n["aktiv"]:
        rows.append(["Regenwasserfilter", "1", "vor Zisterne", "552", ""])
        rows.append(["Zisterne", "1", "V = %s m³; mit Überlauf zur Entwässerung"
                     % de(n["zisterne_m3"]), "552", "zählt nicht als Rückhaltevolumen"])
        rows.append(["Betriebswasserpumpe", "1", "Förderdaten nach Bemessung", "412", ""])
        rows.append(["Betriebswasserzentrale mit Trinkwassernachspeisung", "1",
                     "freier Auslauf DIN EN 1717 Typ AA; Verwendung: %s"
                     % ", ".join(n["verwendung"][:5] or ["Bewässerung"]), "412",
                     "Leitungen und Zapfstellen als Betriebswasser kennzeichnen"])
    s = cfg["stauraumkanal"]
    if s["aktiv"]:
        rows.append(["Stauraumkanal (überdimensionierte Leitung)", "1",
                     "DN %s; L = %s m; V = %s m³" % (de_int(s["dn"]), de(s["laenge_m"]),
                                                     de(stauraum_volumen(cfg))), "551", ""])
    b = cfg["rueckhaltebecken"]
    if b["aktiv"]:
        rows.append(["Regenrückhaltebecken", "1", "V = %s m³; %s"
                     % (de(b["volumen_m3"]), BAUARTEN[b["bauart"]]), "551", ""])
    if cfg["drosselschacht"]["aktiv"]:
        rows.append(["Drosselschacht mit Drosselorgan", "1", "Qdr = %s l/s"
                     % de(cfg["drosselschacht"]["drosselabfluss_l_s"]), "551",
                     "Einstellwert protokollieren"])
    if cfg["uebergabeschacht"]["aktiv"]:
        rows.append(["Übergabeschacht", "1", "Kontrolle / Probenahme vor Grundstücksgrenze",
                     "551", ""])
    if cfg["versorger"]["einleitstelle"] == "versickerung":
        rows.append(["Versickerungsanlage (Rigole)", "1", "Bemessung nach DWA-A 138", "551", ""])
    return rows + cfg["geraete_zusatz"]


def schnittstellen_rows(cfg):
    """Spalten wie SCHNITT_KEYS. Zeilen erscheinen nur fuer vorhandene Komponenten."""
    v = cfg["versorger"]
    rows = [
        ["Dachablauf / Dachabdichtung", "411", "Dachabdichtung (KG 363)",
         "Lage, Anzahl und Höhe der Abläufe DN %d, Gefälle, Anschlussflansch" % DACHABLAUF_DN,
         "Einbau Ablaufkörper und Rohranschluss: Sanitär; Eindichten: Dachabdichtung",
         "Dichtheitsprüfung Leitung: Sanitär; gemeinsame Sichtabnahme der Eindichtung"],
        ["Fall- und Sammelleitung / Durchdringungen", "411",
         "Rohbau (KG 330/350), Brandschutz",
         "Aussparungen, Kernbohrungen, Schächte, Schall- und Brandschutzanforderungen",
         "Aussparungen: Rohbau; Leitungen und Abschottungen: Sanitär",
         "Dichtheitsprüfung: Sanitär; Abnahme Abschottungen mit Brandschutz"],
        ["Grundleitungen / Gründung, Erdbau", "411 / 551",
         "Rohbau Gründung (KG 320), Erdbau (KG 510)",
         "Trassen, Höhen, Durchführungen Bodenplatte, Übergabepunkt Gebäude/Außenanlage",
         "Gräben und Verfüllung: Erdbau; Rohrverlegung: Sanitär / Tiefbau",
         "Dichtheitsprüfung DIN EN 1610 und Kamerabefahrung vor Verfüllung/Abnahme"],
    ]
    if cfg["notueberlauf"]:
        rows.append(["Notüberlauf / Attika, Fassade", "411",
                     "Dachabdichtung (KG 363), Fassade (KG 330)",
                     "Lage und Höhe der Notüberläufe, freier Auslauf, Aufstauhöhe (Statik)",
                     "Öffnung Attika: Rohbau/Fassade; Eindichten: Dachabdichtung",
                     "Sichtprüfung Höhenlage; Nachweis Notentwässerung: Sanitärplanung"])
    if any(d["typ"] != "standard" or d["begruenung"] != "keine" for d in cfg["daecher"]):
        rows.append(["Retentions-/Schwammdach, Dachbegrünung", "411",
                     "Dachbegrünung (KG 363), Tragwerk (KG 361)",
                     "Anstauhöhe, Lastannahmen Wasser/Substrat, Drosselabfluss je Ablauf, "
                     "Kontrollschächte, Kiesstreifen",
                     "Retentionsaufbau und Begrünung: Dachbegrünung; Ablaufdrossel: Sanitär",
                     "Einstellung Ablaufdrosseln protokollieren; gemeinsame Abnahme, "
                     "Übergabe Pflegeanweisung"])
    if any(d["pv"] for d in cfg["daecher"]):
        rows.append(["Dachentwässerung / PV-Anlage", "411",
                     "Elektro, Eigenstromversorgung (KG 442)",
                     "Aufständerung, Auflast, Abstand zu Abläufen und Notüberläufen, "
                     "Wartungswege, Kabelführung",
                     "PV-Montage und Kabel: Elektro; keine Durchdringung ohne Abstimmung "
                     "Dachabdichtung",
                     "Inbetriebnahme PV: Elektro; Sichtprüfung freie Abläufe: Sanitär"])
    if cfg["rinne"]["vorhanden"]:
        rows.append(["Entwässerungsrinne / Beläge", "551",
                     "Außenanlagen, Oberbau (KG 530)",
                     "Lage, Belastungsklasse, Gefälle der Flächen, Anschlusshöhe",
                     "Rinne setzen: Außenanlagen; Anschluss an Sammelleitung: Tiefbau/Sanitär",
                     "Spülung und Funktionsprüfung nach Belagsfertigstellung"])
    n = cfg["regenwassernutzung"]
    if n["aktiv"]:
        rows.append(["Betriebswasser / Trinkwasser", "412", "Trinkwasserinstallation (KG 412)",
                     "Nachspeisung über freien Auslauf Typ AA, Kennzeichnung, keine "
                     "Verbindung zum Trinkwassernetz",
                     "Trinkwasserzuleitung bis Nachspeisung: Trinkwasser; ab Zentrale: "
                     "Regenwassernutzung",
                     "Druck- und Dichtheitsprüfung, Funktionsprüfung Nachspeisung, "
                     "Anzeige beim Versorger / Gesundheitsamt"])
        rows.append(["Pumpe, Zentrale / Stromversorgung", "412",
                     "Elektro, Niederspannung (KG 444)",
                     "Anschlussleistung, Absicherung, Potentialausgleich, Kabelweg zur Zisterne",
                     "Zuleitung bis Klemme: Elektro; Geräteanschluss: Sanitär",
                     "Drehrichtung, Trockenlaufschutz, gemeinsame Inbetriebnahme"])
        rows.append(["Zisterne / Erdbau, Außenanlagen", "552",
                     "Erdbau (KG 510), Außenanlagen (KG 530)",
                     "Baugrube, Auftriebssicherung, Überdeckung, Belastungsklasse Abdeckung",
                     "Aushub, Bettung, Verfüllung: Erdbau; Behälter und Leitungen: Sanitär",
                     "Dichtheitsprüfung, Füllstandsprüfung vor Verfüllung"])
        if any("ewässer" in x for x in (n["verwendung"] or ["Bewässerung"])):
            rows.append(["Betriebswasser / Bewässerung", "412",
                         "Freianlagen, Vegetation (KG 570), Bewässerung (KG 552)",
                         "Entnahmestellen, Bedarf, Druck, Steuerung der Bewässerung",
                         "bis Übergabearmatur: Sanitär; Bewässerungsnetz: Freianlagen",
                         "gemeinsame Funktionsprüfung, Einweisung Betreiber"])
    if cfg["stauraumkanal"]["aktiv"] or cfg["rueckhaltebecken"]["aktiv"]:
        rows.append(["Stauraumkanal, Rückhaltebecken / Tiefbau", "551",
                     "Erdbau (KG 510), Außenanlagen (KG 530/540)",
                     "Lage, Höhen, Stauziel, Auftrieb, Überdeckung, Zugänglichkeit für Wartung",
                     "Aushub, Bettung, Verfüllung: Erdbau; Bauwerk und Leitungen: Tiefbau",
                     "Dichtheitsprüfung, Kontrolle Stauziel, Abnahme vor Überbauung"])
    if cfg["drosselschacht"]["aktiv"]:
        rows.append(["Drosselschacht / Einleitbeschränkung", "551",
                     "Versorger / Entwässerungsbetrieb (KG 221/230)",
                     "zulässiger Drosselabfluss %s l/s, Nachweis Rückhaltevolumen, "
                     "Entwässerungsgenehmigung" % de(cfg["drosselschacht"]["drosselabfluss_l_s"]),
                     "Drosselorgan einbauen und einstellen: Tiefbau/Sanitär",
                     "Einstellprotokoll Drossel; Abnahme durch Versorger"])
    if v["einleitstelle"] == "versickerung":
        rows.append(["Versickerungsanlage / Baugrund", "551",
                     "Erdbau (KG 510), Wasserbehörde",
                     "Durchlässigkeit, Grundwasserabstand, Abstand zu Gebäuden, "
                     "wasserrechtliche Erlaubnis",
                     "Aushub und Verfüllung: Erdbau; Rigole und Zulauf: Tiefbau",
                     "Abnahme vor Verfüllung; Anzeige / Abnahme Wasserbehörde"])
    else:
        rows.append(["%s / %s" % ("Übergabeschacht" if cfg["uebergabeschacht"]["aktiv"]
                                  else "Anschlussleitung", EINLEITSTELLEN[v["einleitstelle"]]),
                     "551", "Versorger / Entwässerungsbetrieb (KG 221/230)",
                     "Anschlusspunkt, Sohlhöhe, Rückstauebene%s, Entwässerungsantrag"
                     % (" (%s)" % v["rueckstauebene"] if v["rueckstauebene"] else ""),
                     "bis Grundstücksgrenze: Tiefbau; Anschluss an öffentliche Anlage: "
                     "nach Vorgabe Versorger",
                     "Dichtheitsprüfung, Kamerabefahrung; Abnahme durch Versorger"])
    if cfg["ueberflutung"]["schadlos_ueberflutbar_m3"]:
        rows.append(["Überflutungsfläche / Freianlagen", "551",
                     "Außenanlagen (KG 530), Vegetation (KG 570)",
                     "Geländemodellierung, Einstauhöhe, V = %s m³, Schutz von Gebäudeöffnungen"
                     % de(cfg["ueberflutung"]["schadlos_ueberflutbar_m3"]),
                     "Geländeprofil und Einfassungen: Außenanlagen",
                     "Höhenaufmaß; Bestätigung Überflutungsnachweis"])
    return rows + cfg["schnittstellen_zusatz"]


class Listen(object):
    """Fliesslayout fuer Tabellen mit festem Seitenumbruch."""

    TOP, BOTTOM, X0, X1 = 281.5, 22.0, 24.0, 406.0
    SIZE, LH, PAD = 2.5, 3.2, 0.8

    def __init__(self, c, cfg):
        self.c, self.cfg, self.z, self.page = c, cfg, None, 1
        self.new_page()

    def new_page(self):
        if self.z is not None:
            self.c.showPage()
        self.page += 1
        self.c.scale(mm, mm)
        self.z = z = Z(self.c)
        p = self.cfg["projekt"]
        z.rect(20, 10, 390, 277, BLACK, None, 0.7)
        z.line(20, 19, 410, 19, BLACK, 0.35)
        z.text(22, 13, p["name"] or "-", 3.2, FB, BLACK, maxw=150)
        z.text(215, 13, "Regenwasseranlage - Gerätetabelle und Schnittstellenliste", 2.8, F,
               BLACK, "c")
        z.text(408, 13, "%s   Blatt %d" % ("   ".join(
            x for x in [p["plan_nr"] + (" / " + p["index"] if p["index"] else ""),
                        p["datum"]] if x), self.page), 2.8, F, BLACK, "r")
        self.y = self.TOP

    def _header(self, title, cols, fort):
        z = self.z
        z.text(self.X0, self.y, title + (" (Fortsetzung)" if fort else ""), 3.6, FB)
        self.y -= 3.0
        h = self.LH + 2 * self.PAD
        x = self.X0
        for name, w in cols:
            z.rect(x, self.y - h, w, h, BLACK, (0.90, 0.92, 0.95), 0.25)
            z.text(x + 1.2, self.y - h + self.PAD + 0.7, name, self.SIZE, FB, BLACK,
                   maxw=w - 2.4)
            x += w
        self.y -= h

    def table(self, title, cols, rows):
        if self.y - 30.0 < self.BOTTOM:
            self.new_page()
        self._header(title, cols, False)
        for row in rows:
            cells = [self.z.wrap(s, self.SIZE, F, w - 2.4) or [""]
                     for s, (_, w) in zip(row, cols)]
            h = max(len(c) for c in cells) * self.LH + 2 * self.PAD
            if self.y - h < self.BOTTOM:
                self.new_page()
                self._header(title, cols, True)
            x = self.X0
            for j, (lines, (_, w)) in enumerate(zip(cells, cols)):
                self.z.rect(x, self.y - h, w, h, BLACK, None, 0.2)
                for i, s in enumerate(lines):
                    self.z.text(x + 1.2, self.y - self.PAD - 2.2 - i * self.LH, s, self.SIZE,
                                FB if j == 1 else F, BLACK, maxw=w - 2.4)
                x += w
            self.y -= h
        self.y -= 7.0

    def note(self, text):
        for s in self.z.wrap(text, 2.4, F, self.X1 - self.X0):
            if self.y < self.BOTTOM:
                self.new_page()
            self.z.text(self.X0, self.y, s, 2.4, F, GREY)
            self.y -= 3.2


def draw_listen(c, cfg):
    L = Listen(c, cfg)
    g = [[str(i + 1)] + r for i, r in enumerate(geraete_rows(cfg))]
    L.table("Gerätetabelle",
            [("Pos.", 12), ("Gerät / Bauteil", 92), ("Anzahl", 18), ("Kenndaten", 140),
             ("KG DIN 276", 24), ("Bemerkung", 96)], g)
    s = [["S%d" % (i + 1)] + r for i, r in enumerate(schnittstellen_rows(cfg))]
    L.table("Schnittstellenliste (Kostengruppen nach DIN 276)",
            [("Nr.", 12), ("Schnittstelle", 58), ("KG", 18), ("Gegengewerk", 62),
             ("Koordination", 82), ("Anschluss", 78), ("Inbetriebnahme", 72)], s)
    L.note("Eigenes Gewerk: Regenwasseranlage - KG 411 Abwasseranlagen (Gebäude), KG 412 "
           "Wasseranlagen (Betriebswasser), KG 551/552 Abwasser- und Wasseranlagen in "
           "Außenanlagen. Kostengruppen nach DIN 276:2018-12; Zuordnung und "
           "Leistungsabgrenzung sind projektspezifisch zu bestätigen.")
    c.showPage()


def render(cfg, warnings, out_path):
    c = canvas.Canvas(out_path, pagesize=(W * mm, H * mm), invariant=1, pageCompression=1)
    c.setTitle("Regenwasseranlage - Prinzipschema")
    c.setAuthor(cfg["projekt"]["bearbeiter"] or "regenwasser-schema")
    c.setSubject(cfg["projekt"]["name"])
    c.setCreator("regenwasser-schema skill")
    c.scale(mm, mm)
    z = Z(c)
    z.rect(20, 10, 390, 277, BLACK, None, 0.7)
    draw_site(z)
    draw_ueberflutungsflaeche(z, cfg)
    draw_building(z, cfg)
    drains = draw_roofs(z, cfg)
    draw_chain(z, cfg, drains)
    draw_nachweis(z, cfg)
    z.rect(20, 10, 390, STRIP - 10, None, WHITE)
    draw_legend(z)
    draw_vorgaben(z, cfg)
    draw_hinweise(z, warnings)
    draw_titleblock(z, cfg)
    z.rect(20, 10, 390, 277, BLACK, None, 0.7)
    c.showPage()
    draw_listen(c, cfg)
    c.save()


def main(argv=None):
    ap = argparse.ArgumentParser(description="Regenwasseranlagen-Schema als PDF erzeugen")
    ap.add_argument("config", help="Konfigurationsdatei (JSON)")
    ap.add_argument("-o", "--output", help="Ausgabe-PDF (Standard: <config>.pdf)")
    ap.add_argument("--check", action="store_true", help="nur prüfen, kein PDF schreiben")
    a = ap.parse_args(argv)
    try:
        with open(a.config, encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError) as exc:
        print("FEHLER: Konfiguration nicht lesbar: %s" % exc, file=sys.stderr)
        return 2
    cfg, errors, warnings = normalize(raw)
    for w_ in warnings:
        print("HINWEIS: " + w_)
    if errors:
        for e in errors:
            print("FEHLER: " + e, file=sys.stderr)
        return 2
    if a.check:
        print("OK: Konfiguration gültig")
        return 0
    out = a.output or os.path.splitext(a.config)[0] + ".pdf"
    render(cfg, warnings, out)
    print("PDF geschrieben: " + out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
