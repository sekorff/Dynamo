# -*- coding: utf-8 -*-
from __future__ import division, unicode_literals

import io
import json
import math
import os
import sys
import unittest

HIER = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HIER))

import datendosen_core as core  # noqa: E402


def rechteck(nummer, x0, y0, x1, y1, funktion="Büro", geschoss="EG", hoehe=0.0,
             fassade=(), tuer=None, raumhoehe=3.0, **extra):
    """Rechteckraum; fassade = Teilmenge von 'S','O','N','W'; tuer = (x, y)."""
    ecken = {"S": ((x0, y0), (x1, y0)), "O": ((x1, y0), (x1, y1)),
             "N": ((x1, y1), (x0, y1)), "W": ((x0, y1), (x0, y0))}
    kontur = [{"p0": list(ecken[k][0]), "p1": list(ecken[k][1]), "fassade": k in fassade,
               "bestueckbar": True, "wand": "%s-%s" % (nummer, k)} for k in "SONW"]
    raum = {"id": nummer, "nummer": nummer, "name": funktion, "funktion": funktion,
            "geschoss": geschoss, "geschoss_hoehe": hoehe, "raumhoehe": raumhoehe,
            "flaeche": (x1 - x0) * (y1 - y0), "kontur": kontur, "oeffnungen": []}
    if tuer:
        raum["oeffnungen"].append({"p": list(tuer), "breite": 1.0, "uk": 0.0, "ok": 2.1,
                                   "typ": "tuer"})
    raum.update(extra)
    return raum


def beispielmodell():
    return {
        "raeume": [
            rechteck("0.03", 8, 2, 12, 7, "Büro", fassade="N", tuer=(10, 2)),
            rechteck("0.01", 0, 0, 16, 2, "Fläche", tuer=(0, 1), name="Flur"),
            rechteck("0.02", 0, 2, 8, 9, "Klassenraum", fassade="NW", tuer=(7, 2)),
            rechteck("0.04", 12, 2, 16, 7, "WC", tuer=(14, 2)),
            rechteck("1.01", 0, 2, 8, 9, "Büro", geschoss="1.OG", hoehe=3.5,
                     fassade="N", tuer=(7, 2), raumhoehe=2.5),
        ],
        "smartboards": [{"p": [7.95, 5.5], "breite": 2.0, "uk_abs": 0.9, "ok_abs": 2.1}],
    }


def dosen_im_raum(erg, nummer, wlan=False):
    return [d for d in erg["dosen"] if d["raum_nummer"] == nummer
            and (d["typ"] == core.TYP_WLAN) == wlan]


