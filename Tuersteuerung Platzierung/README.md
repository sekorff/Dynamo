# Türsteuerung platzieren

Setzt für ausgewählte Türen eines IFC-Modells die Bauteile der Türsteuerung als Quader mit ihren Außenmaßen, weist IFC-Klasse und Typ zu und verbindet sie mit Leerrohren. Es gibt eine Aufputz- und eine Unterputz-Variante.

Grundlage ist das Ausführungsschema: Drehtürantrieb dormakaba ED 100/250, Türzentrale GEZE TZ 320 BSN, Haftmagnet GEZE MA 500.

![Vorschau beider Varianten](vorschau.png)

## Dateien

| Datei | Inhalt |
|---|---|
| `platzierung.json` | Die Platzierungsanweisung: Bauteile, Maße, IFC-Klassen, Positionen, Leerrohre |
| `tuersteuerung_platzieren.py` | Wendet die Anweisung auf Türen eines IFC-Modells an |
| `testmodell_erzeugen.py` | Baut das Testmodell und beide Varianten |
| `testmodell_pruefen.py` | Prüft die erzeugten Modelle gegen die Anweisung |
| `testmodell.ifc`, `testmodell_AP.ifc`, `testmodell_UP.ifc` | Testmodell leer, Aufputz, Unterputz |

## Auslösen

Voraussetzung: Python 3 und `pip install ifcopenshell`. Das Modell muss IFC4 oder IFC4X3 sein.

```
python tuersteuerung_platzieren.py modell.ifc -o modell_AP.ifc --variante AP
python tuersteuerung_platzieren.py modell.ifc -o modell_UP.ifc --variante UP
```

Welche Türen bestückt werden:

| Angabe | Wirkung |
|---|---|
| keine | Türen mit dem Merkmal `Tuersteuerung.Fluchttuersteuerung = TRUE` |
| `--tuer <GlobalId>` | genau diese Tür, mehrfach möglich |
| `--name "T0[12]"` | Türen, deren Name auf den regulären Ausdruck passt |
| `--alle` | alle Türen |

Ein zweiter Lauf auf dieselbe Tür ersetzt die frühere Bestückung. So lässt sich eine Tür von AP auf UP umstellen.

## Bauteile

| BMK | Bauteil | B × H × T in mm | IFC-Klasse | PredefinedType | Maße aus |
|---|---|---|---|---|---|
| -A2 | Drehtürantrieb ED 100/250 | 685 × 70 × 130 | IfcActuator | ELECTRICACTUATOR | dormakaba Montageanleitung |
| -A1 | Türzentrale TZ 320 BSN | AP 77 × 197 × 88, UP 98 × 260 × 11,5 | IfcUnitaryControlElement | CONTROLPANEL | GEZE Produktdatenblätter |
| -Y1 | Haftmagnet MA 500 | 265 × 66 × 41 | IfcActuator | USERDEFINED „HAFTMAGNET“ | GEZE Produktdatenblatt |
| -S1, -S2 | Taster innen, außen | AP 80 × 80 × 45, UP 80 × 80 × 12 | IfcSwitchingDevice | MOMENTARYSWITCH | Annahme |
| -S5 | Türkontakt | 60 × 13 × 13 | IfcSensor | CONTACTSENSOR | Annahme |
| -B1, -B2 | Sicherheitssensoren | (Türbreite − 100) × 45 × 40 | IfcSensor | MOVEMENTSENSOR | Annahme |
| -X1 | Übergabedose zur Trasse | AP 150 × 100 × 60, UP 160 × 110 × 3 | IfcJunctionBox | POWER | Annahme |
| R1–R8 | Leerrohre M16 / M20 | Außendurchmesser 16 / 20 | IfcCableCarrierSegment | CONDUITSEGMENT | Nenngröße |

IFC kennt keine eigene Klasse für Haftmagnete. `IfcActuator` mit `USERDEFINED` ist die nächstliegende Wahl.

Jedes Bauteil bekommt ein Typobjekt (z. B. `IfcActuatorType`), das Merkmalset `Tuersteuerung` (BMK, Variante, Tür, Maße, Quelle der Maße) und gehört zu einem `IfcDistributionSystem` je Tür.

