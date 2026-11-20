#!/usr/bin/env python3
"""Deterministischer Generator für Schmutzwasser-Anlagenschemata (PDF).

Eingabe:  JSON-Konfiguration (Projekt, Versorgerprofil, Übergabe, Stränge)
Ausgabe:  PDF-Fließschema (nicht maßstäblich) + Prüfprotokoll auf stdout

Blattrahmen, Plankopf, Änderungsindex und Legende zeichnet ausschließlich der
Skill mp-plankopf (MP_400_Plankopf); das Schema liegt in dessen Zeichenbereich.

Gleiche Eingabe erzeugt byte-identische PDF-Dateien (kein Zeitstempel,
keine Zufallswerte, feste Layoutregeln).

Exit-Codes: 0 = ok, 1 = Eingabefehler, 2 = Regelverstoß (keine PDF erzeugt)
"""
import argparse
import json
import os
import sys

from reportlab.lib.units import mm
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# --------------------------------------------------------------------------
# Stammdaten
# --------------------------------------------------------------------------
PK = None                      # Modul plankopf aus dem Skill mp-plankopf
SCHRIFT = {}                   # False -> Avenir Book, True -> Avenir Medium
RSE_STRICH = (1.5, 0.75)       # wie Legendeneintrag "gestrichelt"
LUEFTUNG_STRICH = (0.6, 1.2)   # wie Legendeneintrag "punktiert"


def lade_plankopf():
    """Importiert mp-plankopf. Es gibt bewusst keinen eigenen Ersatz-Plankopf."""
    global PK
    orte = [os.environ.get("MP_PLANKOPF_SKILL"), os.path.join(os.path.dirname(SKILL_DIR), "mp-plankopf"),
            os.path.join(os.path.expanduser("~"), ".claude", "skills", "mp-plankopf")]
    for ort in orte:
        if ort and os.path.isfile(os.path.join(ort, "scripts", "plankopf.py")):
            sys.path.insert(0, os.path.join(ort, "scripts"))
            import plankopf
            PK = plankopf
            try:
                PK._schrift()
            except PK.PlankopfFehler as e:
                raise Eingabefehler("Plankopf (mp-plankopf): %s" % e)
            SCHRIFT[False], SCHRIFT[True] = PK.FONT, PK._S["fett"]
            return
    raise Eingabefehler("Skill 'mp-plankopf' nicht gefunden. Erwartet neben diesem Skill (.claude/skills/"
                        "mp-plankopf), unter ~/.claude/skills/mp-plankopf oder im Ordner aus der "
                        "Umgebungsvariable MP_PLANKOPF_SKILL. Ohne ihn wird kein Plan erzeugt.")

# Abwasserart -> (Bezeichnung, Linienfarbe RGB)
ARTEN = {
    "haeuslich": ("Häusliches Schmutzwasser (fäkalienhaltig)", (0.45, 0.27, 0.10)),
    "grauwasser": ("Grauwasser (fäkalienfrei)", (0.45, 0.45, 0.45)),
    "fetthaltig": ("Fetthaltiges Abwasser", (0.90, 0.50, 0.00)),
    "staerkehaltig": ("Stärkehaltiges Abwasser", (0.70, 0.60, 0.00)),
    "mineraloelhaltig": ("Mineralölhaltiges Abwasser", (0.50, 0.15, 0.60)),
    "whg": ("Abwasser von WHG-/AwSV-Flächen", (0.80, 0.05, 0.10)),
    "sonstig": ("Sonstiges gewerbliches Abwasser", (0.10, 0.45, 0.55)),
}
ART_REIHENFOLGE = list(ARTEN)

C_SAMMEL = (0.0, 0.0, 0.0)
C_BW = (0.0, 0.55, 0.20)   # Betriebswasser
C_TW = (0.0, 0.35, 0.85)   # Trinkwasser
C_ROT = (0.80, 0.0, 0.0)
C_GRENZE = (0.2, 0.2, 0.2)

NUM = (int, float)
GEMEINSAM = {"typ": str, "id": str, "bezeichnung": str, "hinweis": str, "aks": str}

# Komponententyp -> (Standardname, zulässige Parameter {name: typ}, Pflichtparameter)
KOMPONENTEN = {
    "schlammfang": ("Schlammfang", {"volumen_l": NUM}, []),
    "fettabscheider": ("Fettabscheider", {"ns": NUM, "schlammfang_l": NUM, "fettspeicher_l": NUM}, ["ns"]),
    "staerkeabscheider": ("Stärkeabscheider", {"nenngroesse": NUM, "volumen_l": NUM}, []),
    "leichtfluessigkeitsabscheider": (
        "Leichtflüssigkeitsabscheider",
        {"ns": NUM, "klasse": str, "schlammfang_l": NUM, "warnanlage": bool, "selbsttaetiger_abschluss": bool},
        ["ns", "klasse"]),
    "probenahme": ("Probenahmeschacht", {"dn": NUM}, []),
    "kontrollschacht": ("Kontrollschacht", {"dn": NUM}, []),
    "hebeanlage": ("Hebeanlage",
                   {"ausfuehrung": str, "foerderstrom_ls": NUM, "foerderhoehe_m": NUM, "druckleitung_dn": NUM}, []),
    "rueckstauverschluss": ("Rückstauverschluss", {"ausfuehrung": str}, []),
    "absperrschieber": ("Absperrschieber", {"automatisch": bool}, []),
    "rueckhaltung": ("Rückhaltung", {"volumen_m3": NUM}, ["volumen_m3"]),
    "neutralisation": ("Neutralisationsanlage", {}, []),
    "emulsionsspaltanlage": ("Emulsionsspaltanlage", {}, []),
    "grauwasseranlage": ("Grauwasseranlage", {"kapazitaet_l_d": NUM, "verwendung": list, "zaehler": bool}, []),
    "sonstige": ("Behandlungsanlage", {"kuerzel": str, "vorbehandlung": bool}, ["kuerzel"]),
}
ALIASE = {  # Alias -> (typ, Vorgabewerte)
    "benzinabscheider": ("leichtfluessigkeitsabscheider", {"klasse": "II", "bezeichnung": "Benzinabscheider"}),
    "koaleszenzabscheider": ("leichtfluessigkeitsabscheider", {"klasse": "I", "bezeichnung": "Koaleszenzabscheider"}),
}
ABSCHEIDER = ("fettabscheider", "staerkeabscheider", "leichtfluessigkeitsabscheider")
KLASSE_RANG = {"II": 1, "I": 2}

VERSORGER_DEFAULT = {
    "name": "Versorger nicht angegeben",
    "satzung": "",
    "entwaesserungssystem": "trenn",          # trenn | misch
    "rueckstauebene": "Straßenoberkante an der Anschlussstelle",
    "uebergabeschacht": {"erforderlich": True, "dn_min": 1000, "lage": "auf dem Grundstück an der Grundstücksgrenze"},
    "anschlusskanal_dn_min": 150,
    "probenahme_an_uebergabe": False,
    "fettabscheider": {"pflicht_bei_fetthaltig": True, "probenahme_erforderlich": True},
    "staerkeabscheider": {"pflicht_bei_staerkehaltig": True, "ablauf_ueber_fettabscheider": "zulaessig"},
    "leichtfluessigkeitsabscheider": {"min_klasse": "II", "probenahme_erforderlich": True, "warnanlage_erforderlich": True},
    "whg": {"min_klasse": "I", "rueckhaltung_erforderlich": True, "absperrung_erforderlich": True,
            "probenahme_erforderlich": True},
    "grauwassernutzung": {"zulaessig": True, "anzeigepflicht": True, "zaehler_erforderlich": True},
    "hebeanlage": {"doppelanlage_bei_gewerbe": True},
    "hinweise": [],
}
VERSORGER_TYPEN = {
    "name": str, "satzung": str, "entwaesserungssystem": str, "rueckstauebene": str,
    "uebergabeschacht": {"erforderlich": bool, "dn_min": NUM, "lage": str},
    "anschlusskanal_dn_min": NUM, "probenahme_an_uebergabe": bool,
    "fettabscheider": {"pflicht_bei_fetthaltig": bool, "probenahme_erforderlich": bool},
    "staerkeabscheider": {"pflicht_bei_staerkehaltig": bool, "ablauf_ueber_fettabscheider": str},
    "leichtfluessigkeitsabscheider": {"min_klasse": str, "probenahme_erforderlich": bool,
                                      "warnanlage_erforderlich": bool},
    "whg": {"min_klasse": str, "rueckhaltung_erforderlich": bool, "absperrung_erforderlich": bool,
            "probenahme_erforderlich": bool},
    "grauwassernutzung": {"zulaessig": bool, "anzeigepflicht": bool, "zaehler_erforderlich": bool},
    "hebeanlage": {"doppelanlage_bei_gewerbe": bool},
    "hinweise": list,
}


class Eingabefehler(Exception):
    pass


def zahl(n):
    """Deterministische Zahlendarstellung mit Dezimalkomma."""
    if isinstance(n, bool):
        return "ja" if n else "nein"
    if isinstance(n, int) or float(n).is_integer():
        return str(int(n))
    return ("%.2f" % n).rstrip("0").rstrip(".").replace(".", ",")


