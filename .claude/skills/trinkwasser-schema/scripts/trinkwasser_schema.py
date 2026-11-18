#!/usr/bin/env python3
"""Deterministischer Generator fuer Trinkwasseranlagen-Schemata (Strangschema) als PDF.

Eingabe:  JSON-Konfiguration (siehe references/konfiguration.md)
Ausgabe:  PDF (gleiche Eingabe -> byte-identische Ausgabe)

Fest eingebaute Regeln (nicht abschaltbar):
  * Am Ende jedes Stranges sitzt eine Hygienestation (HS).
  * Aussenzapfstellen und Anbindungen adiabater Kuehlung werden ausschliesslich
    ueber eine Trennstation (TS) angeschlossen.
  * Maximal drei Druckstufen.

Aufruf:
  python3 trinkwasser_schema.py anlage.json -o schema.pdf
  python3 trinkwasser_schema.py anlage.json --check      # nur pruefen/berechnen
"""
import argparse
import json
import math
import sys

from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

RHO_G = 0.0981  # bar je m Wassersaeule

BEMESSUNG_DEFAULT = {
    "mindestfliessdruck_bar": 1.0,
    "verlust_hausanschluss_bar": 0.6,      # Wasserzaehler + Filter
    "verlust_stockwerk_bar": 0.3,          # Stockwerksleitung / Apparate
    "verlust_wwb_zentral_bar": 0.2,
    "verlust_wwb_dezentral_bar": 0.5,
    "rohrreibung_mbar_je_m": 10.0,
    "zuschlag_einzelwiderstaende": 0.5,    # Anteil auf Rohrreibung
    "horizontale_leitungslaenge_m": 30.0,
    "entnahme_ueber_fussboden_m": 1.0,
    "max_ruhedruck_bar": 5.0,
}

WWB_ARTEN = ("zentral", "dezentral", "keine")
VERSORGUNG_ARTEN = ("auto", "netzdruck", "dea")
DEA_ANSCHLUSS = ("unmittelbar", "mittelbar")


class ConfigError(Exception):
    pass


# --------------------------------------------------------------------------
# Validierung
# --------------------------------------------------------------------------
def _keys(obj, path, required, optional=()):
    if not isinstance(obj, dict):
        raise ConfigError(f"{path}: Objekt erwartet")
    allowed = set(required) | set(optional)
    for k in obj:
        if k not in allowed:
            raise ConfigError(
                f"{path}: unbekannter Schluessel '{k}' (erlaubt: {', '.join(sorted(allowed))})")
    for k in required:
        if k not in obj:
            raise ConfigError(f"{path}: Pflichtangabe '{k}' fehlt")


def _num(obj, key, path, minimum=None):
    v = obj[key]
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ConfigError(f"{path}.{key}: Zahl erwartet")
    if minimum is not None and v < minimum:
        raise ConfigError(f"{path}.{key}: muss >= {minimum} sein")
    return float(v)


def _text(obj, key, path):
    v = obj[key]
    if not isinstance(v, str) or not v.strip():
        raise ConfigError(f"{path}.{key}: nicht-leerer Text erwartet")
    try:
        v.encode("cp1252")
    except UnicodeEncodeError:
        raise ConfigError(f"{path}.{key}: enthaelt nicht darstellbare Zeichen")
    return v.strip()


def _unique(names, path):
    seen = set()
    for n in names:
        if n in seen:
            raise ConfigError(f"{path}: Name '{n}' mehrfach vergeben")
        seen.add(n)


