#!/usr/bin/env python3
"""Weboberflaeche fuer die Rauchmelder-Platzierung.

IFC hochladen, Parameter waehlen, Ergebnis (Plaene, CSV, Meldegruppentabelle,
IFC) ansehen und herunterladen. Die bearbeitete Meldegruppentabelle kann wieder
hochgeladen werden; die Berechnung laeuft dann mit ihr erneut.

Start lokal:   APP_PASSWORT=geheim python webapp.py
Im Container:  gunicorn webapp:app   (siehe Dockerfile)

Umgebungsvariablen
  APP_BENUTZER   Benutzername der Anmeldung (Standard: planer)
  APP_PASSWORT   Passwort der Anmeldung (Pflicht)
  DATEN_ORDNER   Ablage der Auftraege (Standard: ./daten)
  MAX_UPLOAD_MB  groesste IFC-Datei in MB (Standard: 500)
  AUFBEWAHRUNG_TAGE  Auftraege aelter als so viele Tage werden geloescht (Standard: 14)
  RECHENZEIT_S   Abbruch der Berechnung nach so vielen Sekunden (Standard: 1800)
"""
from __future__ import annotations

import hmac
import io
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
import zipfile
from pathlib import Path

from flask import (Flask, Response, abort, redirect, render_template_string, request,
                   send_file, url_for)
from werkzeug.utils import secure_filename

PROGRAMM = Path(__file__).with_name("rauchmelder_platzierung.py")
DATEN = Path(os.environ.get("DATEN_ORDNER", Path(__file__).with_name("daten"))).resolve()
BENUTZER = os.environ.get("APP_BENUTZER", "planer")
PASSWORT = os.environ.get("APP_PASSWORT", "")
AUFBEWAHRUNG = float(os.environ.get("AUFBEWAHRUNG_TAGE", "14")) * 86400
RECHENZEIT = int(os.environ.get("RECHENZEIT_S", "1800"))

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = int(os.environ.get("MAX_UPLOAD_MB", "500")) * 1024 * 1024
DATEN.mkdir(parents=True, exist_ok=True)

# Im Formular einstellbare Parameter: Name -> (Beschriftung, Art, Standard, Auswahl)
PARAMETER = {
    "rasterdecke": ("Rastermaß für Rasterdecken ohne Maßangabe [m] (0 = keine Annahme)", "zahl", "0", None),
    "rasterdecke_alle": ("jede Abhangdecke als Rasterdecke behandeln", "schalter", "", None),
    "raster_ursprung": ("Lage des Plattenrasters, wenn im Modell nicht definiert", "wahl", "mitte",
                        ["mitte", "ecke"]),
    "zd_versatz": ("Versatz Zwischendeckenmelder um eine Rasterplatte", "wahl", "auto",
                   ["auto", "gerade", "diagonal"]),
    "zwischendecke_min": ("Zwischendeckenmelder ab Hohlraumhöhe [m]", "zahl", "0.29", None),
    "dkm_abstand": ("Abstand Druckknopfmelder in Verkehrswegen [m]", "zahl", "25", None),
    "vba_max_flaeche": ("max. Fläche virtueller Brandabschnitt [m²]", "zahl", "1600", None),
    "mg_max_melder": ("max. automatische Melder je Meldegruppe", "ganzzahl", "32", None),
    "erdgeschoss": ("Name des Erdgeschosses (leer = automatisch)", "text", "", None),
    "ifc_ausgabe": ("IFC-Datei mit eingefügten Meldern erzeugen", "schalter", "on", None),
}
AUFTRAG_ID = re.compile(r"^[0-9a-f]{32}$")


# --------------------------------------------------------------------------- #
# Anmeldung und Aufraeumen
# --------------------------------------------------------------------------- #
@app.before_request
def anmeldung():
    if request.endpoint == "bereit":
        return None
    if not PASSWORT:
        return Response("APP_PASSWORT ist nicht gesetzt - Anwendung gesperrt.", 503)
    zugang = request.authorization
    if zugang and zugang.type == "basic" \
            and hmac.compare_digest(zugang.username or "", BENUTZER) \
            and hmac.compare_digest(zugang.password or "", PASSWORT):
        return None
    return Response("Anmeldung erforderlich", 401,
                    {"WWW-Authenticate": 'Basic realm="Rauchmelder-Platzierung"'})


def aufraeumen() -> None:
    grenze = time.time() - AUFBEWAHRUNG
    for ordner in DATEN.iterdir():
        if ordner.is_dir() and AUFTRAG_ID.match(ordner.name) and ordner.stat().st_mtime < grenze:
            shutil.rmtree(ordner, ignore_errors=True)


