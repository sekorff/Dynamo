# -*- coding: utf-8 -*-
"""Platzierungs-Kern fuer Daten-Doppeldosen und WLAN-Anschlusspunkte.

Reines Python ohne Revit-Abhaengigkeit (laeuft unter IronPython 2.7, CPython 3
und auf der Kommandozeile). Alle Laengen in Metern, Hoehen bezogen auf OKFF.

Eingabe ist ein neutrales Modell (dict), siehe ``beispiel/modell.json``.
Der Revit-Adapter ``datendosen_revit.py`` erzeugt dieses Modell aus dem Projekt.
"""
from __future__ import division, print_function, unicode_literals

import copy
import io
import json
import math
import re
import sys

try:
    text_type = unicode  # noqa: F821  (IronPython 2.7)
except NameError:
    text_type = str

STANDARD_CONFIG = {
    # --- Raumauswahl -------------------------------------------------------
    # Parameter, in denen die Raumfunktion gesucht wird (erster gefuellter zaehlt).
    "funktion_parameter": ["Funktion", "Raumfunktion", "Nutzungsart", "Nutzung"],
    # Raeume mit dieser Funktion erhalten KEINE Datendosen.
    "ausschluss_funktionen": ["Fläche"],
    # "exakt": Funktion == Eintrag, "enthaelt": Eintrag kommt in Funktion vor.
    "ausschluss_modus": "exakt",

    # --- Anzahl Doppeldosen je Raum ---------------------------------------
    "dosen_standard": {"je_m2": 10.0, "min": 1, "max": None},
    # Abweichende Regeln je Funktion, z.B.
    # {"funktion_enthaelt": ["Büro"], "je_m2": 6.0, "min": 2, "max": null}
    "dosen_regeln": [],

    # --- Geometrie ---------------------------------------------------------
    "mindestabstand": 1.35,
    "dosen_hoehe": 0.30,
    "eckabstand": 0.30,
    "oeffnung_randabstand": 0.15,
    "oeffnung_suchabstand": 0.60,
    "raster": 0.05,
    "fassade_zuerst": True,
    "vorhangfassade_bestueckbar": False,
    # Wandtypen, deren Name einen dieser Texte enthaelt, gelten zusaetzlich als Fassade.
    "fassade_wandtyp_enthaelt": [],
    # Wanddosen bis zu dieser Hoehe gelten als "bodennah" (Nummerierung zuerst).
    "bodennah_bis_hoehe": 1.50,

    # --- Smartboards -------------------------------------------------------
    "smartboard": {
        "erkennung": ["Smartboard", "Smart Board", "Smart-Board"],
        "breite_parameter": ["Breite", "Width"],
        "mitte_achsabstand": 0.071,
        "seitlicher_abstand": 0.15,
        "zaehlt_zur_raumanzahl": False,
    },

    # --- Zusaetzliche Deckendosen (optional) -------------------------------
    # z.B. {"funktion_enthaelt": ["Besprechung"], "anzahl": 1}
    "decken_regeln": [],
    "decken_wandabstand": 0.50,

    # --- WLAN --------------------------------------------------------------
    "wlan": {
        "aktiv": True,
        "hoehe_soll": 2.50,
        "hoehe_min": 2.30,
        "abstand_unter_decke": 0.15,
        "oeffentlich": ["Flur", "Foyer", "Aula", "Mensa", "Eingang", "Halle",
                        "Treppe", "Warte", "Lobby", "Empfang", "Cafeteria",
                        "Bibliothek", "Pausen"],
        "immer": ["Klasse", "Unterricht", "Fachraum", "Besprechung",
                  "Konferenz", "Seminar"],
        "nie": ["WC", "Lager", "Technik", "Putz", "Schacht", "Abstell",
                "Aufzug", "Dusche"],
        "flaeche_je_ap_oeffentlich": 200.0,
        "flaeche_je_ap": 150.0,
        # Untergeordnete Raeume erhalten nur dann einen AP, wenn im Geschoss
        # kein AP naeher als diese Reichweite liegt (0 = jeder Raum).
        "reichweite": 8.0,
        "ports": 2,
        "eigenes_patchfeld": False,
    },

    # --- Nummerierung / Patchfelder ---------------------------------------
    # "hoehe": Geschosse von unten nach oben; "raumnummer": Geschoss mit der
    # kleinsten Raumnummer zuerst.
    "geschoss_reihenfolge": "hoehe",
    "ports_je_patchfeld": 24,
    "ports_je_dose": 2,
    "patchfeld_start": 1,
    "format_dosennummer": "{raum}-{lfd:02d}",
    "format_wlannummer": "{raum}-AP{lfd:02d}",
    "format_patch": "PF{pf:02d}/{port_a:02d}-{port_b:02d}",

    # --- Revit -------------------------------------------------------------
    "revit": {
        "familie_datendose": {"familie": "Daten-Doppeldose", "typ": ""},
        "familie_wlan": {"familie": "Daten-Doppeldose", "typ": ""},
        "familien_drehung_grad": 0.0,
        "vorherige_loeschen": True,
        "markierung": "DATENDOSE-AUTO",
        # Leerer Name = Parameter wird nicht geschrieben. "nummer" leer -> Kennzeichen.
        "parameter": {"nummer": "", "patchfeld": "Patchfeld", "port": "Port",
                      "raumnummer": "Raumnummer", "dosentyp": "Dosentyp"},
        "verknuepfte_modelle": True,
        "csv_pfad": "",
    },
}