def validate(cfg):
    """Prueft die Konfiguration und liefert ein normalisiertes Modell."""
    _keys(cfg, "konfiguration",
          ("projekt", "versorger", "geschosse", "druckstufen"),
          ("wasseraufbereitung", "bemessung"))

    p = cfg["projekt"]
    _keys(p, "projekt", ("name", "standort", "datum"), ("plan_nr", "index", "bearbeiter"))
    projekt = {k: _text(p, k, "projekt") for k in p}

    v = cfg["versorger"]
    _keys(v, "versorger", ("name", "versorgungsdruck_min_bar", "versorgungsdruck_max_bar"),
          ("hausanschluss_dn", "dea_anschluss", "vorgaben"))
    versorger = {
        "name": _text(v, "name", "versorger"),
        "p_min": _num(v, "versorgungsdruck_min_bar", "versorger", 0.1),
        "p_max": _num(v, "versorgungsdruck_max_bar", "versorger", 0.1),
        "dn": int(_num(v, "hausanschluss_dn", "versorger", 15)) if "hausanschluss_dn" in v else None,
        "dea_anschluss": v.get("dea_anschluss", "unmittelbar"),
        "vorgaben": [],
    }
    if versorger["p_max"] < versorger["p_min"]:
        raise ConfigError("versorger: versorgungsdruck_max_bar < versorgungsdruck_min_bar")
    if versorger["dea_anschluss"] not in DEA_ANSCHLUSS:
        raise ConfigError(f"versorger.dea_anschluss: erlaubt sind {DEA_ANSCHLUSS}")
    vg = v.get("vorgaben", [])
    if not isinstance(vg, list):
        raise ConfigError("versorger.vorgaben: Liste erwartet")
    for i, _ in enumerate(vg):
        versorger["vorgaben"].append(_text(vg, i, "versorger.vorgaben"))

    bem = dict(BEMESSUNG_DEFAULT)
    if "bemessung" in cfg:
        _keys(cfg["bemessung"], "bemessung", (), tuple(BEMESSUNG_DEFAULT))
        for k in cfg["bemessung"]:
            bem[k] = _num(cfg["bemessung"], k, "bemessung", 0)

    gs = cfg["geschosse"]
    if not isinstance(gs, list) or not gs:
        raise ConfigError("geschosse: nicht-leere Liste erwartet (von unten nach oben)")
    geschosse = []
    kote = 0.0
    for i, g in enumerate(gs):
        path = f"geschosse[{i}]"
        _keys(g, path, ("name", "hoehe_m"))
        h = _num(g, "hoehe_m", path, 2.0)
        geschosse.append({"name": _text(g, "name", path), "hoehe": h, "kote": kote})
        kote += h
    _unique([g["name"] for g in geschosse], "geschosse")
    gidx = {g["name"]: i for i, g in enumerate(geschosse)}

    def floor(name, path):
        if name not in gidx:
            raise ConfigError(f"{path}: Geschoss '{name}' ist nicht in 'geschosse' definiert")
        return gidx[name]

    ds = cfg["druckstufen"]
    if not isinstance(ds, list) or not 1 <= len(ds) <= 3:
        raise ConfigError("druckstufen: 1 bis 3 Druckstufen erforderlich")
    stufen = []
    prev_top = -1
    for i, d in enumerate(ds):
        path = f"druckstufen[{i}]"
        _keys(d, path, ("name", "von", "bis", "wwb", "straenge"), ("versorgung",))
        lo, hi = floor(d["von"], path + ".von"), floor(d["bis"], path + ".bis")
        if hi < lo:
            raise ConfigError(f"{path}: 'bis' liegt unter 'von'")
        if lo <= prev_top:
            raise ConfigError(f"{path}: Druckstufen muessen von unten nach oben und "
                              "ohne Ueberlappung angegeben werden")
        prev_top = hi
        if d["wwb"] not in WWB_ARTEN:
            raise ConfigError(f"{path}.wwb: erlaubt sind {WWB_ARTEN}")
        vers = d.get("versorgung", "auto")
        if vers not in VERSORGUNG_ARTEN:
            raise ConfigError(f"{path}.versorgung: erlaubt sind {VERSORGUNG_ARTEN}")
        ss = d["straenge"]
        if not isinstance(ss, list) or not ss:
            raise ConfigError(f"{path}.straenge: mindestens ein Strang erforderlich")
        straenge = []
        for j, s in enumerate(ss):
            sp = f"{path}.straenge[{j}]"
            _keys(s, sp, ("name",), ("nutzung", "aussenzapfstellen", "adiabate_kuehlung"))
            sonder = {}
            for key, typ in (("aussenzapfstellen", "AZ"), ("adiabate_kuehlung", "AK")):
                lst = s.get(key, [])
                if not isinstance(lst, list):
                    raise ConfigError(f"{sp}.{key}: Liste von Geschossnamen erwartet")
                for gname in lst:
                    fi = floor(gname, f"{sp}.{key}")
                    if not lo <= fi <= hi:
                        raise ConfigError(f"{sp}.{key}: Geschoss '{gname}' liegt ausserhalb "
                                          f"der Druckstufe ({d['von']} bis {d['bis']})")
                    sonder.setdefault(fi, []).append(typ)
            for fi, lst in sonder.items():
                if len(lst) > 2:
                    raise ConfigError(f"{sp}: hoechstens 2 Sonderanschluesse je Geschoss "
                                      f"({geschosse[fi]['name']}) - weiteren Strang anlegen")
                lst.sort(reverse=True)  # AZ vor AK
            straenge.append({"name": _text(s, "name", sp),
                             "nutzung": _text(s, "nutzung", sp) if "nutzung" in s else "",
                             "sonder": sonder})
        stufen.append({"name": _text(d, "name", path), "lo": lo, "hi": hi,
                       "wwb": d["wwb"], "versorgung_soll": vers, "straenge": straenge})
    _unique([s["name"] for s in stufen], "druckstufen")
    _unique([s["name"] for d in stufen for s in d["straenge"]], "straenge")

    aufb = None
    if "wasseraufbereitung" in cfg:
        w = cfg["wasseraufbereitung"]
        _keys(w, "wasseraufbereitung", (), ("enthaertung", "osmose", "ve_wasser"))
        if "osmose" not in w and "ve_wasser" not in w:
            raise ConfigError("wasseraufbereitung: 'osmose' und/oder 've_wasser' angeben")
        enth = w.get("enthaertung", False)
        if not isinstance(enth, bool):
            raise ConfigError("wasseraufbereitung.enthaertung: true/false erwartet")
        aufb = {"enthaertung": enth, "netze": []}
        for key, med in (("osmose", "RO"), ("ve_wasser", "VE")):
            if key not in w:
                continue
            path = f"wasseraufbereitung.{key}"
            _keys(w[key], path, ("verbraucher",))
            vb = w[key]["verbraucher"]
            if not isinstance(vb, list) or not vb:
                raise ConfigError(f"{path}.verbraucher: mindestens ein Verbraucher erforderlich")
            verbraucher = {}
            for j, x in enumerate(vb):
                xp = f"{path}.verbraucher[{j}]"
                _keys(x, xp, ("name", "geschoss"))
                fi = floor(x["geschoss"], xp + ".geschoss")
                if fi in verbraucher:
                    raise ConfigError(f"{xp}: je Geschoss nur ein Verbraucher je Netz "
                                      "(Bezeichnungen zusammenfassen)")
                verbraucher[fi] = _text(x, "name", xp)
            aufb["netze"].append({"medium": med, "verbraucher": verbraucher})

    model = {"projekt": projekt, "versorger": versorger, "bemessung": bem,
             "geschosse": geschosse, "stufen": stufen, "aufbereitung": aufb}
    berechne(model)
    return model


# --------------------------------------------------------------------------
# Vorbemessung der Druckstufen nach Versorgervorgaben
# --------------------------------------------------------------------------
def _ceil1(x):
    return math.ceil(round(x * 10, 6)) / 10.0


def _floor1(x):
    return math.floor(round(x * 10, 6)) / 10.0