def auftrag_ordner(auftrag: str) -> Path:
    if not AUFTRAG_ID.match(auftrag):
        abort(404)
    ordner = DATEN / auftrag
    if not ordner.is_dir():
        abort(404)
    return ordner


# --------------------------------------------------------------------------- #
# Berechnung
# --------------------------------------------------------------------------- #
def lese_parameter(formular) -> dict[str, str]:
    """Uebernimmt nur bekannte Parameter und prueft ihre Werte."""
    werte = {}
    for name, (_, art, _, auswahl) in PARAMETER.items():
        roh = (formular.get(name) or "").strip()
        if art == "schalter":
            werte[name] = "on" if roh else ""
        elif art == "wahl":
            if roh not in auswahl:
                abort(400, f"Ungültiger Wert für {name}")
            werte[name] = roh
        elif art in ("zahl", "ganzzahl"):
            try:
                zahl = float(roh.replace(",", "."))
            except ValueError:
                abort(400, f"{name}: Zahl erwartet")
            if not 0 <= zahl <= 100000:
                abort(400, f"{name}: Wert außerhalb des zulässigen Bereichs")
            werte[name] = str(int(zahl)) if art == "ganzzahl" else repr(zahl)
        else:
            if len(roh) > 80 or not re.fullmatch(r"[\w .\-+/]*", roh):
                abort(400, f"{name}: unzulässige Zeichen")
            werte[name] = roh
    return werte


def rechne(ordner: Path) -> None:
    """Startet das Platzierungsprogramm fuer den Auftrag und speichert den Bericht."""
    stand = json.loads((ordner / "auftrag.json").read_text(encoding="utf-8"))
    befehl = [sys.executable, str(PROGRAMM), str(ordner / stand["ifc"]), "-o", str(ordner)]
    for name, wert in stand["parameter"].items():
        art = PARAMETER[name][1]
        schalter = "--" + name.replace("_", "-")
        if art == "schalter":
            if wert:
                befehl.append(schalter)
        elif wert != "":
            befehl += [schalter, wert]
    try:
        lauf = subprocess.run(befehl, capture_output=True, text=True, timeout=RECHENZEIT,
                              cwd=ordner, env={**os.environ, "MPLCONFIGDIR": "/tmp/matplotlib"})
        bericht, fehler, ok = lauf.stdout, lauf.stderr, lauf.returncode == 0
    except subprocess.TimeoutExpired:
        bericht, fehler, ok = "", f"Abbruch: Rechenzeit über {RECHENZEIT} s.", False
    # Serverpfade gehoeren nicht in die Anzeige
    bericht, fehler = (t.replace(str(ordner) + os.sep, "") for t in (bericht, fehler))
    (ordner / "bericht.txt").write_text(bericht, encoding="utf-8")
    (ordner / "fehler.txt").write_text(fehler, encoding="utf-8")
    stand.update(ok=ok, laeufe=stand.get("laeufe", 0) + 1, zeit=time.strftime("%d.%m.%Y %H:%M"))
    (ordner / "auftrag.json").write_text(json.dumps(stand, ensure_ascii=False, indent=1),
                                         encoding="utf-8")


def ergebnisdateien(ordner: Path) -> list[Path]:
    intern = {"auftrag.json", "bericht.txt", "fehler.txt"}
    return sorted(p for p in ordner.iterdir() if p.is_file() and p.name not in intern)


# --------------------------------------------------------------------------- #
# Seiten
# --------------------------------------------------------------------------- #
RAHMEN = """<!doctype html><html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Rauchmelder-Platzierung</title>
<style>
 :root{--tinte:#1f2d3a;--grau:#5d6d7e;--linie:#d5dbe1;--akzent:#c0392b;--flaeche:#f4f6f8}
 body{font:16px/1.5 system-ui,sans-serif;color:var(--tinte);background:#fff;margin:0}
 main{max-width:1100px;margin:0 auto;padding:16px}
 h1{font-size:1.5rem;margin:.2em 0}h2{font-size:1.15rem;margin-top:1.6em;border-bottom:1px solid var(--linie)}
 a{color:var(--akzent)} .hinweis{color:var(--grau);font-size:.92rem}
 fieldset{border:1px solid var(--linie);border-radius:6px;margin:1em 0;padding:.8em 1em}
 label{display:block;margin:.5em 0} input[type=text],select{padding:.35em;min-width:9em}
 button{background:var(--akzent);color:#fff;border:0;border-radius:5px;padding:.6em 1.2em;font-size:1rem;cursor:pointer}
 pre{background:var(--flaeche);padding:12px;overflow-x:auto;font-size:.8rem;border-radius:6px}
 img{max-width:100%;border:1px solid var(--linie);border-radius:6px}
 table{border-collapse:collapse} td,th{padding:.3em .8em;border-bottom:1px solid var(--linie);text-align:left}
 .fehler{background:#fdecea;border:1px solid var(--akzent);padding:10px;border-radius:6px}
</style></head><body><main>
<h1><a href="{{ url_for('start') }}" style="text-decoration:none;color:inherit">Rauchmelder-Platzierung</a></h1>
{% block inhalt %}{% endblock %}
</main></body></html>"""