TYP_STANDARD = "standard"
TYP_SB_MITTE = "smartboard_mitte"
TYP_SB_LINKS = "smartboard_links"
TYP_SB_RECHTS = "smartboard_rechts"
TYP_DECKE = "decke"
TYP_WLAN = "wlan"


# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------
def config_zusammenfuehren(basis, ueberschreibung):
    """Rekursives Zusammenfuehren zweier Konfigurations-dicts (neue Kopie)."""
    ergebnis = copy.deepcopy(basis)
    for key, wert in (ueberschreibung or {}).items():
        if isinstance(wert, dict) and isinstance(ergebnis.get(key), dict):
            ergebnis[key] = config_zusammenfuehren(ergebnis[key], wert)
        else:
            ergebnis[key] = copy.deepcopy(wert)
    return ergebnis


def config_laden(pfad=None):
    """Standardkonfiguration, optional ueberlagert durch eine JSON-Datei."""
    if not pfad:
        return copy.deepcopy(STANDARD_CONFIG)
    with io.open(pfad, "r", encoding="utf-8-sig") as f:
        return config_zusammenfuehren(STANDARD_CONFIG, json.load(f))


def natuerlicher_schluessel(text):
    """Alphanumerische Sortierung: '2' < '10', Ziffern vor Buchstaben."""
    teile = re.findall(r"\d+|\D+", text_type(text if text is not None else "").strip())
    schluessel = []
    for teil in teile:
        if teil.isdigit():
            schluessel.append((0, int(teil), ""))
        else:
            schluessel.append((1, 0, teil.lower()))
    return schluessel


def _enthaelt(text, begriffe):
    t = (text or "").lower()
    for b in begriffe or []:
        if b and b.lower() in t:
            return True
    return False


def polygon_flaeche(punkte):
    """Vorzeichenbehaftete Flaeche (positiv = gegen den Uhrzeigersinn)."""
    a = 0.0
    n = len(punkte)
    for i in range(n):
        x0, y0 = punkte[i][0], punkte[i][1]
        x1, y1 = punkte[(i + 1) % n][0], punkte[(i + 1) % n][1]
        a += x0 * y1 - x1 * y0
    return a / 2.0


def _dist3(a, b):
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2)