def berechne(model):
    b, v, gs = model["bemessung"], model["versorger"], model["geschosse"]
    hinweise = []
    n_dea = 0
    for st in model["stufen"]:
        h_top = gs[st["hi"]]["kote"] + b["entnahme_ueber_fussboden_m"]
        h_low = gs[st["lo"]]["kote"] + b["entnahme_ueber_fussboden_m"]
        dp_leit = (b["rohrreibung_mbar_je_m"] * (h_top + b["horizontale_leitungslaenge_m"])
                   * (1 + b["zuschlag_einzelwiderstaende"]) / 1000.0)
        dp_wwb = {"zentral": b["verlust_wwb_zentral_bar"],
                  "dezentral": b["verlust_wwb_dezentral_bar"], "keine": 0.0}[st["wwb"]]
        p_stufe = (RHO_G * h_top + b["mindestfliessdruck_bar"] + b["verlust_stockwerk_bar"]
                   + dp_wwb + dp_leit)
        p_erf = p_stufe + b["verlust_hausanschluss_bar"]
        vers = st["versorgung_soll"]
        if vers == "auto":
            vers = "netzdruck" if v["p_min"] >= p_erf else "dea"
        st.update({"h_top": h_top, "h_low": h_low, "p_erf": p_erf, "versorgung": vers,
                   "dm_bar": None, "dea_bar": None, "dea_nr": None})
        if vers == "netzdruck":
            if v["p_min"] < p_erf:
                hinweise.append(
                    f"{st['name']}: Netzdruck vorgegeben, aber Mindestversorgungsdruck "
                    f"{bar(v['p_min'])} < erforderlich {bar(p_erf)}.")
            p_ruhe = v["p_max"] - RHO_G * h_low
            if p_ruhe > b["max_ruhedruck_bar"]:
                dm = _floor1(b["max_ruhedruck_bar"] + RHO_G * h_low)
                st["dm_bar"] = dm
                p_ruhe = dm - RHO_G * h_low
                if dm < p_stufe:
                    hinweise.append(
                        f"{st['name']}: Druckminderer {bar(dm)} unterschreitet den "
                        f"Bedarf {bar(p_stufe)} - Druckstufe teilen.")
        else:
            n_dea += 1
            st["dea_nr"] = n_dea
            st["dea_bar"] = _ceil1(p_stufe)
            p_ruhe = st["dea_bar"] - RHO_G * h_low
            if p_ruhe > b["max_ruhedruck_bar"]:
                hinweise.append(
                    f"{st['name']}: Ruhedruck an der untersten Entnahmestelle {bar(p_ruhe)} "
                    f"> {bar(b['max_ruhedruck_bar'])} - Druckstufe teilen oder "
                    "Stockwerksdruckminderer vorsehen.")
        st["p_ruhe"] = p_ruhe
    model["hinweise"] = hinweise


def bar(x):
    return f"{x:.1f}".replace(".", ",") + " bar"


def kote_txt(x):
    return ("+" if x >= 0 else "-") + f"{abs(x):.2f}".replace(".", ",")


# --------------------------------------------------------------------------
# Zeichnen
# --------------------------------------------------------------------------
STY = {  # Medium: (Farbe, Strichstaerke, Strichmuster)
    "PWC": ((0.00, 0.55, 0.27), 1.4, None),
    "PWH": ((0.85, 0.10, 0.10), 1.4, None),
    "PWHC": ((0.60, 0.15, 0.60), 1.1, (4, 2)),
    "BW": ((0.35, 0.35, 0.35), 1.2, (6, 2, 1, 2)),
    "RO": ((0.00, 0.45, 0.75), 1.4, (8, 2)),
    "VE": ((0.85, 0.50, 0.00), 1.4, (2, 2)),
}
MEDIEN_TXT = {
    "PWC": "PWC  Trinkwasser kalt",
    "PWH": "PWH  Trinkwasser warm",
    "PWHC": "PWH-C  Zirkulation",
    "BW": "Betriebswasser nach Trennstation",
    "RO": "RO  Osmosewasser (Permeat)",
    "VE": "VE  vollentsalztes Wasser",
}
TINT = [(0.90, 0.95, 1.00), (1.00, 0.96, 0.86), (0.95, 0.90, 0.98)]
FONT, FONT_B = "Helvetica", "Helvetica-Bold"

# Geometrie (Punkte)
FH = 78          # Hoehe Geschossband
ZH = 260         # Hoehe Technikzentrale
ROOF = 26
INTAKE_W = 215   # Hausanschluss / Geschossbeschriftung
EQ_W = 110       # Apparatespalte je Druckstufe
SP = 140         # Strangabstand
SP_AUFB = 115    # Strangabstand Aufbereitung
PANEL_W = 320
GAP = 16
MARGIN = 30
FORMATE = [("A3", 1190.55, 841.89), ("A2", 1683.78, 1190.55),
           ("A1", 2383.94, 1683.78), ("A0", 3370.39, 2383.94)]


