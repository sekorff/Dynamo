---
name: regenwasser-schema
description: Erzeugt deterministisch ein Regenwasseranlagen-Schema (Prinzipschema Grundstücksentwässerung Regenwasser) als PDF im M&P-Planrahmen mit Plankopf (Skill mp-plankopf, DIN A2 quer, wahlweise A1/A0). Verwenden, wenn ein Regenwasser-/Entwässerungsschema mit Übergabeschacht, Drosselschacht, Regenrückhaltebecken, Stauraumkanal (überdimensionierte Leitung), überdimensionierter Rinne, Schwammdach, Retentionsdach (extensiv/intensiv begrünt), PV oder Regenwassernutzung nach den Standortvorgaben des Versorgers gebraucht wird.
---

# Regenwasseranlagen-Schema (PDF)

Das Schema wird ausschließlich vom Skript `scripts/generate.py` gezeichnet. Du
zeichnest nichts selbst und änderst das Skript nicht, um ein einzelnes Projekt
abzubilden: Du schreibst eine JSON-Konfiguration und rufst das Skript auf.
Gleiche Konfiguration ergibt eine byte-identische PDF-Datei.

## Ablauf

1. **Vorgaben klären.** Fehlen Angaben zum Standort/Versorger (Einleitstelle,
   Einleitbeschränkung, gefordertes Rückhaltevolumen, Übergabeschacht,
   Zulässigkeit der Regenwassernutzung) oder zu den Dachflächen, frage nach.
   Erfinde keine Versorgervorgaben und keine Bemessungswerte; nicht genannte
   Zahlen bleiben `null` bzw. werden weggelassen.
2. **Konfiguration schreiben** (`<projekt>.json`, UTF-8) nach der Referenz unten.
   Vorlagen: `examples/vollausbau.json`, `examples/retention_offen.json`,
   `examples/minimal.json`.
3. **Prüfen und erzeugen:**
   ```bash
   python3 .claude/skills/regenwasser-schema/scripts/generate.py <projekt>.json --check
   python3 .claude/skills/regenwasser-schema/scripts/generate.py <projekt>.json -o <projekt>.pdf
   ```
   Voraussetzung: Python 3 mit `reportlab` und `pillow` sowie der Skill
   `mp-plankopf` (siehe „Plankopf“).
4. **Ergebnis melden.** `FEHLER:` (Exit-Code 2, kein PDF) bedeutet: Konfiguration
   ungültig oder eine Versorgervorgabe ist verletzt. Behebe das nicht durch
   Lockern der Vorgabe, sondern lege es dem Nutzer vor. `HINWEIS:`-Zeilen stehen
   auch im PDF im Feld „Hinweise“; gib sie an den Nutzer weiter.

Setze `projekt.datum` nur, wenn der Nutzer ein Datum nennt oder wünscht – das
Skript trägt selbst kein Tagesdatum ein (Determinismus).

## Plankopf

Rahmen, Plankopf und Legendenzone kommen **immer** aus dem Skill `mp-plankopf`
(Revit-Familie MP_400_Plankopf, Schrift Avenir LT Std) – auf jeder Seite. Es
gibt kein anderes Schriftfeld und keinen Schalter, um ihn abzuwählen; den
Plankopf nie von Hand nachzeichnen.

- Das Skript sucht den Skill in dieser Reihenfolge: Umgebungsvariable
  `MP_PLANKOPF_SKILL`, Nachbarordner `.claude/skills/mp-plankopf`,
  `~/.claude/skills/mp-plankopf`. Fehlt er oder fehlt die Schrift, bricht das
  Skript mit `FEHLER:` ab – dann den Nutzer um den Skill bitten, nicht ausweichen.
- `mp-plankopf` ist intern (lizenzpflichtige Schrift, Logos) und liegt deshalb
  **nicht** in diesem öffentlichen Repository (`.gitignore`). Auch erzeugte PDFs
  werden nicht eingecheckt.
- Seite 1: Schema im Zeichenbereich, Symbollegende in der Legendenzone über dem
  Plankopf. Seite 2 ff.: Tabellen im Zeichenbereich. Das Zusatzfeld „Blatt“
  trägt die Blattnummer.
- Byte-identische Ausgabe gilt bei gleicher reportlab-Version (mp-plankopf nennt
  4.2.5 als Referenz).

## Konfiguration

Unbekannte Schlüssel sind Fehler. Alle Abschnitte außer `daecher` sind optional.