# ---------------------------------------------------------------------------
# Raumumlauf
# ---------------------------------------------------------------------------
class Umlauf(object):
    """Raumkontur, im Uhrzeigersinn (Draufsicht) durchlaufen.

    Wer durch die Tuer eintritt und sich nach links wendet, laeuft die Waende
    in der Draufsicht im Uhrzeigersinn ab - genau so waechst die Koordinate s.
    """

    def __init__(self, kontur):
        segs = []
        for k in kontur:
            p0 = (float(k["p0"][0]), float(k["p0"][1]))
            p1 = (float(k["p1"][0]), float(k["p1"][1]))
            laenge = math.hypot(p1[0] - p0[0], p1[1] - p0[1])
            if laenge < 1e-6:
                continue
            segs.append({"p0": p0, "p1": p1, "len": laenge,
                         "fassade": bool(k.get("fassade", False)),
                         "bestueckbar": bool(k.get("bestueckbar", True)),
                         "wand": k.get("wand")})
        if polygon_flaeche([s["p0"] for s in segs]) > 0:
            segs.reverse()
            for s in segs:
                s["p0"], s["p1"] = s["p1"], s["p0"]
        pos = 0.0
        for s in segs:
            s["s0"] = pos
            s["dx"] = (s["p1"][0] - s["p0"][0]) / s["len"]
            s["dy"] = (s["p1"][1] - s["p0"][1]) / s["len"]
            # Inneres liegt bei Umlauf im Uhrzeigersinn rechts der Laufrichtung.
            s["nx"], s["ny"] = s["dy"], -s["dx"]
            s["gesperrt"] = []
            pos += s["len"]
        self.segs = segs
        self.laenge = pos
        self.start = 0.0
        self.punkte = [s["p0"] for s in segs]
        n = len(segs)
        for i, s in enumerate(segs):
            v = segs[i - 1]
            nach = segs[(i + 1) % n]
            s["ecke_anfang"] = self._ist_ecke(v, s)
            s["ecke_ende"] = self._ist_ecke(s, nach)

    @staticmethod
    def _ist_ecke(a, b):
        kreuz = a["dx"] * b["dy"] - a["dy"] * b["dx"]
        punkt = a["dx"] * b["dx"] + a["dy"] * b["dy"]
        return abs(math.atan2(kreuz, punkt)) > math.radians(15.0)

    def rel(self, s_abs):
        return (s_abs - self.start) % self.laenge

    def projiziere(self, p):
        """Naechster Konturpunkt: (abstand, s_abs, segment, t)."""
        best = None
        for s in self.segs:
            t = (p[0] - s["p0"][0]) * s["dx"] + (p[1] - s["p0"][1]) * s["dy"]
            t = min(max(t, 0.0), s["len"])
            x = s["p0"][0] + t * s["dx"]
            y = s["p0"][1] + t * s["dy"]
            d = math.hypot(p[0] - x, p[1] - y)
            if best is None or d < best[0] - 1e-9:
                best = (d, s["s0"] + t, s, t)
        return best

    def punkt(self, seg, t, z):
        return {"x": seg["p0"][0] + t * seg["dx"], "y": seg["p0"][1] + t * seg["dy"],
                "z": z, "nx": seg["nx"], "ny": seg["ny"],
                "s": self.rel(seg["s0"] + t), "fassade": seg["fassade"],
                "wand": seg["wand"]}

    def enthaelt(self, p):
        x, y = p[0], p[1]
        innen = False
        pts = self.punkte
        n = len(pts)
        for i in range(n):
            x0, y0 = pts[i]
            x1, y1 = pts[(i + 1) % n]
            if (y0 > y) != (y1 > y):
                if x < x0 + (y - y0) * (x1 - x0) / (y1 - y0):
                    innen = not innen
        return innen

    def sperre_oeffnung(self, oeffnung, suchabstand, rand):
        p = oeffnung["p"]
        halb = float(oeffnung.get("breite", 1.0)) / 2.0
        wand = oeffnung.get("wand")
        for s in self.segs:
            if wand is not None and s["wand"] is not None and wand != s["wand"]:
                continue
            t = (p[0] - s["p0"][0]) * s["dx"] + (p[1] - s["p0"][1]) * s["dy"]
            quer = abs((p[0] - s["p0"][0]) * s["nx"] + (p[1] - s["p0"][1]) * s["ny"])
            if quer > suchabstand or t < -halb or t > s["len"] + halb:
                continue
            s["gesperrt"].append((t - halb - rand, t + halb + rand,
                                  float(oeffnung.get("uk", 0.0)),
                                  float(oeffnung.get("ok", 2.1))))

    def kandidaten(self, hoehe, raster, eckabstand):
        """Zulaessige Wandpunkte in Hoehe ``hoehe``, sortiert nach s ab Startpunkt."""
        liste = []
        for s in self.segs:
            if not s["bestueckbar"]:
                continue
            a = eckabstand if s["ecke_anfang"] else 0.0
            b = eckabstand if s["ecke_ende"] else 0.0
            frei = s["len"] - a - b
            if frei < 0:
                continue
            n = int(math.floor(frei / raster + 1e-9))
            t0 = a + (frei - n * raster) / 2.0
            for i in range(n + 1):
                t = t0 + i * raster
                ok = True
                for (g0, g1, z0, z1) in s["gesperrt"]:
                    if g0 < t < g1 and z0 - 0.10 < hoehe < z1 + 0.10:
                        ok = False
                        break
                if ok:
                    liste.append(self.punkt(s, t, hoehe))
        liste.sort(key=lambda k: k["s"])
        return liste

    def innenpunkt(self):
        """Punkt mit groesstem Wandabstand (liegt auch bei L-Raeumen innen)."""
        xs = [p[0] for p in self.punkte]
        ys = [p[1] for p in self.punkte]
        raster = self.innenraster()
        if not raster:
            return (sum(xs) / len(xs), sum(ys) / len(ys))
        # Bei langen Raeumen (Flur) liegen viele Punkte gleich weit von den
        # Waenden entfernt - dann den mittleren davon nehmen.
        grenze = 0.9 * max(r[2] for r in raster)
        tief = [r for r in raster if r[2] >= grenze]
        mx = sum(r[0] for r in tief) / len(tief)
        my = sum(r[1] for r in tief) / len(tief)
        return min(tief, key=lambda r: math.hypot(r[0] - mx, r[1] - my))[:2]

    def innenraster(self, n=30):
        """Rasterpunkte im Raum als (x, y, wandabstand)."""
        xs = [p[0] for p in self.punkte]
        ys = [p[1] for p in self.punkte]
        x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
        schritt = max(x1 - x0, y1 - y0) / float(n)
        if schritt <= 0:
            return []
        punkte = []
        nx = int((x1 - x0) / schritt) + 1
        ny = int((y1 - y0) / schritt) + 1
        for i in range(nx + 1):
            for j in range(ny + 1):
                p = (x0 + (i + 0.5) * schritt, y0 + (j + 0.5) * schritt)
                if self.enthaelt(p):
                    punkte.append((p[0], p[1], self.projiziere(p)[0]))
        return punkte


# ---------------------------------------------------------------------------
# Auswahl von Punkten mit Mindestabstand
# ---------------------------------------------------------------------------
def _xyz(k):
    return (k["x"], k["y"], k["z"])