class Zeichner:
    def __init__(self, c, model):
        self.c, self.m = c, model

    # ---- Grundelemente ----
    def pipe(self, med, pts):
        col, w, dash = STY[med]
        c = self.c
        c.setStrokeColorRGB(*col)
        c.setLineWidth(w)
        c.setDash(*([dash] if dash else [[]]))
        c.setLineJoin(0)
        p = c.beginPath()
        p.moveTo(*pts[0])
        for q in pts[1:]:
            p.lineTo(*q)
        c.drawPath(p, stroke=1, fill=0)
        c.setDash([])

    def dot(self, med, x, y):
        self.c.setFillColorRGB(*STY[med][0])
        self.c.circle(x, y, 1.7, stroke=0, fill=1)

    def _sym(self, lw=0.8):
        c = self.c
        c.setStrokeColorRGB(0, 0, 0)
        c.setFillColorRGB(1, 1, 1)
        c.setLineWidth(lw)
        c.setDash([])

    def text(self, x, y, s, size=6, bold=False, align="l", col=(0, 0, 0)):
        c = self.c
        c.setFillColorRGB(*col)
        c.setFont(FONT_B if bold else FONT, size)
        if align == "c":
            c.drawCentredString(x, y, s)
        elif align == "r":
            c.drawRightString(x, y, s)
        else:
            c.drawString(x, y, s)

    def wrap(self, s, width, size, bold=False):
        font = FONT_B if bold else FONT
        lines, cur = [], ""
        for word in s.split():
            t = (cur + " " + word).strip()
            if cur and stringWidth(t, font, size) > width:
                lines.append(cur)
                cur = word
            else:
                cur = t
        if cur:
            lines.append(cur)
        return lines

    # ---- Symbole (x, y = Mittelpunkt) ----
    def valve(self, x, y, vertical=False, s=5.0):
        self._sym()
        p = self.c.beginPath()
        a, b = s, s * 0.65
        if vertical:
            p.moveTo(x - b, y - a); p.lineTo(x + b, y - a); p.lineTo(x - b, y + a)
            p.lineTo(x + b, y + a)
        else:
            p.moveTo(x - a, y - b); p.lineTo(x - a, y + b); p.lineTo(x + a, y - b)
            p.lineTo(x + a, y + b)
        p.close()
        self.c.drawPath(p, stroke=1, fill=1)

    def dm(self, x, y, vertical=False):
        self.valve(x, y, vertical)
        c = self.c
        if vertical:
            c.line(x, y, x - 8, y)
            pts = [(x - 8, y - 3.5), (x - 8, y + 3.5), (x - 12, y)]
        else:
            c.line(x, y, x, y + 8)
            pts = [(x - 3.5, y + 8), (x + 3.5, y + 8), (x, y + 12)]
        c.setFillColorRGB(0, 0, 0)
        p = c.beginPath()
        p.moveTo(*pts[0]); p.lineTo(*pts[1]); p.lineTo(*pts[2]); p.close()
        c.drawPath(p, stroke=1, fill=1)

    def box(self, x, y, w, h, label, size=5.5, lw=0.8, r=0):
        self._sym(lw)
        if r:
            self.c.roundRect(x - w / 2, y - h / 2, w, h, r, stroke=1, fill=1)
        else:
            self.c.rect(x - w / 2, y - h / 2, w, h, stroke=1, fill=1)
        if label:
            self.text(x, y - size * 0.35, label, size, True, "c")

    def filt(self, x, y):
        self.box(x, y, 16, 12, "")
        self.c.setDash([1.5, 1.5])
        self.c.line(x - 8, y - 6, x + 8, y + 6)
        self.c.setDash([])

    def pump(self, x, y, r=8.0, direction="up"):
        self._sym()
        c = self.c
        c.circle(x, y, r, stroke=1, fill=1)
        k = r * 0.87
        if direction == "up":
            pts = [(x - k, y - r * 0.5), (x + k, y - r * 0.5), (x, y + r)]
        elif direction == "down":
            pts = [(x - k, y + r * 0.5), (x + k, y + r * 0.5), (x, y - r)]
        else:
            pts = [(x - r * 0.5, y - k), (x - r * 0.5, y + k), (x + r, y)]
        p = c.beginPath()
        p.moveTo(*pts[0]); p.lineTo(*pts[2]); p.lineTo(*pts[1])
        c.drawPath(p, stroke=1, fill=0)

    def tank(self, x, y, w=18.0, h=16.0):
        self._sym()
        c = self.c
        c.rect(x - w / 2, y - h / 2, w, h, stroke=0, fill=1)
        p = c.beginPath()
        p.moveTo(x - w / 2, y + h / 2); p.lineTo(x - w / 2, y - h / 2)
        p.lineTo(x + w / 2, y - h / 2); p.lineTo(x + w / 2, y + h / 2)
        c.drawPath(p, stroke=1, fill=0)
        c.setLineWidth(0.5)
        c.line(x - w / 2 + 2, y + h / 2 - 5, x + w / 2 - 2, y + h / 2 - 5)

    def hs(self, x, y, w=26.0, h=14.0):
        """Hygienestation mit Ablauf nach rechts."""
        self.box(x, y, w, h, "HS", 6.5, 1.2)
        c = self.c
        c.setLineWidth(0.7)
        xa = x + w / 2 + 7
        c.line(x + w / 2, y - 3, xa, y - 3)
        c.line(xa, y - 3, xa, y - 7)
        c.setFillColorRGB(0, 0, 0)
        p = c.beginPath()
        p.moveTo(xa - 2.2, y - 6); p.lineTo(xa + 2.2, y - 6); p.lineTo(xa, y - 10); p.close()
        c.drawPath(p, stroke=1, fill=1)

    def ts(self, x, y):
        """Trennstation."""
        self.box(x, y, 18, 10, "TS", 5.5, 1.2)

    def az(self, x, y):
        self._sym()
        self.c.circle(x, y, 6, stroke=1, fill=1)
        self.text(x, y - 1.9, "AZ", 5, True, "c")

    def ak(self, x, y):
        self._sym()
        c = self.c
        p = c.beginPath()
        p.moveTo(x - 6, y); p.lineTo(x - 3, y + 5.5); p.lineTo(x + 3, y + 5.5)
        p.lineTo(x + 6, y); p.lineTo(x + 3, y - 5.5); p.lineTo(x - 3, y - 5.5); p.close()
        c.drawPath(p, stroke=1, fill=1)
        self.text(x, y - 1.9, "AK", 5, True, "c")

    def est(self, x, y, w=12.0, h=22.0):
        self.box(x, y, w, h, "", r=2)

    # ---- Layout ----
    def layout(self):
        m = self.m
        x = INTAKE_W
        for st in m["stufen"]:
            st["xb"] = x
            st["xs"] = [x + EQ_W + 60 + i * SP for i in range(len(st["straenge"]))]
            st["xe"] = st["xs"][-1] + 78
            x = st["xe"] + 8
        a = m["aufbereitung"]
        if a:
            a["xb"] = x
            cur = x + 25 + 34
            if a["enthaertung"]:
                a["x_enth"] = cur + 15
                cur += 48
            prev_xs = None
            for n in a["netze"]:
                if n["medium"] == "RO":
                    n["x_unit"] = cur + 15
                    n["x_tank"] = cur + 52
                    n["x_pump"] = cur + 80
                    n["xs"] = cur + 106
                else:
                    n["x_unit"] = cur + 15
                    n["xs"] = cur + 44
                if prev_xs is not None:
                    n["xs"] = max(n["xs"], prev_xs + SP_AUFB)
                prev_xs = n["xs"]
                cur = n["xs"] + 18
                n["xe"] = n["xs"] + SP_AUFB - 20
            x = a["netze"][-1]["xe"] + 8
        self.draw_w = x
        self.draw_h = max(ZH + len(m["geschosse"]) * FH + ROOF, 600)
        self.W = self.draw_w + GAP + PANEL_W
        self.H = self.draw_h
        self.yz0 = 0.0
        self.yf = [ZH + i * FH for i in range(len(m["geschosse"]))]
        self.y_main = self.yz0 + 30
        self.y_zh = self.yz0 + 160

    # ---- Gebaeude ----
    def gebaeude(self):
        c, m = self.c, self.m
        n = len(m["geschosse"])
        ytop = ZH + n * FH
        for i, st in enumerate(m["stufen"]):
            c.setFillColorRGB(*TINT[i])
            c.rect(st["xb"], self.yf[st["lo"]], st["xe"] - st["xb"],
                   (st["hi"] - st["lo"] + 1) * FH, stroke=0, fill=1)
        c.setStrokeColorRGB(0.45, 0.45, 0.45)
        c.setLineWidth(0.5)
        for i in range(n + 1):
            y = ZH + i * FH
            c.line(0, y, self.draw_w, y)
        c.setLineWidth(1.0)
        c.rect(0, 0, self.draw_w, ytop, stroke=1, fill=0)
        for i, g in enumerate(m["geschosse"]):
            self.text(8, self.yf[i] + FH / 2 + 1, g["name"], 10, True)
            self.text(8, self.yf[i] + FH / 2 - 10, "OKFF " + kote_txt(g["kote"]) + " m", 6,
                      col=(0.3, 0.3, 0.3))
        self.text(8, ZH - 13, "Technikzentrale (schematisch)", 8, True)
        for i, st in enumerate(m["stufen"]):
            x, y = st["xb"] + 6, self.yf[st["lo"]] + FH - 12
            gs = m["geschosse"]
            self.text(x, y, st["name"], 8, True)
            lines = [gs[st["lo"]]["name"] if st["lo"] == st["hi"]
                     else f"{gs[st['lo']]['name']} bis {gs[st['hi']]['name']}",
                     self.vers_txt(st),
                     "WWB " + st["wwb"] if st["wwb"] != "keine" else "ohne WWB"]
            for k, s in enumerate(lines):
                self.text(x, y - 9 - 8 * k, s, 6)

    def vers_txt(self, st):
        if st["versorgung"] == "dea":
            return f"DEA {st['dea_nr']}: {bar(st['dea_bar'])}"
        if st["dm_bar"] is not None:
            return f"Netzdruck, DM {bar(st['dm_bar'])}"
        return "Netzdruck"

    # ---- Hausanschluss ----
    def hausanschluss(self):
        v = self.m["versorger"]
        y = self.y_main
        a = self.m["aufbereitung"]
        x_end = (a["xb"] + 25) if a else (self.m["stufen"][-1]["xb"] + 25)
        self.pipe("PWC", [(-18, y), (x_end, y)])
        c = self.c
        c.setFillColorRGB(*STY["PWC"][0])
        p = c.beginPath()
        p.moveTo(-18, y - 3.5); p.lineTo(-18, y + 3.5); p.lineTo(-10, y); p.close()
        c.drawPath(p, stroke=0, fill=1)
        self.text(10, y + 26, "Versorger: " + v["name"], 6, True)
        s = f"p = {bar(v['p_min'])[:-4]} - {bar(v['p_max'])}"
        if v["dn"]:
            s = f"Hausanschluss DN {v['dn']}, " + s
        self.text(10, y + 18, s, 6)
        items = [(40, "AV"), (75, "WZ"), (110, "AV"), (145, "F")]
        for x, k in items:
            if k == "AV":
                self.valve(x, y)
            elif k == "WZ":
                self.box(x, y, 22, 13, "WZ", 6)
            else:
                self.filt(x, y)
        for x, s in ((75, "Wasserzähler"), (145, "Filter")):
            self.text(x, y - 15, s, 5.5, align="c")
        self.text(x_end, y - 10, "Hauptverteiler PWC", 5.5, align="r", col=STY["PWC"][0])

    # ---- Druckstufe ----
    def stufe(self, st):
        v = self.m["versorger"]
        xb, xs = st["xb"], st["xs"]
        xq = xb + 25
        y0, y1 = self.y_main, self.y_zh
        zentral = st["wwb"] == "zentral"
        self.pipe("PWC", [(xq, y0), (xq, y1), (xs[-1], y1)])
        self.dot("PWC", xq, y0)
        comps = [("AV", "")]
        if st["versorgung"] == "dea":
            if v["dea_anschluss"] == "mittelbar":
                comps.append(("VB", "Vorbehälter (freier Auslauf)"))
            comps.append(("DEA", f"DEA {st['dea_nr']}  {bar(st['dea_bar'])}"))
        if st["dm_bar"] is not None:
            comps.append(("DM", f"DM {bar(st['dm_bar'])}"))
        for i, (k, label) in enumerate(comps):
            y = y0 + (y1 - y0) * (i + 1) / (len(comps) + 1)
            if k == "AV":
                self.valve(xq, y, True)
            elif k == "VB":
                self.tank(xq, y)
            elif k == "DEA":
                self.pump(xq, y)
            else:
                self.dm(xq, y, True)
            if label:
                self.text(xq + 13, y - 2, label, 5.5)

        y_h, y_c = self.yz0 + 222, self.yz0 + 236
        if zentral:
            xw = xb + 75
            self.pipe("PWC", [(xw, y1), (xw, self.yz0 + 172)])
            self.dot("PWC", xw, y1)
            self.pipe("PWH", [(xw, self.yz0 + 210), (xw, y_h), (xs[-1] + 7, y_h)])
            self.pipe("PWHC", [(xs[-1] + 14, y_c), (xw + 30, y_c), (xw + 30, self.yz0 + 190),
                               (xw + 13, self.yz0 + 190)])
            self.box(xw, self.yz0 + 191, 26, 38, "TWE", 6, r=6)
            self.pump(xw + 30, self.yz0 + 208, 4.5, "down")

        top = self.yf[st["hi"]]
        y_hs = top + 68
        for i, s in enumerate(st["straenge"]):
            x = xs[i]
            last = i == len(xs) - 1
            self.pipe("PWC", [(x, y1), (x, y_hs)])
            if not last:
                self.dot("PWC", x, y1)
            if zentral:
                self.pipe("PWH", [(x + 7, y_h), (x + 7, y_hs)])
                self.pipe("PWHC", [(x + 14, y_c), (x + 14, top + 53), (x + 7, top + 53)])
                self.dot("PWHC", x + 7, top + 53)
                if not last:
                    self.dot("PWH", x + 7, y_h)
                    self.dot("PWHC", x + 14, y_c)
            for dx in ((0, 7, 14) if zentral else (0,)):
                self.valve(x + dx, self.yz0 + 249, True, 3.2)
            for fi in range(st["lo"], st["hi"] + 1):
                self.etage(st, s, x, fi)
            self.hs(x + 7, y_hs + 0)
            self.text(x + 34, y_hs + 3, s["name"], 6.5, True)
            if s["nutzung"]:
                self.text(x + 34, y_hs - 4, s["nutzung"][:16], 5.5)

    def etage(self, st, s, x, fi):
        yf = self.yf[fi]
        yb = yf + 34
        self.pipe("PWC", [(x, yb), (x + 52, yb)])
        self.dot("PWC", x, yb)
        if st["wwb"] == "zentral":
            self.pipe("PWH", [(x + 7, yb + 12), (x + 52, yb + 12)])
            self.dot("PWH", x + 7, yb + 12)
        elif st["wwb"] == "dezentral":
            self.pipe("PWC", [(x + 24, yb), (x + 24, yb + 6)])
            self.dot("PWC", x + 24, yb)
            self.pipe("PWH", [(x + 34, yb + 12), (x + 52, yb + 12)])
            self.box(x + 24, yb + 12, 20, 12, "DWE", 5)
        self.est(x + 58, yb + 6)
        for k, typ in enumerate(s["sonder"].get(fi, [])):
            y = yf + 10 + 15 * k
            self.pipe("PWC", [(x, y), (x - 18, y)])
            self.dot("PWC", x, y)
            self.pipe("BW", [(x - 36, y), (x - 46, y)])
            self.ts(x - 27, y)
            (self.az if typ == "AZ" else self.ak)(x - 52, y)

    # ---- Wasseraufbereitung ----
    def aufbereitung(self):
        a = self.m["aufbereitung"]
        if not a:
            return
        xq = a["xb"] + 25
        y0, y1 = self.y_main, self.y_zh
        y_ts = y0 + 85
        self.pipe("PWC", [(xq, y0), (xq, y_ts)])
        self.valve(xq, y0 + 40, True)
        med = "BW"
        prev = (xq, y_ts)
        pts = [prev, (xq, y1)]
        units = []
        if a["enthaertung"]:
            units.append(("ENTH", a["x_enth"], "Enthärtung"))
        for n in a["netze"]:
            x_from = pts[-1]
            if n["medium"] == "RO":
                self.pipe(med, pts + [(n["x_unit"], y1)])
                self.pipe("RO", [(n["x_unit"], y1), (n["xs"], y1)])
                units += [("UO", n["x_unit"], "Umkehrosmose"), ("TANK", n["x_tank"], "Permeat"),
                          ("PUMP", n["x_pump"], "")]
                med = "RO"
            else:
                self.pipe(med, pts + [(n["x_unit"], y1)])
                self.pipe("VE", [(n["x_unit"], y1), (n["xs"], y1)])
                units.append(("VE", n["x_unit"], "Vollentsalzung"))
                med = "VE"
            pts = [(n["xs"], y1)]
            del x_from
        self.ts(xq, y_ts)
        self.text(xq + 13, y_ts - 2, "Trennstation", 5.5)
        for k, x, label in units:
            if k == "TANK":
                self.tank(x, y1 + 2, 18, 20)
            elif k == "PUMP":
                self.pump(x, y1, 7, "right")
            else:
                self.box(x, y1, 30, 20, k, 6)
            if label:
                self.text(x, y1 - 19, label, 5.5, align="c")
        self.text(a["xb"] + 6, self.yz0 + ZH - 13, "Wasseraufbereitung", 8, True)
        for idx, n in enumerate(a["netze"]):
            x, med = n["xs"], n["medium"]
            top = self.yf[max(n["verbraucher"])]
            y_hs = top + 68
            self.pipe(med, [(x, y1), (x, y_hs)])
            if idx < len(a["netze"]) - 1:
                self.dot(med, x, y1)
            self.valve(x, self.yz0 + 249, True, 3.2)
            for fi in sorted(n["verbraucher"]):
                yb = self.yf[fi] + 34
                self.pipe(med, [(x, yb), (x + 52, yb)])
                self.dot(med, x, yb)
                self.est(x + 58, yb, 12, 14)
                for k, line in enumerate(self.wrap(n["verbraucher"][fi], SP_AUFB - 30, 5.5)[:2]):
                    self.text(x + 6, yb - 14 - 7 * k, line, 5.5)
            self.hs(x + 7, y_hs)
            self.text(x + 34, y_hs + 3, med, 6.5, True, col=STY[med][0])
            self.text(x + 34, y_hs - 4, "Osmose" if med == "RO" else "VE-Wasser", 5.5)

    # ---- Seitenfeld ----
    def panel(self):
        c, m = self.c, self.m
        x0, w = self.draw_w + GAP, PANEL_W
        c.setStrokeColorRGB(0, 0, 0)
        c.setLineWidth(1.0)
        c.rect(x0, 0, w, self.H, stroke=1, fill=0)
        xi, wi = x0 + 8, w - 16
        y = self.H - 14

        def head(s):
            nonlocal y
            self.text(xi, y, s, 8, True)
            c.setLineWidth(0.5)
            c.line(xi, y - 3, xi + wi, y - 3)
            y -= 13

        def para(s, size=6, lead=7.5, bullet=False):
            nonlocal y
            lines = self.wrap(s, wi - (8 if bullet else 0), size)
            for k, line in enumerate(lines):
                if bullet and k == 0:
                    self.text(xi, y, "-", size)
                self.text(xi + (8 if bullet else 0), y, line, size)
                y -= lead

        v, b = m["versorger"], m["bemessung"]
        head("Standortvorgaben Versorger")
        para(v["name"])
        para(f"Versorgungsdruck min. {bar(v['p_min'])} / max. {bar(v['p_max'])}"
             + (f", Hausanschluss DN {v['dn']}" if v["dn"] else ""))
        para(f"Anschluss Druckerhöhungsanlagen: {v['dea_anschluss']}")
        for s in v["vorgaben"]:
            para(s, bullet=True)
        y -= 6

        head("Druckstufen (Vorbemessung)")
        cols = [0, 58, 138, 178, 262]
        for cx, s in zip(cols, ["Stufe", "Geschosse", "p erf.", "Versorgung", "p Ruhe"]):
            self.text(xi + cx, y, s, 6, True)
        y -= 8
        gs = m["geschosse"]
        for st in m["stufen"]:
            row = [st["name"][:14], f"{gs[st['lo']]['name']} - {gs[st['hi']]['name']}"[:20],
                   bar(st["p_erf"]), self.vers_txt(st), bar(st["p_ruhe"])]
            for cx, s in zip(cols, row):
                self.text(xi + cx, y, s, 6)
            y -= 8
        y -= 2
        para(f"p erf. = geod. Höhe + Mindestfließdruck {bar(b['mindestfliessdruck_bar'])} + "
             "Verluste (Hausanschluss, Leitung, Stockwerk, WWB), bezogen auf den Hausanschluss. "
             "p Ruhe = Ruhedruck an der untersten Entnahmestelle. Überschlägige Vorbemessung, "
             "ersetzt nicht die Berechnung nach DIN 1988-300.", 5.5, 6.8)
        y -= 6

        head("Planungsgrundsätze / Hinweise")
        para("Am Ende jedes Stranges ist eine Hygienestation (HS) angeordnet.", bullet=True)
        para("Außenzapfstellen (AZ) und Anbindungen adiabater Kühlung (AK) sind ausschließlich "
             "über eine Trennstation (TS) angeschlossen.", bullet=True)
        if m["aufbereitung"]:
            para("Osmose- und VE-Wasser sind über eine Trennstation vom Trinkwassernetz "
                 "getrennt.", bullet=True)
        for s in m["hinweise"]:
            for k, line in enumerate(self.wrap("! " + s, wi, 6, True)):
                self.text(xi, y, line, 6, True, col=(0.75, 0, 0))
                y -= 7.5
        y -= 6

        head("Legende")
        for med in ("PWC", "PWH", "PWHC", "BW", "RO", "VE"):
            self.pipe(med, [(xi, y + 2), (xi + 30, y + 2)])
            self.text(xi + 40, y, MEDIEN_TXT[med], 6)
            y -= 10
        y -= 4
        sym = [
            (lambda x, yy: self.valve(x, yy), "Absperrarmatur"),
            (lambda x, yy: self.box(x, yy, 22, 11, "WZ", 5.5), "Wasserzähler"),
            (lambda x, yy: self.filt(x, yy), "Filter (rückspülbar)"),
            (lambda x, yy: self.dm(x, yy - 4), "DM  Druckminderer"),
            (lambda x, yy: self.pump(x, yy, 6.5), "DEA  Druckerhöhungsanlage"),
            (lambda x, yy: self.tank(x, yy, 16, 12), "Vorbehälter / Permeatbehälter"),
            (lambda x, yy: self.box(x, yy, 22, 12, "TWE", 5, r=4),
             "TWE  zentraler Trinkwassererwärmer"),
            (lambda x, yy: self.box(x, yy, 20, 11, "DWE", 5),
             "DWE  dezentraler Trinkwassererwärmer"),
            (lambda x, yy: self.box(x, yy, 22, 11, "HS", 6, 1.2), "HS  Hygienestation (Strangende)"),
            (lambda x, yy: self.ts(x, yy), "TS  Trennstation"),
            (lambda x, yy: self.az(x, yy), "AZ  Außenzapfstelle"),
            (lambda x, yy: self.ak(x, yy), "AK  Anbindung adiabate Kühlung"),
            (lambda x, yy: self.est(x, yy, 10, 12), "Entnahmestellen / Verbraucher"),
            (lambda x, yy: self.box(x, yy, 24, 11, "ENTH", 5), "Enthärtung"),
            (lambda x, yy: self.box(x, yy, 24, 11, "UO", 5), "Umkehrosmose"),
            (lambda x, yy: self.box(x, yy, 24, 11, "VE", 5), "Vollentsalzung"),
        ]
        col_w = wi / 2
        y_start = y
        half = (len(sym) + 1) // 2
        for i, (fn, label) in enumerate(sym):
            cx = xi + (i // half) * col_w
            yy = y_start - (i % half) * 17
            fn(cx + 15, yy + 2)
            self.text(cx + 34, yy, label, 5.5)

        # Schriftfeld
        p = m["projekt"]
        h = 86
        c.setStrokeColorRGB(0, 0, 0)
        c.setLineWidth(1.0)
        c.setFillColorRGB(1, 1, 1)
        c.rect(x0, 0, w, h, stroke=1, fill=1)
        c.setLineWidth(0.5)
        for yy in (22, 44, 64):
            c.line(x0, yy, x0 + w, yy)
        for xx in (w / 4, w / 2, 3 * w / 4):
            c.line(x0 + xx, 0, x0 + xx, 22)
        self.text(x0 + 6, 78, "Planinhalt", 5, col=(0.4, 0.4, 0.4))
        self.text(x0 + 6, 68, "Trinkwasseranlage - Strangschema", 9, True)
        self.text(x0 + 6, 57, "Projekt", 5, col=(0.4, 0.4, 0.4))
        self.text(x0 + 6, 48, p["name"][:60], 8, True)
        self.text(x0 + 6, 37, "Standort", 5, col=(0.4, 0.4, 0.4))
        self.text(x0 + 6, 27, p["standort"][:70], 7)
        cells = [("Plan-Nr.", p.get("plan_nr", "-")), ("Index", p.get("index", "-")),
                 ("Datum", p["datum"]), ("Bearbeiter", p.get("bearbeiter", "-"))]
        for i, (k, val) in enumerate(cells):
            self.text(x0 + i * w / 4 + 5, 15, k, 5, col=(0.4, 0.4, 0.4))
            self.text(x0 + i * w / 4 + 5, 5, val[:16], 7, True)
        self.text(x0 + w - 6, 78, "ohne Maßstab", 5, align="r", col=(0.4, 0.4, 0.4))

    def alles(self):
        self.gebaeude()
        self.hausanschluss()
        for st in self.m["stufen"]:
            self.stufe(st)
        self.aufbereitung()
        self.panel()


def render(model, out_path):
    c = canvas.Canvas(out_path, invariant=1, pageCompression=1)
    z = Zeichner(c, model)
    z.layout()
    need_w, need_h = z.W + 24, z.H  # 24: Platz fuer Netzanschlusspfeil links
    name, pw, ph = FORMATE[-1]
    for fmt in FORMATE:  # kleinstes Format, auf dem der Inhalt mit >= 85 % Groesse Platz hat
        if (fmt[1] - 2 * MARGIN >= 0.85 * need_w
                and fmt[2] - 2 * MARGIN >= 0.85 * need_h):
            name, pw, ph = fmt
            break
    scale = min((pw - 2 * MARGIN) / need_w, (ph - 2 * MARGIN) / need_h, 1.5)
    c.setPageSize((pw, ph))
    c.setTitle("Trinkwasseranlage - Strangschema: " + model["projekt"]["name"])
    c.setAuthor(model["projekt"].get("bearbeiter", ""))
    c.setSubject("Trinkwasseranlagen-Schema")
    c.setCreator("trinkwasser-schema")
    c.translate((pw - need_w * scale) / 2 + 24 * scale, (ph - need_h * scale) / 2)
    c.scale(scale, scale)
    z.alles()
    c.showPage()
    c.save()
    return name


def bericht(model):
    out = []
    for st in model["stufen"]:
        gs = model["geschosse"]
        out.append(f"{st['name']}: {gs[st['lo']]['name']} bis {gs[st['hi']]['name']} | "
                   f"p_erf {bar(st['p_erf'])} | {Zeichner.vers_txt(None, st)} | "
                   f"p_Ruhe {bar(st['p_ruhe'])} | WWB {st['wwb']} | "
                   f"{len(st['straenge'])} Strang/Straenge")
    for h in model["hinweise"]:
        out.append("HINWEIS: " + h)
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Trinkwasseranlagen-Schema als PDF erzeugen")
    ap.add_argument("config", help="JSON-Konfiguration der Anlage")
    ap.add_argument("-o", "--output", help="Ausgabe-PDF (Standard: <config>.pdf)")
    ap.add_argument("--check", action="store_true", help="nur pruefen und Vorbemessung ausgeben")
    args = ap.parse_args(argv)
    try:
        with open(args.config, encoding="utf-8") as f:
            cfg = json.load(f)
        model = validate(cfg)
    except (OSError, json.JSONDecodeError) as e:
        print(f"FEHLER: Konfiguration nicht lesbar: {e}", file=sys.stderr)
        return 2
    except ConfigError as e:
        print(f"FEHLER: {e}", file=sys.stderr)
        return 2
    print(bericht(model))
    if args.check:
        return 0
    out = args.output or (args.config.rsplit(".", 1)[0] + ".pdf")
    fmt = render(model, out)
    print(f"PDF geschrieben: {out} (Format {fmt} quer)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