START = """{% extends rahmen %}{% block inhalt %}
<p>IFC-Datei mit Raumvolumen hochladen. Das Programm setzt Rauch- und Druckknopfmelder,
bildet Meldegruppen und liefert Pläne, Tabellen und optional eine IFC-Datei mit den Meldern.</p>
<form method="post" action="{{ url_for('neu') }}" enctype="multipart/form-data">
 <fieldset><legend>Modell</legend>
  <label>IFC-Datei <input type="file" name="ifc" accept=".ifc,.ifczip" required></label>
  <label>Meldegruppentabelle (optional, bearbeitete CSV aus einem früheren Lauf)
   <input type="file" name="tabelle" accept=".csv"></label>
 </fieldset>
 <fieldset><legend>Parameter</legend>
 {% for name, (text, art, standard, auswahl) in parameter.items() %}
  <label>
  {% if art == 'schalter' %}<input type="checkbox" name="{{ name }}" {{ 'checked' if standard }}> {{ text }}
  {% elif art == 'wahl' %}{{ text }}
    <select name="{{ name }}">{% for a in auswahl %}<option {{ 'selected' if a == standard }}>{{ a }}</option>{% endfor %}</select>
  {% else %}{{ text }} <input type="text" name="{{ name }}" value="{{ standard }}">{% endif %}
  </label>
 {% endfor %}
 </fieldset>
 <button>Berechnen</button>
 <p class="hinweis">Große Modelle brauchen einige Minuten; die Seite lädt, bis das Ergebnis da ist.</p>
</form>
{% if auftraege %}<h2>Bisherige Aufträge</h2><table>
 <tr><th>Modell</th><th>Stand</th><th>Läufe</th></tr>
 {% for a in auftraege %}<tr><td><a href="{{ url_for('ergebnis', auftrag=a.id) }}">{{ a.ifc }}</a></td>
  <td>{{ a.zeit }}</td><td>{{ a.laeufe }}</td></tr>{% endfor %}
</table><p class="hinweis">Aufträge werden nach {{ tage }} Tagen gelöscht.</p>{% endif %}
{% endblock %}"""

ERGEBNIS = """{% extends rahmen %}{% block inhalt %}
<p><strong>{{ stand.ifc }}</strong> · Stand {{ stand.zeit }} · Lauf {{ stand.laeufe }}</p>
{% if not stand.ok %}<div class="fehler"><strong>Die Berechnung ist fehlgeschlagen.</strong>
<pre>{{ fehler }}</pre></div>{% endif %}
{% if dateien %}<h2>Dateien</h2>
<p><a href="{{ url_for('paket', auftrag=auftrag) }}">Alles als ZIP herunterladen</a></p>
<table>{% for d in dateien %}<tr><td><a href="{{ url_for('datei', auftrag=auftrag, name=d.name) }}">{{ d.name }}</a></td>
 <td class="hinweis">{{ '%.0f' % (d.stat().st_size / 1024) }} kB</td></tr>{% endfor %}</table>{% endif %}
<h2>Meldegruppen nachträglich ändern</h2>
<p>Die Datei <code>…_meldegruppen.csv</code> herunterladen, in Excel die Spalten <em>Meldegruppe</em>,
<em>Meldegruppen_Text</em>, <em>Virt_Brandabschnitt</em> oder <em>Bemerkung</em> ändern, als CSV speichern
und hier wieder hochladen. Die Melder werden danach neu durchnummeriert.</p>
<form method="post" action="{{ url_for('tabelle', auftrag=auftrag) }}" enctype="multipart/form-data">
 <input type="file" name="tabelle" accept=".csv" required> <button>Tabelle übernehmen und neu berechnen</button>
</form>
{% for bild in bilder %}<h2>{{ bild.name }}</h2>
<a href="{{ url_for('datei', auftrag=auftrag, name=bild.name) }}"><img src="{{ url_for('datei', auftrag=auftrag, name=bild.name) }}" alt="Plan {{ bild.name }}"></a>{% endfor %}
<h2>Bericht</h2><pre>{{ bericht }}</pre>
{% if stand.ok and fehler %}<h2>Hinweise des Programms</h2><pre>{{ fehler }}</pre>{% endif %}
<form method="post" action="{{ url_for('loeschen', auftrag=auftrag) }}"
      onsubmit="return confirm('Auftrag mit allen Dateien löschen?')">
 <p><button style="background:#5d6d7e">Auftrag löschen</button></p></form>
{% endblock %}"""