# --------------------------------------------------------------------------
# Einlesen und strenge Validierung
# --------------------------------------------------------------------------
def pruefe_typ(wert, typ, pfad):
    if typ is NUM:
        ok = isinstance(wert, NUM) and not isinstance(wert, bool)
    else:
        ok = isinstance(wert, typ)
    if not ok:
        name = "Zahl" if typ is NUM else {str: "Text", bool: "true/false", list: "Liste", dict: "Objekt"}[typ]
        raise Eingabefehler("%s: erwartet %s, erhalten %r" % (pfad, name, wert))


def pruefe_objekt(obj, spec, pfad, pflicht=()):
    pruefe_typ(obj, dict, pfad)
    for k in sorted(obj):
        if k not in spec:
            raise Eingabefehler("%s: unbekannter Schlüssel '%s' (zulässig: %s)" % (pfad, k, ", ".join(sorted(spec))))
        if isinstance(spec[k], dict):
            pruefe_objekt(obj[k], spec[k], pfad + "." + k)
        else:
            pruefe_typ(obj[k], spec[k], pfad + "." + k)
    for k in pflicht:
        if k not in obj:
            raise Eingabefehler("%s: Pflichtangabe '%s' fehlt" % (pfad, k))


def lade_json(pfad):
    try:
        with open(pfad, encoding="utf-8") as f:
            return json.load(f)
    except OSError as e:
        raise Eingabefehler("Datei nicht lesbar: %s (%s)" % (pfad, e.strerror))
    except json.JSONDecodeError as e:
        raise Eingabefehler("Ungültiges JSON in %s: %s" % (pfad, e))


def lade_versorger(angabe, basis):
    """Versorgerprofil: Inline-Objekt, Dateipfad oder Profilname aus versorger/."""
    if isinstance(angabe, str):
        kandidaten = [os.path.join(basis, angabe), os.path.join(SKILL_DIR, "versorger", angabe),
                      os.path.join(SKILL_DIR, "versorger", angabe + ".json")]
        treffer = [k for k in kandidaten if os.path.isfile(k)]
        if not treffer:
            raise Eingabefehler("Versorgerprofil '%s' nicht gefunden" % angabe)
        angabe = lade_json(treffer[0])
    pruefe_objekt(angabe, VERSORGER_TYPEN, "versorger")
    v = {}
    for k, d in VERSORGER_DEFAULT.items():
        if isinstance(d, dict):
            v[k] = dict(d, **angabe.get(k, {}))
        else:
            v[k] = angabe.get(k, d)
    if v["entwaesserungssystem"] not in ("trenn", "misch"):
        raise Eingabefehler("versorger.entwaesserungssystem: 'trenn' oder 'misch'")
    if v["staerkeabscheider"]["ablauf_ueber_fettabscheider"] not in ("zulaessig", "erforderlich", "unzulaessig"):
        raise Eingabefehler("versorger.staerkeabscheider.ablauf_ueber_fettabscheider: "
                            "'zulaessig', 'erforderlich' oder 'unzulaessig'")
    for k in ("leichtfluessigkeitsabscheider", "whg"):
        if v[k]["min_klasse"] not in KLASSE_RANG:
            raise Eingabefehler("versorger.%s.min_klasse: 'I' oder 'II'" % k)
    for i, h in enumerate(v["hinweise"]):
        pruefe_typ(h, str, "versorger.hinweise[%d]" % i)
    return v


def lade_konfiguration(pfad):
    cfg = lade_json(pfad)
    pruefe_objekt(cfg, {"projekt": dict, "blatt": object, "plankopf": dict, "versorger": object, "uebergabe": dict,
                        "sammelleitung_dn": NUM, "straenge": list, "hinweise": list, "planungsgrundsaetze": list},
                  "konfiguration", pflicht=("projekt", "versorger", "uebergabe", "straenge"))
    pruefe_objekt(cfg["projekt"], {"name": str, "standort": str, "plan_nr": str, "index": str, "datum": str,
                                   "bearbeiter": str, "bauherr": str}, "projekt", pflicht=("name", "datum"))
    cfg.setdefault("plankopf", {})
    # Ohne 'blatt' gilt A2 - außer das Format kommt als Familienparameter (A0/A1/A2/Blattgröße) über felder
    fam = cfg["plankopf"].get("felder", {}) if isinstance(cfg["plankopf"], dict) else {}
    fam_blatt = isinstance(fam, dict) and any(k in fam for k in ("A0", "A1", "A2", "Blattgröße"))
    cfg.setdefault("blatt", None if fam_blatt else "A2")
    if isinstance(cfg["blatt"], str):
        cfg["blatt"] = {"format": cfg["blatt"]}
    pruefe_objekt(cfg["plankopf"], {"logo": object, "felder": dict, "zellen": dict, "freifelder": dict, "aenderungsindex": dict,
                                    "zusatzfelder": list, "optionen": dict}, "plankopf")
    cfg["versorger"] = lade_versorger(cfg["versorger"], os.path.dirname(os.path.abspath(pfad)))
    pruefe_objekt(cfg["uebergabe"], {"schacht": bool, "dn": NUM, "anschlusskanal_dn": NUM, "probenahme": bool,
                                     "bezeichnung": str, "aks": str}, "uebergabe", pflicht=("schacht", "anschlusskanal_dn"))
    for liste in ("hinweise", "planungsgrundsaetze"):
        cfg.setdefault(liste, [])
        for i, h in enumerate(cfg[liste]):
            pruefe_typ(h, str, "%s[%d]" % (liste, i))

    if not cfg["straenge"]:
        raise Eingabefehler("straenge: mindestens ein Strang erforderlich")
    ids = set()
    for i, s in enumerate(cfg["straenge"]):
        p = "straenge[%d]" % i
        pruefe_objekt(s, {"id": str, "bezeichnung": str, "abwasserart": str, "quellen": list, "dn": NUM,
                          "unter_rueckstauebene": bool, "komponenten": list, "ziel": dict},
                      p, pflicht=("id", "bezeichnung", "abwasserart"))
        if s["id"] in ids:
            raise Eingabefehler("%s: Strang-ID '%s' doppelt" % (p, s["id"]))
        ids.add(s["id"])
        if s["abwasserart"] not in ARTEN:
            raise Eingabefehler("%s.abwasserart: zulässig sind %s" % (p, ", ".join(ARTEN)))
        s.setdefault("quellen", [])
        s.setdefault("unter_rueckstauebene", False)
        s.setdefault("komponenten", [])
        for q in s["quellen"]:
            pruefe_typ(q, str, p + ".quellen[]")
        komp = []
        for j, k in enumerate(s["komponenten"]):
            kp = "%s.komponenten[%d]" % (p, j)
            pruefe_typ(k, dict, kp)
            if "typ" not in k:
                raise Eingabefehler("%s: 'typ' fehlt" % kp)
            k = dict(k)
            if k["typ"] in ALIASE:
                typ, vorgabe = ALIASE[k["typ"]]
                k = dict(vorgabe, **k)
                k["typ"] = typ
            if k["typ"] not in KOMPONENTEN:
                raise Eingabefehler("%s.typ: unbekannt '%s' (zulässig: %s)" % (
                    kp, k["typ"], ", ".join(sorted(list(KOMPONENTEN) + list(ALIASE)))))
            name, params, pflicht = KOMPONENTEN[k["typ"]]
            pruefe_objekt(k, dict(GEMEINSAM, **params), kp, pflicht=pflicht)
            k.setdefault("bezeichnung", name)
            if k["typ"] == "leichtfluessigkeitsabscheider":
                if k["klasse"] not in KLASSE_RANG:
                    raise Eingabefehler("%s.klasse: 'I' oder 'II'" % kp)
                k.setdefault("warnanlage", True)
                k.setdefault("selbsttaetiger_abschluss", True)
            if k["typ"] == "hebeanlage":
                k.setdefault("ausfuehrung", "einzel")
                if k["ausfuehrung"] not in ("einzel", "doppel"):
                    raise Eingabefehler("%s.ausfuehrung: 'einzel' oder 'doppel'" % kp)
            if k["typ"] == "grauwasseranlage":
                k.setdefault("zaehler", True)
                k.setdefault("verwendung", ["WC-Spülung"])
                for w in k["verwendung"]:
                    pruefe_typ(w, str, kp + ".verwendung[]")
            komp.append(k)
        s["komponenten"] = komp

    # Ziele (Einleitung in anderen Strang vor einer Komponente) auflösen
    nach_id = {s["id"]: s for s in cfg["straenge"]}
    for s in cfg["straenge"]:
        z = s.get("ziel")
        if z is None:
            continue
        p = "strang %s.ziel" % s["id"]
        pruefe_objekt(z, {"strang": str, "vor": str}, p, pflicht=("strang", "vor"))
        if z["strang"] == s["id"] or z["strang"] not in nach_id:
            raise Eingabefehler("%s.strang: unbekannter oder eigener Strang '%s'" % (p, z["strang"]))
        idx = [n for n, k in enumerate(nach_id[z["strang"]]["komponenten"]) if k.get("id") == z["vor"]]
        if len(idx) != 1:
            raise Eingabefehler("%s.vor: Komponenten-ID '%s' in Strang %s nicht (eindeutig) vorhanden"
                                % (p, z["vor"], z["strang"]))
        z["index"] = idx[0]
    for s in cfg["straenge"]:  # Zyklen ausschließen
        gesehen, akt = set(), s
        while akt.get("ziel"):
            if akt["id"] in gesehen:
                raise Eingabefehler("Zyklische Strang-Ziele bei Strang %s" % s["id"])
            gesehen.add(akt["id"])
            akt = nach_id[akt["ziel"]["strang"]]
    return cfg


