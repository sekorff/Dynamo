---
name: regenwasser-schema
description: Erzeugt deterministisch ein Regenwasseranlagen-Schema (Prinzipschema Grundstücksentwässerung Regenwasser) als PDF, DIN A3 quer. Verwenden, wenn ein Regenwasser-/Entwässerungsschema mit Übergabeschacht, Drosselschacht, Regenrückhaltebecken, Stauraumkanal (überdimensionierte Leitung), überdimensionierter Rinne, Schwammdach, Retentionsdach (extensiv/intensiv begrünt), PV oder Regenwassernutzung nach den Standortvorgaben des Versorgers gebraucht wird.
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
   Voraussetzung: Python 3 mit `reportlab` (`pip install reportlab`).
4. **Ergebnis melden.** `FEHLER:` (Exit-Code 2, kein PDF) bedeutet: Konfiguration
   ungültig oder eine Versorgervorgabe ist verletzt. Behebe das nicht durch
   Lockern der Vorgabe, sondern lege es dem Nutzer vor. `HINWEIS:`-Zeilen stehen
   auch im PDF im Feld „Hinweise“; gib sie an den Nutzer weiter.

Setze `projekt.datum` nur, wenn der Nutzer ein Datum nennt oder wünscht – das
Skript trägt selbst kein Tagesdatum ein (Determinismus).

## Konfiguration

Unbekannte Schlüssel sind Fehler. Alle Abschnitte außer `daecher` sind optional.

| Schlüssel | Werte | Bedeutung |
|---|---|---|
| `projekt.name`, `.standort`, `.plan_nr`, `.index`, `.datum`, `.bearbeiter` | Text | Schriftfeld |
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
| `dachablauf.regenspende_r5_5_l_s_ha` | 300 | Standortwert nach KOSTRA-DWD erfragen; fehlt er, erscheint ein Hinweis im Plan |
| `daecher[].abflussbeiwert` | 1,0 / 0,5 / 0,2 | Cs; Standard nach Begrünung keine / extensiv / intensiv (DIN 1986-100 Tab. 9) |

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
(Rückstausicherung).

## Grenzen

Prinzipschema ohne Maßstab. Es ersetzt keine Bemessung (DIN 1986-100,
DIN EN 12056-3, DWA-A 117/138) und keinen Überflutungsnachweis; das steht auch
auf dem Plan. Mehr als vier Dachflächen: auf mehrere Schemata aufteilen.