@app.get("/bereit")
def bereit():
    return "ok"


@app.get("/")
def start():
    aufraeumen()
    auftraege = []
    for ordner in DATEN.iterdir():
        datei = ordner / "auftrag.json"
        if AUFTRAG_ID.match(ordner.name) and datei.is_file():
            stand = json.loads(datei.read_text(encoding="utf-8"))
            auftraege.append({"id": ordner.name, "mtime": datei.stat().st_mtime, **stand})
    auftraege.sort(key=lambda a: -a["mtime"])
    return render_template_string(START, rahmen=app.jinja_env.from_string(RAHMEN),
                                  parameter=PARAMETER, auftraege=auftraege[:50],
                                  tage=int(AUFBEWAHRUNG / 86400))


@app.post("/neu")
def neu():
    hochgeladen = request.files.get("ifc")
    if hochgeladen is None or not hochgeladen.filename:
        abort(400, "Keine IFC-Datei gewählt")
    name = secure_filename(hochgeladen.filename) or "modell.ifc"
    if not name.lower().endswith((".ifc", ".ifczip")):
        abort(400, "Bitte eine .ifc- oder .ifczip-Datei hochladen")
    parameter = lese_parameter(request.form)
    auftrag = uuid.uuid4().hex
    ordner = DATEN / auftrag
    ordner.mkdir()
    hochgeladen.save(ordner / name)
    tabelle = request.files.get("tabelle")
    if tabelle is not None and tabelle.filename:
        tabelle.save(ordner / f"{Path(name).stem}_meldegruppen.csv")
    (ordner / "auftrag.json").write_text(
        json.dumps({"ifc": name, "parameter": parameter}, ensure_ascii=False), encoding="utf-8")
    rechne(ordner)
    return redirect(url_for("ergebnis", auftrag=auftrag), 303)


@app.post("/auftrag/<auftrag>/tabelle")
def tabelle(auftrag: str):
    ordner = auftrag_ordner(auftrag)
    hochgeladen = request.files.get("tabelle")
    if hochgeladen is None or not hochgeladen.filename:
        abort(400, "Keine Tabelle gewählt")
    stand = json.loads((ordner / "auftrag.json").read_text(encoding="utf-8"))
    # Das Programm sichert die vorherige Tabelle selbst als .bak
    hochgeladen.save(ordner / f"{Path(stand['ifc']).stem}_meldegruppen.csv")
    rechne(ordner)
    return redirect(url_for("ergebnis", auftrag=auftrag), 303)


@app.get("/auftrag/<auftrag>")
def ergebnis(auftrag: str):
    ordner = auftrag_ordner(auftrag)
    stand = json.loads((ordner / "auftrag.json").read_text(encoding="utf-8"))
    dateien = [d for d in ergebnisdateien(ordner) if d.name != stand["ifc"]]
    lies = lambda n: (ordner / n).read_text(encoding="utf-8") if (ordner / n).is_file() else ""  # noqa: E731
    return render_template_string(
        ERGEBNIS, rahmen=app.jinja_env.from_string(RAHMEN), auftrag=auftrag, stand=stand,
        dateien=dateien, bilder=[d for d in dateien if d.suffix == ".png"],
        bericht=lies("bericht.txt"), fehler=lies("fehler.txt"))


@app.get("/auftrag/<auftrag>/datei/<name>")
def datei(auftrag: str, name: str):
    ordner = auftrag_ordner(auftrag)
    erlaubt = {d.name: d for d in ergebnisdateien(ordner)}  # nur vorhandene Dateien des Auftrags
    if name not in erlaubt:
        abort(404)
    return send_file(erlaubt[name], as_attachment=erlaubt[name].suffix != ".png")


@app.get("/auftrag/<auftrag>/paket.zip")
def paket(auftrag: str):
    ordner = auftrag_ordner(auftrag)
    stand = json.loads((ordner / "auftrag.json").read_text(encoding="utf-8"))
    puffer = io.BytesIO()
    with zipfile.ZipFile(puffer, "w", zipfile.ZIP_DEFLATED) as archiv:
        for d in ergebnisdateien(ordner):
            if d.name != stand["ifc"]:
                archiv.write(d, d.name)
        archiv.write(ordner / "bericht.txt", "bericht.txt")
    puffer.seek(0)
    return send_file(puffer, as_attachment=True, mimetype="application/zip",
                     download_name=f"{Path(stand['ifc']).stem}_brandmelder.zip")


@app.post("/auftrag/<auftrag>/loeschen")
def loeschen(auftrag: str):
    shutil.rmtree(auftrag_ordner(auftrag), ignore_errors=True)
    return redirect(url_for("start"), 303)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "8000")))
