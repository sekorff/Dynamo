# RWA-Fenster mit außenliegendem Sonnenschutz und innenliegender Verdunkelung

Prinzipschema: `RWA_Sonnenschutz_Verdunkelung_Schema.svg` (Vorschau als `.png`).

## Funktion

**Brandfall (Priorität 1)** – die RWA-Zentrale ist führend.

1. Auslösung über RWA-Druckknopfmelder oder BMA.
2. Die RWA-Zentrale steuert die Notstrom-Kits von Sonnenschutz und Verdunkelung an (Notfahrt AUF).
3. Beide Anlagen melden „Parkposition erreicht“ zurück; überwachte Laufzeit maximal 60 s.
4. Erst nach beiden Rückmeldungen wird der Fensterantrieb freigegeben, das Fenster öffnet.
5. Alarm, Endlagen und Störungen gehen an die GLT.

**Lüftung (Priorität 2)** – Befehl Lüften AUF/ZU von der GLT an die RWA-Zentrale. Das Fenster öffnet nur bei geschlossener Sicherheitskette: Sonnenschutz und Verdunkelung in Parkposition, keine Störung, kein RWA-Alarm. Solange das Fenster nicht geschlossen ist, sind beide Behänge gegen Fahren gesperrt.

**Meldungen an die GLT** – je Anlage Betrieb, Sammelstörung, Netzausfall/Akkustörung und Position; von der RWA zusätzlich Alarm, Fenster AUF/ZU und Laufzeitüberschreitung.

**Notstrom** – Akku in RWA-Zentrale (72 h) und in den Notstrom-Kits von Sonnenschutz und Verdunkelung.

## Offene Punkte

- Verhalten des Fensters, wenn nach 60 s keine Rückmeldung vorliegt (Zwangsöffnung oder Sperre), ist mit dem Brandschutzkonzept festzulegen.
- Die Vorlaufzeit von 60 s verlängert die Gesamtöffnungszeit des NRWG gegenüber EN 12101-2 und ist als Abweichung abzustimmen.

Prinzipschema, keine Ausführungsplanung.

## Ausführungsschema D+H / WAREMA

`RWA_Ausfuehrungsschema_DH_WAREMA.pdf` (A3 quer, 2 Seiten) zeigt dieselbe Funktion mit konkreten Geräten:

- Seite 1: Schema mit den Klemmenbezeichnungen der Hersteller und Kabelliste mit Typ und maximaler Leitungslänge (auch einzeln als `.svg` / `.png`).
- Seite 2: Bauteilliste und Schnittstellenliste mit Zuständigkeiten nach Kostengruppen DIN 276 (Quelle: `RWA_Ausfuehrung_Seite2_Bauteile_Schnittstellen.html`).

Geräte: RWA-Zentrale D+H RZN 4404-K V2 mit Jalousiemodul AT 41, RWA-Taster RT 45, D+H-Fensterantrieb mit BRV-Meldung; je Behang ein WAREMA MSE Notstrom-Kit (Art.-Nr. 2013 983) mit USV (Art.-Nr. 2013 984).

Quellen der Klemmen und Leitungsangaben:

- D+H RZN 4402-K V2 / 4404-K V2, Gebrauchsanleitung 99.823.80, Stand 3.7/06/19
- D+H Jalousiemodul AT 41, Gebrauchsanleitung Rev. 2.2 (Anschluss mit 230-V-AC-Beschattungsantrieb)
- WAREMA Notstrom-Kit, Bedienungs- und Installationsanleitung 2014458, gültig ab 1. April 2022

Maximale Leitungslängen: Herstellerwerte gibt es nur für die Antriebsleitung (D+H-Tabelle) und die konfektionierten WAREMA-Leitungen. Alle mit * markierten Längen sind eigene Richtwerte und vom Elektroplaner nachzuweisen. Die offenen Punkte stehen im Schema unter „Vor Ausführung zu klären“.