def _gierig(kandidaten, fest, abstand, mindestabstand, limit=None):
    gewaehlt = []
    for k in kandidaten:
        p = _xyz(k)
        if any(_dist3(p, f) < mindestabstand - 1e-9 for f in fest):
            continue
        if any(_dist3(p, _xyz(g)) < abstand - 1e-9 for g in gewaehlt):
            continue
        gewaehlt.append(k)
        if limit is not None and len(gewaehlt) >= limit:
            break
    return gewaehlt


def _gueltig(auswahl, fest, mindestabstand):
    pts = [_xyz(k) for k in auswahl]
    for i, p in enumerate(pts):
        if any(_dist3(p, f) < mindestabstand - 1e-9 for f in fest):
            return False
        for q in pts[:i]:
            if _dist3(p, q) < mindestabstand - 1e-9:
                return False
    return True


def waehle_punkte(kandidaten, anzahl, fest, mindestabstand):
    """Bis zu ``anzahl`` Kandidaten, moeglichst gleichmaessig verteilt.

    Gibt weniger Punkte zurueck, wenn der Mindestabstand nicht mehr zulaesst.
    """
    if anzahl <= 0 or not kandidaten:
        return []
    maximal = _gierig(kandidaten, fest, mindestabstand, mindestabstand)
    if len(maximal) <= anzahl:
        return maximal
    n = len(kandidaten)
    gleich = [kandidaten[min(n - 1, int((i + 0.5) * n / anzahl))] for i in range(anzahl)]
    if _gueltig(gleich, fest, mindestabstand):
        return gleich
    # Groesster Abstand, bei dem die gierige Auswahl noch ``anzahl`` Punkte findet.
    lo, hi = mindestabstand, mindestabstand + 200.0
    for _ in range(24):
        mitte = (lo + hi) / 2.0
        if len(_gierig(kandidaten, fest, mitte, mindestabstand, anzahl)) >= anzahl:
            lo = mitte
        else:
            hi = mitte
    return _gierig(kandidaten, fest, lo, mindestabstand, anzahl)


# ---------------------------------------------------------------------------
# Raumaufbereitung
# ---------------------------------------------------------------------------
def _funktionstext(raum):
    return "%s %s" % (raum.get("funktion") or "", raum.get("name") or "")


def ist_ausgeschlossen(raum, cfg):
    funktion = (raum.get("funktion") or "").strip().lower()
    for eintrag in cfg["ausschluss_funktionen"]:
        e = eintrag.strip().lower()
        if cfg["ausschluss_modus"] == "enthaelt":
            if e and e in funktion:
                return True
        elif e == funktion:
            return True
    return False


def dosen_anzahl(raum, cfg):
    regel = cfg["dosen_standard"]
    for r in cfg["dosen_regeln"]:
        if _enthaelt(_funktionstext(raum), r.get("funktion_enthaelt")):
            regel = config_zusammenfuehren(cfg["dosen_standard"], r)
            break
    minimum = max(1, int(regel.get("min") or 1))
    je = regel.get("je_m2")
    n = minimum
    if je:
        n = max(minimum, int(math.ceil(float(raum.get("flaeche") or 0.0) / float(je) - 1e-9)))
    if regel.get("max"):
        n = min(n, max(minimum, int(regel["max"])))
    return n


def _raum_vorbereiten(raum, cfg, warnungen):
    umlauf = Umlauf(raum.get("kontur") or [])
    if len(umlauf.segs) < 3:
        warnungen.append("Raum %s: keine gueltige Raumkontur." % raum.get("nummer"))
        return None
    if not raum.get("flaeche"):
        raum["flaeche"] = abs(polygon_flaeche(umlauf.punkte))
    tueren = []
    for o in raum.get("oeffnungen") or []:
        umlauf.sperre_oeffnung(o, cfg["oeffnung_suchabstand"], cfg["oeffnung_randabstand"])
        if o.get("typ") == "tuer":
            proj = umlauf.projiziere(o["p"])
            if proj[0] <= cfg["oeffnung_suchabstand"]:
                tueren.append((int(o.get("prio", 1)), natuerlicher_schluessel(o.get("kennung")),
                               round(o["p"][0], 3), round(o["p"][1], 3), proj[1]))
    if tueren:
        tueren.sort()
        umlauf.start = tueren[0][4]
    else:
        # Ohne Tuer: Ecke unten links als Startpunkt.
        ecke = min(umlauf.segs, key=lambda s: (round(s["p0"][0] + s["p0"][1], 3),
                                              round(s["p0"][0], 3)))
        umlauf.start = ecke["s0"]
        warnungen.append("Raum %s: keine Tuer gefunden, Startpunkt = Raumecke unten links."
                         % raum.get("nummer"))
    start = _punkt_bei(umlauf, umlauf.start)
    raum["_start"] = (start[0], start[1], 0.0)
    raum["_umlauf"] = umlauf
    return umlauf


