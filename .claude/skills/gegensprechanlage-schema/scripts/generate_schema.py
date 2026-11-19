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

EINBAUORTE = {
    "tuer": "Tür",
    "tor": "Tor",
    "schranke": "Schranke",
    "vereinzelung": "Vereinzelungsanlage",
    "aufzug": "Aufzug",
    "poller": "Poller",
    "schleuse": "Schleuse",
    "rampe": "Rampe / Anlieferung",
    "kassenautomat": "Kassenautomat / Parksäule",
    "notrufsaeule": "Notrufsäule",
    "klingeltableau": "Klingeltableau",
    "fluchttuer": "Fluchttür",
    "paketanlage": "Briefkasten- / Paketanlage",
    "drehtuer": "Drehtür",
}
EINBAUORTE_INNEN = {
    "tuer": "Tür",
    "pforte": "Pforte / Empfang",
    "leitstand": "Leitstand / Sicherheitszentrale",
    "wohnung": "Wohnung / Büro",
    "etagenruf": "Etagenruf / Nebenstelle",
    "schleuseninnenraum": "Schleuseninnenraum",
    "aufzugvorraum": "Aufzugvorraum",
    "tiefgarage": "Tiefgarage",
}
PLATTFORMEN = {"pc": "PC", "smartphone": "Smartphone", "tablet": "Tablet"}
ZUSATZ = {"etagentaster": "Etagentaster", "zweitwecker": "Zweitwecker",
          "signalleuchte": "Signalleuchte"}
OHNE_FREIGABE = ("notrufsaeule", "etagenruf")
CLIENT_GRUPPE = "*CLIENT*"      # interne Gruppe, kann mit keiner ID kollidieren
TYP_TEXT = {"aussen": "Außensprechstelle", "innen": "Innensprechstelle",
            "client": "Software-Client", "relais": "IP-Relais (Smart Relais)"}
TYP_KURZ = {"aussen": "AUSSEN", "innen": "INNEN", "client": "CLIENT", "relais": "RELAIS"}
EINBAUORTE_RELAIS = dict(EINBAUORTE_INNEN, technikraum="Technikraum / Verteiler")
LOKAL = ("aussen", "relais")    # Typen mit hart verdrahtetem Relaiskontakt
FREIGABE_KURZ = {
    "tuer": "Tür",
    "tor": "Tor",
    "schranke": "Schranke",
    "vereinzelung": "Vereinzelung",
    "aufzug": "Aufzug",
    "poller": "Poller",
    "schleuse": "Schleuse",
    "fluchttuer": "Fluchttür",
    "drehtuer": "Drehtür",
}
AKTOR = {
    "tuer": "Türöffner",
    "tor": "Torsteuerung",
    "schranke": "Schrankensteuerung",
    "vereinzelung": "Vereinzelungsanlage",
    "aufzug": "Aufzugsteuerung",
    "poller": "Pollersteuerung",
    "schleuse": "Schleusensteuerung",
    "fluchttuer": "Fluchttürsteuerung",
    "drehtuer": "Drehtürsteuerung",
}
FREIGABE_REIHENFOLGE = ("tuer", "tor", "schranke", "vereinzelung", "aufzug",
                        "poller", "schleuse", "fluchttuer", "drehtuer")
