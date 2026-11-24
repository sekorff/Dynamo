# Geprüfte Herstellerdaten D+H und WAREMA

Aus den Originalanleitungen gelesen (Stand der Dokumente beachten, vor Verdrahtung mit der aktuellen Fassung abgleichen).

## D+H RZN 4404-K V2 – Gebrauchsanleitung 99.823.80, Stand 3.7/06/19

- 230 V AC, 50 Hz, 120 VA, Standby 5,8 W. Ausgang 24 V DC, 4 A. 1 Linie / 1 Gruppe, max. 4 A je Gruppe.
- Kurzzeitbetrieb 30 % ED. Max. 14 Brandmelder, max. 8 RWA-Taster je Linie.
- Notversorgung 72 h: 2 × 12 V / 2,2 Ah (Typ 2); mit D+H-Alarmmitteln 2 × 12 V / 3,4 Ah (Typ 8). Bis 2 A Ausgangsstrom: 2 × 12 V / 1,2 Ah (Typ 1), mit Alarmmitteln 2,2 Ah.
- Gehäuse GEH-KST (Kunststoff, IP 30) oder GEH-S-RWA (Stahlblech, IP 54).
- Klemmen:
  - Netz: L, N (PE nur KS-Version). Separater Stromkreis, Sicherung kennzeichnen.
  - RM 1–2: R, – (Brandmelder / BMA).
  - RT 3–8: T, –, K, A, Z, S (RWA-Taster).
  - LT 1–4: L, N–, Z, A (Lüftungstaster: N– gegen Z = ZU, gegen A = AUF; L = Aufmeldung).
  - MOT 5–7: E (Überwachung), a, b.
  - Ext. (Schraubklemmen): N–, N+, Z, +, M, D1, D2.
  - Stiftleiste: D1, N–, N+, S, –, A, K, Z, D2.
  - Bedeutung: – / + notversorgt; N– / N+ nicht notversorgt; A Sammelalarm; K Kontrolle; S Sammelstörung; M Haftmagnete (max. 500 mA); Z Eingang Zentral ZU.
- RT 45: Klemme 2 → T, 1 → –, 6 → K, 4 → A, 7 → Z, 5 → S. Im letzten Taster 10 kΩ zwischen 3 und 8.
- BMA: Arbeitskontakt mit Alarmwiderstand 1 kΩ, Endwiderstand 10 kΩ, an RM R / –.
- Antrieb: Endmodul EM 47 K (2 × 47 kΩ) am letzten Antrieb in der Abzweigdose (orange, schwarz, schwarz).
- Im RWA-Fall erreichen Antriebe mit Schnelllauf (HS) die Öffnungsposition in max. 60 s.
- DIP S1: 1 AUF-Laufzeitbegrenzung, 7 Linienstörung = Alarm, 8 Gruppenstörung = Alarm, 10 Nachtaktung.
- Kein Eingang für eine externe Rückmeldung oder Freigabe des Antriebsausgangs.
- Nicht dokumentiert: elektrische Daten der Ausgänge A / S / K, Grenzlängen der Linien, Artikelnummern.

## D+H Jalousiemodul AT 41 – Gebrauchsanleitung Rev. 2.2, Id. 30.551.70

- Verzögert das Anlaufen der Fensterantriebe; die Beschattung erhält gleichzeitig ein Rücklaufsignal. Gilt bei RWA-Auslösung und bei Lüftung.
- Verzögerung per Potentiometer 40 … 110 s; einzustellen auf das 1,5-Fache der Rücklaufzeit.
- Steckt auf dem Gruppenklemmplatz (LT / MOT) u. a. der RZN 4402/04-K.
- Klemmen: 1 Aufm., 2 N–, 3 zu, 4 auf, 5 Überw., 6 Mot.a, 7 Mot.b; P1–P4. Kontakt „Flügel zu“ zwischen P2 und P3, max. 60 V / 2 A. P4 = Meldung vom Antrieb.
- Nur mit D+H-Antrieben; braucht die -B-Meldung eines Antriebs je Gruppe (KA / ZA …-BRV: weiß, braun, gelb).
- Bei geschlossenem Fenster ist die Beschattung normal bedienbar; bei geöffnetem Fenster bleibt sie eingefahren gesperrt.
- AUF-Laufzeitbegrenzung der Zentrale muss deaktiviert sein.
- Für 230-V-Beschattung sieht D+H das Schaltrelais TRL 8 außerhalb des Zentralengehäuses vor.