def _punkt_bei(umlauf, s_abs):
    s_abs = s_abs % umlauf.laenge
    for s in umlauf.segs:
        if s["s0"] <= s_abs <= s["s0"] + s["len"] + 1e-9:
            t = s_abs - s["s0"]
            return (s["p0"][0] + t * s["dx"], s["p0"][1] + t * s["dy"])
    return umlauf.segs[0]["p0"]


def _smartboards_zuordnen(modell, raeume, warnungen):
    for sb in modell.get("smartboards") or []:
        if sb.get("raum") is not None:
            ziel = [r for r in raeume if r.get("id") == sb["raum"] or r.get("nummer") == sb["raum"]]
        else:
            ziel = []
            mitte = (float(sb["uk_abs"]) + float(sb["ok_abs"])) / 2.0
            for r in raeume:
                boden = float(r.get("geschoss_hoehe") or 0.0)
                hoehe = float(r.get("raumhoehe") or 4.0)
                if not (boden - 0.05 <= mitte <= boden + hoehe + 0.05):
                    continue
                u = r["_umlauf"]
                d = u.projiziere(sb["p"])[0]
                if u.enthaelt(sb["p"]) or d <= 0.5:
                    ziel.append((0 if u.enthaelt(sb["p"]) else 1, d, r))
            ziel.sort(key=lambda z: z[:2])
            ziel = [z[2] for z in ziel]
        if not ziel:
            warnungen.append("Smartboard bei (%.2f, %.2f) liegt in keinem Raum."
                             % (sb["p"][0], sb["p"][1]))
            continue
        ziel[0].setdefault("_smartboards", []).append(sb)


# ---------------------------------------------------------------------------
# Platzierung je Raum
# ---------------------------------------------------------------------------
def _dose(raum, typ, punkt, montage="wand"):
    d = dict(punkt)
    d.update({"typ": typ, "montage": montage, "raum_id": raum.get("id"),
              "raum_nummer": raum.get("nummer"), "raum_name": raum.get("name"),
              "geschoss": raum.get("geschoss"),
              "z_abs": float(raum.get("geschoss_hoehe") or 0.0) + punkt["z"]})
    return d


def _smartboard_dosen(raum, cfg):
    umlauf = raum["_umlauf"]
    sbc = cfg["smartboard"]
    dosen = []
    boden = float(raum.get("geschoss_hoehe") or 0.0)
    for sb in raum.get("_smartboards") or []:
        if "uk" in sb:
            uk, ok = float(sb["uk"]), float(sb["ok"])
        else:
            uk, ok = float(sb["uk_abs"]) - boden, float(sb["ok_abs"]) - boden
        _, _, seg, t = umlauf.projiziere(sb["p"])
        halb = float(sb.get("breite") or 2.0) / 2.0
        achse = sbc["mitte_achsabstand"] / 2.0
        seite = halb + sbc["seitlicher_abstand"]
        # Blick auf die Tafel: links = kleineres s (Umlauf im Uhrzeigersinn).
        plan = [(TYP_SB_LINKS, t - seite, ok), (TYP_SB_MITTE, t - achse, (uk + ok) / 2.0),
                (TYP_SB_MITTE, t + achse, (uk + ok) / 2.0), (TYP_SB_RECHTS, t + seite, ok)]
        for typ, tt, z in plan:
            tt = min(max(tt, 0.05), seg["len"] - 0.05)
            dosen.append(_dose(raum, typ, umlauf.punkt(seg, tt, z)))
    return dosen


def _standard_dosen(raum, cfg, anzahl, fest, warnungen):
    umlauf = raum["_umlauf"]
    kand = umlauf.kandidaten(cfg["dosen_hoehe"], cfg["raster"], cfg["eckabstand"])
    dmin = cfg["mindestabstand"]
    fassade = [k for k in kand if k["fassade"]] if cfg["fassade_zuerst"] else []
    rest = [k for k in kand if not (k["fassade"] and cfg["fassade_zuerst"])]
    gewaehlt = waehle_punkte(fassade, anzahl, fest, dmin)
    if len(gewaehlt) < anzahl and rest:
        # "Folgend": weiter im Uhrzeigersinn hinter dem letzten Fassadenpunkt.
        if fassade:
            ende = fassade[-1]["s"]
            rest.sort(key=lambda k: (k["s"] - ende) % umlauf.laenge)
        fest2 = list(fest) + [_xyz(g) for g in gewaehlt]
        gewaehlt = gewaehlt + waehle_punkte(rest, anzahl - len(gewaehlt), fest2, dmin)
    if len(gewaehlt) < anzahl:
        warnungen.append("Raum %s: nur %d von %d Doppeldosen platzierbar (Mindestabstand %.2f m)."
                         % (raum.get("nummer"), len(gewaehlt), anzahl, dmin))
    return [_dose(raum, TYP_STANDARD, g) for g in gewaehlt]


def _streue_innen(umlauf, anzahl, wandabstand):
    """``anzahl`` Punkte im Raum, moeglichst weit voneinander und von den Waenden."""
    raster = umlauf.innenraster()
    if not raster:
        return []
    erlaubt = [r for r in raster if r[2] >= wandabstand] or raster
    punkte = [max(erlaubt, key=lambda r: r[2])]
    while len(punkte) < anzahl:
        naechster = max(erlaubt, key=lambda r: min(
            [2.0 * r[2]] + [math.hypot(r[0] - q[0], r[1] - q[1]) for q in punkte]))
        if naechster in punkte:
            break
        punkte.append(naechster)
    return punkte