# --------------------------------------------------------------------------
# Regelprüfung
# --------------------------------------------------------------------------
def fliessweg(cfg, s):
    """Alle Komponenten, die das Abwasser des Strangs bis zur Sammelleitung durchfließt."""
    nach_id = {x["id"]: x for x in cfg["straenge"]}
    weg = list(s["komponenten"])
    akt = s
    while akt.get("ziel"):
        ziel = nach_id[akt["ziel"]["strang"]]
        weg += ziel["komponenten"][akt["ziel"]["index"]:]
        akt = ziel
    return weg


def pruefe_regeln(cfg):
    """Liefert Liste (stufe, code, text); stufe in FEHLER/WARNUNG/HINWEIS."""
    v, u, erg = cfg["versorger"], cfg["uebergabe"], []

    def add(stufe, code, text):
        erg.append((stufe, code, text))

    # Übergabe
    vs = v["uebergabeschacht"]
    if vs["erforderlich"] and not u["schacht"]:
        add("FEHLER", "U01", "Versorger verlangt einen Übergabeschacht (%s); keiner vorgesehen." % vs["lage"])
    if u["schacht"]:
        if "dn" not in u:
            add("FEHLER", "U02", "Übergabeschacht ohne Angabe 'dn'.")
        elif u["dn"] < vs["dn_min"]:
            add("FEHLER", "U02", "Übergabeschacht DN %s kleiner als Mindestmaß DN %s des Versorgers."
                % (zahl(u["dn"]), zahl(vs["dn_min"])))
    if u["anschlusskanal_dn"] < v["anschlusskanal_dn_min"]:
        add("FEHLER", "U03", "Anschlusskanal DN %s kleiner als Mindestnennweite DN %s des Versorgers."
            % (zahl(u["anschlusskanal_dn"]), zahl(v["anschlusskanal_dn_min"])))
    if v["probenahme_an_uebergabe"] and not u.get("probenahme", False):
        add("FEHLER", "U04", "Versorger verlangt eine Probenahmemöglichkeit an der Übergabestelle.")

    for s in cfg["straenge"]:
        sid, art = "Strang %s" % s["id"], s["abwasserart"]
        weg = fliessweg(cfg, s)
        typen = [k["typ"] for k in weg]

        def nach(typ_a, typ_b):
            """Gibt es typ_b hinter dem ersten typ_a im Fließweg?"""
            return typ_a in typen and typ_b in typen[typen.index(typ_a) + 1:]

        # Rückstau
        if s["unter_rueckstauebene"]:
            if "hebeanlage" not in typen:
                if "rueckstauverschluss" not in typen:
                    add("FEHLER", "R01", "%s liegt unter der Rückstauebene, hat aber weder Hebeanlage noch "
                        "Rückstauverschluss (DIN EN 12056-4, DIN 1986-100)." % sid)
                elif any(t in ABSCHEIDER for t in typen):
                    add("FEHLER", "R02", "%s: Abscheideranlagen unter der Rückstauebene sind über eine Hebeanlage "
                        "zu entwässern; Rückstauverschluss genügt nicht." % sid)
                else:
                    add("WARNUNG", "R03", "%s: Rückstauverschluss statt Hebeanlage nur unter den Voraussetzungen "
                        "der DIN EN 12056-4 (Gefälle zum Kanal, untergeordnete Nutzung) zulässig." % sid)
        # Hebeanlage nicht vor Abscheidern
        if "hebeanlage" in typen:
            hinter = typen[typen.index("hebeanlage") + 1:]
            if any(t in ABSCHEIDER for t in hinter):
                add("FEHLER", "R04", "%s: Hebeanlage liegt vor einer Abscheideranlage; Pumpen vor Abscheidern "
                    "sind unzulässig (Emulsionsbildung). Hebeanlage hinter den Abscheider setzen." % sid)
        if v["hebeanlage"]["doppelanlage_bei_gewerbe"] and art not in ("haeuslich", "grauwasser"):
            for k in s["komponenten"]:
                if k["typ"] == "hebeanlage" and k["ausfuehrung"] != "doppel":
                    add("FEHLER", "R05", "%s: Versorger verlangt bei gewerblichem Abwasser eine Doppelhebeanlage."
                        % sid)
        # Fäkalien nicht über Abscheider
        if art == "haeuslich" and any(t in ABSCHEIDER for t in typen):
            add("FEHLER", "A01", "%s: Fäkalienhaltiges Abwasser darf nicht über Abscheideranlagen geführt werden."
                % sid)
        # Fett
        if art == "fetthaltig":
            if "fettabscheider" not in typen:
                add("FEHLER" if v["fettabscheider"]["pflicht_bei_fetthaltig"] else "WARNUNG", "F01",
                    "%s: Fetthaltiges Abwasser ohne Fettabscheider (DIN EN 1825, DIN 4040-100)." % sid)
            elif v["fettabscheider"]["probenahme_erforderlich"] and not nach("fettabscheider", "probenahme"):
                add("FEHLER", "F02", "%s: Versorger verlangt Probenahmemöglichkeit hinter dem Fettabscheider." % sid)
        # Stärke
        if art == "staerkehaltig":
            modus = v["staerkeabscheider"]["ablauf_ueber_fettabscheider"]
            if "staerkeabscheider" not in typen:
                add("FEHLER" if v["staerkeabscheider"]["pflicht_bei_staerkehaltig"] else "WARNUNG", "S01",
                    "%s: Stärkehaltiges Abwasser ohne Stärkeabscheider." % sid)
            elif modus == "erforderlich" and not nach("staerkeabscheider", "fettabscheider"):
                add("FEHLER", "S02", "%s: Versorger verlangt, den Ablauf des Stärkeabscheiders über den "
                    "Fettabscheider zu führen." % sid)
            elif modus == "unzulaessig" and nach("staerkeabscheider", "fettabscheider"):
                add("FEHLER", "S03", "%s: Versorger lässt die Einleitung des Stärkeabscheider-Ablaufs in den "
                    "Fettabscheider nicht zu." % sid)
        # Mineralöl / WHG
        if art in ("mineraloelhaltig", "whg"):
            vl = v["leichtfluessigkeitsabscheider"] if art == "mineraloelhaltig" else v["whg"]
            lfa = [k for k in weg if k["typ"] == "leichtfluessigkeitsabscheider"]
            if art == "mineraloelhaltig" and not lfa:
                add("FEHLER", "L01", "%s: Mineralölhaltiges Abwasser ohne Leichtflüssigkeitsabscheider "
                    "(DIN EN 858, DIN 1999-100)." % sid)
            for k in lfa:
                if KLASSE_RANG[k["klasse"]] < KLASSE_RANG[vl["min_klasse"]]:
                    add("FEHLER", "L02", "%s: Abscheider Klasse %s genügt nicht; gefordert ist Klasse %s."
                        % (sid, k["klasse"], vl["min_klasse"]))
                if not k["selbsttaetiger_abschluss"]:
                    add("FEHLER", "L03", "%s: Leichtflüssigkeitsabscheider ohne selbsttätigen Abschluss "
                        "(DIN EN 858-1)." % sid)
                if v["leichtfluessigkeitsabscheider"]["warnanlage_erforderlich"] and not k["warnanlage"]:
                    add("FEHLER", "L04", "%s: Versorger verlangt eine Warnanlage am "
                        "Leichtflüssigkeitsabscheider." % sid)
            if lfa and vl["probenahme_erforderlich"] and not nach("leichtfluessigkeitsabscheider", "probenahme"):
                add("FEHLER", "L05", "%s: Versorger verlangt Probenahmemöglichkeit hinter dem "
                    "Leichtflüssigkeitsabscheider." % sid)
        if art == "whg":
            vw = v["whg"]
            vorb = [k for k in weg if k["typ"] in ("leichtfluessigkeitsabscheider", "neutralisation",
                                                   "emulsionsspaltanlage")
                    or (k["typ"] == "sonstige" and k.get("vorbehandlung", False))]
            if not vorb:
                add("FEHLER", "W01", "%s: Abwasser von WHG-Flächen ohne Vorbehandlung (Abscheider, Neutralisation, "
                    "Emulsionsspaltanlage o. glw.)." % sid)
            elif vw["probenahme_erforderlich"] and "probenahme" not in typen[typen.index(vorb[0]["typ"]) + 1:]:
                add("FEHLER", "W04", "%s: Versorger verlangt Probenahmemöglichkeit hinter der Vorbehandlung." % sid)
            if vw["rueckhaltung_erforderlich"] and "rueckhaltung" not in typen:
                add("FEHLER", "W02", "%s: Versorger/AwSV verlangt ein Rückhaltevolumen für WHG-Flächen." % sid)
            if vw["absperrung_erforderlich"] and "absperrschieber" not in typen:
                add("FEHLER", "W03", "%s: Absperreinrichtung (Havarieschieber) für WHG-Fläche fehlt." % sid)
        # Grauwasser
        for k in s["komponenten"]:
            if k["typ"] != "grauwasseranlage":
                continue
            vg = v["grauwassernutzung"]
            if art != "grauwasser":
                add("FEHLER", "G01", "%s: Grauwasseranlage nur in Strängen der Abwasserart 'grauwasser'." % sid)
            if not vg["zulaessig"]:
                add("FEHLER", "G02", "%s: Der Versorger lässt Grauwassernutzung am Standort nicht zu." % sid)
            if vg["zaehler_erforderlich"] and not k["zaehler"]:
                add("FEHLER", "G03", "%s: Versorger verlangt Mengenerfassung (Zähler) der Grauwasser-/"
                    "Betriebswassernutzung." % sid)
            if vg["anzeigepflicht"]:
                add("HINWEIS", "G04", "%s: Grauwassernutzung ist dem Versorger und dem Gesundheitsamt anzuzeigen; "
                    "Betriebswassernetz kennzeichnen, keine Verbindung zum Trinkwassernetz." % sid)
    return erg


