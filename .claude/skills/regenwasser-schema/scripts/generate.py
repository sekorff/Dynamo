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
    "dachablauf": {
        "abflussvermoegen_l_s": ("num?", 4.5),
        "regenspende_r5_5_l_s_ha": ("num?", 300.0),
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


def dachablauf(cfg, d):
    """(Cs, max. Flaeche je Ablauf DN 100 in m2, Anzahl Ablaeufe) - fest gerundet."""
    a = cfg["dachablauf"]
    cs = d["abflussbeiwert"] if d["abflussbeiwert"] is not None \
        else CS_STANDARD[d["begruenung"]]
    amax = int(math.floor(a["abflussvermoegen_l_s"] * 10000.0
                          / (a["regenspende_r5_5_l_s_ha"] * cs) + 1e-9))
    return cs, amax, int(math.ceil(DACHFLAECHE_M2 / float(amax) - 1e-9))
TOP_KEYS = set(SCHEMA) | {"daecher", "notueberlauf"}


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

    for k in ("abflussvermoegen_l_s", "regenspende_r5_5_l_s_ha"):
        if not cfg["dachablauf"][k]:
            errors.append("dachablauf.%s: Zahl > 0 erwartet" % k)
            cfg["dachablauf"][k] = SCHEMA["dachablauf"][k][1]
    if "regenspende_r5_5_l_s_ha" not in (raw.get("dachablauf") or {}):
        warnings.append("Regenspende r(5,5) nicht angegeben - Standardwert 300 l/(s·ha) "
                        "angesetzt; Standortwert (KOSTRA-DWD) einsetzen.")
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
        "Überflutungsnachweis und Notentwässerung nach DIN 1986-100 führen.",
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
    draw_building(z, cfg)
    drains = draw_roofs(z, cfg)
    draw_chain(z, cfg, drains)
    z.rect(20, 10, 390, STRIP - 10, None, WHITE)
    draw_legend(z)
    draw_vorgaben(z, cfg)
    draw_hinweise(z, warnings)
    draw_titleblock(z, cfg)
    z.rect(20, 10, 390, 277, BLACK, None, 0.7)
    c.showPage()
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
