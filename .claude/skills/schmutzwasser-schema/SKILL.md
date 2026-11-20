---
name: schmutzwasser-schema
description: Erzeugt deterministisch ein Schmutzwasser-Anlagenschema (Fließschema der Grundstücksentwässerung) als PDF aus einer JSON-Konfiguration und prüft es gegen die Standortvorgaben des Versorgers. Verwenden, wenn ein Schmutzwasserschema, Entwässerungsschema oder Abwasser-Anlagenschema mit Übergabeschacht, Grauwassernutzung, Hebeanlagen, Fett-/Stärkeabscheider, Benzin-/Leichtflüssigkeitsabscheider oder WHG-/AwSV-Flächen gewünscht wird.
---

# Schmutzwasser-Anlagenschema (PDF)

Das Schema wird ausschließlich vom Skript `scripts/schmutzwasser_schema.py` gezeichnet. Du schreibst nur die
JSON-Konfiguration; Layout, Symbole, Regelprüfung und PDF sind fest codiert. Gleiche Eingabe ergibt eine
byte-identische PDF (kein Zeitstempel, kein Zufall). Zeichne nie selbst und ändere das Skript nicht, um eine
Prüfung zu umgehen.

## Plankopf: ausschließlich mp-plankopf

Blattrahmen, Plankopf, Änderungsindex, Zusatzfelder und Legende kommen **ausschließlich** aus dem Skill
`mp-plankopf` (MP_400_Plankopf). Dieses Skript ruft dessen `plankopf.draw()` auf und zeichnet das Schema nur in
die gemeldete Zone `zeichenbereich`; die Legendeneinträge übergibt es an die Legendenzone des Plankopfs. Es gibt
keinen eigenen Rahmen, kein eigenes Schriftfeld und keinen Ersatz: Fehlt `mp-plankopf` oder dessen Schrift
(Avenir LT Std), bricht das Skript mit Exit 1 ab. Dann den Nutzer informieren – nie einen Plankopf nachbauen.

`mp-plankopf` wird in dieser Reihenfolge gesucht: Umgebungsvariable `MP_PLANKOPF_SKILL`, Nachbarordner
`.claude/skills/mp-plankopf`, `~/.claude/skills/mp-plankopf`. Der Skill enthält lizenzpflichtige Schriften und
Logos und liegt deshalb nicht in diesem Repository (per `.gitignore` ausgeschlossen); er wird aus der Datei
`mp-plankopf.skill` (ZIP) an einen dieser Orte entpackt. Erzeugte PDFs enthalten Schrift und Logo und werden
ebenfalls nicht eingecheckt.

Voraussetzung: Python 3 und `pip install -r <mp-plankopf>/requirements.txt` (reportlab, pillow). Byte-identische
Ausgabe gilt bei gleicher reportlab-Version.

## Ablauf

1. **Angaben klären.** Fehlende Angaben beim Nutzer erfragen, nicht erfinden: Projekt/Standort, Versorger und
   dessen Vorgaben (Satzung, Trenn-/Mischsystem, Rückstauebene, Übergabeschacht, Abscheider-, Probenahme- und
   Grauwasserregeln), Abwasseranfallstellen je Strang, Lage zur Rückstauebene, Bemessungswerte (NS, DN, Volumina).
2. **Versorgerprofil wählen oder anlegen.** Vorhandene Profile liegen in `versorger/`. Für einen neuen Versorger
   eine Datei `versorger/<name>.json` aus den echten Vorgaben anlegen. Die mitgelieferten `muster_*`-Profile sind
   Beispielwerte und keine Vorgaben eines realen Versorgers.
3. **Konfiguration schreiben** (Aufbau unten, Vorlagen in `beispiele/`).
4. **Erzeugen:**
   ```bash
   python3 .claude/skills/schmutzwasser-schema/scripts/schmutzwasser_schema.py <konfig>.json -o <ausgabe>.pdf
   ```
