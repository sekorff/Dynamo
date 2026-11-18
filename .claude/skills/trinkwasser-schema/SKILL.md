---
name: trinkwasser-schema
description: Erzeugt deterministisch ein Trinkwasseranlagen-Schema (Strangschema) als PDF aus einer JSON-Beschreibung der Anlage - mit bis zu drei Druckstufen nach den Standortvorgaben des Versorgers (Netzdruck, Druckminderer, Druckerhöhungsanlage), zentraler oder dezentraler Warmwasserbereitung, VE-Wasser und Osmoseanlage, Hygienestation an jedem Strangende und Trennstation vor Außenzapfstellen und adiabater Kühlung. Immer verwenden, wenn ein Trinkwasserschema, Strangschema, Sanitärschema, TW-Schema, Schema der Trinkwasserinstallation oder ein PDF-Plan der Wasserversorgung eines Gebäudes gewünscht oder geändert wird, auch wenn das Wort "Schema" nicht fällt (z. B. "zeichne mir die Trinkwasserversorgung für das Hochhaus mit DEA").
---

# Trinkwasseranlagen-Schema (PDF)

Das Schema wird nicht frei gezeichnet, sondern von
`scripts/trinkwasser_schema.py` aus einer JSON-Konfiguration erzeugt. Deine
Aufgabe ist nur, die Angaben des Nutzers in diese Konfiguration zu übersetzen
und das Skript auszuführen. So entsteht bei gleicher Eingabe immer exakt das
gleiche PDF (byte-identisch), und die Planungsregeln können nicht versehentlich
verletzt werden.

## Ablauf

1. **Angaben sammeln.** Benötigt werden:
   - Projekt: Name, Standort, Datum (das Datum wird nie automatisch gesetzt -
     sonst wäre die Ausgabe nicht reproduzierbar).
   - Versorger-Standortvorgaben: Name, minimaler und maximaler
     Versorgungsdruck am Hausanschluss, ggf. DN, Anschlussart der
     Druckerhöhung (`unmittelbar` / `mittelbar`), weitere Vorgaben als Text.
   - Geschosse von unten nach oben mit Geschosshöhe.
   - Druckstufen (1 bis 3): Geschossbereich, WWB-Art, Stränge, je Strang die
     Geschosse mit Außenzapfstelle bzw. adiabater Kühlung.
   - Optional Wasseraufbereitung: Osmose und/oder VE-Wasser mit Verbrauchern.

   Fehlen Versorgungsdruck, Geschosse oder die Aufteilung der Druckstufen,
   frage nach - diese Werte bestimmen das Schema und dürfen nicht geraten
   werden. Alles andere hat sinnvolle Standardwerte.

2. **Konfiguration schreiben.** Kopiere `assets/beispiel_3_druckstufen.json`
   (Vollausbau) oder `assets/beispiel_minimal.json` als Ausgangspunkt. Alle
   Schlüssel und Standardwerte stehen in `references/konfiguration.md`.

3. **Prüfen und erzeugen.**

   ```bash
   python3 scripts/trinkwasser_schema.py anlage.json --check     # nur Prüfung + Vorbemessung
   python3 scripts/trinkwasser_schema.py anlage.json -o schema.pdf
   ```

   Benötigt Python 3 mit `reportlab` (`pip install reportlab`). Exit-Code 2
   bedeutet einen Konfigurationsfehler; die Meldung nennt den Pfad des
   fehlerhaften Eintrags. Korrigiere die JSON-Datei, nicht das Skript.

4. **Ergebnis übergeben.** Liefere das PDF zusammen mit der JSON-Datei (sie
   ist die reproduzierbare Quelle) und gib die Vorbemessung der Druckstufen
   sowie alle `HINWEIS`-Zeilen der Skriptausgabe an den Nutzer weiter.

## Fest eingebaute Regeln

Diese Regeln setzt das Skript selbst um; sie sind bewusst nicht konfigurierbar,
und die Konfiguration kennt deshalb keine Schalter dafür:

- **Hygienestation (HS) am Ende jedes Stranges** - bei allen
  Trinkwassersträngen und ebenso bei den Osmose- und VE-Strängen.
- **Trennstation (TS)** vor jeder Außenzapfstelle (AZ) und jeder Anbindung
  adiabater Kühlung (AK) sowie vor der Wasseraufbereitung.
- **Höchstens drei Druckstufen**, von unten nach oben, ohne Überlappung.

Wünscht der Nutzer eine Abweichung davon (z. B. Außenzapfstelle ohne
Trennstation), erzeuge das nicht durch Umgehung, sondern weise darauf hin, dass
der Skill diese Vorgaben zwingend umsetzt.

## Versorgung der Druckstufen

Mit `"versorgung": "auto"` (Standard) entscheidet das Skript je Druckstufe aus
den Versorgervorgaben:

- Mindestversorgungsdruck ≥ erforderlicher Druck → **Netzdruck**
- sonst → **Druckerhöhungsanlage (DEA)**; bei `dea_anschluss: "mittelbar"`
  mit Vorbehälter
- Ruhedruck an der untersten Entnahmestelle über dem Grenzwert (5 bar) →
  zusätzlich **Druckminderer (DM)**

`"netzdruck"` oder `"dea"` erzwingen die Versorgungsart. Passt eine Druckstufe
rechnerisch nicht (Netzdruck reicht nicht, Ruhedruck zu hoch), erscheint ein
rot gedruckter Hinweis im Plan und in der Skriptausgabe - dann die Aufteilung
der Druckstufen mit dem Nutzer anpassen. Die Rechnung ist eine überschlägige
Vorbemessung und ersetzt nicht die Bemessung nach DIN 1988-300; die Parameter
lassen sich unter `bemessung` anpassen.

## Ausgabe

Eine PDF-Seite im Querformat; das Blattformat (A3 bis A0) wird aus der
Anlagengröße bestimmt. Links das Gebäude mit Geschossbändern, farbig
hinterlegten Druckstufen und Technikzentrale, rechts Versorgervorgaben,
Druckstufentabelle, Hinweise, Legende und Schriftfeld.
