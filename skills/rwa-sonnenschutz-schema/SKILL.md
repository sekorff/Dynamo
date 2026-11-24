---
name: rwa-sonnenschutz-schema
description: Erstellt Funktions- und Ausführungsschemata für RWA-Fenster mit außenliegendem Sonnenschutz und innenliegender Verdunkelung (TGA / Elektro / Gebäudeautomation) – mit Herstellerklemmen, Kabelliste und maximalen Leitungslängen, Bauteilliste, Schnittstellenliste nach DIN 276 und Leistungsbilanz der Motorabgänge. Verwenden bei Anfragen zu RWA, NRWG, Rauchabzug, Sonnenschutz- oder Verdunkelungsansteuerung im Brandfall, Notstrom-Kit, Brandfallsteuerung, BMA-Ansteuerung, GLT-Lüftungsfunktion mit Sicherheitskette oder Schnittstellenlisten zwischen diesen Gewerken.
---

# RWA-Fenster mit Sonnenschutz und Verdunkelung

Der Skill liefert in Stufen ein abstimmungsfähiges Planungsdokument (A3 quer, PDF):

1. **Funktionsschema** – herstellerneutral: Auslösung, Ablauf, Sicherheitskette, Meldungen.
2. **Ausführungsschema** – konkrete Geräte, Klemmen, Kabelliste mit Typ und maximaler Länge.
3. **Bauteilliste und Schnittstellenliste** – Zuständigkeiten nach Kostengruppen DIN 276.
4. **Leistungsbilanz** – Motorabgänge, Notstromquellen, Netzabgänge, Ausbaugrenzen.

Nur die Stufen liefern, die verlangt sind. Sprache der Dokumente: Deutsch.

## Vorgehen

### 1. Anforderungen festhalten

Aus der Anfrage ableiten, nur bei echten Lücken nachfragen:

- Welche Anlage ist führend (in der Regel die RWA-Zentrale)?
- Auslösung: RWA-Taster, BMA, Rauchmelder.
- Reihenfolge und Zeitvorgabe (z. B. Behänge in 60 s in Parkposition, danach Fenster auf).
- Rückmeldungen, Meldungen an die GLT, Lüftungsfunktion mit Sicherheitskette.
- Notstrom je Anlage.
- Hersteller. Stellt der genannte Hersteller die Geräteart nicht her, das sagen und den wahrscheinlich gemeinten nennen (Beispiel: DEHN fertigt keine RWA-Zentralen, gemeint war D+H).

### 2. Funktionsschema

Vorlage: `assets/beispiel/RWA_Sonnenschutz_Verdunkelung_Schema.svg`. Immer enthalten: Ablauf Brandfall als Schrittkette mit Zeitstrahl, Sicherheitskette Lüftung als UND-Verknüpfung, Legende, Planungshinweise.

### 3. Ausführungsschema

1. **Originalanleitungen der Hersteller beschaffen und lesen** (Anschlusspläne als Bild ansehen, nicht nur den Text). Ohne Originalunterlage keine Klemmenbezeichnung und keine Artikelnummer eintragen. Geprüfte Daten für D+H und WAREMA: `references/herstellerdaten-dh-warema.md`.
2. Prüfen, ob der Hersteller die Aufgabe selbst löst (bei D+H das Jalousiemodul AT 41 mit eigenem Anschlussplan für 230-V-Beschattung). Die Herstellerlösung geht einer Eigenkonstruktion vor.
3. `scripts/ausfuehrungsschema.py` anpassen und ausführen. Geräte, Klemmen, Kabelliste und Hinweise stehen dort als Daten.
4. Jede Leitung erhält eine Kabelnummer, im Schema ein Etikett mit der maximalen Länge und in der Kabelliste: Klemmen von/nach, Typ, maximale Länge, Grundlage. Rechenweg: `references/leitungslaengen.md`.

### 4. Listen und Leistungsbilanz

Vorlage `assets/seiten_2-3_vorlage.html` anpassen. Kostengruppen: `references/din276-kostengruppen.md`. Zuständigkeiten je Schnittstelle getrennt für Leitung liefern/verlegen, Koordination, Anschließen (beide Seiten) und Inbetriebnahme.

Leistungsbilanz je Motorabgang: zulässig laut Hersteller, angesetzt, Strom, Auslastung, Reserve; dazu Notstromquellen, Netzabgänge, Ausbaugrenzen. Dauerlasten (Koppelrelais) gegen die Akkukapazität rechnen.

### 5. PDF bauen und prüfen

```bash
python3 scripts/ausfuehrungsschema.py schema.svg
scripts/pdf_bauen.sh schema.svg seiten_2-3.html ausgabe.pdf
```

Seiten als Bild rendern und ansehen (`pdftoppm -r 75 -png`). Prüfen: Seitenzahl stimmt, kein Text läuft über Rahmen oder Leitungen, Kabelnummern in Schema, Kabelliste und Schnittstellenliste sind dieselben.

## Regeln

- **Herkunft kennzeichnen.** Drei Arten von Angaben auseinanderhalten: Herstellerwert (mit Dokument und Stand), eigene Berechnung (mit Rechenweg, im Dokument mit * markiert), Annahme (als Annahme benannt, z. B. Antriebsleistung vor Typwahl).
- **Nichts erfinden.** Fehlt eine Klemme, Artikelnummer oder Grenzlänge, steht dort „beim Hersteller anfragen“.
- **Widersprüche zur Vorgabe offen nennen**, im Dokument unter „Vor Ausführung zu klären“ und in der Antwort. Typisch:
  - Die Zentrale hat keinen Eingang für die Rückmeldung; die Reihenfolge ist rein zeitgesteuert.
  - Die Zeitvorgabe passt nicht zur Fahrzeit des Behangs.
  - Die Vorlaufzeit verlängert die Öffnungszeit des NRWG gegenüber EN 12101-2.
  - Der Hersteller stuft sein Produkt als Komfortsteuerung ein; Freigabe über das Brandschutzkonzept nötig.
- **Sicherheitsrichtung.** Ruhestromprinzip: Drahtbruch oder Relaisausfall führt zum sicheren Zustand (Behänge oben). Die GLT ist im Brandfall nie führend. RWA-Alarm hat Vorrang vor Lüftung und GLT-Befehlen.
- **Keine Ausführungsfreigabe.** Das Ergebnis ist ein Entwurf zur Abstimmung mit Herstellern, Elektroplaner und Brandschutz; so beschriften.
- **Zuständigkeiten sind ein Vorschlag**, verbindlich erst mit Leistungsverzeichnissen.

## Dateien

| Pfad | Inhalt |
|---|---|
| `scripts/ausfuehrungsschema.py` | Generator Seite 1 (SVG) mit Zeichenhilfen und Tabellenfunktion |
| `scripts/pdf_bauen.sh` | Fügt SVG und HTML-Seiten zum PDF zusammen |
| `assets/seiten_2-3_vorlage.html` | Bauteilliste, Schnittstellenliste, Leistungsbilanz |
| `assets/beispiel/` | Fertiges Beispiel: Funktionsschema, Ausführungsschema, PDF |
| `references/herstellerdaten-dh-warema.md` | Geprüfte Klemmen, Daten und Grenzen mit Quellen |
| `references/leitungslaengen.md` | Herstellertabellen und Rechenweg für Richtwerte |
| `references/din276-kostengruppen.md` | Kostengruppen und übliche Zuordnung |