| Schlüssel | Werte | Bedeutung |
|---|---|---|
| `projekt.name`, `.standort`, `.plan_nr`, `.index`, `.datum`, `.bearbeiter` | Text | Plankopf: Bauvorhaben, Zeichnungs-Nr. mit Index, „erstellt“ |
| `projekt.projekt_nr`, `.bauherr`, `.planungsphase`, `.status` | Text | Plankopf; `status` nur `FREIGEGEBEN`, `ZUR PRÜFUNG`, `VORABZUG` oder leer |
| `blatt.format` | `A2` (Standard) \| `A1` \| `A0` | Blattgröße; das Schema wird maßstäblich in den Zeichenbereich eingepasst |
| `plankopf.logo`, `.felder`, `.freifelder`, `.zusatzfelder`, `.aenderungsindex`, `.optionen` | wie in `mp-plankopf/references/api.md` | überschreibt bzw. ergänzt die abgeleiteten Plankopfwerte (z. B. Standort-Logo, geprüft, Änderungsindex) |
| `versorger.name` | Text | Versorger / Entwässerungsbetrieb |
| `versorger.einleitstelle` | `regenwasserkanal` \| `mischwasserkanal` \| `gewaesser` \| `versickerung` | Einleitstelle am Ende der Kette |
| `versorger.einleitbeschraenkung_l_s` | Zahl \| `null` | max. Einleitmenge; erzwingt Drosselschacht mit Qdr ≤ Grenzwert |
| `versorger.rueckhaltevolumen_min_m3` | Zahl \| `null` | gefordertes Rückhaltevolumen; wird gegen die Summe geprüft |
| `versorger.uebergabeschacht_gefordert` | bool | erzwingt `uebergabeschacht.aktiv` |
| `versorger.regenwassernutzung_zulaessig` | bool (Standard `true`) | `false` verbietet `regenwassernutzung.aktiv` |
| `versorger.rueckstauebene`, `.bemerkung` | Text | Angabe im Vorgabenfeld |
| `daecher[]` (1–4) | Liste | Dachflächen von links nach rechts |
| `daecher[].name` | Text | Bezeichnung |
| `daecher[].typ` | `standard` \| `schwammdach` \| `retentionsdach` | Dachaufbau |
| `daecher[].begruenung` | `keine` \| `extensiv` \| `intensiv` | Schwamm-/Retentionsdach verlangen `extensiv` oder `intensiv` |
| `daecher[].pv` | bool | mit / ohne PV |
| `daecher[].retentionsvolumen_m3` | Zahl \| `null` | Beschriftung; zählt zum Rückhalt |
| `notueberlauf` | bool (Standard `true`) | Notüberlauf über Attika |
| `rinne.vorhanden`, `.ueberdimensioniert` | bool | Entwässerungsrinne, optional als Rückhalt |
| `rinne.bezeichnung`, `.volumen_m3` | Text, Zahl | z. B. „NW 300, L = 25 m“; Volumen zählt zum Rückhalt |
| `stauraumkanal.aktiv`, `.dn`, `.laenge_m` | bool, Zahl, Zahl | überdimensionierte Leitung; V = π·d²/4·L wird berechnet |
| `rueckhaltebecken.aktiv`, `.volumen_m3`, `.bauart` | bool, Zahl, `unterirdisch` \| `offen` | Regenrückhaltebecken |
| `drosselschacht.aktiv`, `.drosselabfluss_l_s` | bool, Zahl | Drosselschacht |
| `uebergabeschacht.aktiv` | bool | Übergabeschacht vor der Grundstücksgrenze |
| `regenwassernutzung.aktiv`, `.zisterne_m3`, `.verwendung[]` | bool, Zahl, Texte | Zisterne mit Filter, Pumpe, Betriebswasserzentrale (max. 5 Verwendungen) |

Jede Dachfläche wird immer mit A = 1000 m² angegeben (fest im Skript, kein
Konfigurationsschlüssel; `flaeche_m2` ist ein Fehler).

**Dachabläufe:** Nennweite fest DN 100. Jeder Ablauf erhält die maximal
anschließbare Fläche `A_max = Q · 10000 / (r(5,5) · Cs)` (auf ganze m²
abgerundet); die Anzahl je Dach ist `aufrunden(1000 / A_max)`. Beides steht in
der Dachbeschriftung.

| Schlüssel | Standard | Bedeutung |
|---|---|---|
| `dachablauf.abflussvermoegen_l_s` | 4,5 | Q je Ablauf DN 100 (Mindestwert DIN EN 1253-1); Herstellerwert einsetzen, wenn bekannt |
| `dachablauf.regenspende_r5_5_l_s_ha` | KOSTRA r(5,5), sonst 300 | nur angeben, wenn keine KOSTRA-Tabelle vorliegt; ein Widerspruch zu `kostra` ist ein Fehler |
| `daecher[].abflussbeiwert` | 1,0 / 0,5 / 0,2 | Cs; Standard nach Begrünung keine / extensiv / intensiv (DIN 1986-100 Tab. 9) |