## WAREMA Notstrom-Kit – Anleitung 2014458, gültig ab 1. April 2022; Datenblatt MSE Notstrom-Kit REG (12/2024)

- Artikelnummern: MSE Notstrom-Kit REG 2013 983, AP 2014 062; USV 2013 984; Ersatzakkupack RBC48 2014 827; Schlüsseltaster Reset 2017 192; roter Taster Hochfahrt 2014 813; Halterset USV 2031 735.
- Nur für 230-V-AC-Antriebe. Ein Antrieb je MSE Notstrom-Kit. Schaltleistung 500 VA je Ausgang. Stromaufnahme MSE 12–21 mA.
- Antriebe je USV: 4 × ca. 100 W, 3 × 140 W, 2 × 200 W, 1 × 320 W (Becker) bzw. 300 W (Somfy).
- Klemmen:
  - X1: 7–9 L, 4–6 N, 1–3 PE (Versorgung von der USV).
  - X2: 1 △ Motor, 2 ▽ Motor, 3 N Motor, 4 PE Motor, 5 n.c., 6 △ MSE, 7 ▽ MSE, 8 N MSE, 9 PE MSE (6–9 = vorgeschaltetes Schaltelement).
  - X5: 1 0V, 2 In+, 3 In1, 4 In2, 5 R (Reset), 6 In3 (GLT), 7 In+.
  - X6: 1 0V, 2 In+, 3 In4 (Hochtaster), 7 NC, 8 C, 9 NO (Störmeldung, max. 3 A; im Normalbetrieb NC geschlossen).
- Eingänge: In1 USV-Störung / Akku / Überlast (hohe Priorität), In2 Netzausfall länger als 25 s (niedrige Priorität), In3 GLT / BMA und In4 Hochtaster (Priorität über Kodierschalter S1 / S2 wählbar, ab Werk hoch).
- In3 ist ab Werk mit In+ gebrückt: Brücke entfernen, potentialfreien Kontakt anschließen (Öffnerkreis). Eingang aktiv 8–30 V DC, 1–2 mA.
- Hohe Priorität verlangt einen Resettaster; niedrige Priorität gibt nach 5 min oder Reset wieder frei.
- Der Störmeldekontakt darf nicht an eine Brandmeldezentrale angeschlossen werden. Eine Endlagenmeldung gibt es nicht.
- USV: Netzleitung 1,8 m an bauseitige Schuko-Steckdose, 6 Ausgänge 230 V AC, Einsteckkarte mit 3 m Leitung an X5: 2 / 3 / 4. Sicherungsabgang von USV und vorgeschaltetem Schaltelement muss derselbe sein.
- Leitungen laut Anschlussplan: 3 × 1,5 mm² (Netz), H05RR-F 3G1,5 (USV → X1), H05RR-F 4G0,75 (Motorleitung), 4 × 1,5 mm² (zum Antrieb), 2×2×0,8 mm Ø geschirmt eindrähtig (Steuerleitungen).
- Fahrzeit mit Notstrom-Kit: Raffstore ca. 70 s je 2,5 m (Anleitung) bzw. ca. 55 s je 2 m (Datenblatt).
- WAREMA nennt das Kit eine Komfortsteuerung. In öffentlichen Gebäuden nicht vor Flucht- und ersten Rettungswegen; am zweiten Rettungsweg nur nach Freigabe durch den Verantwortlichen nach § 66 MBO.
- Bei Frost kann ein angefrorener Behang nicht hochfahren.

## Bezugsquellen der Dokumente

Die Herstellerseiten und Händler mit den Anleitungen müssen in der Netzwerkfreigabe der Umgebung stehen. Verwendet wurden: willenig.at (RZN 4404-K), foppa.ch (AT 41), der-sonnenschutz-shop.de (WAREMA).