## Lage an der Tür

Bezug ist die Tür selbst: `u` läuft ab der Bandkante entlang der Türbreite, `z` ab Unterkante Tür. Die Regel spiegelt sich bei Band rechts von selbst. Die Bandseite ist die Seite, zu der die Tür öffnet (IFC: +y der Tür). Die Wanddicke liest das Skript aus der Öffnung.

- **Bandgegenseite (Fluchtseite):** Antrieb über der Tür ab Bandkante, Haftmagnet und Türkontakt oben an der Schlossseite, Türzentrale 200 mm neben der Tür mit Nottaste auf 1050 mm, Taster innen 500 mm neben der Tür auf 850 mm, Übergabedose über der Türzentrale.
- **Bandseite:** Taster außen auf 850 mm, Sicherheitssensor.

Alle Abstände und Höhen stehen als Ausdrücke in `platzierung.json` und lassen sich dort ändern.

## Leerrohre

| Rohr | Größe | Von → Nach | Kabel |
|---|---|---|---|
| R1 | M20 | -A2 → -X1 | -W1 (230 V Antrieb) |
| R2 | M16 | -A2 → -X1 | -W12 (GLT Antrieb) |
| R3 | M20 | -A1 → -X1 | -W2 (230 V Türzentrale) |
| R4 | M20 | -A1 → -X1 | -W3, -W11 (BMA, GLT Türzentrale) |
| R5 | M16 | -A2 → -A1 | -W6 (Kurzzeitentriegelung) |
| R6 | M20 | -Y1 → -A1 | -W4, -W5 (Magnet, Türkontakt) |
| R7 | M16 | -A2 → -S1 | -W7 (Taster innen) |
| R8 | M16 | -A2 → -S2 | -W8 (Taster außen, mit Wanddurchführung) |

- **Aufputz:** Rohre liegen auf der Wandfläche der Bandgegenseite und enden an den Gehäusen.
- **Unterputz:** Rohrachse 35 mm tief in der Wand. Türzentrale, Taster und Übergabedose bekommen zusätzlich einen Einbaukörper in der Wand. An Aufputzgeräten (Antrieb, Magnet) tritt das Rohr mit einem kurzen Stich an der Wandfläche aus. Wände unter 115 mm werden übersprungen.

Die Rohre sind so geordnet, dass sie sich nicht kreuzen. Jedes Segment ist über `IfcRelConnectsElements` mit seinem Nachbarn und den beiden Endgeräten verbunden. 230 V und Signalkabel liegen in getrennten Rohren.

Ohne Leerrohr: die Sensorleitungen -W9/-W10 (Kabelübergang am Türblatt) und die Weiterführung ab der Übergabedose zur Trasse.

## Testmodell

```
python testmodell_erzeugen.py
python testmodell_pruefen.py
```

Das Testmodell hat zwei Wände im rechten Winkel und drei Türen: T01 (Band links, Wand 240 mm), T02 (Band rechts, Wand 175 mm, um 90° gedreht) und T03 ohne Auslöser-Merkmal. Die Prüfung kontrolliert Vollständigkeit, IFC-Klassen und Typen, Außenmaße, Lage der Rohre zur Wand, Durchdringungen und die durchgehende Verbindung jedes Leerrohrs.

## Grenzen

- Die Körper sind Quader mit den Außenmaßen, keine Herstellergeometrie.
- Maße mit Quelle „Annahme“ gehören zu Bauteilen ohne festgelegtes Fabrikat.
- Die Sicherheitssensoren sitzen am Türblatt. Sie sind vor den Wandflächen platziert, nicht auf der tatsächlichen Blattebene.
- Rohre verlaufen rechtwinklig mit Eckstoß, ohne Bögen und ohne Biegeradius.
- Das Skript prüft nicht, ob neben und über der Tür genug freie Wand vorhanden ist (benötigt: 600 mm neben der Schlossseite, 420 mm über der Tür).
- Die Regel geht davon aus, dass der Ursprung der Tür auf Höhe Fertigfußboden liegt.