WAEHL_WEGE = {"ip": "IP (LAN / WAN)", "mobil": "Mobilfunk (LTE / 5G)"}
# Gewerke nach DIN 276 (Kostengruppen) - Katalog fuer die Schnittstellenliste
KG_KATALOG = [
    ("KG 334", "Außenwandöffnungen", "Außentüren, Tore und Drehtüren im Gebäude samt Antrieb und Steuerung"),
    ("KG 344", "Innenwandöffnungen", "Innentüren, Schleusen- und Fluchttüren samt Steuerung"),
    ("KG 381", "Allgemeine Einbauten", "Briefkasten- und Paketanlagen"),
    ("KG 444", "Niederspannungsinstallationsanlagen", "230-V-Stromkreise und Anschlüsse"),
    ("KG 451", "Telekommunikationsanlagen", "TK-Anlage"),
    ("KG 452", "Such- und Signalanlagen", "Gegensprechanlage (dieses Gewerk): Server, Sprechstellen, IP-Relais, Wählgerät"),
    ("KG 456", "Gefahrenmelde- und Alarmanlagen", "Zugangskontrollanlage"),
    ("KG 457", "Datenübertragungsnetze", "Switches, Kupfer- und LWL-Verkabelung, Medienkonverter"),
    ("KG 461", "Aufzugsanlagen", "Aufzugsteuerung und Kabinentableau"),
    ("KG 470", "Nutzungsspezifische und verfahrenstechnische Anlagen", "Parkierungsanlage, Kassenautomaten"),
    ("KG 480", "Gebäude- und Anlagenautomation", "GLT / Gebäudeautomation"),
    ("KG 531", "Einfriedungen (Außenanlagen)", "Tore, Schranken, Poller und Vereinzelungsanlagen im Außenbereich"),
]
GEGENGEWERK_FREIGABE = {
    "tuer": "KG 334 / 344 Türen",
    "tor": "KG 334 / 531 Toranlage",
    "schranke": "KG 531 Schrankenanlage",
    "vereinzelung": "KG 344 / 531 Vereinzelungsanlage",
    "aufzug": "KG 461 Aufzugsanlage",
    "poller": "KG 531 Polleranlage",
    "schleuse": "KG 334 / 344 Schleusentüren",
    "fluchttuer": "KG 334 / 344 Fluchttüranlage",
    "drehtuer": "KG 334 Drehtüranlage",
}
EINBAU_FREMD = {     # Einbauort -> (Gegengewerk, Uebergabepunkt, KG Anschluss)
    "aufzug": ("KG 461 Aufzugsanlage", "Einbau in Kabinen- / Ruftableau, Leitung über Hängekabel", "KG 461"),
    "kassenautomat": ("KG 470 Parkierungsanlage", "Einbau in Automat / Säule des Herstellers", "KG 470"),
    "paketanlage": ("KG 381 Briefkasten- / Paketanlage", "Einbau in Anlagenmodul des Herstellers", "KG 381"),
}
SCHNITT_FELDER = ("gegengewerk", "koordination", "anschluss", "inbetriebnahme")
SCHNITT_KEYS = (["netz_poe", "netz_lwl", "strom_lokal", "strom_zentral", "server_netz",
                 "client_netz", "tk_anlage", "waehlgeraet_ip", "waehlgeraet_mobil", "glt",
                 "zugangskontrolle"]
                + ["freigabe_" + f for f in ("tuer", "tor", "schranke", "vereinzelung", "aufzug",
                                             "poller", "schleuse", "fluchttuer", "drehtuer")]
                + ["einbau_aufzug", "einbau_kassenautomat", "einbau_paketanlage"])

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
C_CLIENT = HexColor("#546E7A")
C_RELAIS = HexColor("#8D5524")
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
    _obj(roh, "wurzel", {"projekt", "netz", "zentrale", "verteiler", "sprechstellen",
                         "schnittstellen"},
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

    ueber = {}
    for key, wert in sorted(_obj(roh.get("schnittstellen", {}), "schnittstellen",
                                 set(SCHNITT_KEYS)).items()):
        pf = "schnittstellen.%s" % key
        _obj(wert, pf, set(SCHNITT_FELDER))
        ueber[key] = {f: _s(wert, f, pf, maxlen=44) for f in SCHNITT_FELDER if f in wert}

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
    felder = {"id", "typ", "einbauort", "plattform", "bezeichnung", "video", "zugangskontrolle",
              "freigabe", "zusatz", "verteiler", "leitungslaenge_m", "ruftasten", "tableau",
              "ruftaste", "freigabe_anzahl"}
    for i, s in enumerate(roh["sprechstellen"]):
        pf = "sprechstellen[%d]" % i
        _obj(s, pf, felder, ("id", "typ"))
        sid = _id(s, pf)
        if sid in ids:
            raise EingabeFehler("%s.id: '%s' ist doppelt vergeben" % (pf, sid))
        ids.add(sid)
        typ = s["typ"]
        if typ not in TYP_TEXT:
            raise EingabeFehler("%s.typ: 'aussen', 'innen', 'client' oder 'relais' erwartet" % pf)
        fg = s.get("freigabe", [])
        if (not isinstance(fg, list) or len(set(map(str, fg))) != len(fg)
                or any(f not in AKTOR for f in fg)):
            raise EingabeFehler("%s.freigabe: Liste ohne Doppelte aus %s erwartet"
                                % (pf, ", ".join(FREIGABE_REIHENFOLGE)))
        fg = [f for f in FREIGABE_REIHENFOLGE if f in fg]
        basis = {"id": sid, "typ": typ, "bezeichnung": _s(s, "bezeichnung", pf, "", 48),
                 "video": _b(s, "video", pf, False), "freigabe": fg,
                 "anzahl": {f: 1 for f in fg}}
        if "freigabe_anzahl" in s:
            fa = s["freigabe_anzahl"]
            if typ != "relais":
                raise EingabeFehler("%s.freigabe_anzahl: nur bei typ 'relais' zulässig" % pf)
            if not isinstance(fa, dict) or any(
                    k not in fg or isinstance(v, bool) or not isinstance(v, int) or not 1 <= v <= 99
                    for k, v in fa.items()):
                raise EingabeFehler("%s.freigabe_anzahl: Objekt {Freigabe: Anzahl 1-99} erwartet, "
                                    "nur für Einträge aus 'freigabe'" % pf)
            basis["anzahl"].update(fa)

        if typ == "client":
            for feld in ("einbauort", "zugangskontrolle", "zusatz", "verteiler",
                         "leitungslaenge_m", "ruftasten", "tableau", "ruftaste"):
                if feld in s:
                    raise EingabeFehler("%s.%s: bei typ 'client' nicht zulässig" % (pf, feld))
            plattform = s.get("plattform", "pc")
            if plattform not in PLATTFORMEN:
                raise EingabeFehler("%s.plattform: einer von %s erwartet"
                                    % (pf, ", ".join(PLATTFORMEN)))
            basis.update({"einbauort": plattform, "ort_text": PLATTFORMEN[plattform],
                          "zk": False, "zusatz": [], "verteiler": CLIENT_GRUPPE,
                          "laenge": 0.0, "lwl": False, "tableau": None, "ruftaste": None})
            stationen.append(basis)
            continue

        if "plattform" in s:
            raise EingabeFehler("%s.plattform: nur bei typ 'client' zulässig" % pf)
        if typ == "relais":
            for feld in ("video", "zugangskontrolle", "zusatz", "ruftasten", "tableau", "ruftaste"):
                if feld in s:
                    raise EingabeFehler("%s.%s: bei typ 'relais' nicht zulässig" % (pf, feld))
            if not fg:
                raise EingabeFehler("%s.freigabe: ein IP-Relais braucht mindestens eine Freigabe" % pf)
        orte = {"aussen": EINBAUORTE, "innen": EINBAUORTE_INNEN, "relais": EINBAUORTE_RELAIS}[typ]
        ort = s.get("einbauort", "technikraum" if typ == "relais" else "tuer")
        if ort not in orte:
            raise EingabeFehler("%s.einbauort: bei typ '%s' einer von %s erwartet"
                                % (pf, typ, ", ".join(orte)))
        if ort in OHNE_FREIGABE and fg:
            raise EingabeFehler("%s.freigabe: bei einbauort '%s' ist keine Freigabe zulässig"
                                % (pf, ort))
        ort_text = orte[ort]
        tasten = s.get("ruftasten")
        if ort == "klingeltableau":
            if isinstance(tasten, bool) or not isinstance(tasten, int) or not 1 <= tasten <= 999:
                raise EingabeFehler("%s.ruftasten: ganze Zahl 1-999 erwartet "
                                    "(Pflicht bei einbauort 'klingeltableau')" % pf)
            ort_text += " (%d Ruftasten)" % tasten
        elif tasten is not None:
            raise EingabeFehler("%s.ruftasten: nur bei einbauort 'klingeltableau' zulässig" % pf)

        zusatz = s.get("zusatz", [])
        if (not isinstance(zusatz, list) or len(set(map(str, zusatz))) != len(zusatz)
                or any(z not in ZUSATZ for z in zusatz)):
            raise EingabeFehler("%s.zusatz: Liste ohne Doppelte aus %s erwartet"
                                % (pf, ", ".join(ZUSATZ)))
        if zusatz and typ != "innen":
            raise EingabeFehler("%s.zusatz: nur bei typ 'innen' zulässig" % pf)
        zusatz = [z for z in ZUSATZ if z in zusatz]

        tableau, ruftaste = s.get("tableau"), s.get("ruftaste")
        if tableau is not None or ruftaste is not None:
            if typ != "innen" or ort != "wohnung":
                raise EingabeFehler("%s: 'tableau' und 'ruftaste' nur bei typ 'innen' mit "
                                    "einbauort 'wohnung' zulässig" % pf)
            if not isinstance(tableau, str) or isinstance(ruftaste, bool) \
                    or not isinstance(ruftaste, int) or ruftaste < 1:
                raise EingabeFehler("%s: 'tableau' (ID) und 'ruftaste' (Zahl >= 1) müssen "
                                    "gemeinsam angegeben werden" % pf)
            ort_text += ", Ruf %s/%d" % (tableau, ruftaste)

        if "verteiler" in s:
            vref = s["verteiler"]
        elif len(vids) == 1:
            vref = vids[0]
        else:
            raise EingabeFehler("%s: Pflichtfeld 'verteiler' fehlt (mehrere Verteiler vorhanden)" % pf)
        if vref not in vids:
            raise EingabeFehler("%s.verteiler: '%s' ist kein definierter Verteiler" % (pf, vref))
        laenge = _n(s, "leitungslaenge_m", pf)
        basis.update({"einbauort": ort, "ort_text": ort_text,
                      "zk": _b(s, "zugangskontrolle", pf, False), "zusatz": zusatz,
                      "verteiler": vref, "laenge": laenge, "lwl": laenge > MAX_KUPFER_M,
                      "tableau": tableau, "ruftaste": ruftaste, "pfad": pf})
        stationen.append(basis)

    tableaus = {s["id"]: s for s in roh["sprechstellen"]
                if isinstance(s, dict) and s.get("einbauort") == "klingeltableau"}
    for s in stationen:
        if s["tableau"] is None:
            continue
        if s["tableau"] not in tableaus:
            raise EingabeFehler("%s.tableau: '%s' ist keine Außensprechstelle mit einbauort "
                                "'klingeltableau'" % (s["pfad"], s["tableau"]))
        if s["ruftaste"] > tableaus[s["tableau"]]["ruftasten"]:
            raise EingabeFehler("%s.ruftaste: Tableau '%s' hat nur %d Ruftasten"
                                % (s["pfad"], s["tableau"], tableaus[s["tableau"]]["ruftasten"]))

    for v in verteiler:
        if not any(s["verteiler"] == v["id"] for s in stationen):
            raise EingabeFehler("verteiler '%s': keine Sprechstelle angeschlossen" % v["id"])

    # feste Zeichenreihenfolge: Verteiler in Eingabereihenfolge, darin Sprechstellen
    gruppen = list(verteiler)
    if any(s["typ"] == "client" for s in stationen):
        gruppen.append({"id": CLIENT_GRUPPE, "bezeichnung": "Software-Clients", "ort": "",
                        "uplink_laenge_m": 0.0, "lwl": False, "client": True})
    spalten = [s for v in gruppen for s in stationen if s["verteiler"] == v["id"]]
    return {"projekt": projekt, "netz": netz, "zentrale": zentrale,
            "verteiler": verteiler, "gruppen": gruppen, "spalten": spalten,
            "schnittstellen": ueber}

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
DASH_CLIENT = [6, 3]


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
        (C_CU, 1.2, DASH_CLIENT, "IP über LAN / WLAN / Mobilfunk, ohne PoE (Software-Client)"),
        (C_CTRL, 1.0, DASH_RELAIS, "Freigabe lokal über Relaiskontakt"),
        (C_CTRL, 1.0, DASH_FERN, "Fernfreigabe über IP (Bedienung Innensprechstelle)"),
        (C_LINE, 0.8, None, "Zusatzgerät an Innensprechstelle (Taster, Wecker, Leuchte)"),
        (C_STOER, 1.2, DASH_STOER, "Störmeldungen an GLT"),
        (C_TK, 1.4, None, "Anbindung TK-Anlage / Wählgerät"),
    ]
    yy = y - 20
    for farbe, lw, dash, label in zeilen:
        linie(c, [(x, yy + 2), (x + 30, yy + 2)], farbe, lw, dash)
        text(c, label, x + 36, yy, 6.2)
        yy -= 7.4

    x = X0 + 250
    text(c, "Legende Geräte", x, y - 11, 7, FB)
    yy = y - 21
    for farbe, label in ((C_AUSSEN, "Außensprechstelle"), (C_INNEN, "Innensprechstelle"),
                         (C_CLIENT, "Software-Client / App (PC, Smartphone, Tablet)"),
                         (C_RELAIS, "IP-Relais (Smart Relais), Freigabe im gesicherten Bereich")):
        box(c, x, yy - 1, 30, 6.5, fill=farbe, stroke=farbe)
        text(c, label, x + 36, yy, 6.2)
        yy -= 9
    yy -= 1.5
    for label, aktiv, farbe, txt in (("VIDEO", True, C_VIDEO, "mit Videokamera / Bildschirm"),
                                     ("ZK", True, C_ZK, "mit Zugangskontrolle (Leser)"),
                                     ("ZK", False, C_ZK, "Ausstattung nicht vorhanden")):
        badge(c, x, yy - 2, 30, 9, label, aktiv, farbe)
        text(c, txt, x + 36, yy, 6.2)
        yy -= 10.5

    x = X0 + 450
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


