# Rauchmelder-Platzierung aus IFC

Platziert punktförmige Rauchmelder automatisch in allen Räumen einer IFC-Datei.
Grundlage sind die Raumvolumen (`IfcSpace`). Die Melder werden je Raum in einem
regelmäßigen Raster gesetzt und raumübergreifend auf gemeinsame Achsen gelegt.
Abhangdecken mit mehr als 29 cm Hohlraum erhalten Zwischendeckenmelder,
durch Unterzüge getrennte Deckenfelder werden wie eigene Räume behandelt.
Bei Rasterdecken sitzen die Melder mittig auf einer Rasterplatte, die
Zwischendeckenmelder um eine Platte versetzt.

![Beispiel](beispiel/beispiel_rauchmelder_EG.png)

## Installation

```
pip install -r requirements.txt
```

## Aufruf

```
python rauchmelder_platzierung.py modell.ifc                 # CSV + PNG-Plan je Geschoss
python rauchmelder_platzierung.py modell.ifc --ifc-ausgabe   # zusätzlich IFC mit Meldern
python rauchmelder_platzierung.py modell.ifc --raster 0.625  # auf Deckenraster fangen
python rauchmelder_platzierung.py --help                     # alle Parameter
```

Zum Ausprobieren erzeugt `python beispiel_ifc_erzeugen.py beispiel.ifc` ein Testmodell.

Ausgaben (neben der IFC-Datei oder im Ordner `-o`):

| Datei | Inhalt |
|---|---|
| `*_rauchmelder.csv` | je Melder: Geschoss, Raum, Bereich (Raum/Zwischendecke), Deckenfeld, Bezeichnung, X/Y/Z in m (Z = Montagehöhe), A, DH, Hinweise |
| `*_rauchmelder_<Geschoss>.png` | Grundriss mit Raum- und Zwischendeckenmeldern, Deckenraster, Unterzügen und Achsen |
| `*_rauchmelder.ifc` | Ursprungsmodell plus Melder als `IfcSensor` (`SMOKESENSOR`), dem Geschoss zugeordnet |

## Verfahren

1. **Raumgeometrie** – Das Volumen jedes `IfcSpace` wird in die XY-Ebene projiziert
   (Grundriss); die Raumhöhe ergibt sich aus der Z-Ausdehnung.
2. **Projektierungswerte** – Aus Grundfläche und Höhe folgen die maximale
   Überwachungsfläche A je Melder und der maximale Horizontalabstand DH:

   | Grundfläche | Raumhöhe | A | DH |
   |---|---|---|---|
   | ≤ 80 m² | ≤ 12 m | 80 m² | 6,6 m |
   | > 80 m² | ≤ 6 m | 60 m² | 5,7 m |
   | > 80 m² | 6 – 12 m | 80 m² | 6,6 m |

   Gänge schmaler als 3 m: Melderabstand bis 15 m (7,5 m zum Gangende).
   Wandabstand mindestens 0,5 m. Räume über 12 m Höhe werden mit Hinweis markiert.
3. **Abhangdecke** – Zu jedem Raum werden die Abhangdecken gesucht
   (`IfcCovering` mit Typ `CEILING`, im oberen Raumbereich oder direkt über dem
   Raumvolumen). Der Hohlraum reicht von der Oberkante der Abhangdecke bis zur
   Unterkante der Rohdecke (`IfcSlab`/`IfcRoof`) bzw. bis zur Oberkante des
   Raumvolumens. Ist er höher als 0,29 m (`--zwischendecke-min`), wird die
   Zwischendecke wie ein eigener Raum projektiert und erhält zusätzliche
   Zwischendeckenmelder (`ZD`), montiert an der Rohdecke. Die Raummelder (`RM`)
   sitzen an der Unterkante der Abhangdecke.