# --------------------------------------------------------------------------
# Layout
# --------------------------------------------------------------------------
HINWEIS_H = 30.0     # Höhe Prüfergebnisfeld unten im Zeichenbereich
QUELLE_B, QUELLE_H = 48.0, 18.0
RASTER = 36.0        # Spaltenraster Komponenten
KB, KH = 24.0, 14.0  # Komponenten-Slot
BAHN_MIN, BAHN_MAX = 28.0, 34.0
UEBERGABE_B = 78.0   # Breite rechts der Sammelleitung (Übergabe, Grenze, Kanal)
MASSSTAB_MAX = 3.0   # größte Vergrößerung des Schemas auf großen Blättern


def berechne_layout(cfg, pk_layout):
    """Ordnet das Schema im Zeichenbereich an, den mp-plankopf meldet.

    Das Schema wird einheitlich vergrößert (Faktor 1 bis MASSSTAB_MAX), bis es den Zeichenbereich
    in Breite oder Höhe füllt. Alle Layoutmaße gelten im lokalen System (Ursprung = untere linke
    Ecke des Zeichenbereichs, Einheit mm vor der Vergrößerung).
    """
    zb = pk_layout["zonen"]["zeichenbereich"]
    blatt = pk_layout["blatt"]["format"]
    if zb is None:
        raise Eingabefehler("Blatt %s hat neben dem Plankopf keinen Zeichenbereich. Größeres 'blatt' wählen." % blatt)

    # Spaltenzuordnung (Zuläufe aus anderen Strängen schieben die Zielkomponente nach rechts)
    nach_id = {s["id"]: s for s in cfg["straenge"]}
    zulauf = {}
    for s in cfg["straenge"]:
        if s.get("ziel"):
            zulauf.setdefault((s["ziel"]["strang"], s["ziel"]["index"]), []).append(s["id"])
    spalten = {}

    def ordne(sid):
        if sid in spalten:
            return spalten[sid]
        cols, c = [], -1
        for i, _ in enumerate(nach_id[sid]["komponenten"]):
            c += 1
            for z in zulauf.get((sid, i), []):
                zc = ordne(z)
                c = max(c, (zc[-1] + 1) if zc else 0)
            cols.append(c)
        spalten[sid] = cols
        return cols

    for s in cfg["straenge"]:
        ordne(s["id"])
    n = len(cfg["straenge"])
    benoetigt = max([c[-1] + 1 for c in spalten.values() if c] or [0])

    fest_b = 6 + QUELLE_B + 10 + 8 + UEBERGABE_B   # Breite ohne Komponentenspalten
    fest_h = 12 + 28 + HINWEIS_H                   # Höhe ohne Strangbahnen
    f = min(zb["breite"] / (fest_b + max(benoetigt, 2) * RASTER), zb["hoehe"] / (fest_h + n * BAHN_MAX))
    f = max(1.0, min(MASSSTAB_MAX, int(f * 1000) / 1000.0))   # abrunden: Inhalt passt sicher
    breite, hoehe = zb["breite"] / f, zb["hoehe"] / f

    L = {"W": pk_layout["blatt"]["breite_mm"], "H": pk_layout["blatt"]["hoehe_mm"], "blatt": blatt,
         "ursprung": (zb["x"], zb["y"]), "faktor": f, "zx": 0.0, "zy": 0.0, "zb": breite, "zo": hoehe,
         "spalten": spalten}
    L["x0"] = 6.0
    L["xk0"] = L["x0"] + QUELLE_B + 10
    L["xs"] = breite - UEBERGABE_B              # Sammelleitung
    L["ytop"] = hoehe - 12
    L["ybot"] = HINWEIS_H + 28
    L["yaus"] = HINWEIS_H + 15                  # Höhe Grundleitung zur Übergabe
    bahn = min(BAHN_MAX, (L["ytop"] - L["ybot"]) / n)
    if bahn < BAHN_MIN:
        raise Eingabefehler("%d Stränge passen nicht auf Blatt %s (max. %d). Größeres 'blatt' wählen."
                            % (n, blatt, max(0, int((L["ytop"] - L["ybot"]) // BAHN_MIN))))
    L["y"] = {s["id"]: L["ytop"] - (i + 0.5) * bahn for i, s in enumerate(cfg["straenge"])}
    max_sp = max(0, int((L["xs"] - 8 - L["xk0"] + 1e-6) // RASTER))
    if benoetigt > max_sp:
        raise Eingabefehler("Es werden %d Komponentenspalten benötigt, Blatt %s bietet %d. Größeres 'blatt' wählen."
                            % (benoetigt, blatt, max_sp))
    return L


def slot_x(L, spalte):
    return L["xk0"] + spalte * RASTER


# --------------------------------------------------------------------------
# Zeichnen
# --------------------------------------------------------------------------
class Zeichner:
    def __init__(self, c):
        self.c = c

    # --- Grundelemente (alle Maße in mm) ---
    def farbe(self, rgb, fuell=None):
        self.c.setStrokeColorRGB(*rgb)
        self.c.setFillColorRGB(*(fuell if fuell is not None else rgb))

    def linie(self, pts, rgb=(0, 0, 0), b=0.35, strich=None):
        c = self.c
        c.setStrokeColorRGB(*rgb)
        c.setLineWidth(b * mm)
        c.setDash([d * mm for d in strich] if strich else [])
        p = c.beginPath()
        p.moveTo(pts[0][0] * mm, pts[0][1] * mm)
        for x, y in pts[1:]:
            p.lineTo(x * mm, y * mm)
        c.drawPath(p, stroke=1, fill=0)
        c.setDash([])

    def rechteck(self, x, y, w, h, rgb=(0, 0, 0), b=0.35, fuell=(1, 1, 1), radius=0):
        c = self.c
        c.setLineWidth(b * mm)
        c.setStrokeColorRGB(*rgb)
        if fuell is not None:
            c.setFillColorRGB(*fuell)
        if radius:
            c.roundRect(x * mm, y * mm, w * mm, h * mm, radius * mm, stroke=1, fill=fuell is not None)
        else:
            c.rect(x * mm, y * mm, w * mm, h * mm, stroke=1, fill=fuell is not None)

    def weiss(self, x, y, w, h):
        self.c.setFillColorRGB(1, 1, 1)
        self.c.rect(x * mm, y * mm, w * mm, h * mm, stroke=0, fill=1)

    def kreis(self, x, y, r, rgb=(0, 0, 0), b=0.35, fuell=(1, 1, 1)):
        c = self.c
        c.setLineWidth(b * mm)
        c.setStrokeColorRGB(*rgb)
        c.setFillColorRGB(*fuell)
        c.circle(x * mm, y * mm, r * mm, stroke=1, fill=1)

    def polygon(self, pts, rgb=(0, 0, 0), fuell=None, b=0.3):
        c = self.c
        c.setLineWidth(b * mm)
        c.setStrokeColorRGB(*rgb)
        if fuell is not None:
            c.setFillColorRGB(*fuell)
        p = c.beginPath()
        p.moveTo(pts[0][0] * mm, pts[0][1] * mm)
        for x, y in pts[1:]:
            p.lineTo(x * mm, y * mm)
        p.close()
        c.drawPath(p, stroke=1, fill=fuell is not None)

    def pfeil(self, x, y, richtung, rgb, g=1.6):
        """Gefüllte Pfeilspitze mit Spitze in (x, y); richtung: r, l, o, u."""
        d = {"r": [(-g, g * 0.5), (-g, -g * 0.5)], "l": [(g, g * 0.5), (g, -g * 0.5)],
             "o": [(-g * 0.5, -g), (g * 0.5, -g)], "u": [(-g * 0.5, g), (g * 0.5, g)]}[richtung]
        self.polygon([(x, y)] + [(x + a, y + b) for a, b in d], rgb, fuell=rgb, b=0.1)

    def text(self, x, y, s, gr=6.0, fett=False, rgb=(0, 0, 0), aus="l", maxb=None, min_gr=3.6, winkel=0):
        """Text; bei maxb (mm) wird die Schrift verkleinert, danach gekürzt."""
        c = self.c
        font = SCHRIFT[bool(fett)]
        s = "".join(ch if ord(ch) in PK._S["glyphen"] else "?" for ch in s)
        if maxb is not None:
            while gr > min_gr and stringWidth(s, font, gr) > maxb * mm:
                gr = round(gr - 0.2, 1)
            if stringWidth(s, font, gr) > maxb * mm:
                while len(s) > 1 and stringWidth(s + "...", font, gr) > maxb * mm:
                    s = s[:-1]
                s += "..."
        c.setFont(font, gr)
        c.setFillColorRGB(*rgb)
        if winkel:
            c.saveState()
            c.translate(x * mm, y * mm)
            c.rotate(winkel)
            x = y = 0
        f = {"l": c.drawString, "m": c.drawCentredString, "r": c.drawRightString}[aus]
        f(x * mm, y * mm, s)
        if winkel:
            c.restoreState()

    def umbruch(self, s, fett, gr, maxb):
        font = SCHRIFT[bool(fett)]
        zeilen, akt = [], ""
        for wort in s.split():
            neu = (akt + " " + wort).strip()
            if akt and stringWidth(neu, font, gr) > maxb * mm:
                zeilen.append(akt)
                akt = wort
            else:
                akt = neu
        return zeilen + ([akt] if akt else [])

    # --- Komponentensymbole: Slot links x, Achse y, Breite KB ---
    def kasten(self, x, y, teile, rgb):
        """Rechteckbehälter mit senkrechten Unterteilungen und Kürzeln."""
        self.rechteck(x, y - KH / 2, KB, KH, rgb, b=0.5)
        b = KB / len(teile)
        for i, t in enumerate(teile):
            if i:
                self.linie([(x + i * b, y - KH / 2), (x + i * b, y + KH / 2)], rgb, 0.35)
            self.text(x + (i + 0.5) * b, y - 1.6, t, 8 if len(t) < 3 else 6.5, True, rgb, "m", maxb=b - 1.5)

    def lueftung(self, x, y, rgb):
        self.linie([(x, y + KH / 2), (x, y + KH / 2 + 4.5)], rgb, 0.3, strich=LUEFTUNG_STRICH)
        self.text(x + 0.8, y + KH / 2 + 2.6, "Lü", 4.2, rgb=rgb)

    def komponente(self, k, x, y, rgb):
        t, mx = k["typ"], x + KB / 2
        self.weiss(x, y - 1.2, KB, 2.4)
        stutzen = lambda r: (self.linie([(x, y), (mx - r, y)], rgb, 0.6),
                             self.linie([(mx + r, y), (x + KB, y)], rgb, 0.6))
        if t == "schlammfang":
            self.kasten(x, y, ["S"], rgb)
        elif t == "fettabscheider":
            self.kasten(x, y, ["S", "F"], rgb)
            self.lueftung(x + 4, y, rgb)
        elif t == "staerkeabscheider":
            self.kasten(x, y, ["St"], rgb)
            self.lueftung(x + 4, y, rgb)
        elif t == "leichtfluessigkeitsabscheider":
            self.kasten(x, y, ["S", "B", "K"] if k["klasse"] == "I" else ["S", "B"], rgb)
            if k["warnanlage"]:
                self.linie([(x + KB - 4, y + KH / 2), (x + KB - 4, y + KH / 2 + 3)], rgb, 0.3)
                self.kreis(x + KB - 4, y + KH / 2 + 4.3, 1.3, rgb, 0.3)
                self.text(x + KB - 4, y + KH / 2 + 3.7, "W", 3.6, True, rgb, "m")
        elif t in ("neutralisation", "emulsionsspaltanlage", "sonstige"):
            kz = {"neutralisation": "pH", "emulsionsspaltanlage": "ESA"}.get(t) or k["kuerzel"]
            self.kasten(x, y, [kz], rgb)
        elif t in ("probenahme", "kontrollschacht"):
            stutzen(5.5)
            self.kreis(mx, y, 5.5, rgb, 0.5)
            self.text(mx, y - 1.6, "P" if t == "probenahme" else "KS", 8, True, rgb, "m")
        elif t == "rueckhaltung":
            stutzen(KB / 2 - 2)
            self.weiss(x + 2, y - KH / 2, KB - 4, KH)
            self.linie([(x + 2, y + KH / 2), (x + 2, y - KH / 2), (x + KB - 2, y - KH / 2), (x + KB - 2, y + KH / 2)],
                       rgb, 0.6)
            self.linie([(x + 2, y + 2.5), (x + KB - 2, y + 2.5)], rgb, 0.25, strich=(1.2, 0.8))
            self.text(mx, y - 4, "RH", 8, True, rgb, "m")
        elif t in ("absperrschieber", "rueckstauverschluss"):
            stutzen(5)
            links = [(mx - 5, y + 3.5), (mx - 5, y - 3.5), (mx, y)]
            rechts = [(mx + 5, y + 3.5), (mx + 5, y - 3.5), (mx, y)]
            self.polygon(links, rgb, fuell=(1, 1, 1), b=0.5)
            self.polygon(rechts, rgb, fuell=rgb if t == "rueckstauverschluss" else (1, 1, 1), b=0.5)
            if t == "absperrschieber":
                self.linie([(mx, y), (mx, y + 5.5)], rgb, 0.4)
                self.linie([(mx - 2.5, y + 5.5), (mx + 2.5, y + 5.5)], rgb, 0.5)
                if k.get("automatisch"):
                    self.kreis(mx, y + 7.2, 1.7, rgb, 0.35)
                    self.text(mx, y + 6.5, "M", 4, True, rgb, "m")
        elif t == "hebeanlage":
            self.weiss(x + 14, y - 1.2, KB - 14, 2.4)
            self.rechteck(x, y - KH / 2, 14, KH, rgb, b=0.5)
            pumpen = [x + 7] if k["ausfuehrung"] == "einzel" else [x + 4, x + 10]
            for px in pumpen:
                self.kreis(px, y - 1.5, 2.6, rgb, 0.4)
                self.polygon([(px - 1.5, y - 2.7), (px + 1.5, y - 2.7), (px, y + 0.6)], rgb, fuell=rgb, b=0.1)
            self.lueftung(x + 2.5, y, rgb)
            # Druckleitung mit Rückstauschleife über die Rückstauebene
            self.linie([(x + 14, y), (x + 16.5, y), (x + 16.5, y + 11), (x + 21, y + 11), (x + 21, y), (x + KB, y)],
                       rgb, 0.6)
            self.linie([(x + 13, y + 8.5), (x + KB + 2, y + 8.5)], C_TW, 0.45, strich=RSE_STRICH)
            self.text(x + KB + 2.5, y + 7.8, "RSE", 4.2, True, C_TW)
        elif t == "grauwasseranlage":
            self.kasten(x, y, ["GW"], rgb)
            self.linie([(x + 6, y + KH / 2), (x + 6, y + KH / 2 + 5)], C_BW, 0.5)
            self.pfeil(x + 6, y + KH / 2 + 5.6, "o", C_BW)
            self.text(x + 7.4, y + KH / 2 + 2.6, "BW", 4.4, True, C_BW)
            self.linie([(x + 17, y + KH / 2 + 5.6), (x + 17, y + KH / 2 + 1.2)], C_TW, 0.5)
            self.pfeil(x + 17, y + KH / 2, "u", C_TW)
            self.text(x + 18.4, y + KH / 2 + 2.6, "TW", 4.4, True, C_TW)

    def beschriftung(self, k, x, y):
        zeilen = [(k["bezeichnung"] + (" (%s)" % k["id"] if k.get("id") else ""), True)]
        t, p = k["typ"], []
        if t == "fettabscheider":
            p = ["NS %s" % zahl(k["ns"])]
            if "schlammfang_l" in k:
                p.append("SF %s l" % zahl(k["schlammfang_l"]))
            if "fettspeicher_l" in k:
                p.append("Fett %s l" % zahl(k["fettspeicher_l"]))
            norm = "DIN EN 1825 / DIN 4040-100"
        elif t == "leichtfluessigkeitsabscheider":
            p = ["Klasse %s" % k["klasse"], "NS %s" % zahl(k["ns"])]
            if "schlammfang_l" in k:
                p.append("SF %s l" % zahl(k["schlammfang_l"]))
            norm = "DIN EN 858 / DIN 1999-100"
        elif t == "staerkeabscheider":
            if "nenngroesse" in k:
                p.append("NG %s" % zahl(k["nenngroesse"]))
            if "volumen_l" in k:
                p.append("V %s l" % zahl(k["volumen_l"]))
            norm = "nach Herstellerangabe"
        elif t == "hebeanlage":
            p = ["Doppelanlage" if k["ausfuehrung"] == "doppel" else "Einzelanlage"]
            if "foerderstrom_ls" in k:
                p.append("Q %s l/s" % zahl(k["foerderstrom_ls"]))
            if "foerderhoehe_m" in k:
                p.append("H %s m" % zahl(k["foerderhoehe_m"]))
            if "druckleitung_dn" in k:
                p.append("DN %s" % zahl(k["druckleitung_dn"]))
            norm = "DIN EN 12050 / DIN EN 12056-4"
        elif t == "rueckhaltung":
            p, norm = ["V %s m³" % zahl(k["volumen_m3"])], "AwSV / TRwS"
        elif t == "grauwasseranlage":
            if "kapazitaet_l_d" in k:
                p.append("%s l/d" % zahl(k["kapazitaet_l_d"]))
            p.append("BW: " + ", ".join(k["verwendung"]))
            norm = "DIN EN 16941-2" + ("; mit Zähler" if k["zaehler"] else "")
        elif t == "rueckstauverschluss":
            p, norm = ([k["ausfuehrung"]] if "ausfuehrung" in k else []), "DIN EN 13564"
        elif t == "absperrschieber":
            p, norm = (["motorisch/automatisch"] if k.get("automatisch") else ["handbetätigt"]), ""
        elif t in ("probenahme", "kontrollschacht", "schlammfang"):
            if "dn" in k:
                p.append("DN %s" % zahl(k["dn"]))
            if "volumen_l" in k:
                p.append("V %s l" % zahl(k["volumen_l"]))
            norm = ""
        else:
            norm = ""
        if k.get("aks"):
            zeilen.append(("AKS " + k["aks"], False))
        if p:
            zeilen.append((", ".join(p), False))
        if k.get("hinweis"):
            zeilen.append((k["hinweis"], False))
        elif norm:
            zeilen.append((norm, False))
        for i, (z, fett) in enumerate(zeilen[:3]):
            self.text(x + KB / 2, y - KH / 2 - 2.4 - i * 2.1, z, 5.0, fett, aus="m", maxb=RASTER - 4)


def hexfarbe(rgb):
    return "#%02X%02X%02X" % tuple(int(round(v * 255)) for v in rgb)


NORMEN = [  # (Bedingung auf Komponententypen/Abwasserarten, Norm, Titel)
    (None, "DIN EN 12056", "Schwerkraftentwässerungsanlagen innerhalb von Gebäuden"),
    (None, "DIN 1986-100", "Entwässerungsanlagen für Gebäude und Grundstücke"),
    ("hebeanlage", "DIN EN 12050", "Abwasserhebeanlagen für die Gebäude- und Grundstücksentwässerung"),
    ("rueckstauverschluss", "DIN EN 13564", "Rückstauverschlüsse für Gebäude"),
    ("fettabscheider", "DIN EN 1825 / DIN 4040-100", "Abscheideranlagen für Fette"),
    ("leichtfluessigkeitsabscheider", "DIN EN 858 / DIN 1999-100", "Abscheideranlagen für Leichtflüssigkeiten"),
    ("grauwasseranlage", "DIN EN 16941-2", "Vor-Ort-Anlagen für Nicht-Trinkwasser – behandeltes Grauwasser"),
    ("whg", "AwSV", "Verordnung über Anlagen zum Umgang mit wassergefährdenden Stoffen"),
]

# Komponententyp -> (Legendensymbol der Plankopf-API, Bezeichnung in der Legende)
SYMBOLE = {
    "schlammfang": ("quadrat", "S  Schlammfang"),
    "fettabscheider": ("quadrat", "S | F  Fettabscheider mit Schlammfang"),
    "staerkeabscheider": ("quadrat", "St  Stärkeabscheider"),
    "probenahme": ("kreis", "P  Probenahmeschacht"),
    "kontrollschacht": ("kreis", "KS  Kontrollschacht"),
    "hebeanlage": ("quadrat", "Hebeanlage (Pumpe im Behälter) mit Rückstauschleife über RSE"),
    "rueckstauverschluss": ("dreieck", "Rückstauverschluss"),
    "absperrschieber": ("dreieck", "Absperrschieber (M = motorisch/automatisch)"),
    "rueckhaltung": ("quadrat", "RH  Rückhaltung"),
    "neutralisation": ("quadrat", "pH  Neutralisationsanlage"),
    "emulsionsspaltanlage": ("quadrat", "ESA  Emulsionsspaltanlage"),
    "grauwasseranlage": ("quadrat", "GW  Grauwasseranlage"),
}


def symbol_eintrag(k):
    if k["typ"] == "leichtfluessigkeitsabscheider":
        return ("quadrat", "S | B | K  Leichtflüssigkeitsabscheider Klasse I (Koaleszenzabscheider)"
                if k["klasse"] == "I" else "S | B  Leichtflüssigkeitsabscheider Klasse II (Benzinabscheider)")
    if k["typ"] == "sonstige":
        return ("quadrat", "%s  %s" % (k["kuerzel"], k["bezeichnung"]))
    return SYMBOLE[k["typ"]]


def legende_abschnitte(cfg):
    """Inhalt der Plankopf-Legende (fester Aufbau nach mp-plankopf): nur was auf dem Plan vorkommt."""
    v, u = cfg["versorger"], cfg["uebergabe"]
    komps = [(s, k) for s in cfg["straenge"] for k in s["komponenten"]]
    typen = [k["typ"] for _, k in komps]
    arten = [a for a in ART_REIHENFOLGE if any(s["abwasserart"] == a for s in cfg["straenge"])]
    ja = lambda b: "erforderlich" if b else "nicht gefordert"

    # 1 Linientypen
    lin = [{"farbe": hexfarbe(ARTEN[a][1]), "staerke_mm": 0.6, "text": ARTEN[a][0]} for a in arten]
    lin.append({"farbe": "#000000", "staerke_mm": 1.0, "text": "Sammel-/Grundleitung, Anschlusskanal"})
    if "grauwasseranlage" in typen:
        lin.append({"farbe": hexfarbe(C_BW), "text": "BW  Betriebswasser (aus Grauwasser)"})
        lin.append({"farbe": hexfarbe(C_TW), "text": "TW  Trinkwasser-Nachspeisung (freier Auslauf)"})
    lin.append({"farbe": hexfarbe(C_TW), "strich": "gestrichelt", "staerke_mm": 0.45, "text": "RSE  Rückstauebene"})
    if any(t in typen for t in ("fettabscheider", "staerkeabscheider", "hebeanlage")):
        lin.append({"farbe": "#000000", "strich": "punktiert", "staerke_mm": 0.3,
                    "text": "Lü  Lüftungsleitung über Dach (in Strangfarbe)"})
    lin.append({"farbe": hexfarbe(C_GRENZE), "strich": "strichpunkt", "staerke_mm": 0.4, "text": "Grundstücksgrenze"})

    # 2 Symbole: je Symbolart ein Eintrag, Komponenten mit AKS zusätzlich einzeln
    sym = [{"symbol": "quadrat", "text": "Abwasseranfallstelle / Strang (Rahmen in Strangfarbe)"},
           {"symbol": "punkt", "text": "Einmündung in Sammelleitung bzw. anderen Strang"}]
    gesehen = set()
    for _, k in komps:
        e = symbol_eintrag(k)
        if e not in gesehen:
            gesehen.add(e)
            sym.append({"symbol": e[0], "text": e[1]})
    if any(k["typ"] == "leichtfluessigkeitsabscheider" and k["warnanlage"] for _, k in komps):
        sym.append({"symbol": "kreis", "text": "W  Warnanlage am Leichtflüssigkeitsabscheider"})
    if u.get("probenahme") and "probenahme" not in typen:
        sym.append({"symbol": "kreis", "text": "P  Probenahmeschacht"})
    if u["schacht"]:
        e = {"symbol": "kreis", "text": "ÜS  " + u.get("bezeichnung", "Übergabeschacht")}
        if u.get("aks"):
            e["aks"] = u["aks"]
        sym.append(e)
    sym.append({"symbol": "flaeche", "farbe": "#D9D9D9", "text": "Öffentlicher %s"
                % ("Schmutzwasserkanal" if v["entwaesserungssystem"] == "trenn" else "Mischwasserkanal")})
    for s, k in komps:
        if k.get("aks"):
            sym.append({"symbol": symbol_eintrag(k)[0], "text": "%s (Strang %s)" % (k["bezeichnung"], s["id"]),
                        "aks": k["aks"]})

    # 3 Planungsgrundsätze
    pg = ["Fließschema der Schmutzwasseranlage; nicht maßstäblich, nicht höhengetreu."]
    if any(s["unter_rueckstauebene"] for s in cfg["straenge"]) and "hebeanlage" in typen:
        pg.append("Ablaufstellen unter der Rückstauebene werden über Hebeanlagen mit Rückstauschleife über die "
                  "Rückstauebene entwässert.")
    if any(t in ABSCHEIDER for t in typen):
        pg.append("Fäkalienhaltiges Abwasser wird nicht über Abscheideranlagen geführt; Hebeanlagen sind den "
                  "Abscheidern nachgeschaltet.")
    if "probenahme" in typen:
        pg.append("Probenahmeschächte liegen in Fließrichtung hinter den Abscheider- bzw. Behandlungsanlagen.")
    if "whg" in arten and "rueckhaltung" in typen and "absperrschieber" in typen:
        pg.append("Abwasser von WHG-/AwSV-Flächen wird mit Absperreinrichtung und Rückhaltung gefasst und vor "
                  "der Einleitung vorbehandelt.")
    if "grauwasseranlage" in typen:
        pg.append("Grauwasser wird aufbereitet und als Betriebswasser genutzt; Überlauf in die Sammelleitung, "
                  "Trinkwassernachspeisung nur über freien Auslauf.")
    pg += cfg["planungsgrundsaetze"]

    # 4 Normenverweise
    nv = [{"norm": n, "titel": t} for bed, n, t in NORMEN if bed is None or bed in typen or bed in arten]

    # 5 Standortvorgaben (Versorgerprofil)
    vs = v["uebergabeschacht"]
    sv = ["Versorger: %s%s." % (v["name"], (" – " + v["satzung"]) if v["satzung"] else ""),
          "Entwässerung im %s; Rückstauebene: %s." % (
              "Trennsystem" if v["entwaesserungssystem"] == "trenn" else "Mischsystem", v["rueckstauebene"]),
          "Übergabeschacht %s%s; Anschlusskanal mind. DN %s%s." % (
              ja(vs["erforderlich"]),
              (", mind. DN %s, %s" % (zahl(vs["dn_min"]), vs["lage"])) if vs["erforderlich"] else "",
              zahl(v["anschlusskanal_dn_min"]),
              "; Probenahme an der Übergabe erforderlich" if v["probenahme_an_uebergabe"] else "")]
    if "fetthaltig" in arten:
        sv.append("Fetthaltiges Abwasser: Fettabscheider %s, Probenahme dahinter %s." % (
            ja(v["fettabscheider"]["pflicht_bei_fetthaltig"]), ja(v["fettabscheider"]["probenahme_erforderlich"])))
    if "staerkehaltig" in arten:
        sv.append("Stärkehaltiges Abwasser: Stärkeabscheider %s; Ablauf über den Fettabscheider %s." % (
            ja(v["staerkeabscheider"]["pflicht_bei_staerkehaltig"]),
            {"zulaessig": "zulässig", "erforderlich": "erforderlich", "unzulaessig": "unzulässig"}[
                v["staerkeabscheider"]["ablauf_ueber_fettabscheider"]]))
    vl = v["leichtfluessigkeitsabscheider"]
    if "mineraloelhaltig" in arten:
        sv.append("Mineralölhaltiges Abwasser: Leichtflüssigkeitsabscheider mind. Klasse %s, Probenahme %s, "
                  "Warnanlage %s." % (vl["min_klasse"], ja(vl["probenahme_erforderlich"]),
                                      ja(vl["warnanlage_erforderlich"])))
    if "whg" in arten:
        vw = v["whg"]
        sv.append("WHG-/AwSV-Flächen: Abscheider mind. Klasse %s, Rückhaltung %s, Absperreinrichtung %s, "
                  "Probenahme %s." % (vw["min_klasse"], ja(vw["rueckhaltung_erforderlich"]),
                                      ja(vw["absperrung_erforderlich"]), ja(vw["probenahme_erforderlich"])))
    if "grauwasseranlage" in typen:
        vg = v["grauwassernutzung"]
        sv.append("Grauwassernutzung %s; Anzeige beim Versorger %s, Mengenerfassung (Zähler) %s." % (
            "zulässig" if vg["zulaessig"] else "nicht zulässig", ja(vg["anzeigepflicht"]),
            ja(vg["zaehler_erforderlich"])))
    if "hebeanlage" in typen and v["hebeanlage"]["doppelanlage_bei_gewerbe"]:
        sv.append("Hebeanlagen für gewerbliches Abwasser als Doppelanlage.")
    sv += v["hinweise"]

    # 7 Weitere Festlegungen
    wf = list(cfg["hinweise"])
    wf.append("Das Schema ersetzt weder Bemessung noch Entwässerungsantrag/Genehmigung; Vorgaben sind vor "
              "Ausführung mit dem Versorger abzustimmen.")
    return {"linientypen": lin, "symbole": sym, "planungsgrundsaetze": pg, "normenverweise": nv,
            "standortvorgaben": sv, "weitere_festlegungen": wf}


def plankopf_spec(cfg, ergebnisse):
    """Eingabe für mp-plankopf: Vorgaben aus dem Projekt, überschrieben durch cfg['plankopf']."""
    p, v, pk = cfg["projekt"], cfg["versorger"], cfg["plankopf"]
    felder = {"Plannummer": p.get("plan_nr", ""), "aktuelle Änderung Index": p.get("index", ""),
              "erstellt Datum": p["datum"], "erstellt von": p.get("bearbeiter", "")}
    felder.update(pk.get("felder", {}))
    if any(e[0] == "FEHLER" for e in ergebnisse):
        felder["Status"] = "VORABZUG"      # Plan mit Abweichungen ist nie freigegeben
    zellen = {"Planart": "Schmutzwasser-Anlagenschema", "Maßstab": "ohne"}
    zellen.update(pk.get("zellen", {}))
    frei = {
        "Block 1": {"titel": "Bauvorhaben", "zeilen": [t for t in (p["name"], p.get("standort", "")) if t]},
        "Block 3": {"titel": "Versorger", "zeilen": [t for t in (v["name"], v["satzung"]) if t]},
        "Block 4": {"titel": "Planinhalt", "zeilen": ["Schmutzwasser-Anlagenschema (Fließschema)",
                                                      "nicht maßstäblich, nicht höhengetreu"]},
    }
    if p.get("bauherr"):
        frei["Block 2"] = {"titel": "Bauherr", "zeilen": [p["bauherr"]]}
    frei.update(pk.get("freifelder", {}))
    spec = {"felder": felder, "zellen": zellen, "freifelder": frei,
            "legende": {"titel": "Legende", "abschnitte": legende_abschnitte(cfg)}}
    if cfg["blatt"] is not None:
        spec["blatt"] = cfg["blatt"]
    for k in ("logo", "aenderungsindex", "zusatzfelder", "optionen"):
        if k in pk:
            spec[k] = pk[k]
    return spec


def zeichne(cfg, L, ergebnisse, pfad, spec, pk_layout):
    v, u, p = cfg["versorger"], cfg["uebergabe"], cfg["projekt"]
    c = canvas.Canvas(pfad, pagesize=(L["W"] * mm, L["H"] * mm), invariant=1, pageCompression=0,
                      initialFontName=PK.FONT)
    felder = spec["felder"]
    c.setTitle("%s" % (felder.get("Plannummer") or "Schmutzwasser-Anlagenschema"))
    c.setAuthor("MP_400_Plankopf")
    c.setCreator("schmutzwasser-schema / mp-plankopf %s" % PK.VERSION)
    c.setSubject("%s" % felder.get("GUID_project_identifier", ""))
    # Rahmen, Plankopf, Änderungsindex, Zusatzfelder und Legende: ausschließlich mp-plankopf
    PK.draw(c, spec, pk_layout)
    c.saveState()
    c.translate(L["ursprung"][0] * mm, L["ursprung"][1] * mm)
    c.scale(L["faktor"], L["faktor"])
    c.setLineJoin(0)
    c.setDash([])
    z = Zeichner(c)
    zx, zy, zb_b = L["zx"], L["zy"], L["zb"]

    z.text(L["x0"], L["zo"] - 6, "Fließschema – nicht maßstäblich, nicht höhengetreu", 7, rgb=(0.3, 0.3, 0.3))
    if any(e[0] == "FEHLER" for e in ergebnisse):
        z.text(L["x0"] + 75, L["zo"] - 6, "ENTWURF MIT ABWEICHUNGEN VON VERSORGERVORGABEN / REGELWERK – NICHT "
               "FREIGEGEBEN", 9, True, C_ROT, maxb=zb_b - 85)

    xs, yaus = L["xs"], L["yaus"]

    # --- Stränge: Leitungen ---
    enden = {}
    for s in cfg["straenge"]:
        y, rgb = L["y"][s["id"]], ARTEN[s["abwasserart"]][1]
        xa = L["x0"] + QUELLE_B
        if s.get("ziel"):
            zs = s["ziel"]
            xe = slot_x(L, L["spalten"][zs["strang"]][zs["index"]]) - 6
            yz = L["y"][zs["strang"]]
            z.linie([(xa, y), (xe, y), (xe, yz)], rgb, 0.6)
            z.pfeil(xe, yz + (2.2 if y > yz else -2.2), "u" if y > yz else "o", rgb)
            enden[s["id"]] = ("ziel", xe, yz)
        else:
            z.linie([(xa, y), (xs, y)], rgb, 0.6)
            z.pfeil(xs - 1.2, y, "r", rgb)
            enden[s["id"]] = ("sammel", xs, y)
        z.pfeil(xa + 7, y, "r", rgb)
        if "dn" in s:
            z.text(xa + 1.5, y + 1.2, "DN %s" % zahl(s["dn"]), 4.6, rgb=rgb)

    # --- Sammelleitung, Übergabe, Kanal ---
    ys_sammel = [e[2] for e in enden.values() if e[0] == "sammel"]
    xg, xkanal = xs + 52, xs + 66
    if ys_sammel:
        z.linie([(xs, max(ys_sammel)), (xs, yaus), (xkanal - 3, yaus)], C_SAMMEL, 1.0)
        z.pfeil(xkanal - 3, yaus, "r", C_SAMMEL, 2.4)
        z.pfeil(xs, yaus + 6, "u", C_SAMMEL, 2.4)
        dn = (" DN %s" % zahl(cfg["sammelleitung_dn"])) if "sammelleitung_dn" in cfg else ""
        z.text(xs + 3.2, (max(ys_sammel) + yaus) / 2, "Sammel-/Grundleitung SW" + dn, 5.5, True, aus="m", winkel=90)
    for kind, x, y in sorted(enden.values()):
        z.kreis(x, y, 0.9, (0, 0, 0), 0.1, fuell=(0, 0, 0))
    if u.get("probenahme"):
        z.kreis(xs + 14, yaus, 4.5, b=0.5)
        z.text(xs + 14, yaus - 1.5, "P", 7.5, True, aus="m")
        z.text(xs + 14, yaus - 7.5, "Probenahme", 4.8, aus="m")
    xsch = xs + 34
    if u["schacht"]:
        z.kreis(xsch, yaus, 6.5, b=0.7)
        z.kreis(xsch, yaus, 5.2, b=0.25)
        z.text(xsch, yaus - 1.5, "ÜS", 7.5, True, aus="m")
        z.text(xsch, yaus - 9.5, u.get("bezeichnung", "Übergabeschacht"), 5.2, True, aus="m", maxb=32)
        z.text(xsch, yaus - 11.8, "DN %s" % zahl(u["dn"]) if "dn" in u else "", 4.8, aus="m")
    else:
        z.text(xsch, yaus - 4, "ohne Übergabeschacht", 5.0, aus="m", rgb=C_ROT if v["uebergabeschacht"]["erforderlich"]
               else (0, 0, 0))
    z.text((xsch + 6.5 + xg) / 2 + 1, yaus + 1.5, "DN %s" % zahl(u["anschlusskanal_dn"]), 4.8, aus="m")
    # Grundstücksgrenze
    z.linie([(xg, yaus - 13), (xg, L["ytop"] + 2)], C_GRENZE, 0.4, strich=(4, 1.2, 0.6, 1.2))
    z.text(xg - 1.2, L["ytop"] - 30, "Grundstücksgrenze", 5.5, True, winkel=90)
    z.text(xg - 5, L["ytop"] - 1, "Grundstück", 5.5, aus="r", rgb=(0.3, 0.3, 0.3))
    z.text(xg + 3, L["ytop"] - 1, "öffentl. Bereich", 5.5, rgb=(0.3, 0.3, 0.3))
    # öffentlicher Kanal
    z.rechteck(xkanal - 3, yaus - 10, 6, 34, b=0.6, fuell=(0.85, 0.85, 0.85))
    kanal = "SW-Kanal" if v["entwaesserungssystem"] == "trenn" else "MW-Kanal"
    z.text(xkanal + 1.6, yaus - 6, "öffentl. " + kanal, 5.5, True, winkel=90)
    z.text(xkanal + 5, yaus + 16, "Trennsystem" if v["entwaesserungssystem"] == "trenn" else "Mischsystem", 5, winkel=90)
    # Rückstauebene (Bezug)
    z.linie([(xs + 6, yaus + 19), (xkanal - 5, yaus + 19)], C_TW, 0.45, strich=RSE_STRICH)
    z.text(xs + 6, yaus + 20, "RSE: " + v["rueckstauebene"], 4.6, True, C_TW, maxb=xkanal - xs - 12)

    # --- Stränge: Quellen, Komponenten ---
    for s in cfg["straenge"]:
        y, (artname, rgb) = L["y"][s["id"]], ARTEN[s["abwasserart"]]
        x0 = L["x0"]
        z.rechteck(x0, y - QUELLE_H / 2, QUELLE_B, QUELLE_H, rgb, 0.5, fuell=(0.97, 0.97, 0.97), radius=1.5)
        z.text(x0 + 2, y + 5.2, "%s  %s" % (s["id"], s["bezeichnung"]), 6.2, True, maxb=QUELLE_B - 4)
        z.text(x0 + 2, y + 2.4, artname, 4.8, rgb=rgb, maxb=QUELLE_B - 4)
        quellen = z.umbruch(", ".join(s["quellen"]), False, 4.8, QUELLE_B - 4)[:2]
        for i, q in enumerate(quellen):
            z.text(x0 + 2, y - 0.4 - i * 2.2, q, 4.8, maxb=QUELLE_B - 4)
        lage = "unter Rückstauebene" if s["unter_rueckstauebene"] else "über Rückstauebene"
        z.text(x0 + 2, y - 7.2, "Lage: " + lage, 4.8, True, C_TW if s["unter_rueckstauebene"] else (0.3, 0.3, 0.3))
        for k, sp in zip(s["komponenten"], L["spalten"][s["id"]]):
            z.komponente(k, slot_x(L, sp), y, rgb)
            z.beschriftung(k, slot_x(L, sp), y)

    # --- Prüfergebnis im Zeichenbereich (Standortvorgaben stehen in der Legende des Plankopfs) ---
    xh, yf = zx + 4, zy + HINWEIS_H
    z.linie([(xh, yf), (zx + zb_b - 4, yf)], b=0.35)
    z.text(xh, yf - 5, "Prüfergebnis gegen Standortvorgaben und Regelwerk", 7, True)
    zeilen = []
    farben = {"FEHLER": C_ROT, "WARNUNG": (0.75, 0.40, 0.0), "HINWEIS": (0.2, 0.2, 0.2)}
    for stufe, code, txt in ergebnisse:
        zeilen.append(("%s %s: %s" % ("ABWEICHUNG" if stufe == "FEHLER" else stufe, code, txt), farben[stufe]))
    if not any(e[0] == "FEHLER" for e in ergebnisse):
        zeilen.append(("Keine Abweichungen festgestellt.", (0, 0.45, 0.15)))
    yy, breite, gr = yf - 9.5, zb_b - 8, 5.2
    umgebrochen = []
    for txt, rgb in zeilen:
        for i, zl in enumerate(z.umbruch(txt, False, gr, breite - 3)):
            umgebrochen.append((("• " if i == 0 else "   ") + zl, rgb))
    max_zeilen = int((yy - zy - 2) // 2.5) + 1
    if len(umgebrochen) > max_zeilen:
        umgebrochen = umgebrochen[:max_zeilen - 1] + [("   ... weitere Einträge siehe Prüfprotokoll (Konsole)", C_ROT)]
    for txt, rgb in umgebrochen:
        z.text(xh, yy, txt, gr, rgb=rgb)
        yy -= 2.5

    c.restoreState()
    c.showPage()
    c.save()


# --------------------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser(description="Erzeugt ein Schmutzwasser-Anlagenschema als PDF.")
    ap.add_argument("konfiguration", help="JSON-Konfigurationsdatei")
    ap.add_argument("-o", "--ausgabe", help="PDF-Ausgabedatei (Standard: <konfiguration>.pdf)")
    ap.add_argument("--nur-pruefen", action="store_true", help="nur Regelprüfung, keine PDF")
    ap.add_argument("--erlaube-abweichungen", action="store_true",
                    help="PDF auch bei Regelverstößen erzeugen (als Entwurf mit Abweichungen gekennzeichnet)")
    a = ap.parse_args(argv)
    try:
        lade_plankopf()
        cfg = lade_konfiguration(a.konfiguration)
        erg = pruefe_regeln(cfg)
        spec = plankopf_spec(cfg, erg)
        try:
            pk_layout = PK.compute_layout(spec)
        except PK.PlankopfFehler as e:
            raise Eingabefehler("Plankopf (mp-plankopf): %s" % e)
        L = berechne_layout(cfg, pk_layout)
    except Eingabefehler as e:
        print("EINGABEFEHLER: %s" % e, file=sys.stderr)
        return 1
    for stufe, code, txt in erg:
        print("%-8s %s  %s" % (stufe, code, txt))
    for w in pk_layout["warnungen"]:
        print("PLANKOPF %s" % w)
    fehler = sum(1 for e in erg if e[0] == "FEHLER")
    print("Prüfung: %d Fehler, %d Warnungen, %d Hinweise" % (
        fehler, sum(1 for e in erg if e[0] == "WARNUNG"), sum(1 for e in erg if e[0] == "HINWEIS")))
    if a.nur_pruefen:
        return 2 if fehler else 0
    if fehler and not a.erlaube_abweichungen:
        print("Keine PDF erzeugt. Konfiguration korrigieren oder --erlaube-abweichungen verwenden.", file=sys.stderr)
        return 2
    aus = a.ausgabe or os.path.splitext(a.konfiguration)[0] + ".pdf"
    zeichne(cfg, L, erg, aus, spec, pk_layout)
    print("PDF erzeugt: %s" % aus)
    return 2 if fehler else 0


if __name__ == "__main__":
    sys.exit(main())