def mal(s, f):
    """Mengenpraefix fuer mehrfach vorhandene Relaisausgaenge."""
    return "%dx " % s["anzahl"][f] if s["anzahl"][f] > 1 else ""


def strang(c, modell, s, cx):
    """Eine Sprechstelle mit Zuleitung ab Switch-Unterkante."""
    netz = modell["netz"]
    typ = s["typ"]
    if typ == "client":
        linie(c, [(cx, Y_SW), (cx, Y_ST_TOP)], C_CU, 1.2, DASH_CLIENT)
        text(c, "IP", cx + 5, 552, 6, FB, C_CU)
        text(c, "LAN / WLAN /", cx + 5, 544, 6, farbe=C_CU)
        text(c, "Mobilfunk", cx + 5, 536, 6, farbe=C_CU)
    elif s["lwl"]:
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
    aussen = typ in LOKAL
    farbe = {"aussen": C_AUSSEN, "innen": C_INNEN, "client": C_CLIENT, "relais": C_RELAIS}[typ]
    x, y = cx - W_ST / 2, Y_ST_TOP - H_ST
    box(c, x, y, W_ST, H_ST, fill=C_WHITE, stroke=farbe, lw=1.2)
    box(c, x, Y_ST_TOP - 16, W_ST, 16, fill=farbe, stroke=farbe, lw=1.2)
    text(c, s["id"], x + 4, Y_ST_TOP - 11.5, 8, FB, C_WHITE, max_w=52)
    text(c, TYP_KURZ[typ], x + W_ST - 4, Y_ST_TOP - 11, 6.2, FB, C_WHITE, "r")
    text(c, TYP_TEXT[typ], cx, Y_ST_TOP - 27, 6.5, FB,
         anker="m")
    text(c, {"client": "auf: ", "relais": "in: "}.get(typ, "an: ") + s["ort_text"], cx, Y_ST_TOP - 36, 6.5, anker="m", max_w=W_ST - 6)
    yy = Y_ST_TOP - 47
    for z in umbrechen(s["bezeichnung"], F, 6.2, W_ST - 8, 2):
        text(c, z, cx, yy, 6.2, farbe=HexColor("#444444"), anker="m")
        yy -= 7.5
    if typ == "relais":
        text(c, "%d Relaisausgänge, PoE" % sum(s["anzahl"].values()), cx, y + 23, 6.2, anker="m")
    else:
        badge(c, x + 5, y + 20, 42, 12, "VIDEO", s["video"], C_VIDEO)
        badge(c, x + W_ST - 47, y + 20, 42, 12, "ZK", s["zk"], C_ZK)
    if s["freigabe"]:
        text(c, "Freigabe lokal (Relais)" if aussen else "Fernfreigabe über IP", cx, y + 7, 6,
             FB, anker="m")
    else:
        text(c, "ohne Freigabe", cx, y + 7, 6, farbe=C_GREY, anker="m")
    n = len(s["freigabe"]) + len(s["zusatz"])
    if not n:
        return
    # Raster passt sich an, damit auch die Maximalbestueckung ueber dem Schriftfeld bleibt
    pitch = min(26.0, (y - (Y0 + FUSS_H + 4)) / n)
    bh = pitch - 6
    bw = W_ST - (26 if s["zusatz"] else 14)
    dash = DASH_RELAIS if aussen else DASH_FERN
    xb = x + 6
    yb = y - pitch
    for f in s["freigabe"]:
        linie(c, [(xb, yb + bh / 2), (x + 14, yb + bh / 2)], C_CTRL, 1.0, dash)
        if aussen:
            box(c, x + 14, yb, bw, bh, fill=C_FILL, lw=0.8)
            text(c, mal(s, f) + AKTOR[f], x + 14 + bw / 2, yb + bh / 2 - 2.3, 6.2, FB, anker="m", max_w=bw - 6)
        else:
            box(c, x + 14, yb, bw, bh, fill=C_WHITE, lw=0.8, dash=DASH_FERN, radius=5)
            text(c, "Freigabe " + FREIGABE_KURZ[f], x + 14 + bw / 2, yb + bh / 2 - 2.3, 6.2,
                 anker="m", max_w=bw - 6)
        yb -= pitch
    if s["freigabe"]:
        linie(c, [(xb, y), (xb, yb + pitch + bh / 2)], C_CTRL, 1.0, dash)
    xr = x + W_ST - 6
    for zz in s["zusatz"]:
        linie(c, [(xr, yb + bh / 2), (x + 14 + bw, yb + bh / 2)], C_LINE, 0.8)
        box(c, x + 14, yb, bw, bh, fill=C_WHITE, lw=0.8)
        text(c, ZUSATZ[zz], x + 14 + bw / 2, yb + bh / 2 - 2.3, 6.2, anker="m", max_w=bw - 6)
        yb -= pitch
    if s["zusatz"]:
        linie(c, [(xr, y), (xr, yb + pitch + bh / 2)], C_LINE, 0.8)

