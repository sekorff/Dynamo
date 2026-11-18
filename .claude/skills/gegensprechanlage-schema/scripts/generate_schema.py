#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministischer Generator fuer ein Gegensprechanlagen-Schema (PDF, DIN A3 quer).

Eingabe : JSON-Anlagenbeschreibung (siehe SKILL.md / examples/beispiel.json)
Ausgabe : PDF mit Schemaseite(n) und Geraete-/Leitungsliste

Determinismus: gleiche Eingabe -> byte-identisches PDF.
  * kein Zugriff auf Uhrzeit, Zufall, Umgebung oder Netzwerk
  * reportlab im "invariant"-Modus, ohne Stream-Kompression
  * nur PDF-Standardschriften (Helvetica), festes Raster, feste Reihenfolge
"""
import argparse
import hashlib
import json
import math
import re
import sys

try:
    from reportlab.lib.colors import HexColor
    from reportlab.pdfbase.pdfmetrics import stringWidth
    from reportlab.pdfgen import canvas
except ImportError:  # pragma: no cover
    sys.stderr.write("FEHLER: reportlab fehlt. Installation: pip install reportlab\n")
    sys.exit(3)

# --------------------------------------------------------------------------
# Feste Regeln und Stammdaten
# --------------------------------------------------------------------------
MAX_KUPFER_M = 90.0          # > 90 m -> LWL + Medienkonverter + PoE-Injektor 230 V
SPALTEN_PRO_SEITE = 10
ZEILEN_PRO_LISTENSEITE = 40

EINBAUORTE = {
    "tuer": "Tür",
    "tor": "Tor",
    "schranke": "Schranke",
    "vereinzelung": "Vereinzelungsanlage",
    "aufzug": "Aufzug",
}
EINBAUORTE_INNEN = ("tuer",)
FREIGABE_KURZ = {
    "tuer": "Tür",
    "tor": "Tor",
    "schranke": "Schranke",
    "vereinzelung": "Vereinzelung",
    "aufzug": "Aufzug",
}
AKTOR = {
    "tuer": "Türöffner",
    "tor": "Torsteuerung",
    "schranke": "Schrankensteuerung",
    "vereinzelung": "Vereinzelungsanlage",
    "aufzug": "Aufzugsteuerung",
}
FREIGABE_REIHENFOLGE = ("tuer", "tor", "schranke", "vereinzelung", "aufzug")
WAEHL_WEGE = {"ip": "IP (LAN / WAN)", "mobil": "Mobilfunk (LTE / 5G)"}
DEFAULT_MELDUNGEN = [
    "Sammelstörung Anlage",
    "Ausfall Sprechstelle",
    "Verbindungsausfall LAN / LWL",
    "Netzausfall 230 V / PoE",
]

# Farben
C_CU = HexColor("#1F5FBF")
C_LWL = HexColor("#E07B00")
C_230 = HexColor("#C62828")
C_CTRL = HexColor("#222222")
C_STOER = HexColor("#8E24AA")
C_TK = HexColor("#2E7D32")
C_LINE = HexColor("#222222")
C_GREY = HexColor("#9E9E9E")
C_FILL = HexColor("#F3F5F7")
C_AUSSEN = HexColor("#1B3A5C")
C_INNEN = HexColor("#2E6B4F")
C_VIDEO = HexColor("#0277BD")
C_ZK = HexColor("#6A1B9A")
C_WHITE = HexColor("#FFFFFF")
C_BLACK = HexColor("#000000")
C_SW = HexColor("#DDE6F0")

F = "Helvetica"
FB = "Helvetica-Bold"

# Blatt DIN A3 quer (pt)
W, H = 1190.55, 841.89
M = 28.0
X0, X1, Y0, Y1 = M, W - M, M, H - M
FUSS_H = 85.0
SF_W = 420.0                 # Schriftfeldbreite
COL_X0 = X0 + 12.0
COL_W = 111.0


class EingabeFehler(Exception):
    pass


# --------------------------------------------------------------------------
# Einlesen und Validieren
# --------------------------------------------------------------------------
def _obj(d, pfad, erlaubt, pflicht=()):
    if not isinstance(d, dict):
        raise EingabeFehler("%s: Objekt erwartet" % pfad)
    for k in d:
        if k not in erlaubt:
            raise EingabeFehler("%s: unbekannter Schlüssel '%s' (erlaubt: %s)"
                                % (pfad, k, ", ".join(sorted(erlaubt))))
    for k in pflicht:
        if k not in d:
            raise EingabeFehler("%s: Pflichtfeld '%s' fehlt" % (pfad, k))
    return d


def _s(d, k, pfad, default=None, maxlen=80):
    v = d.get(k, default)
    if v is None:
        raise EingabeFehler("%s: Pflichtfeld '%s' fehlt" % (pfad, k))
    if not isinstance(v, str):
        raise EingabeFehler("%s.%s: Text erwartet" % (pfad, k))
    v = " ".join(v.split())
    if len(v) > maxlen:
        raise EingabeFehler("%s.%s: maximal %d Zeichen" % (pfad, k, maxlen))
    try:
        v.encode("cp1252")
    except UnicodeEncodeError:
        raise EingabeFehler("%s.%s: enthält Zeichen ausserhalb von Windows-1252" % (pfad, k))
    return v


def _b(d, k, pfad, default):
    v = d.get(k, default)
    if not isinstance(v, bool):
        raise EingabeFehler("%s.%s: true/false erwartet" % (pfad, k))
    return v


def _n(d, k, pfad, default=None):
    v = d.get(k, default)
    if v is None:
        raise EingabeFehler("%s: Pflichtfeld '%s' fehlt" % (pfad, k))
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0 or v > 100000:
        raise EingabeFehler("%s.%s: Zahl >= 0 (Meter) erwartet" % (pfad, k))
    return float(v)


def _id(d, pfad):
    v = _s(d, "id", pfad, maxlen=10)
    if not re.match(r"^[A-Za-z0-9._\-]{1,10}$", v):
        raise EingabeFehler("%s.id: 1-10 Zeichen aus A-Z, a-z, 0-9, '.', '_', '-'" % pfad)
    return v


def validieren(roh):
    _obj(roh, "wurzel", {"projekt", "netz", "zentrale", "verteiler", "sprechstellen"},
         ("projekt", "verteiler", "sprechstellen"))

    p = _obj(roh["projekt"], "projekt",
             {"name", "nummer", "bauherr", "planer", "datum", "index", "plan_nr"},
             ("name", "datum"))
    projekt = {
        "name": _s(p, "name", "projekt", maxlen=70),
        "nummer": _s(p, "nummer", "projekt", "", 20),
        "bauherr": _s(p, "bauherr", "projekt", "", 40),
        "planer": _s(p, "planer", "projekt", "", 40),
        "datum": _s(p, "datum", "projekt", maxlen=10),
        "index": _s(p, "index", "projekt", "-", 4),
        "plan_nr": _s(p, "plan_nr", "projekt", "", 24),
    }
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", projekt["datum"]):
        raise EingabeFehler("projekt.datum: Format JJJJ-MM-TT erwartet "
                            "(das Datum wird nie aus der Systemuhr gelesen)")

    n = _obj(roh.get("netz", {}), "netz", {"kupfer_typ", "lwl_typ"})
    netz = {
        "kupfer_typ": _s(n, "kupfer_typ", "netz", "Cat.7", 14),
        "lwl_typ": _s(n, "lwl_typ", "netz", "LWL OM4", 14),
    }

    z = _obj(roh.get("zentrale", {}), "zentrale",
             {"server", "core_switch", "tk_anlage", "waehlgeraet", "glt"})
    sv = _obj(z.get("server", {}), "zentrale.server", {"bezeichnung", "ort"})
    cs = _obj(z.get("core_switch", {}), "zentrale.core_switch", {"bezeichnung", "ort"})
    tk = _obj(z.get("tk_anlage", {}), "zentrale.tk_anlage",
              {"vorhanden", "bezeichnung", "schnittstelle"})
    wg = _obj(z.get("waehlgeraet", {}), "zentrale.waehlgeraet",
              {"vorhanden", "bezeichnung", "wege", "ziel"})
    gl = _obj(z.get("glt", {}), "zentrale.glt",
              {"vorhanden", "bezeichnung", "schnittstelle", "meldungen"})

    wege = wg.get("wege", ["ip", "mobil"])
    if (not isinstance(wege, list) or not wege or len(set(wege)) != len(wege)
            or any(w not in WAEHL_WEGE for w in wege)):
        raise EingabeFehler("zentrale.waehlgeraet.wege: Liste aus 'ip' und/oder 'mobil' erwartet")
    wege = [w for w in ("ip", "mobil") if w in wege]

    meld = gl.get("meldungen", DEFAULT_MELDUNGEN)
    if not isinstance(meld, list) or not 1 <= len(meld) <= 6:
        raise EingabeFehler("zentrale.glt.meldungen: Liste mit 1-6 Texten erwartet")
    meld = [_s({"m": m}, "m", "zentrale.glt.meldungen[%d]" % i, maxlen=40)
            for i, m in enumerate(meld)]

    zentrale = {
        "server": {"bezeichnung": _s(sv, "bezeichnung", "zentrale.server", "Intercom-Server", 34),
                   "ort": _s(sv, "ort", "zentrale.server", "", 34)},
        "core_switch": {"bezeichnung": _s(cs, "bezeichnung", "zentrale.core_switch",
                                          "Core-Switch", 30),
                        "ort": _s(cs, "ort", "zentrale.core_switch", "", 30)},
        "tk_anlage": {"vorhanden": _b(tk, "vorhanden", "zentrale.tk_anlage", True),
                      "bezeichnung": _s(tk, "bezeichnung", "zentrale.tk_anlage", "TK-Anlage", 30),
                      "schnittstelle": _s(tk, "schnittstelle", "zentrale.tk_anlage",
                                          "SIP-Trunk", 22)},
        "waehlgeraet": {"vorhanden": _b(wg, "vorhanden", "zentrale.waehlgeraet", True),
                        "bezeichnung": _s(wg, "bezeichnung", "zentrale.waehlgeraet",
                                          "Wählgerät", 30),
                        "wege": wege,
                        "ziel": _s(wg, "ziel", "zentrale.waehlgeraet", "Leitstelle", 40)},
        "glt": {"vorhanden": _b(gl, "vorhanden", "zentrale.glt", True),
                "bezeichnung": _s(gl, "bezeichnung", "zentrale.glt", "GLT", 30),
                "schnittstelle": _s(gl, "schnittstelle", "zentrale.glt",
                                    "potentialfreie Kontakte", 34),
                "meldungen": meld},
    }

    ids = set()
    if not isinstance(roh["verteiler"], list) or not roh["verteiler"]:
        raise EingabeFehler("verteiler: Liste mit mindestens einem PoE-Switch erwartet")
    verteiler = []
    for i, v in enumerate(roh["verteiler"]):
        pf = "verteiler[%d]" % i
        _obj(v, pf, {"id", "bezeichnung", "ort", "uplink_laenge_m"}, ("id",))
        vid = _id(v, pf)
        if vid in ids:
            raise EingabeFehler("%s.id: '%s' ist doppelt vergeben" % (pf, vid))
        ids.add(vid)
        laenge = _n(v, "uplink_laenge_m", pf, 0)
        verteiler.append({"id": vid,
                          "bezeichnung": _s(v, "bezeichnung", pf, "PoE-Switch", 34),
                          "ort": _s(v, "ort", pf, "", 34),
                          "uplink_laenge_m": laenge,
                          "lwl": laenge > MAX_KUPFER_M})
    vids = [v["id"] for v in verteiler]

    if not isinstance(roh["sprechstellen"], list) or not roh["sprechstellen"]:
        raise EingabeFehler("sprechstellen: Liste mit mindestens einer Sprechstelle erwartet")
    stationen = []
    for i, s in enumerate(roh["sprechstellen"]):
        pf = "sprechstellen[%d]" % i
        _obj(s, pf, {"id", "typ", "einbauort", "bezeichnung", "video", "zugangskontrolle",
                     "freigabe", "verteiler", "leitungslaenge_m"},
             ("id", "typ", "leitungslaenge_m"))
        sid = _id(s, pf)
        if sid in ids:
            raise EingabeFehler("%s.id: '%s' ist doppelt vergeben" % (pf, sid))
        ids.add(sid)
        typ = s["typ"]
        if typ not in ("aussen", "innen"):
            raise EingabeFehler("%s.typ: 'aussen' oder 'innen' erwartet" % pf)
        ort = s.get("einbauort", "tuer")
        if ort not in EINBAUORTE:
            raise EingabeFehler("%s.einbauort: einer von %s erwartet"
                                % (pf, ", ".join(EINBAUORTE)))
        if typ == "innen" and ort not in EINBAUORTE_INNEN:
            raise EingabeFehler("%s.einbauort: Innensprechstellen sind nur an 'tuer' zulässig" % pf)
        fg = s.get("freigabe", [])
        if (not isinstance(fg, list) or len(set(map(str, fg))) != len(fg)
                or any(f not in AKTOR for f in fg)):
            raise EingabeFehler("%s.freigabe: Liste ohne Doppelte aus %s erwartet"
                                % (pf, ", ".join(FREIGABE_REIHENFOLGE)))
        fg = [f for f in FREIGABE_REIHENFOLGE if f in fg]
        if "verteiler" in s:
            vref = s["verteiler"]
        elif len(vids) == 1:
            vref = vids[0]
        else:
            raise EingabeFehler("%s: Pflichtfeld 'verteiler' fehlt (mehrere Verteiler vorhanden)" % pf)
        if vref not in vids:
            raise EingabeFehler("%s.verteiler: '%s' ist kein definierter Verteiler" % (pf, vref))
        laenge = _n(s, "leitungslaenge_m", pf)
        stationen.append({"id": sid, "typ": typ, "einbauort": ort,
                          "bezeichnung": _s(s, "bezeichnung", pf, "", 48),
                          "video": _b(s, "video", pf, False),
                          "zk": _b(s, "zugangskontrolle", pf, False),
                          "freigabe": fg, "verteiler": vref,
                          "laenge": laenge, "lwl": laenge > MAX_KUPFER_M})

    for v in verteiler:
        if not any(s["verteiler"] == v["id"] for s in stationen):
            raise EingabeFehler("verteiler '%s': keine Sprechstelle angeschlossen" % v["id"])

    # feste Zeichenreihenfolge: Verteiler in Eingabereihenfolge, darin Sprechstellen
    spalten = [s for v in verteiler for s in stationen if s["verteiler"] == v["id"]]
    return {"projekt": projekt, "netz": netz, "zentrale": zentrale,
            "verteiler": verteiler, "spalten": spalten}


# --------------------------------------------------------------------------
# Zeichenhilfen
# --------------------------------------------------------------------------
def fmt_m(v):
    if v == int(v):
        return "%d m" % int(v)
    return ("%.1f m" % v).replace(".", ",")


def kuerzen(txt, font, size, max_w):
    if stringWidth(txt, font, size) <= max_w:
        return txt
    while txt and stringWidth(txt + "...", font, size) > max_w:
        txt = txt[:-1]
    return txt.rstrip() + "..."


def text(c, txt, x, y, size=6.5, font=F, farbe=C_BLACK, anker="l", max_w=None, min_size=5.0):
    """Einzeilige Schrift; bei max_w erst verkleinern, dann kuerzen."""
    if not txt:
        return
    if max_w is not None:
        while size > min_size and stringWidth(txt, font, size) > max_w:
            size = round(size - 0.25, 2)
        txt = kuerzen(txt, font, size, max_w)
    c.setFillColor(farbe)
    c.setFont(font, size)
    if anker == "m":
        c.drawCentredString(x, y, txt)
    elif anker == "r":
        c.drawRightString(x, y, txt)
    else:
        c.drawString(x, y, txt)


def umbrechen(txt, font, size, max_w, max_zeilen):
    zeilen, akt = [], ""
    for wort in txt.split():
        probe = (akt + " " + wort).strip()
        if stringWidth(probe, font, size) <= max_w or not akt:
            akt = probe
        else:
            zeilen.append(akt)
            akt = wort
    if akt:
        zeilen.append(akt)
    if len(zeilen) > max_zeilen:
        zeilen = zeilen[:max_zeilen - 1] + [" ".join(zeilen[max_zeilen - 1:])]
    return [kuerzen(z, font, size, max_w) for z in zeilen]


def box(c, x, y, w, h, fill=C_WHITE, stroke=C_LINE, lw=0.8, dash=None, radius=0):
    c.setLineWidth(lw)
    c.setStrokeColor(stroke)
    c.setFillColor(fill)
    c.setDash(dash or [])
    if radius:
        c.roundRect(x, y, w, h, radius, stroke=1, fill=1)
    else:
        c.rect(x, y, w, h, stroke=1, fill=1)
    c.setDash([])


def linie(c, punkte, farbe=C_LINE, lw=1.0, dash=None):
    c.setLineWidth(lw)
    c.setStrokeColor(farbe)
    c.setDash(dash or [])
    pfad = c.beginPath()
    pfad.moveTo(*punkte[0])
    for pt in punkte[1:]:
        pfad.lineTo(*pt)
    c.drawPath(pfad, stroke=1, fill=0)
    c.setDash([])


def punkt(c, x, y, farbe=C_LINE, r=1.8):
    c.setFillColor(farbe)
    c.setStrokeColor(farbe)
    c.circle(x, y, r, stroke=0, fill=1)


def geraet(c, x, y, w, h, titel, zeilen=(), fill=C_FILL):
    box(c, x, y, w, h, fill=fill, lw=1.0)
    text(c, titel, x + w / 2, y + h - 12, 8.5, FB, anker="m", max_w=w - 8)
    yy = y + h - 22
    for z in zeilen:
        text(c, z, x + w / 2, yy, 6.5, anker="m", max_w=w - 8)
        yy -= 8.5


DASH_RELAIS = [4, 2]
DASH_FERN = [1, 2]
DASH_STOER = [5, 2, 1, 2]


def badge(c, x, y, w, h, label, aktiv, farbe):
    if aktiv:
        box(c, x, y, w, h, fill=farbe, stroke=farbe, lw=0.6, radius=2)
        text(c, label, x + w / 2, y + h / 2 - 2.2, 6.2, FB, C_WHITE, "m")
    else:
        box(c, x, y, w, h, fill=C_WHITE, stroke=C_GREY, lw=0.6, radius=2)
        text(c, label, x + w / 2, y + h / 2 - 2.2, 6.2, F, C_GREY, "m")
        linie(c, [(x + 3, y + 2), (x + w - 3, y + h - 2)], C_GREY, 0.6)


# --------------------------------------------------------------------------
# Blattrahmen, Schriftfeld, Legende
# --------------------------------------------------------------------------
def rahmen(c, modell, inhalt, blatt, blaetter):
    p = modell["projekt"]
    c.setLineWidth(1.2)
    c.setStrokeColor(C_BLACK)
    c.rect(X0, Y0, X1 - X0, Y1 - Y0, stroke=1, fill=0)
    linie(c, [(X0, Y0 + FUSS_H), (X1, Y0 + FUSS_H)], C_BLACK, 1.0)
    sx = X1 - SF_W
    linie(c, [(sx, Y0), (sx, Y0 + FUSS_H)], C_BLACK, 1.0)
    linie(c, [(sx, Y0 + 55), (X1, Y0 + 55)], C_BLACK, 0.6)
    linie(c, [(sx, Y0 + 28), (X1, Y0 + 28)], C_BLACK, 0.6)
    text(c, "Projekt", sx + 4, Y0 + 77, 5.5, farbe=C_GREY)
    text(c, p["name"], sx + 4, Y0 + 62, 11, FB, max_w=SF_W - 8, min_size=7)
    text(c, "Planinhalt", sx + 4, Y0 + 48, 5.5, farbe=C_GREY)
    text(c, inhalt, sx + 4, Y0 + 34, 10, FB, max_w=SF_W - 8, min_size=7)
    zellen = [("Bauherr", p["bauherr"], 90), ("Planer", p["planer"], 90),
              ("Projekt-Nr.", p["nummer"], 60), ("Plan-Nr.", p["plan_nr"], 70),
              ("Datum", p["datum"], 50), ("Index", p["index"], 25),
              ("Blatt", "%d / %d" % (blatt, blaetter), 35)]
    x = sx
    for kopf, wert, breite in zellen:
        if x > sx:
            linie(c, [(x, Y0), (x, Y0 + 28)], C_BLACK, 0.6)
        text(c, kopf, x + 3, Y0 + 20, 5.5, farbe=C_GREY)
        text(c, wert, x + 3, Y0 + 7, 7.5, FB, max_w=breite - 6)
        x += breite


def legende(c, modell):
    netz = modell["netz"]
    y = Y0 + FUSS_H
    x = X0 + 8
    text(c, "Legende Leitungen", x, y - 11, 7, FB)
    zeilen = [
        (C_CU, 1.4, None, "%s, IP-Datenleitung mit PoE (bis %d m)" % (netz["kupfer_typ"], MAX_KUPFER_M)),
        (C_LWL, 2.2, None, "%s, Lichtwellenleiter (über %d m)" % (netz["lwl_typ"], MAX_KUPFER_M)),
        (C_230, 1.2, None, "230 V AC, lokale Einspeisung PoE-Injektor"),
        (C_CTRL, 1.0, DASH_RELAIS, "Freigabe lokal über Relaiskontakt"),
        (C_CTRL, 1.0, DASH_FERN, "Fernfreigabe über IP (Bedienung Innensprechstelle)"),
        (C_STOER, 1.2, DASH_STOER, "Störmeldungen an GLT"),
        (C_TK, 1.4, None, "Anbindung TK-Anlage / Wählgerät"),
    ]
    yy = y - 21
    for farbe, lw, dash, label in zeilen:
        linie(c, [(x, yy + 2), (x + 30, yy + 2)], farbe, lw, dash)
        text(c, label, x + 36, yy, 6.2)
        yy -= 9.2

    x = X0 + 250
    text(c, "Legende Geräte", x, y - 11, 7, FB)
    yy = y - 24
    box(c, x, yy - 1, 30, 8, fill=C_AUSSEN, stroke=C_AUSSEN)
    text(c, "Außensprechstelle", x + 36, yy, 6.2)
    yy -= 11
    box(c, x, yy - 1, 30, 8, fill=C_INNEN, stroke=C_INNEN)
    text(c, "Innensprechstelle", x + 36, yy, 6.2)
    yy -= 12
    badge(c, x, yy - 2, 30, 9, "VIDEO", True, C_VIDEO)
    text(c, "mit Videokamera / Bildschirm", x + 36, yy, 6.2)
    yy -= 12
    badge(c, x, yy - 2, 30, 9, "ZK", True, C_ZK)
    text(c, "mit Zugangskontrolle (Leser)", x + 36, yy, 6.2)
    yy -= 12
    badge(c, x, yy - 2, 30, 9, "ZK", False, C_ZK)
    text(c, "Ausstattung nicht vorhanden", x + 36, yy, 6.2)

    x = X0 + 430
    text(c, "Planungsregeln", x, y - 11, 7, FB)
    regeln = [
        "Vernetzung durchgängig auf IP-Basis, Speisung der Sprechstellen über PoE.",
        "Leitungslänge bis %d m: Kupferleitung, PoE direkt vom Switch." % MAX_KUPFER_M,
        "Leitungslänge über %d m: LWL bis Medienkonverter (MK) am Einbauort," % MAX_KUPFER_M,
        "PoE-Einspeisung lokal über Injektor 230 V AC / PoE.",
        "MK und Injektor benötigen einen 230-V-Anschluss am Einbauort.",
        "Störmeldungen werden zentral gesammelt und an die GLT übergeben.",
        "Schema ohne Maßstab; Leitungslängen laut Eingabedaten.",
    ]
    yy = y - 21
    for r in regeln:
        text(c, r, x, yy, 6.2, max_w=X1 - SF_W - x - 6)
        yy -= 9.2


# --------------------------------------------------------------------------
# Schemaseite
# --------------------------------------------------------------------------
Y_KOPF = 742.0      # Unterkante Zentralgeraete
H_KOPF = 56.0
X_MITTE = W / 2
Y_CORE = 690.0
Y_BUS = 676.0
Y_SW = 614.0
H_SW = 26.0
Y_ST_TOP = 470.0
H_ST = 98.0
W_ST = 98.0


def kopf(c, modell):
    z = modell["zentrale"]
    ym = Y_KOPF + H_KOPF / 2
    sx, sw = X_MITTE - 90, 180.0
    # Server und Core-Switch
    geraet(c, sx, Y_KOPF, sw, H_KOPF, z["server"]["bezeichnung"],
           ["Zentrale Gegensprechanlage (IP / SIP)", z["server"]["ort"]])
    linie(c, [(X_MITTE, Y_KOPF), (X_MITTE, Y_CORE + 26)], C_CU, 1.4)
    box(c, X_MITTE - 70, Y_CORE, 140, 26, fill=C_SW, lw=1.0)
    text(c, z["core_switch"]["bezeichnung"], X_MITTE, Y_CORE + 15, 7.5, FB, anker="m", max_w=132)
    text(c, z["core_switch"]["ort"], X_MITTE, Y_CORE + 6, 6, anker="m", max_w=132)

    # TK-Anlage
    if z["tk_anlage"]["vorhanden"]:
        tx, tw = 270.0, 150.0
        geraet(c, tx, Y_KOPF, tw, H_KOPF, z["tk_anlage"]["bezeichnung"],
               ["Telefonanlage", "Rufweiterleitung / Türruf"])
        linie(c, [(tx + tw, ym), (sx, ym)], C_TK, 1.4)
        text(c, z["tk_anlage"]["schnittstelle"], (tx + tw + sx) / 2, ym + 4, 6.2,
             farbe=C_TK, anker="m", max_w=sx - tx - tw - 6)

    # Waehlgeraet
    if z["waehlgeraet"]["vorhanden"]:
        wx, ww = 770.0, 150.0
        geraet(c, wx, Y_KOPF, ww, H_KOPF, z["waehlgeraet"]["bezeichnung"],
               ["eigenes Wählgerät", "Übertragung " + " + ".join(
                   {"ip": "IP", "mobil": "Mobil"}[w] for w in z["waehlgeraet"]["wege"])])
        linie(c, [(sx + sw, ym), (wx, ym)], C_TK, 1.4)
        text(c, "Notruf / Alarm", (sx + sw + wx) / 2, ym + 4, 6.2, farbe=C_TK, anker="m")
        wege = z["waehlgeraet"]["wege"]
        ys = [Y_KOPF + 32, Y_KOPF] if len(wege) == 2 else [Y_KOPF + 16]
        for weg, yb in zip(wege, ys):
            bx = 990.0
            linie(c, [(wx + ww, yb + 12), (bx, yb + 12)], C_TK, 1.4)
            box(c, bx, yb, 150, 24, fill=C_WHITE, lw=0.8, radius=6)
            text(c, WAEHL_WEGE[weg], bx + 68, yb + 9, 7, FB, anker="m", max_w=120)
            if weg == "mobil":   # Antennensymbol
                ax, ay = bx + 138, yb + 5
                linie(c, [(ax, ay), (ax, ay + 14)], C_LINE, 0.9)
                linie(c, [(ax - 5, ay + 14), (ax, ay + 8), (ax + 5, ay + 14)], C_LINE, 0.9)
        text(c, "Ziel: " + z["waehlgeraet"]["ziel"], 1065, Y_KOPF - 10, 6.2, anker="m", max_w=150)

    # GLT
    if z["glt"]["vorhanden"]:
        gx, gw, gy, gh = 45.0, 170.0, Y_KOPF - 14, H_KOPF + 14
        box(c, gx, gy, gw, gh, fill=C_FILL, lw=1.0)
        text(c, z["glt"]["bezeichnung"], gx + gw / 2, gy + gh - 12, 8.5, FB, anker="m", max_w=gw - 8)
        yy = gy + gh - 22
        for m in z["glt"]["meldungen"]:
            text(c, "- " + m, gx + 6, yy, 6, max_w=gw - 12)
            yy -= 7.6
        yl = Y_KOPF - 20
        xs = sx + 20
        linie(c, [(xs, Y_KOPF), (xs, yl), (gx + gw + 12, yl), (gx + gw + 12, gy + 20), (gx + gw, gy + 20)],
              C_STOER, 1.2, DASH_STOER)
        text(c, "Störmeldungen: " + z["glt"]["schnittstelle"], (gx + gw + 12 + xs) / 2, yl + 3,
             6.2, farbe=C_STOER, anker="m", max_w=xs - gx - gw - 24)


def strang(c, modell, s, cx):
    """Eine Sprechstelle mit Zuleitung ab Switch-Unterkante."""
    netz = modell["netz"]
    if s["lwl"]:
        linie(c, [(cx, Y_SW), (cx, 576)], C_LWL, 2.2)
        text(c, netz["lwl_typ"], cx + 5, 600, 6, farbe=C_LWL, max_w=48)
        text(c, fmt_m(s["laenge"]), cx + 5, 592, 6, FB, C_LWL, max_w=48)
        box(c, cx - 36, 556, 72, 20, fill=C_WHITE, lw=0.8)
        text(c, "Medienkonverter", cx, 568, 6, FB, anker="m", max_w=68)
        text(c, "LWL / Kupfer", cx, 560, 5.5, anker="m")
        linie(c, [(cx, 556), (cx, 530)], C_CU, 1.4)
        box(c, cx - 36, 506, 72, 24, fill=C_WHITE, lw=0.8)
        text(c, "PoE-Injektor", cx, 520, 6, FB, anker="m")
        text(c, "230 V AC / PoE", cx, 511, 5.5, anker="m")
        # lokale 230-V-Einspeisung fuer Medienkonverter und Injektor
        xv = cx - 46
        linie(c, [(cx - 36, 566), (xv, 566), (xv, 518), (cx - 36, 518)], C_230, 1.2)
        linie(c, [(xv, 542), (xv - 6, 542)], C_230, 1.2)
        punkt(c, xv, 542, C_230, 1.5)
        text(c, "230 V", xv - 1, 546, 5.5, FB, C_230, "r")
        linie(c, [(cx, 506), (cx, Y_ST_TOP)], C_CU, 1.4)
        text(c, "PoE", cx + 5, 485, 6, FB, C_CU)
    else:
        linie(c, [(cx, Y_SW), (cx, Y_ST_TOP)], C_CU, 1.4)
        text(c, netz["kupfer_typ"], cx + 5, 552, 6, farbe=C_CU, max_w=48)
        text(c, fmt_m(s["laenge"]), cx + 5, 544, 6, FB, C_CU, max_w=48)
        text(c, "PoE", cx + 5, 536, 6, FB, C_CU)
    punkt(c, cx, Y_SW, C_LWL if s["lwl"] else C_CU)

    # Sprechstelle
    aussen = s["typ"] == "aussen"
    farbe = C_AUSSEN if aussen else C_INNEN
    x, y = cx - W_ST / 2, Y_ST_TOP - H_ST
    box(c, x, y, W_ST, H_ST, fill=C_WHITE, stroke=farbe, lw=1.2)
    box(c, x, Y_ST_TOP - 16, W_ST, 16, fill=farbe, stroke=farbe, lw=1.2)
    text(c, s["id"], x + 4, Y_ST_TOP - 11.5, 8, FB, C_WHITE, max_w=52)
    text(c, "AUSSEN" if aussen else "INNEN", x + W_ST - 4, Y_ST_TOP - 11, 6.2, FB, C_WHITE, "r")
    text(c, ("Außensprechstelle" if aussen else "Innensprechstelle"), cx, Y_ST_TOP - 27, 6.5, FB,
         anker="m")
    text(c, "an: " + EINBAUORTE[s["einbauort"]], cx, Y_ST_TOP - 36, 6.5, anker="m", max_w=W_ST - 6)
    yy = Y_ST_TOP - 47
    for z in umbrechen(s["bezeichnung"], F, 6.2, W_ST - 8, 2):
        text(c, z, cx, yy, 6.2, farbe=HexColor("#444444"), anker="m")
        yy -= 7.5
    badge(c, x + 5, y + 20, 42, 12, "VIDEO", s["video"], C_VIDEO)
    badge(c, x + W_ST - 47, y + 20, 42, 12, "ZK", s["zk"], C_ZK)
    if not s["freigabe"]:
        text(c, "ohne Freigabe", cx, y + 7, 6, farbe=C_GREY, anker="m")
        return
    text(c, "Freigabe lokal (Relais)" if aussen else "Fernfreigabe über IP", cx, y + 7, 6,
         FB, anker="m")
    dash = DASH_RELAIS if aussen else DASH_FERN
    xb = x + 6
    yb = y - 26
    for f in s["freigabe"]:
        linie(c, [(xb, yb + 10), (x + 14, yb + 10)], C_CTRL, 1.0, dash)
        if aussen:
            box(c, x + 14, yb, W_ST - 14, 20, fill=C_FILL, lw=0.8)
            text(c, AKTOR[f], x + 14 + (W_ST - 14) / 2, yb + 7.5, 6.2, FB, anker="m", max_w=W_ST - 20)
        else:
            box(c, x + 14, yb, W_ST - 14, 20, fill=C_WHITE, lw=0.8, dash=DASH_FERN, radius=5)
            text(c, "Freigabe " + FREIGABE_KURZ[f], x + 14 + (W_ST - 14) / 2, yb + 7.5, 6.2,
                 anker="m", max_w=W_ST - 20)
        yb -= 26
    linie(c, [(xb, y), (xb, yb + 36)], C_CTRL, 1.0, dash)


def schemaseite(c, modell, spalten, erste_ids):
    kopf(c, modell)
    netz = modell["netz"]
    vmap = {v["id"]: v for v in modell["verteiler"]}
    # Gruppen aufeinanderfolgender Spalten je Verteiler
    gruppen = []
    for i, s in enumerate(spalten):
        if gruppen and gruppen[-1][0] == s["verteiler"]:
            gruppen[-1][2] = i
        else:
            gruppen.append([s["verteiler"], i, i])
    trunk_x = []
    for vid, a, b in gruppen:
        v = vmap[vid]
        xa = COL_X0 + a * COL_W + 5
        xb = COL_X0 + (b + 1) * COL_W - 5
        xm = (xa + xb) / 2
        trunk_x.append(xm)
        forts = spalten[a]["id"] != erste_ids[vid]
        farbe = C_LWL if v["lwl"] else C_CU
        linie(c, [(xm, Y_BUS), (xm, Y_SW + H_SW)], farbe, 2.2 if v["lwl"] else 1.4)
        punkt(c, xm, Y_BUS, farbe)
        if v["uplink_laenge_m"] > 0:
            lab = "%s  %s" % (netz["lwl_typ"] if v["lwl"] else netz["kupfer_typ"],
                              fmt_m(v["uplink_laenge_m"]))
        else:
            lab = "Uplink"
        text(c, lab, xm + 5, Y_SW + H_SW + 12, 6, FB, farbe, max_w=xb - xm - 4)
        box(c, xa, Y_SW, xb - xa, H_SW, fill=C_SW, lw=1.0)
        titel = "%s  %s%s" % (v["id"], v["bezeichnung"], "  (Forts.)" if forts else "")
        text(c, titel, xm, Y_SW + 15, 7.5, FB, anker="m", max_w=xb - xa - 8)
        unter = "PoE-Switch, 230 V AC" + (", SFP-Uplink" if v["lwl"] else "")
        if v["ort"]:
            unter = v["ort"] + "  |  " + unter
        text(c, unter, xm, Y_SW + 6, 6, anker="m", max_w=xb - xa - 8)
    xs = trunk_x + [X_MITTE]
    linie(c, [(min(xs), Y_BUS), (max(xs), Y_BUS)], C_LINE, 2.0)
    linie(c, [(X_MITTE, Y_CORE), (X_MITTE, Y_BUS)], C_LINE, 2.0)
    for i, s in enumerate(spalten):
        strang(c, modell, s, COL_X0 + i * COL_W + COL_W / 2)


# --------------------------------------------------------------------------
# Geraete- und Leitungsliste
# --------------------------------------------------------------------------
SPALTEN_LISTE = [("Pos.", 30), ("ID", 60), ("Typ", 80), ("Einbauort", 95), ("Bezeichnung", 215),
                 ("Video", 38), ("ZK", 38), ("Freigabe", 190), ("Verteiler", 60), ("Länge", 50),
                 ("Leitung / Übertragung", 124), ("Speisung", 134)]


def listenzeilen(modell):
    netz = modell["netz"]
    zeilen = []
    for v in modell["verteiler"]:
        zeilen.append([v["id"], "PoE-Switch", v["ort"], v["bezeichnung"], "", "", "", "Core",
                       fmt_m(v["uplink_laenge_m"]) if v["uplink_laenge_m"] > 0 else "-",
                       (netz["lwl_typ"] + " (SFP)") if v["lwl"] else netz["kupfer_typ"],
                       "230 V AC"])
    for s in modell["spalten"]:
        fern = " (IP)" if s["typ"] == "innen" else ""
        zeilen.append([
            s["id"], "Außensprechstelle" if s["typ"] == "aussen" else "Innensprechstelle",
            EINBAUORTE[s["einbauort"]], s["bezeichnung"],
            "ja" if s["video"] else "nein", "ja" if s["zk"] else "nein",
            (", ".join(FREIGABE_KURZ[f] for f in s["freigabe"]) + fern) if s["freigabe"] else "-",
            s["verteiler"], fmt_m(s["laenge"]),
            (netz["lwl_typ"] + " + Medienkonv.") if s["lwl"] else netz["kupfer_typ"],
            "PoE-Injektor 230 V lokal" if s["lwl"] else "PoE vom Switch"])
    return [[str(i + 1)] + z for i, z in enumerate(zeilen)]


def summen(c, modell):
    sp = modell["spalten"]
    lwl = sum(1 for s in sp if s["lwl"])
    werte = [
        ("Außensprechstellen", sum(1 for s in sp if s["typ"] == "aussen")),
        ("Innensprechstellen", sum(1 for s in sp if s["typ"] == "innen")),
        ("davon mit Video", sum(1 for s in sp if s["video"])),
        ("davon mit Zugangskontrolle", sum(1 for s in sp if s["zk"])),
        ("Freigaben gesamt", sum(len(s["freigabe"]) for s in sp)),
        ("PoE-Switches", len(modell["verteiler"])),
        ("PoE-Ports belegt (Kupfer)", len(sp) - lwl),
        ("LWL-Strecken Sprechstellen", lwl),
        ("LWL-Uplinks Verteiler", sum(1 for v in modell["verteiler"] if v["lwl"])),
        ("Medienkonverter LWL / Kupfer", lwl),
        ("PoE-Injektoren 230 V", lwl),
        ("230-V-Anschlüsse am Einbauort", lwl),
    ]
    y = Y0 + FUSS_H
    text(c, "Summen", X0 + 8, y - 11, 7, FB)
    for i, (label, wert) in enumerate(werte):
        x = X0 + 8 + (i // 6) * 240
        yy = y - 22 - (i % 6) * 9.6
        text(c, label, x, yy, 6.5)
        text(c, str(wert), x + 200, yy, 6.5, FB, anker="r")


def listenseite(c, modell, zeilen):
    summen(c, modell)
    y = Y1 - 30
    x = COL_X0 - 2
    breite = sum(b for _, b in SPALTEN_LISTE)
    box(c, x, y, breite, 16, fill=C_AUSSEN, stroke=C_AUSSEN)
    xx = x
    for kopf_txt, b in SPALTEN_LISTE:
        text(c, kopf_txt, xx + 3, y + 5, 7, FB, C_WHITE, max_w=b - 6)
        xx += b
    for n, z in enumerate(zeilen):
        y -= 14
        if n % 2 == 0:
            box(c, x, y, breite, 14, fill=C_FILL, stroke=C_FILL, lw=0.1)
        xx = x
        for wert, (_, b) in zip(z, SPALTEN_LISTE):
            text(c, wert, xx + 3, y + 4, 6.8, max_w=b - 6)
            xx += b
    linie(c, [(x, y), (x + breite, y)], C_LINE, 0.6)


# --------------------------------------------------------------------------
# Hauptprogramm
# --------------------------------------------------------------------------
def erzeugen(modell, ziel):
    sp = modell["spalten"]
    # Seitenumbruch: Verteiler bleiben zusammen, sofern sie auf ein Blatt passen
    seiten = [[]]
    for v in modell["verteiler"]:
        gruppe = [s for s in sp if s["verteiler"] == v["id"]]
        frei = SPALTEN_PRO_SEITE - len(seiten[-1])
        if len(gruppe) > frei and len(gruppe) <= SPALTEN_PRO_SEITE and seiten[-1]:
            seiten.append([])
        for s in gruppe:
            if len(seiten[-1]) == SPALTEN_PRO_SEITE:
                seiten.append([])
            seiten[-1].append(s)
    zeilen = listenzeilen(modell)
    listen = [zeilen[i:i + ZEILEN_PRO_LISTENSEITE]
              for i in range(0, len(zeilen), ZEILEN_PRO_LISTENSEITE)]
    blaetter = len(seiten) + len(listen)
    erste_ids = {}
    for s in sp:
        erste_ids.setdefault(s["verteiler"], s["id"])

    c = canvas.Canvas(ziel, pagesize=(W, H), invariant=1, pageCompression=0)
    c.setTitle("Schema Gegensprechanlage - " + modell["projekt"]["name"])
    c.setAuthor(modell["projekt"]["planer"] or "-")
    c.setSubject("Gegensprechanlage IP / PoE")
    c.setCreator("gegensprechanlage-schema")
    blatt = 0
    for teil in seiten:
        blatt += 1
        rahmen(c, modell, "Schema Gegensprechanlage (IP / PoE)", blatt, blaetter)
        legende(c, modell)
        schemaseite(c, modell, teil, erste_ids)
        c.showPage()
    for teil in listen:
        blatt += 1
        rahmen(c, modell, "Gegensprechanlage - Geräte- und Leitungsliste", blatt, blaetter)
        listenseite(c, modell, teil)
        c.showPage()
    c.save()
    return blaetter


def main(argv=None):
    ap = argparse.ArgumentParser(description="Erzeugt ein Gegensprechanlagen-Schema als PDF.")
    ap.add_argument("eingabe", help="JSON-Datei mit der Anlagenbeschreibung")
    ap.add_argument("-o", "--ausgabe", help="Ziel-PDF (Standard: <eingabe>.pdf)")
    ap.add_argument("--nur-pruefen", action="store_true", help="nur validieren, kein PDF erzeugen")
    args = ap.parse_args(argv)
    try:
        with open(args.eingabe, "r", encoding="utf-8") as fh:
            roh = json.load(fh)
        modell = validieren(roh)
    except (OSError, ValueError) as e:
        sys.stderr.write("FEHLER: %s\n" % e)
        return 2
    except EingabeFehler as e:
        sys.stderr.write("EINGABEFEHLER: %s\n" % e)
        return 2
    lwl = [s["id"] for s in modell["spalten"] if s["lwl"]]
    print("Eingabe gültig: %d Sprechstellen, %d Verteiler, LWL-Strecken (> %d m): %s"
          % (len(modell["spalten"]), len(modell["verteiler"]), MAX_KUPFER_M,
             ", ".join(lwl) if lwl else "keine"))
    if args.nur_pruefen:
        return 0
    ziel = args.ausgabe or re.sub(r"\.json$", "", args.eingabe) + ".pdf"
    blaetter = erzeugen(modell, ziel)
    with open(ziel, "rb") as fh:
        digest = hashlib.sha256(fh.read()).hexdigest()
    print("PDF: %s (%d Blatt)" % (ziel, blaetter))
    print("SHA256: %s" % digest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