def _decken_dosen(raum, cfg):
    anzahl = 0
    for r in cfg["decken_regeln"]:
        if _enthaelt(_funktionstext(raum), r.get("funktion_enthaelt")):
            anzahl = int(r.get("anzahl") or 0)
            break
    if anzahl <= 0:
        return []
    umlauf = raum["_umlauf"]
    hoehe = float(raum.get("raumhoehe") or 3.0)
    dosen = []
    for (x, y, _) in _streue_innen(umlauf, anzahl, cfg["decken_wandabstand"]):
        s_abs = umlauf.projiziere((x, y))[1]
        dosen.append(_dose(raum, TYP_DECKE, {"x": x, "y": y, "z": hoehe, "nx": 0.0, "ny": 0.0,
                                             "s": umlauf.rel(s_abs), "fassade": False,
                                             "wand": None}, "decke"))
    return dosen


def _raum_reihenfolge(raum, dosen, cfg):
    """Erst bodennahe Wanddosen, dann hohe Wanddosen, dann Decke - je im Umlauf."""
    start = raum["_start"]
    raster = max(cfg["raster"], 1e-6)

    def schluessel(d):
        if d["montage"] == "decke":
            stufe = 2
        elif d["z"] <= cfg["bodennah_bis_hoehe"] + 1e-9:
            stufe = 0
        else:
            stufe = 1
        return (stufe, int(round(d["s"] / raster)), _dist3(_xyz(d), start))

    return sorted(dosen, key=schluessel)


# ---------------------------------------------------------------------------
# WLAN
# ---------------------------------------------------------------------------
def wlan_hoehe(raum, cfg, warnungen):
    w = cfg["wlan"]
    rh = raum.get("raumhoehe")
    if not rh:
        return w["hoehe_soll"]
    frei = float(rh) - w["abstand_unter_decke"]
    if frei >= w["hoehe_soll"]:
        return w["hoehe_soll"]
    if frei < w["hoehe_min"]:
        warnungen.append("Raum %s: Raumhoehe %.2f m erlaubt keine WLAN-Hoehe von %.2f m."
                         % (raum.get("nummer"), float(rh), w["hoehe_min"]))
    return max(frei, 0.0)


def wlan_klasse(raum, cfg):
    """0 = oeffentlich, 1 = untergeordnet (immer), 2 = untergeordnet (nach Reichweite), None = nie."""
    w = cfg["wlan"]
    text = _funktionstext(raum)
    if _enthaelt(text, w["nie"]):
        return None
    if _enthaelt(text, w["oeffentlich"]):
        return 0
    if _enthaelt(text, w["immer"]):
        return 1
    return 2


def _wlan_dosen(raeume, cfg, warnungen):
    w = cfg["wlan"]
    ergebnis = []
    klassen = [(wlan_klasse(r, cfg), r) for r in raeume]
    reihenfolge = sorted([k for k in klassen if k[0] is not None],
                         key=lambda k: (0 if k[0] == 0 else 1, natuerlicher_schluessel(k[1].get("nummer"))))
    for klasse, raum in reihenfolge:
        umlauf = raum["_umlauf"]
        mitte = umlauf.innenpunkt()
        if klasse == 2:
            nah = [a for a in ergebnis if a["geschoss"] == raum.get("geschoss") and
                   math.hypot(a["x"] - mitte[0], a["y"] - mitte[1]) <= w["reichweite"]]
            if nah:
                continue
        je = w["flaeche_je_ap_oeffentlich"] if klasse == 0 else w["flaeche_je_ap"]
        anzahl = max(1, int(math.ceil(float(raum.get("flaeche") or 0.0) / je - 1e-9)))
        hoehe = wlan_hoehe(raum, cfg, warnungen)
        kand = umlauf.kandidaten(hoehe, cfg["raster"] * 2.0, cfg["eckabstand"])
        if not kand:
            warnungen.append("Raum %s: keine Wandflaeche fuer WLAN-Anschluss." % raum.get("nummer"))
            continue
        aps = [min(kand, key=lambda k: math.hypot(k["x"] - mitte[0], k["y"] - mitte[1]))]
        while len(aps) < anzahl:
            naechster = max(kand, key=lambda k: min(_dist3(_xyz(k), _xyz(a)) for a in aps))
            if min(_dist3(_xyz(naechster), _xyz(a)) for a in aps) < cfg["mindestabstand"]:
                break
            aps.append(naechster)
        aps.sort(key=lambda k: k["s"])
        for i, ap in enumerate(aps):
            d = _dose(raum, TYP_WLAN, ap)
            d["lfd"] = i + 1
            d["oeffentlich"] = klasse == 0
            ergebnis.append(d)
    return ergebnis