def schemaseite(c, modell, spalten, erste_ids):
    kopf(c, modell)
    netz = modell["netz"]
    vmap = {v["id"]: v for v in modell["gruppen"]}
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
        forts = "  (Forts.)" if spalten[a]["id"] != erste_ids[vid] else ""
        if v.get("client"):
            linie(c, [(xm, Y_BUS), (xm, Y_SW + H_SW)], C_CU, 1.2, DASH_CLIENT)
            punkt(c, xm, Y_BUS, C_CU)
            text(c, "IP-Netz", xm + 5, Y_SW + H_SW + 12, 6, FB, C_CU, max_w=xb - xm - 4)
            box(c, xa, Y_SW, xb - xa, H_SW, fill=C_WHITE, lw=1.0, dash=DASH_CLIENT)
            text(c, v["bezeichnung"] + forts, xm, Y_SW + 15, 7.5, FB, anker="m", max_w=xb - xa - 8)
            text(c, "LAN / WLAN / Mobilfunk, ohne PoE-Port", xm, Y_SW + 6, 6, anker="m",
                 max_w=xb - xa - 8)
            continue
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
        titel = "%s  %s%s" % (v["id"], v["bezeichnung"], forts)
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
SPALTEN_LISTE = [("Pos.", 30), ("ID", 60), ("Typ", 75), ("Einbauort / Plattform", 125),
                 ("Bezeichnung", 135), ("Video", 38), ("ZK", 38), ("Freigabe", 200),
                 ("Zusatzgeräte", 100), ("Verteiler", 60), ("Länge", 50),
                 ("Leitung / Übertragung", 110), ("Speisung", 93)]


