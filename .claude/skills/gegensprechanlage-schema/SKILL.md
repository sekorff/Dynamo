---
name: gegensprechanlage-schema
description: Erzeugt deterministisch ein Gegensprechanlagen-Schema (Intercom / Türsprechanlage) als PDF im Format DIN A3 quer. Verwenden, wenn ein Schema, Strangschema oder Übersichtsplan einer IP-Gegensprechanlage gefragt ist - mit Außen- und Innensprechstellen an Türen, Toren, Schranken, Vereinzelungsanlagen, Aufzügen, Pollern, Schleusen, Rampen, Kassenautomaten, Notrufsäulen, Klingeltableaus, Fluchttüren, Paketanlagen und Drehtüren, mit/ohne Video und Zugangskontrolle, Freigaben, PoE-Speisung, LWL-Strecken über 90 m mit Medienkonverter und PoE-Injektor 230 V, TK-Anlage, Wählgerät (IP/Mobilfunk) und Störmeldungen an die GLT.
---

# Gegensprechanlage-Schema (PDF)

Das Schema wird **ausschließlich** vom Skript `scripts/generate_schema.py` gezeichnet.
Die Aufgabe besteht nur darin, die Anlage als JSON zu beschreiben und das Skript auszuführen.
Niemals selbst zeichnen, das Skript nicht für einen Einzelfall verändern und keine Koordinaten vorgeben.

Gleiche JSON-Eingabe ergibt ein byte-identisches PDF (gleiche SHA256-Prüfsumme):
kein Zugriff auf Uhrzeit, Zufall oder Netzwerk, festes Raster, feste Reihenfolge.

## Ablauf

1. **Angaben sammeln.** Fehlende Pflichtangaben beim Nutzer erfragen, nichts erfinden:
   Projektname, Plandatum, Verteiler (PoE-Switches) und je Sprechstelle
   ID, Typ, Einbauort, Video, Zugangskontrolle, Freigaben, Verteiler, Leitungslänge in Metern.
   Ist eine Leitungslänge unbekannt, nachfragen - sie entscheidet über Kupfer oder LWL.
2. **JSON schreiben** nach dem Vorbild `examples/beispiel.json` (UTF-8).
3. **Prüfen:** `python3 scripts/generate_schema.py anlage.json --nur-pruefen`
4. **Erzeugen:** `python3 scripts/generate_schema.py anlage.json -o schema.pdf`
5. Dem Nutzer PDF-Pfad, Blattzahl, die gemeldeten LWL-Strecken und die SHA256 nennen.

Bei `EINGABEFEHLER` (Exit-Code 2) die JSON-Datei korrigieren, nicht das Skript.
Voraussetzung: Python 3 und `reportlab` (`pip install reportlab`).

## Feste Regeln des Generators

| Regel | Umsetzung |
|---|---|
| Vernetzung | durchgängig IP; Sprechstellen hängen an PoE-Switches (Verteiler), diese am Core-Switch |
| Leitungslänge bis 90 m | Kupferleitung, PoE direkt vom Switch |
| Leitungslänge über 90 m | LWL bis Medienkonverter am Einbauort, PoE lokal über Injektor 230 V AC / PoE |
| Verteiler-Uplink über 90 m | LWL-Uplink (SFP), Switch hat eigene 230-V-Versorgung |
| Außensprechstelle | Freigabe lokal über Relaiskontakt auf Türöffner, Tor-, Schranken-, Aufzug-, Poller-, Schleusen-, Fluchttür-, Drehtürsteuerung, Vereinzelungsanlage |
| Notrufsäule | nur Ruf, keine Freigabe zulässig |
| Klingeltableau | Anzahl `ruftasten` ist Pflicht |
| Innensprechstelle | nur an Türen; Freigabe als Fernfreigabe über IP |
| Zentrale | Intercom-Server, TK-Anlage, eigenes Wählgerät (IP und/oder Mobilfunk), Störmeldungen an GLT |
| Blattaufteilung | 10 Sprechstellen je Schemablatt; Verteiler bleiben zusammen, sofern sie auf ein Blatt passen; danach Geräte- und Leitungsliste mit Summen |