# ---------------------------------------------------------------------------
# Patchfelder
# ---------------------------------------------------------------------------
class Patchbelegung(object):
    def __init__(self, cfg):
        self.groesse = int(cfg["ports_je_patchfeld"])
        self.pf = int(cfg["patchfeld_start"])
        self.port = 0
        self.fmt = cfg["format_patch"]

    def sprung(self):
        """Naechstes Patchfeld beginnen, auch wenn das aktuelle nicht voll ist."""
        if self.port > 0:
            self.pf += 1
            self.port = 0

    def belege(self, dose, ports):
        if self.port + ports > self.groesse:
            self.pf += 1
            self.port = 0
        dose["patchfeld"] = self.pf
        dose["port_a"] = self.port + 1
        dose["port_b"] = self.port + ports
        dose["patch"] = self.fmt.format(pf=self.pf, port_a=dose["port_a"], port_b=dose["port_b"],
                                        geschoss=dose.get("geschoss") or "")
        self.port += ports


# ---------------------------------------------------------------------------
# Hauptfunktion
# ---------------------------------------------------------------------------
def platziere(modell, cfg=None):
    """Berechnet alle Dosen. Rueckgabe: {"dosen": [...], "warnungen": [...]}."""
    cfg = config_zusammenfuehren(STANDARD_CONFIG, cfg or {})
    warnungen = []
    raeume = []
    for raum in modell.get("raeume") or []:
        raum = dict(raum)
        if _raum_vorbereiten(raum, cfg, warnungen) is not None:
            raeume.append(raum)
    _smartboards_zuordnen(modell, raeume, warnungen)

    # Geschosse ordnen.
    geschosse = {}
    for r in raeume:
        g = geschosse.setdefault(r.get("geschoss"), {"hoehe": float(r.get("geschoss_hoehe") or 0.0),
                                                     "raeume": []})
        g["raeume"].append(r)
    for g in geschosse.values():
        g["raeume"].sort(key=lambda r: natuerlicher_schluessel(r.get("nummer")))
    if cfg["geschoss_reihenfolge"] == "raumnummer":
        folge = sorted(geschosse.values(),
                       key=lambda g: natuerlicher_schluessel(g["raeume"][0].get("nummer")))
    else:
        folge = sorted(geschosse.values(), key=lambda g: g["hoehe"])

    wlan = _wlan_dosen(raeume, cfg, warnungen) if cfg["wlan"]["aktiv"] else []
    patch = Patchbelegung(cfg)
    alle = []
    for g in folge:
        patch.sprung()
        for raum in g["raeume"]:
            if ist_ausgeschlossen(raum, cfg):
                if raum.get("_smartboards"):
                    warnungen.append("Raum %s ist ausgeschlossen, enthaelt aber ein Smartboard."
                                     % raum.get("nummer"))
                continue
            sb = _smartboard_dosen(raum, cfg)
            anzahl = dosen_anzahl(raum, cfg)
            if cfg["smartboard"]["zaehlt_zur_raumanzahl"]:
                anzahl = max(0, anzahl - len(sb))
            fest = [_xyz(d) for d in sb]
            dosen = sb + _standard_dosen(raum, cfg, anzahl, fest, warnungen)
            dosen += _decken_dosen(raum, cfg)
            if not dosen:
                warnungen.append("Raum %s: keine Doppeldose platzierbar." % raum.get("nummer"))
            for i, d in enumerate(_raum_reihenfolge(raum, dosen, cfg)):
                d["lfd"] = i + 1
                d["nummer"] = cfg["format_dosennummer"].format(
                    raum=raum.get("nummer") or "", lfd=d["lfd"], geschoss=raum.get("geschoss") or "")
                patch.belege(d, int(cfg["ports_je_dose"]))
                alle.append(d)
        aps = [a for a in wlan if a["geschoss"] == g["raeume"][0].get("geschoss")]
        if aps and cfg["wlan"]["eigenes_patchfeld"]:
            patch.sprung()
        for a in aps:  # bereits sortiert: oeffentlich vor untergeordnet
            a["nummer"] = cfg["format_wlannummer"].format(
                raum=a["raum_nummer"] or "", lfd=a["lfd"], geschoss=a["geschoss"] or "")
            patch.belege(a, int(cfg["wlan"]["ports"]))
            alle.append(a)
    for i, d in enumerate(alle):
        d["index"] = i + 1
    return {"dosen": alle, "warnungen": warnungen}


# ---------------------------------------------------------------------------
# Ausgabe
# ---------------------------------------------------------------------------
CSV_SPALTEN = ["index", "nummer", "typ", "montage", "geschoss", "raum_nummer", "raum_name",
               "lfd", "patchfeld", "port_a", "port_b", "patch", "x", "y", "z", "z_abs"]


def csv_text(dosen, dezimalkomma=True):
    zeilen = [";".join(CSV_SPALTEN)]
    for d in dosen:
        zellen = []
        for sp in CSV_SPALTEN:
            w = d.get(sp)
            if isinstance(w, float):
                w = "%.3f" % w
                if dezimalkomma:
                    w = w.replace(".", ",")
            zellen.append("" if w is None else text_type(w).replace(";", ","))
        zeilen.append(";".join(zellen))
    return "\r\n".join(zeilen) + "\r\n"