def listenzeilen(modell):
    netz = modell["netz"]
    zeilen = []
    for v in modell["verteiler"]:
        zeilen.append([v["id"], "PoE-Switch", v["ort"], v["bezeichnung"], "", "", "", "", "Core",
                       fmt_m(v["uplink_laenge_m"]) if v["uplink_laenge_m"] > 0 else "-",
                       (netz["lwl_typ"] + " (SFP)") if v["lwl"] else netz["kupfer_typ"],
                       "230 V AC"])
    for s in modell["spalten"]:
        client = s["typ"] == "client"
        fern = "" if s["typ"] in LOKAL else " (IP)"
        relais = s["typ"] == "relais"
        if client:
            netzteil = ["-", "-", "IP (LAN / WLAN / Mobil)", "-"]
        else:
            netzteil = [s["verteiler"], fmt_m(s["laenge"]),
                        (netz["lwl_typ"] + " + Medienkonv.") if s["lwl"] else netz["kupfer_typ"],
                        "PoE-Injektor 230 V lokal" if s["lwl"] else "PoE vom Switch"]
        zeilen.append([
            s["id"], TYP_TEXT[s["typ"]], s["ort_text"], s["bezeichnung"],
            "-" if relais else ("ja" if s["video"] else "nein"),
            "-" if client or relais else ("ja" if s["zk"] else "nein"),
            (", ".join(mal(s, f) + FREIGABE_KURZ[f] for f in s["freigabe"]) + fern) if s["freigabe"] else "-",
            ", ".join(ZUSATZ[z] for z in s["zusatz"]) or "-"] + netzteil)
    return [[str(i + 1)] + z for i, z in enumerate(zeilen)]