**Regenspenden (KOSTRA) und Überflutungsnachweis.** Die Regenspenden sind
standortabhängig und werden nie geschätzt oder aus dem Gedächtnis ergänzt: Der
Nutzer liefert die Werte seines Rasterfelds aus der aktuellen KOSTRA-DWD-Tabelle
(derzeit KOSTRA-DWD-2020). Ohne Werte bleibt der Nachweis leer und der Plan
trägt einen roten Hinweis.

| Schlüssel | Standard | Bedeutung |
|---|---|---|
| `kostra.datensatz` | `KOSTRA-DWD-2020` | Bezeichnung des Datensatzes |
| `kostra.rasterfeld` | leer | z. B. „Spalte 123 / Zeile 45“ |
| `kostra.regenspenden_l_s_ha` | – | `{"<D in min>": {"<T in a>": r}}`; T aus 1, 2, 3, 5, 10, 20, 30, 50, 100; max. 6 Dauerstufen. Für den Nachweis nötig: D = 5, 10, 15 mit T = 2, 30, 100 |
| `ueberflutung.wiederkehrzeit_a` | 30 | maßgebender Nachweis: 30 oder 100 (beide werden dargestellt) |
| `ueberflutung.befestigte_flaeche_m2`, `.befestigte_flaeche_cs` | 0 / 1,0 | weitere angeschlossene befestigte Fläche |
| `ueberflutung.schadlos_ueberflutbar_m3` | 0 | Volumen, das schadlos auf dem Grundstück überflutet werden darf |

Dargestellt werden oben rechts die Regenspendentabelle bis T = 100 a und der
Nachweis: `V = (r(D,T) · A_ges / 10000 − Q_ab) · D · 60 / 1000` für D = 5, 10,
15 min, größter Wert maßgebend, verglichen mit Rückhalteräumen + schadlos
überflutbarem Volumen. `Q_ab` ist der Drosselabfluss, ohne Drossel
`Σ r(D,2) · A · Cs / 10000`. Die Tabellenwerte werden auf Monotonie geprüft
(r steigt mit T, fällt mit D); Verstöße sind Fehler.

**Seite 2 ff.: Gerätetabelle und Schnittstellenliste.** Beide Tabellen werden
aus den aktiven Komponenten abgeleitet (feste Reihenfolge Dach → Gebäude →
Grundstück, fester Seitenumbruch). Die Schnittstellenliste hat die Spalten
Schnittstelle, KG (DIN 276), Gegengewerk, Koordination, Anschluss,
Inbetriebnahme; eine Zeile erscheint nur, wenn die Komponente vorhanden ist.
Projektspezifische Ergänzungen (alle Felder Text, optional):

| Schlüssel | Felder |
|---|---|
| `geraete_zusatz[]` | `bezeichnung`, `anzahl`, `kenndaten`, `kg`, `bemerkung` |
| `schnittstellen_zusatz[]` | `schnittstelle`, `kg`, `gegengewerk`, `koordination`, `anschluss`, `inbetriebnahme` |

Standardtexte der Tabellen nicht im Skript projektweise umschreiben, sondern
über die Zusatzlisten ergänzen.

Reihenfolge der Kette ist fest: Dachabläufe → Fallleitungen → Sammelleitung
(→ Rinne) → Zisterne → Stauraumkanal → Rückhaltebecken → Drosselschacht →
Übergabeschacht → Grundstücksgrenze → Einleitstelle.

## Feste Prüfregeln

Fehler (kein PDF):
- Übergabeschacht gefordert, aber nicht aktiv.
- Einleitbeschränkung gesetzt (außer bei Versickerung) ohne Drosselschacht, oder
  Drosselabfluss größer als die Einleitbeschränkung.
- Rückhaltevolumen (Becken + Stauraumkanal + überdimensionierte Rinne +
  Dachretention; die Zisterne zählt nicht) kleiner als gefordert.
- Regenwassernutzung aktiv, obwohl nicht zulässig.
- Schwamm-/Retentionsdach ohne Begrünung; aktive Komponente ohne Pflichtwert.

Hinweise (im PDF rot): Drossel ohne Rückhalteraum, Rückhalteraum ohne Drossel,
PV auf Intensivbegrünung, Übergabeschacht bei Versickerung, Mischwasserkanal
(Rückstausicherung), fehlende/unvollständige KOSTRA-Werte, maßgebender
Überflutungsnachweis nicht erfüllt.

## Grenzen

Prinzipschema ohne Maßstab. Es ersetzt keine Bemessung (DIN 1986-100,
DIN EN 12056-3, DWA-A 117/138); der Überflutungsnachweis ist ein Überschlag in
Anlehnung an DIN 1986-100 und vom Fachplaner zu prüfen. Das steht auch auf dem
Plan. Mehr als vier Dachflächen: auf mehrere Schemata aufteilen.