class KernTest(unittest.TestCase):
    def setUp(self):
        self.erg = core.platziere(beispielmodell())

    def test_natuerliche_sortierung(self):
        werte = ["10", "2", "A1", "1.10", "1.2", "1a"]
        self.assertEqual(sorted(werte, key=core.natuerlicher_schluessel),
                         ["1.2", "1.10", "1a", "2", "10", "A1"])

    def test_flaeche_bekommt_keine_dose_aber_jeder_andere_raum(self):
        self.assertEqual(dosen_im_raum(self.erg, "0.01"), [])
        for nr in ("0.02", "0.03", "0.04", "1.01"):
            self.assertGreaterEqual(len(dosen_im_raum(self.erg, nr)), 1, nr)

    def test_anzahl_nach_raumgroesse(self):
        # 20 m2 -> 2, 56 m2 -> 6 (+4 Smartboard)
        self.assertEqual(len(dosen_im_raum(self.erg, "0.03")), 2)
        std = [d for d in dosen_im_raum(self.erg, "0.02") if d["typ"] == core.TYP_STANDARD]
        self.assertEqual(len(std), 6)

    def test_mindestabstand(self):
        for nr in ("0.02", "0.03", "0.04", "1.01"):
            ds = dosen_im_raum(self.erg, nr)
            for i, a in enumerate(ds):
                for b in ds[:i]:
                    if a["typ"].startswith("smartboard") and b["typ"].startswith("smartboard"):
                        continue
                    d = core._dist3(core._xyz(a), core._xyz(b))
                    self.assertGreaterEqual(d, 1.35 - 1e-6, (nr, a["nummer"], b["nummer"]))

    def test_fassade_zuerst(self):
        # Buero 0.03: beide Dosen an der Nordfassade (y = 7)
        for d in dosen_im_raum(self.erg, "0.03"):
            self.assertTrue(d["fassade"])
            self.assertAlmostEqual(d["y"], 7.0)

    def test_weiter_auf_folgewand_wenn_fassade_voll(self):
        modell = {"raeume": [rechteck("1", 0, 0, 3, 10, fassade="N", tuer=(1.5, 0))]}
        erg = core.platziere(modell, {"dosen_standard": {"je_m2": 5.0}})
        ds = erg["dosen"]
        self.assertEqual(len([d for d in ds if d["typ"] == core.TYP_STANDARD]), 6)
        self.assertEqual(len([d for d in ds if d["fassade"]]), 2)
        self.assertEqual(erg["warnungen"], [])

    def test_keine_dose_in_tueroeffnung(self):
        modell = {"raeume": [rechteck("1", 0, 0, 4, 4, tuer=(2, 0))]}
        erg = core.platziere(modell, {"dosen_standard": {"je_m2": 1.0}, "wlan": {"aktiv": False}})
        self.assertGreater(len(erg["dosen"]), 4)
        for d in erg["dosen"]:
            if abs(d["y"]) < 1e-6:
                self.assertGreater(abs(d["x"] - 2.0), 0.5 + 0.15 - 1e-6)

    def test_nummerierung_links_herum_im_uhrzeigersinn(self):
        # Tuer in der Suedwand: links vom Eintretenden liegt Westen -> W, N, O.
        modell = {"raeume": [rechteck("1", 0, 0, 6, 6, tuer=(3, 0))]}
        erg = core.platziere(modell, {"dosen_standard": {"je_m2": 3.0}, "fassade_zuerst": False,
                                      "wlan": {"aktiv": False}})
        ds = sorted(erg["dosen"], key=lambda d: d["lfd"])
        winkel = [math.atan2(d["y"] - 3, d["x"] - 3) for d in ds]
        start = math.atan2(-3, 0)
        lauf = [(start - w) % (2 * math.pi) for w in winkel]  # Uhrzeigersinn = Winkel faellt
        self.assertEqual(lauf, sorted(lauf))
        self.assertLess(ds[0]["x"], 3.0)

    def test_umlaufrichtung_unabhaengig_von_konturrichtung(self):
        a = rechteck("1", 0, 0, 6, 6, tuer=(3, 0))
        b = rechteck("1", 0, 0, 6, 6, tuer=(3, 0))
        b["kontur"] = [{"p0": k["p1"], "p1": k["p0"], "fassade": k["fassade"],
                        "bestueckbar": True, "wand": k["wand"]} for k in reversed(b["kontur"])]
        cfg = {"dosen_standard": {"je_m2": 3.0}, "wlan": {"aktiv": False}}
        pa = [(round(d["x"], 3), round(d["y"], 3)) for d in core.platziere({"raeume": [a]}, cfg)["dosen"]]
        pb = [(round(d["x"], 3), round(d["y"], 3)) for d in core.platziere({"raeume": [b]}, cfg)["dosen"]]
        self.assertEqual(pa, pb)

    def test_smartboard(self):
        sb = [d for d in dosen_im_raum(self.erg, "0.02") if d["typ"].startswith("smartboard")]
        self.assertEqual(sorted(d["typ"] for d in sb),
                         ["smartboard_links", "smartboard_mitte", "smartboard_mitte",
                          "smartboard_rechts"])
        mitte = [d for d in sb if d["typ"] == core.TYP_SB_MITTE]
        for d in mitte:
            self.assertAlmostEqual(d["x"], 8.0)
            self.assertAlmostEqual(d["z"], 1.5)
        self.assertAlmostEqual(sum(d["y"] for d in mitte) / 2, 5.5)
        links = [d for d in sb if d["typ"] == core.TYP_SB_LINKS][0]
        rechts = [d for d in sb if d["typ"] == core.TYP_SB_RECHTS][0]
        # Ostwand, Blick nach Osten: links = Norden.
        self.assertAlmostEqual(links["y"], 5.5 + 1.15)
        self.assertAlmostEqual(rechts["y"], 5.5 - 1.15)
        self.assertAlmostEqual(links["z"], 2.1)
        self.assertAlmostEqual(rechts["z"], 2.1)

    def test_bodennah_vor_hoch_vor_decke(self):
        erg = core.platziere(beispielmodell(), {"decken_regeln": [
            {"funktion_enthaelt": ["Klassenraum"], "anzahl": 2}]})
        ds = sorted(dosen_im_raum(erg, "0.02"), key=lambda d: d["lfd"])
        self.assertEqual([d["lfd"] for d in ds], list(range(1, len(ds) + 1)))
        stufen = [2 if d["montage"] == "decke" else (0 if d["z"] <= 1.5 else 1) for d in ds]
        self.assertEqual(stufen, sorted(stufen))
        self.assertEqual(stufen.count(2), 2)
        for stufe in (0, 1):
            s = [d["s"] for d, st in zip(ds, stufen) if st == stufe]
            self.assertEqual(s, sorted(s))

    def test_gleiche_stelle_entscheidet_3d_abstand(self):
        raum = rechteck("1", 0, 0, 6, 6, tuer=(3, 0))
        raum = dict(raum)
        u = core._raum_vorbereiten(raum, core.STANDARD_CONFIG, [])
        seg = u.segs[0]
        oben = core._dose(raum, core.TYP_STANDARD, u.punkt(seg, 1.0, 1.2))
        unten = core._dose(raum, core.TYP_STANDARD, u.punkt(seg, 1.0, 0.3))
        folge = core._raum_reihenfolge(raum, [oben, unten], core.STANDARD_CONFIG)
        self.assertEqual([d["z"] for d in folge], [0.3, 1.2])

    def test_reihenfolge_und_patchfeldsprung_je_geschoss(self):
        daten = [d for d in self.erg["dosen"]]
        self.assertEqual(daten[0]["raum_nummer"], "0.02")  # 0.01 ist Flaeche
        eg = [d for d in daten if d["geschoss"] == "EG"]
        og = [d for d in daten if d["geschoss"] == "1.OG"]
        self.assertLess(max(d["index"] for d in eg), min(d["index"] for d in og))
        self.assertNotEqual(eg[-1]["port_b"], 24)  # Patchfeld im EG nicht voll ...
        self.assertEqual(og[0]["patchfeld"], eg[-1]["patchfeld"] + 1)  # ... trotzdem Sprung
        self.assertEqual(og[0]["port_a"], 1)
        belegt = set()
        for d in daten:
            for p in range(d["port_a"], d["port_b"] + 1):
                self.assertNotIn((d["patchfeld"], p), belegt)
                belegt.add((d["patchfeld"], p))
            self.assertLessEqual(d["port_b"], 24)

    def test_wlan_erst_oeffentlich_dann_untergeordnet(self):
        eg = [d for d in self.erg["dosen"] if d["geschoss"] == "EG" and d["typ"] == core.TYP_WLAN]
        self.assertEqual(eg[0]["raum_nummer"], "0.01")  # Flur trotz Funktion "Fläche"
        oeff = [d["oeffentlich"] for d in eg]
        self.assertEqual(oeff, sorted(oeff, reverse=True))
        self.assertIn("0.02", [d["raum_nummer"] for d in eg])  # Klassenraum immer
        self.assertNotIn("0.04", [d["raum_nummer"] for d in eg])  # WC nie
        letzte_daten = max(d["index"] for d in self.erg["dosen"]
                           if d["geschoss"] == "EG" and d["typ"] != core.TYP_WLAN)
        self.assertGreater(min(d["index"] for d in eg), letzte_daten)

    def test_wlan_hoehe(self):
        w = []
        self.assertEqual(core.wlan_hoehe({"raumhoehe": 3.0}, core.STANDARD_CONFIG, w), 2.5)
        self.assertAlmostEqual(core.wlan_hoehe({"raumhoehe": 2.5}, core.STANDARD_CONFIG, w), 2.35)
        self.assertEqual(w, [])
        self.assertAlmostEqual(core.wlan_hoehe({"raumhoehe": 2.4}, core.STANDARD_CONFIG, w), 2.25)
        self.assertEqual(len(w), 1)
        for d in self.erg["dosen"]:
            if d["typ"] == core.TYP_WLAN:
                self.assertGreaterEqual(d["z"], 2.30)

    def test_raum_ohne_platz_meldet_warnung(self):
        modell = {"raeume": [rechteck("1", 0, 0, 2, 2, tuer=(1, 0))]}
        erg = core.platziere(modell, {"dosen_standard": {"je_m2": 0.2}})
        self.assertTrue(any("nur" in w for w in erg["warnungen"]))
        self.assertGreaterEqual(len(erg["dosen"]), 1)

    def test_config_datei_entspricht_standard(self):
        pfad = os.path.join(os.path.dirname(HIER), "config.json")
        with io.open(pfad, encoding="utf-8") as f:
            self.assertEqual(json.load(f), core.STANDARD_CONFIG)

    def test_csv(self):
        text = core.csv_text(self.erg["dosen"])
        self.assertEqual(len(text.strip().splitlines()), len(self.erg["dosen"]) + 1)


if __name__ == "__main__":
    unittest.main()