5. **Ergebnis auswerten.** Exit 0: PDF erzeugt. Exit 1: Eingabefehler (Meldung nennt den Schlüssel; Meldungen
   mit „Plankopf (mp-plankopf):" stammen aus dem Plankopf-Skill und sind wörtlich weiterzugeben). Exit 2:
   Regelverstoß, keine PDF. Bei Exit 2 die Konfiguration fachlich korrigieren und die Änderung dem Nutzer nennen.
   Nur wenn der Nutzer ausdrücklich einen Entwurf trotz Abweichungen will, `--erlaube-abweichungen` verwenden;
   die PDF trägt dann einen roten Abweichungsvermerk. `--nur-pruefen` prüft ohne PDF.
6. Prüfprotokoll (stdout) samt Warnungen, Hinweisen und `PLANKOPF`-Zeilen an den Nutzer weitergeben.

## Konfiguration

```json
{
  "projekt": { "name": "…", "datum": "18.11.2026", "standort": "…", "bauherr": "…", "plan_nr": "…", "index": "V0", "bearbeiter": "…" },
  "blatt": "A2",
  "plankopf": { "logo": "Gruppe", "felder": { "Status": "VORABZUG", "Planungsphase": "Entwurfsplanung" }, "freifelder": { "Kopf links": "…" } },
  "versorger": "muster_trennsystem",
  "uebergabe": { "schacht": true, "dn": 1000, "anschlusskanal_dn": 150, "probenahme": false, "bezeichnung": "Übergabeschacht ÜS1" },
  "sammelleitung_dn": 150,
  "straenge": [ … ],
  "hinweise": [ "freier Text im Hinweisfeld" ]
}
```

- Pflicht: `projekt.name`, `projekt.datum`, `versorger`, `uebergabe.schacht`, `uebergabe.anschlusskanal_dn`, `straenge`.
- `projekt.datum` wird als Text in den Plankopf übernommen (kein Systemdatum, sonst wäre die Ausgabe nicht
  reproduzierbar).
- `blatt`: Formatname (`A0`–`A3`, Vorgabe `A2`) oder Objekt nach der mp-plankopf-API
  (`{"format": "benutzerdefiniert", "breite_mm": …, "hoehe_mm": …}`). Der Plankopf belegt rechts 180 mm; das
  Schema wird im verbleibenden Zeichenbereich einheitlich bis Faktor 3 vergrößert. Richtwerte: A3 nur für
  Kleinstanlagen (1 Komponente je Strang, kurze Legende), A2 bis 5 Komponenten je Strang, A1 für alles Größere.
  Passt etwas nicht, meldet das Skript es – dann größeres Blatt wählen. A4 hat keinen Zeichenbereich.
- `plankopf` (optional): wird an mp-plankopf durchgereicht – `logo`, `felder`, `freifelder`, `aenderungsindex`,
  `zusatzfelder`, `optionen`. Feldnamen und Regeln stehen in `<mp-plankopf>/references/api.md`; vor dem Befüllen
  lesen. `legende` ist nicht einstellbar, sie wird aus den verwendeten Leitungen und Kürzeln erzeugt.
- Vorbelegung des Plankopfs aus `projekt`: `Planart` = „Schmutzwasser-Anlagenschema", `Maßstab` = „ohne",
  `Plannummer` = `plan_nr`, `aktuelle Änderung Index` = `index`, `erstellt Datum`/`erstellt von` =
  `datum`/`bearbeiter`; Block 1 Bauvorhaben, Block 2 Bauherr, Block 3 Versorger, Block 4 Planinhalt. Angaben
  unter `plankopf` überschreiben das. Bei Regelverstößen (`--erlaube-abweichungen`) wird `Status` zwingend
  `VORABZUG`.
- `versorger`: Profilname aus `versorger/`, Pfad relativ zur Konfiguration oder Inline-Objekt.
- Unbekannte Schlüssel sind Fehler.

### Strang

```json
{
  "id": "S5", "bezeichnung": "Bistro-Küche UG", "abwasserart": "fetthaltig", "dn": 100,
  "quellen": ["Spülmaschine", "Bodenabläufe"],
  "unter_rueckstauebene": true,
  "komponenten": [ { "typ": "fettabscheider", "id": "FA1", "ns": 4 }, { "typ": "probenahme" }, { "typ": "hebeanlage", "ausfuehrung": "doppel" } ],
  "ziel": { "strang": "S9", "vor": "FA9" }
}
```

- `abwasserart`: `haeuslich`, `grauwasser`, `fetthaltig`, `staerkehaltig`, `mineraloelhaltig`, `whg`, `sonstig`.
- `komponenten` in Fließrichtung. Reihenfolge der Stränge = Reihenfolge im Plan von oben nach unten.
- `ziel` (optional): Strang mündet nicht in die Sammelleitung, sondern vor der Komponente mit der ID `vor` in
  einen anderen Strang (z. B. Stärkeabscheider-Ablauf vor den Fettabscheider).

### Komponenten

Jede Komponente kann zusätzlich `id`, `bezeichnung` und `hinweis` (dritte Beschriftungszeile) tragen.

| `typ` | Parameter (fett = Pflicht) |
|---|---|
| `fettabscheider` (mit Schlammfang) | **`ns`**, `schlammfang_l`, `fettspeicher_l` |
| `staerkeabscheider` | `nenngroesse`, `volumen_l` |
| `leichtfluessigkeitsabscheider` | **`ns`**, **`klasse`** (`I`/`II`), `schlammfang_l`, `warnanlage`, `selbsttaetiger_abschluss` |
| `benzinabscheider` | Alias: Leichtflüssigkeitsabscheider Klasse II |
| `koaleszenzabscheider` | Alias: Leichtflüssigkeitsabscheider Klasse I |
| `hebeanlage` (mit Rückstauschleife) | `ausfuehrung` (`einzel`/`doppel`), `foerderstrom_ls`, `foerderhoehe_m`, `druckleitung_dn` |
| `rueckstauverschluss` | `ausfuehrung` (Text) |
| `grauwasseranlage` | `kapazitaet_l_d`, `verwendung` (Liste), `zaehler` |
| `rueckhaltung` (WHG) | **`volumen_m3`** |
| `absperrschieber` (WHG-Havarieschieber) | `automatisch` |
| `neutralisation`, `emulsionsspaltanlage` | – |
| `probenahme`, `kontrollschacht` | `dn` |
| `schlammfang` | `volumen_l` |
| `sonstige` | **`kuerzel`**, `vorbehandlung` (zählt als WHG-Vorbehandlung) |

### Typische Ketten

- Fettabscheider ohne Hebeanlage: `fettabscheider → probenahme`
- Fettabscheider mit Hebeanlage (unter Rückstauebene): `fettabscheider → probenahme → hebeanlage`
- Stärke + Fett: Strang `staerkehaltig` mit `staerkeabscheider` und `ziel` vor den Fettabscheider, oder eigenständig
  in die Sammelleitung – je nach Versorgerprofil.
- Benzinabscheider: `benzinabscheider`/`koaleszenzabscheider → probenahme` (`→ hebeanlage`)
- WHG-Fläche: `absperrschieber → rueckhaltung → Vorbehandlung → probenahme` (`→ hebeanlage`)
- Grauwassernutzung: Strang `grauwasser` mit `grauwasseranlage`; Überlauf läuft in die Sammelleitung,
  Betriebswasser und Trinkwassernachspeisung zeichnet das Symbol.

## Versorgerprofil

Alle Schlüssel optional; fehlende Werte kommen aus `VERSORGER_DEFAULT` im Skript.

```json
{
  "name": "…", "satzung": "…",
  "entwaesserungssystem": "trenn",
  "rueckstauebene": "Straßenoberkante an der Anschlussstelle",
  "uebergabeschacht": { "erforderlich": true, "dn_min": 1000, "lage": "…" },
  "anschlusskanal_dn_min": 150,
  "probenahme_an_uebergabe": false,
  "fettabscheider": { "pflicht_bei_fetthaltig": true, "probenahme_erforderlich": true },
  "staerkeabscheider": { "pflicht_bei_staerkehaltig": true, "ablauf_ueber_fettabscheider": "zulaessig" },
  "leichtfluessigkeitsabscheider": { "min_klasse": "I", "probenahme_erforderlich": true, "warnanlage_erforderlich": true },
  "whg": { "min_klasse": "I", "rueckhaltung_erforderlich": true, "absperrung_erforderlich": true, "probenahme_erforderlich": true },
  "grauwassernutzung": { "zulaessig": true, "anzeigepflicht": true, "zaehler_erforderlich": true },
  "hebeanlage": { "doppelanlage_bei_gewerbe": true },
  "hinweise": ["…"]
}
```

`ablauf_ueber_fettabscheider`: `zulaessig`, `erforderlich` oder `unzulaessig`.

## Prüfregeln

| Code | Regel |
|---|---|
| U01–U04 | Übergabeschacht gefordert / Mindest-DN Schacht / Mindest-DN Anschlusskanal / Probenahme an der Übergabe |
| R01–R03 | Strang unter Rückstauebene braucht Hebeanlage; Rückstauverschluss nur ohne Abscheider (dann Warnung) |
| R04 | Hebeanlage nie vor einem Abscheider |
| R05 | Doppelhebeanlage bei gewerblichem Abwasser, wenn vom Versorger gefordert |
| A01 | Fäkalienhaltiges Abwasser nie über Abscheider (auch nicht über `ziel`) |
| F01–F02 | Fettabscheider-Pflicht, Probenahme dahinter |
| S01–S03 | Stärkeabscheider-Pflicht, Ablauf über Fettabscheider gefordert / unzulässig |
| L01–L05 | Leichtflüssigkeitsabscheider: Pflicht, Mindestklasse, selbsttätiger Abschluss, Warnanlage, Probenahme |
| W01–W04 | WHG-Fläche: Vorbehandlung, Rückhaltung, Absperrung, Probenahme |
| G01–G04 | Grauwasseranlage: nur im Grauwasserstrang, Zulässigkeit, Zähler, Anzeigepflicht (Hinweis) |

Die Regeln bilden Grundsätze aus DIN EN 12056, DIN 1986-100, DIN EN 1825/DIN 4040-100, DIN EN 858/DIN 1999-100
und AwSV vereinfacht ab. Das Schema ist ein nicht maßstäbliches Fließschema; es ersetzt weder Bemessung noch
Entwässerungsantrag. Darauf beim Übergeben der PDF hinweisen.
