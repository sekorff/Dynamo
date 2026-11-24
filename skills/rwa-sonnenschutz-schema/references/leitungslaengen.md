# Maximale Leitungslängen

## Herstellerwerte (vorrangig)

D+H Antriebsleitung RZN 4404-K, Länge in m je Gesamtstrom:

| Gesamtstrom (A) | 0,5 | 1 | 1,5 | 2 | 2,5 | 3 | 3,5 | 4 |
|---|---|---|---|---|---|---|---|---|
| 3 × 1,5 mm² | 240 | 120 | 80 | 60 | 48 | 40 | 34 | 30 |
| 3 × 2,5 mm² | 400 | 200 | 130 | 100 | 80 | 65 | 55 | 50 |

Formel: Querschnitt (mm²) = Leitungslänge (m) × Gesamtstrom (A) / 80.

- Antriebsleitung mindestens dreiadrig (2 Adern Versorgung, 1 Ader Überwachung); mit AT 41 eine vierte Ader für die Meldung. Eine grün-gelbe Ader darf nicht benutzt werden.
- D+H-Aderzahlen: RT 45 6 × 0,8 Ø (RT 45-LT 10 × 0,8 Ø), Brandmelder und Fremdsteuerung 2 × 0,8 Ø, Lüftungstaster 4 × 0,8 Ø. Kabeltypen nennt D+H nicht.
- WAREMA: USV-Netzleitung 1,8 m, Leitung der Einsteckkarte 3 m (werksseitig).

## Eigene Richtwerte (im Dokument mit * kennzeichnen)

Nur wo der Hersteller keine Grenze nennt; Nachweis bleibt beim Elektroplaner.

- **Signalleitung 0,8 mm Ø:** Schleifenwiderstand ca. 73 Ω/km. Richtwert 200 m ≈ 15 Ω – klein gegen 1 kΩ Alarmwiderstand und unkritisch bei Eingängen mit 1–2 mA.
- **230-V-Zuleitung 1,5 mm² hinter B 16 A:** Abschaltbedingung Zs ≤ 230 V / (5 × 16 A) = 2,87 Ω; mit 0,6 Ω Vorimpedanz und ca. 30 Ω/km Schleife (warm) ergeben sich rund 75 m.
- **Motorleitung 1,5 mm²:** Spannungsfall ΔU = 2 × L × I / (κ × A), κ = 56. Bei 1,4 A und 100 m: 3,3 V = 1,4 %.

## Übliche Kabeltypen

| Zweck | Typ |
|---|---|
| Signal ohne Funktionserhalt | J-Y(St)Y n×2×0,8 mm |
| BMA-Leitung | J-Y(St)Y 2×2×0,8 mm, rot |
| Signal mit Funktionserhalt | JE-H(St)H…Bd FE180/E30 n×2×0,8 mm |
| Antriebsleitung 24 V mit Funktionserhalt | NHXH FE180/E30, 5-adrig, gn/ge nicht belegen |
| 230 V | NYM-J; mit Funktionserhalt NHXH-J FE180/E30 |

Funktionserhalt-Klasse immer aus Brandschutzkonzept und Leitungsanlagen-Richtlinie übernehmen.
