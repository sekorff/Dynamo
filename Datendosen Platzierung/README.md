# Datendosen Platzierung

Platziert Daten-Doppeldosen und WLAN-Anschlusspunkte raumweise in Revit,
nummeriert sie und belegt die Patchfelder.

| Datei | Zweck |
|---|---|
| `Datendosen Platzierung.dyn` | Dynamo-Graph (Dynamo 2.x) mit einem Python-Knoten |
| `datendosen_revit.py` | liest Räume, Wände, Türen/Fenster, Smartboards; setzt die Familien |
| `datendosen_core.py` | Platzierungsregeln, ohne Revit lauffähig |
| `config.json` | alle Einstellungen (Standardwerte) |
| `beispiel/modell.json` | Beispielmodell für den Lauf ohne Revit |
| `tests/` | Tests des Regelwerks |

## Regeln

- **Räume:** Jeder Raum, dessen Funktion nicht `Fläche` ist, erhält mindestens eine
  Doppeldose. Weitere nach Raumgröße: eine je angefangene 10 m² (`dosen_standard`,
  je Funktion abweichend über `dosen_regeln`).
- **Mindestabstand:** 1,35 m zwischen den Dosen eines Raums (räumlich gemessen).
  Die vier Dosen eines Smartboards sind untereinander ausgenommen.
- **Reihenfolge der Räume:** je Geschoss von unten nach oben, darin nach
  alphanumerisch kleinster Raumnummer (`2` vor `10`, Ziffern vor Buchstaben).
- **Im Raum:** Startpunkt ist die Tür (bevorzugt die zum Flur). Von dort links herum,
  in der Draufsicht also im Uhrzeigersinn. Erst bodennahe Wanddosen (bis 1,50 m),
  dann höhere Wanddosen, dann Deckendosen – jeweils wieder vom selben Startpunkt.
  Liegen zwei Dosen an derselben Stelle, entscheidet der 3D-Abstand zum Startpunkt.
- **Standardplatzierung:** zuerst an der Fassade (Außenwand), dann auf den im
  Umlauf folgenden Wänden. Nicht in Tür-/Fensteröffnungen, 0,30 m Eckabstand.
- **Smartboard:** zwei Doppeldosen mittig hinter dem Board, je eine links und
  rechts auf Höhe der Oberkante.
- **WLAN:** erst öffentliche Bereiche (Flur, Foyer, Aula …), danach untergeordnete
  Räume. Höhe 2,50 m OKFF, bei niedriger Decke bis hinab auf 2,30 m.
  WLAN richtet sich nicht nach dem Ausschluss `Fläche`.
- **Patchfelder:** 24 Ports, 2 je Doppeldose. Mit jedem Geschoss beginnt ein neues
  Patchfeld, auch wenn das vorige nicht voll ist. Je Geschoss erst die Datendosen,
  dann die WLAN-Anschlüsse.

## Anwendung in Revit

1. Dosenfamilie laden und in `config.json` unter `revit.familie_datendose`
   (und `familie_wlan`) eintragen. Am einfachsten ist eine nicht gehostete Familie.
2. `Datendosen Platzierung.dyn` öffnen, den Ordner mit den Skripten wählen.
3. Mit `Platzieren? = False` laufen lassen: Es wird nur gerechnet, das Ergebnis
   und die Hinweise stehen im Watch-Knoten.
4. Mit `True` werden die Familien gesetzt. Zuvor automatisch gesetzte Dosen
   (Kommentar beginnt mit `DATENDOSE-AUTO`) werden dabei ersetzt.

Nummer und Patchbelegung stehen im Kommentar und im Kennzeichen; sind die
Parameter `Patchfeld`, `Port`, `Raumnummer`, `Dosentyp` an der Familie vorhanden,
werden sie ebenfalls gefüllt. Mit `revit.csv_pfad` entsteht zusätzlich eine Liste.

Räume werden aus dem Projekt gelesen, sonst MEP-Räume, sonst die Räume der
verknüpften Modelle. Smartboards sind Familien, deren Name `Smartboard` enthält.

## Ohne Revit

    python datendosen_core.py beispiel/modell.json --svg vorschau.svg --geschoss EG
    python -m unittest discover -s tests

## Stand

Das Regelwerk ist getestet. Der Revit-Teil wurde ohne Revit geschrieben und ist
dort noch nicht gelaufen – zuerst im Probelauf und an einer Projektkopie prüfen.