4. **Rasterdecke** – Ist die Abhangdecke eine Rasterdecke, sitzt jeder Melder
   zwingend mittig auf einer vollen Rasterplatte (keine Randzuschnitte); die
   Melderreihen liegen dadurch auf Plattenreihen, und Achsverschiebungen erfolgen
   nur um ganze Platten. Das Raster wird so erkannt:
   - **Einzeln modellierte Platten** (viele kleine `IfcCovering`): Rastermaß,
     Richtung und Lage stammen direkt aus dem Modell.
   - **Maßangabe** wie `625x625` in Name, Typ, Material oder Eigenschaften der
     Abhangdecke: Rastermaß aus dem Modell; die Lage des Rasters ist dann nicht
     definiert und wird angenommen (`--raster-ursprung mitte`: symmetrisch im
     Raum, oder `ecke`: volle Platten ab der Raumecke).
   - **Vorgabe** `--rasterdecke 0.625` für Decken, die nur als Rasterdecke
     bezeichnet sind; mit `--rasterdecke-alle` für jede Abhangdecke.

   Die **Zwischendeckenmelder** über einer Rasterdecke werden gegenüber dem
   jeweiligen Raummelder um eine Rasterplatte versetzt – zuerst gerade, wenn das
   nicht regelkonform geht diagonal (`--zd-versatz auto|gerade|diagonal`).
   Braucht ein Zwischendeckenfeld eine andere Melderzahl als der Raum darunter
   (z. B. weil ein Unterzug nur die Zwischendecke teilt), erhält es ein eigenes
   Raster auf Plattenmitten; dabei liegt nie ein Zwischendeckenmelder auf der
   Platte eines Raummelders.
5. **Unterzüge** – Ragt ein Unterzug (`IfcBeam`) unter die Decke, wird die Fläche
   an ihm geteilt und jedes Deckenfeld wie ein separater Raum behandelt (eigene
   Werte für A/DH, eigenes Raster, Wandabstand auch zum Unterzug). Das gilt für
   die Raumdecke und getrennt davon für die Zwischendecke: Ein Unterzug, der
   vollständig über der Abhangdecke liegt, teilt nur die Zwischendecke.
6. **Raster** – Parallel zur dominanten Wandrichtung des Raums wird je Deckenfeld
   das kleinste Raster (Reihen × Spalten) gesucht, bei dem kein Punkt weiter als DH
   vom nächsten Melder entfernt ist und die Melderzahl ≥ Fläche / A ist.
   Rasterpunkte außerhalb verwinkelter Flächen entfallen.
7. **Achsen** – Die Flächen werden von groß nach klein bearbeitet, Räume vor
   Zwischendecken. Jedes Raster wird um höchstens `--max-verschiebung`
   (Standard 1,5 m) auf bereits vorhandene Achsen bzw. das Deckenraster
   geschoben – nur wenn danach weiterhin alle Regeln eingehalten sind.
   Die Achsen gelten über alle Geschosse.

## Grenzen

- Die Normwerte sind Voreinstellungen für flache Decken (Neigung ≤ 20°) und per
  Parameter änderbar. **Sie ersetzen keine Fachplanung** und sind gegen die gültige
  DIN VDE 0833-2 / VdS 2095 und das Brandschutzkonzept zu prüfen.
- Unterzüge teilen die Decke standardmäßig immer (`--unterzug-min 0`); eine
  Mindesthöhe lässt sich per Parameter setzen. Restflächen unter 0,5 m²
  (`--min-feld`) bleiben ohne Melder und werden als Hinweis gemeldet.
- Bei Rasterdecken ohne modellierte Platten ist die Rasterlage eine Annahme
  (im Bericht als „Lage angenommen“ ausgewiesen) und vor Ort bzw. gegen den
  Deckenspiegel zu prüfen.
- Abhangdecken und Unterzüge werden nur erkannt, wenn sie im Modell als
  `IfcCovering` (CEILING) bzw. `IfcBeam` vorliegen. Fehlt die Rohdecke, erscheint
  ein Hinweis zur manuellen Prüfung.
- Nicht berücksichtigt: Dachneigung, Doppelböden, Einbauten, Lüftungsauslässe, Leuchten sowie die Raumnutzung
  (z. B. Nassräume ohne Melder – dafür `--min-flaeche` oder Nachbearbeitung der CSV).
- Die Abstandsprüfung rechnet Luftlinie innerhalb des Raums.