Die Grenze von 90 m ist fest (genau 90 m gilt noch als Kupfer).
Reihenfolge im Plan: Verteiler in Eingabereihenfolge, darin Sprechstellen in Eingabereihenfolge.

## JSON-Referenz

Unbekannte Schlüssel werden abgelehnt. Texte nur mit Zeichen aus Windows-1252 (Umlaute sind erlaubt).

### `projekt` (Pflicht)

| Feld | Pflicht | Bedeutung |
|---|---|---|
| `name` | ja | Projektname im Schriftfeld |
| `datum` | ja | Plandatum `JJJJ-MM-TT`; wird nie aus der Systemuhr gelesen |
| `nummer`, `plan_nr`, `bauherr`, `planer` | nein | Schriftfeld |
| `index` | nein | Planindex, Standard `-` |

### `netz` (optional)

`kupfer_typ` (Standard `Cat.7`), `lwl_typ` (Standard `LWL OM4`) - reine Beschriftung.

### `zentrale` (optional, alle Teile haben Standardwerte)

| Objekt | Felder |
|---|---|
| `server` | `bezeichnung`, `ort` |
| `core_switch` | `bezeichnung`, `ort` |
| `tk_anlage` | `vorhanden` (Standard `true`), `bezeichnung`, `schnittstelle` (Standard `SIP-Trunk`) |
| `waehlgeraet` | `vorhanden` (Standard `true`), `bezeichnung`, `wege` (`["ip","mobil"]`, mindestens einer), `ziel` |
| `glt` | `vorhanden` (Standard `true`), `bezeichnung`, `schnittstelle` (Standard `potentialfreie Kontakte`), `meldungen` (1-6 Texte) |

### `verteiler` (Pflicht, mindestens einer)

| Feld | Pflicht | Bedeutung |
|---|---|---|
| `id` | ja | eindeutig, 1-10 Zeichen `A-Z a-z 0-9 . _ -` |
| `bezeichnung`, `ort` | nein | Beschriftung |
| `uplink_laenge_m` | nein | Leitungslänge zum Core-Switch, Standard 0 |

Jeder Verteiler muss mindestens eine Sprechstelle haben.

### `sprechstellen` (Pflicht, mindestens eine)

| Feld | Pflicht | Werte |
|---|---|---|
| `id` | ja | eindeutig über Verteiler und Sprechstellen |
| `typ` | ja | `aussen` oder `innen` |
| `einbauort` | nein | `tuer` (Standard), `tor`, `schranke`, `vereinzelung`, `aufzug`, `poller`, `schleuse`, `rampe`, `kassenautomat`, `notrufsaeule`, `klingeltableau`, `fluchttuer`, `paketanlage`, `drehtuer`; bei `innen` nur `tuer` |
| `ruftasten` | bei `klingeltableau` | Anzahl Ruftasten 1-999; sonst nicht zulässig |
| `bezeichnung` | nein | z. B. "Haupteingang" |
| `video` | nein | `true` / `false` (Standard `false`) |
| `zugangskontrolle` | nein | `true` / `false` (Standard `false`) |
| `freigabe` | nein | Liste aus `tuer`, `tor`, `schranke`, `vereinzelung`, `aufzug`, `poller`, `schleuse`, `fluchttuer`, `drehtuer`; leer = ohne Freigabe |
| `verteiler` | ja* | ID des PoE-Switches (*entfällt bei genau einem Verteiler) |
| `leitungslaenge_m` | ja | Länge Switch bis Sprechstelle in Metern |

Übliche Zuordnung Einbauort zu Freigabe: `rampe` -> `tor`, `kassenautomat` -> `schranke`,
`paketanlage` -> `tuer`, `klingeltableau` -> `tuer`. Aufzugnotruf, WC-Notruf und
Notfall-/Gefahren-Sprechanlagen sind eigene Anlagen und gehören nicht in dieses Schema.

## Dateien

- `scripts/generate_schema.py` - Validierung und PDF-Erzeugung
- `examples/beispiel.json` - vollständiges Beispiel mit allen Varianten
- `examples/beispiel.pdf` - zugehöriges Ergebnis (Referenz für das Erscheinungsbild)