def csv_schreiben(pfad, dosen):
    with io.open(pfad, "w", encoding="utf-8-sig", newline="") as f:
        f.write(csv_text(dosen))


def svg_text(modell, dosen, geschoss=None):
    """Einfache Grundriss-Vorschau (ein Geschoss) zur Kontrolle ohne Revit."""
    raeume = [r for r in modell.get("raeume") or []
              if geschoss is None or r.get("geschoss") == geschoss]
    pts = [k["p0"] for r in raeume for k in r.get("kontur") or []]
    if not pts:
        return "<svg xmlns='http://www.w3.org/2000/svg'/>"
    x0 = min(p[0] for p in pts) - 1
    x1 = max(p[0] for p in pts) + 1
    y0 = min(p[1] for p in pts) - 1
    y1 = max(p[1] for p in pts) + 1
    m = 40.0

    def X(x):
        return (x - x0) * m

    def Y(y):
        return (y1 - y) * m

    out = ["<svg xmlns='http://www.w3.org/2000/svg' width='%d' height='%d' "
           "font-family='sans-serif' font-size='11'>" % ((x1 - x0) * m, (y1 - y0) * m),
           "<rect width='100%' height='100%' fill='white'/>"]
    for r in raeume:
        for k in r["kontur"]:
            farbe = "#1565c0" if k.get("fassade") else "#555"
            if not k.get("bestueckbar", True):
                farbe = "#bbb"
            out.append("<line x1='%.1f' y1='%.1f' x2='%.1f' y2='%.1f' stroke='%s' stroke-width='%d'/>"
                       % (X(k["p0"][0]), Y(k["p0"][1]), X(k["p1"][0]), Y(k["p1"][1]), farbe,
                          4 if k.get("fassade") else 2))
        for o in r.get("oeffnungen") or []:
            out.append("<circle cx='%.1f' cy='%.1f' r='%.1f' fill='none' stroke='%s'/>"
                       % (X(o["p"][0]), Y(o["p"][1]), o.get("breite", 1.0) * m / 2,
                          "#2e7d32" if o.get("typ") == "tuer" else "#90caf9"))
        cx = sum(k["p0"][0] for k in r["kontur"]) / len(r["kontur"])
        cy = sum(k["p0"][1] for k in r["kontur"]) / len(r["kontur"])
        out.append("<text x='%.1f' y='%.1f' text-anchor='middle' fill='#999'>%s %s</text>"
                   % (X(cx), Y(cy), r.get("nummer"), r.get("funktion") or ""))
    for sb in modell.get("smartboards") or []:
        out.append("<rect x='%.1f' y='%.1f' width='10' height='10' fill='#ffb300'/>"
                   % (X(sb["p"][0]) - 5, Y(sb["p"][1]) - 5))
    farben = {TYP_STANDARD: "#d32f2f", TYP_WLAN: "#7b1fa2", TYP_DECKE: "#00838f"}
    for d in dosen:
        if geschoss is not None and d["geschoss"] != geschoss:
            continue
        f = farben.get(d["typ"], "#ef6c00")
        out.append("<circle cx='%.1f' cy='%.1f' r='4' fill='%s'/>" % (X(d["x"]), Y(d["y"]), f))
        out.append("<text x='%.1f' y='%.1f' fill='%s'>%s%s</text>"
                   % (X(d["x"] + d["nx"] * 0.25) + 3, Y(d["y"] + d["ny"] * 0.25) + 4, f,
                      "AP" if d["typ"] == TYP_WLAN else "", d["lfd"]))
    out.append("</svg>")
    return "\n".join(out)


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="Daten-Doppeldosen platzieren (ohne Revit).")
    ap.add_argument("modell", help="Modell als JSON")
    ap.add_argument("-c", "--config", help="Konfiguration als JSON")
    ap.add_argument("-o", "--csv", help="Ergebnisliste als CSV schreiben")
    ap.add_argument("--svg", help="Grundriss-Vorschau schreiben")
    ap.add_argument("--geschoss", help="Geschoss fuer die Vorschau")
    args = ap.parse_args(argv)
    with io.open(args.modell, "r", encoding="utf-8-sig") as f:
        modell = json.load(f)
    ergebnis = platziere(modell, config_laden(args.config))
    for d in ergebnis["dosen"]:
        print("%-10s %-18s %-6s %-16s z=%.2f" % (d["nummer"], d["typ"], d["geschoss"],
                                                 d["patch"], d["z"]))
    for w in ergebnis["warnungen"]:
        print("WARNUNG: " + w)
    if args.csv:
        csv_schreiben(args.csv, ergebnis["dosen"])
    if args.svg:
        with io.open(args.svg, "w", encoding="utf-8") as f:
            f.write(svg_text(modell, ergebnis["dosen"], args.geschoss))
    return 0


if __name__ == "__main__":
    sys.exit(main())