def summen(c, modell):
    sp = modell["spalten"]
    hw = [s for s in sp if s["typ"] != "client"]
    lwl = sum(1 for s in hw if s["lwl"])
    werte = [
        ("Außensprechstellen", sum(1 for s in sp if s["typ"] == "aussen")),
        ("Innensprechstellen", sum(1 for s in sp if s["typ"] == "innen")),
        ("Software-Clients", len(sp) - len(hw)),
        ("IP-Relais (Smart Relais)", sum(1 for s in sp if s["typ"] == "relais")),
        ("davon mit Video", sum(1 for s in sp if s["video"])),
        ("davon mit Zugangskontrolle", sum(1 for s in sp if s["zk"])),
        ("Freigaben gesamt", sum(sum(s["anzahl"].values()) for s in sp)),
        ("Zusatzgeräte Innensprechstellen", sum(len(s["zusatz"]) for s in sp)),
        ("PoE-Switches", len(modell["verteiler"])),
        ("PoE-Ports belegt (Kupfer)", len(hw) - lwl),
        ("LWL-Strecken Sprechstellen", lwl),
        ("LWL-Uplinks Verteiler", sum(1 for v in modell["verteiler"] if v["lwl"])),
        ("Medienkonverter LWL / Kupfer", lwl),
        ("PoE-Injektoren 230 V", lwl),
        ("230-V-Anschlüsse am Einbauort", lwl),
    ]
    y = Y0 + FUSS_H
    text(c, "Summen", X0 + 8, y - 11, 7, FB)
    for i, (label, wert) in enumerate(werte):
        x = X0 + 8 + (i // 5) * 235
        yy = y - 24 - (i % 5) * 11
        text(c, label, x, yy, 6.5)
        text(c, str(wert), x + 190, yy, 6.5, FB, anker="r")


# --------------------------------------------------------------------------
# Schnittstellenliste (Gewerke nach DIN 276)
# --------------------------------------------------------------------------
SPALTEN_SCHNITT = [("Nr.", 30), ("Schnittstelle", 170), ("Betroffene Geräte", 204),
                   ("Gegenstelle / Gegengewerk", 170), ("Art / Übergabepunkt", 220),
                   ("Anz.", 32), ("Koordination", 96), ("Anschluss", 96), ("Inbetriebnahme", 96)]
SPALTEN_KG = [("Kostengruppe", 80), ("Bezeichnung nach DIN 276", 300),
              ("Leistungsumfang in diesem Projekt", 734)]


def schnittstellenzeilen(modell):
    z = modell["zentrale"]
    sp = modell["spalten"]
    hw = [s for s in sp if s["typ"] != "client"]
    zeilen = []

    def neu(key, name, geraete, gegen, art, koord="KG 452", anschluss="KG 452", ibn="KG 452",
            anzahl=None):
        if not geraete:
            return
        u = modell["schnittstellen"].get(key, {})
        zeilen.append([name, ", ".join(geraete), u.get("gegengewerk", gegen), art,
                       str(len(geraete) if anzahl is None else anzahl),
                       u.get("koordination", koord), u.get("anschluss", anschluss),
                       u.get("inbetriebnahme", ibn)])

    netz = "KG 457 Datenübertragungsnetz"
    strom = "KG 444 Niederspannungsinstallation"
    lwl = [s["id"] for s in hw if s["lwl"]]
    neu("netz_poe", "Sprechstelle / IP-Relais - Datennetz (PoE)", [s["id"] for s in hw if not s["lwl"]], netz,
        "RJ45 am PoE-Switch-Port, Übergabe an Datendose / Patchfeld", anschluss="KG 457")
    neu("netz_lwl", "Sprechstelle / IP-Relais - Datennetz (LWL-Strecke)", lwl, netz,
        "LWL bis Medienkonverter am Einbauort, Übergabe RJ45", anschluss="KG 457", ibn="KG 457")
    neu("strom_lokal", "230-V-Einspeisung Medienkonverter / PoE-Injektor", lwl, strom,
        "230 V AC, Abzweigdose / Steckdose am Einbauort", anschluss="KG 444", ibn="KG 444")
    neu("strom_zentral", "230-V-Versorgung Server und PoE-Switches",
        ["Server"] + [v["id"] for v in modell["verteiler"]], strom,
        "230 V AC, Stromkreis im Technikraum / Verteiler", anschluss="KG 444", ibn="KG 444")
    neu("server_netz", "Intercom-Server - Core-Switch", ["Server"], netz,
        "RJ45 / IP, VLAN und Adressierung", anschluss="KG 457")
    neu("client_netz", "Software-Client - IP-Netz", [s["id"] for s in sp if s["typ"] == "client"],
        "Nutzer-IT / KG 457", "LAN / WLAN / Mobilfunk, Endgerät bauseits", anschluss="Nutzer-IT")
    if z["tk_anlage"]["vorhanden"]:
        neu("tk_anlage", "Intercom-Server - TK-Anlage", ["Server"], "KG 451 TK-Anlage",
            z["tk_anlage"]["schnittstelle"] + ", Rufnummernplan", anschluss="KG 451")
    if z["waehlgeraet"]["vorhanden"]:
        ziel = z["waehlgeraet"]["ziel"]
        if "ip" in z["waehlgeraet"]["wege"]:
            neu("waehlgeraet_ip", "Wählgerät - Übertragungsweg IP", ["Wählgerät"],
                "KG 457 / Provider, " + ziel, "IP über LAN / WAN, Freischaltung Firewall")
        if "mobil" in z["waehlgeraet"]["wege"]:
            neu("waehlgeraet_mobil", "Wählgerät - Übertragungsweg Mobilfunk", ["Wählgerät"],
                "Bauherr / Provider, " + ziel, "LTE / 5G, SIM-Karte bauseits")
    if z["glt"]["vorhanden"]:
        neu("glt", "Störmeldungen - GLT", ["Server"], "KG 480 Gebäudeautomation",
            z["glt"]["schnittstelle"] + ", Übergabeklemmen an der Zentrale",
            koord="KG 480", anschluss="KG 480", ibn="KG 480", anzahl=len(z["glt"]["meldungen"]))
    neu("zugangskontrolle", "Leser Zugangskontrolle in Sprechstelle",
        [s["id"] for s in hw if s["zk"]], "KG 456 Zugangskontrollanlage",
        "Leser in der Sprechstelle, Anbindung an ZK-Zentrale",
        koord="KG 456", anschluss="KG 456", ibn="KG 456")
    for f in FREIGABE_REIHENFOLGE:
        fremd = "KG 461" if f == "aufzug" else "KG 452"
        lokal = [s for s in hw if s["typ"] in LOKAL and f in s["freigabe"]]
        neu("freigabe_" + f, "Freigabe " + FREIGABE_KURZ[f] + " (Relaiskontakt)",
            [s["id"] for s in lokal], GEGENGEWERK_FREIGABE[f],
            "potentialfreier Kontakt, Übergabeklemme " + AKTOR[f], anschluss=fremd, ibn=fremd,
            anzahl=sum(s["anzahl"][f] for s in lokal))
    for ort in ("aufzug", "kassenautomat", "paketanlage"):
        gegen, art, kg = EINBAU_FREMD[ort]
        neu("einbau_" + ort, "Einbau Sprechstelle in " + EINBAUORTE[ort],
            [s["id"] for s in hw if s["typ"] == "aussen" and s["einbauort"] == ort],
            gegen, art, anschluss=kg)
    return [[str(i + 1)] + zl for i, zl in enumerate(zeilen)]


# --------------------------------------------------------------------------
# Listenblaetter: Tabellen fliessen untereinander, Kopf wird je Blatt wiederholt
# --------------------------------------------------------------------------
LISTE_OBEN = Y1 - 12
LISTE_UNTEN = Y0 + FUSS_H + 8
LISTE_X = COL_X0 - 2


def listen_fluss(modell):
    tabellen = [
        ("Geräte- und Leitungsliste", SPALTEN_LISTE, listenzeilen(modell), False),
        ("Schnittstellenliste - Zuständigkeit der Gewerke nach DIN 276 (Kostengruppen)",
         SPALTEN_SCHNITT, schnittstellenzeilen(modell), True),
        ("Gewerke nach DIN 276 (Kostengruppen)", SPALTEN_KG, [list(k) for k in KG_KATALOG], False),
    ]
    seiten = [[]]
    y = LISTE_OBEN
    for titel, spalten, zeilen, umbruch in tabellen:
        fertig = []
        for zl in zeilen:
            if umbruch:
                zellen = [umbrechen(w, F, 6.8, b - 6, 40) or [""] for w, (_, b) in zip(zl, spalten)]
            else:
                zellen = [[w] for w in zl]
            fertig.append((zellen, 14 + 8 * (max(len(z) for z in zellen) - 1)))
        if seiten[-1]:
            y -= 16
        kopf_offen = False
        for n, (zellen, h) in enumerate(fertig):
            if y - h - (0 if kopf_offen else 34) < LISTE_UNTEN:
                seiten.append([])
                y = LISTE_OBEN
                kopf_offen = False
            if not kopf_offen:
                seiten[-1].append(("titel", y, titel + ("  (Fortsetzung)" if n else "")))
                seiten[-1].append(("kopf", y - 34, spalten))
                y -= 34
                kopf_offen = True
            y -= h
            seiten[-1].append(("zeile", y, spalten, zellen, h, n % 2 == 0))
    return seiten


def listenseite(c, modell, ops):
    summen(c, modell)
    x = LISTE_X
    for op in ops:
        if op[0] == "titel":
            text(c, op[2], x, op[1] - 12, 9, FB)
        elif op[0] == "kopf":
            breite = sum(b for _, b in op[2])
            box(c, x, op[1], breite, 16, fill=C_AUSSEN, stroke=C_AUSSEN)
            xx = x
            for kopf_txt, b in op[2]:
                text(c, kopf_txt, xx + 3, op[1] + 5, 7, FB, C_WHITE, max_w=b - 6)
                xx += b
        else:
            _, y, spalten, zellen, h, grau = op
            breite = sum(b for _, b in spalten)
            if grau:
                box(c, x, y, breite, h, fill=C_FILL, stroke=C_FILL, lw=0.1)
            linie(c, [(x, y), (x + breite, y)], C_GREY, 0.3)
            xx = x
            for zelle, (_, b) in zip(zellen, spalten):
                yy = y + h - 10
                for zeile in zelle:
                    text(c, zeile, xx + 3, yy, 6.8, max_w=b - 6)
                    yy -= 8
                xx += b


# --------------------------------------------------------------------------
# Hauptprogramm
# --------------------------------------------------------------------------
def erzeugen(modell, ziel):
    sp = modell["spalten"]
    # Seitenumbruch: Verteiler bleiben zusammen, sofern sie auf ein Blatt passen
    seiten = [[]]
    for v in modell["gruppen"]:
        gruppe = [s for s in sp if s["verteiler"] == v["id"]]
        frei = SPALTEN_PRO_SEITE - len(seiten[-1])
        if len(gruppe) > frei and len(gruppe) <= SPALTEN_PRO_SEITE and seiten[-1]:
            seiten.append([])
        for s in gruppe:
            if len(seiten[-1]) == SPALTEN_PRO_SEITE:
                seiten.append([])
            seiten[-1].append(s)
    listen = listen_fluss(modell)
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
        rahmen(c, modell, "Gegensprechanlage - Geräte-, Leitungs- und Schnittstellenliste", blatt, blaetter)
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
    print("Eingabe gültig: %d Sprechstellen/Clients, %d Verteiler, LWL-Strecken (> %d m): %s"
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
