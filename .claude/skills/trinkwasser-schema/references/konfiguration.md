# Konfiguration (JSON)

Unbekannte Schlüssel werden abgelehnt (Exit-Code 2) - so fallen Tippfehler auf,
statt stillschweigend ignoriert zu werden. Texte müssen im Zeichensatz
Windows-1252 darstellbar sein (Umlaute und ß sind in Ordnung).

## Oberste Ebene

| Schlüssel | Pflicht | Inhalt |
|---|---|---|
| `projekt` | ja | Schriftfeld |
| `versorger` | ja | Standortvorgaben des Versorgers |
| `geschosse` | ja | Liste von unten nach oben |
| `druckstufen` | ja | 1 bis 3 Druckstufen, von unten nach oben |
| `wasseraufbereitung` | nein | Osmose und/oder VE-Wasser |
| `bemessung` | nein | Parameter der Vorbemessung |

## `projekt`

`name`, `standort`, `datum` (Pflicht, freier Text, z. B. `"18.11.2026"`),
optional `plan_nr`, `index`, `bearbeiter`.

## `versorger`

| Schlüssel | Pflicht | Inhalt |
|---|---|---|
| `name` | ja | Name des Versorgers |
| `versorgungsdruck_min_bar` | ja | Mindestversorgungsdruck am Hausanschluss |
| `versorgungsdruck_max_bar` | ja | maximaler Ruhedruck am Hausanschluss |
| `hausanschluss_dn` | nein | Nennweite |
| `dea_anschluss` | nein | `"unmittelbar"` (Standard) oder `"mittelbar"` (Vorbehälter mit freiem Auslauf) |
| `vorgaben` | nein | Liste freier Texte, werden im Plan aufgeführt |

## `geschosse`

Liste von Objekten `{"name": "EG", "hoehe_m": 3.5}`, unterstes Geschoss zuerst.
Die Oberkante Fußboden des untersten Geschosses ist die Bezugshöhe ±0,00 und
zugleich die Höhe des Hausanschlusses.

## `druckstufen`

| Schlüssel | Pflicht | Inhalt |
|---|---|---|
| `name` | ja | z. B. `"Druckstufe 1"` |
| `von`, `bis` | ja | unterstes / oberstes versorgtes Geschoss (Namen aus `geschosse`) |
| `wwb` | ja | `"zentral"` (TWE in der Zentrale, PWH + Zirkulation), `"dezentral"` (DWE je Geschoss) oder `"keine"` |
| `versorgung` | nein | `"auto"` (Standard), `"netzdruck"`, `"dea"` |
| `straenge` | ja | mindestens ein Strang |

Strang:

| Schlüssel | Pflicht | Inhalt |
|---|---|---|
| `name` | ja | eindeutig, z. B. `"S1.1"` |
| `nutzung` | nein | kurze Bezeichnung (max. ca. 16 Zeichen) |
| `aussenzapfstellen` | nein | Liste von Geschossnamen innerhalb der Druckstufe |
| `adiabate_kuehlung` | nein | Liste von Geschossnamen innerhalb der Druckstufe |

Je Strang und Geschoss sind höchstens zwei Sonderanschlüsse (AZ/AK) möglich;
für mehr einen weiteren Strang anlegen. Jeder Strang versorgt alle Geschosse
seiner Druckstufe. Hygienestation und Trennstation werden automatisch gesetzt.

## `wasseraufbereitung`

```json
"wasseraufbereitung": {
  "enthaertung": true,
  "osmose":    {"verbraucher": [{"name": "Dampfbefeuchter RLT", "geschoss": "UG"}]},
  "ve_wasser": {"verbraucher": [{"name": "Laborzapfstellen", "geschoss": "1.OG"}]}
}
```

- Mindestens `osmose` oder `ve_wasser`. Sind beide vorhanden, wird die
  Vollentsalzung der Umkehrosmose nachgeschaltet.
- Kette: Hauptverteiler → Trennstation → (Enthärtung) → Umkehrosmose mit
  Permeatbehälter und Pumpe → RO-Strang → Vollentsalzung → VE-Strang.
- Je Netz und Geschoss ein Verbraucher (mehrere im Namen zusammenfassen).

## `bemessung` (alle optional)

| Schlüssel | Standard |
|---|---|
| `mindestfliessdruck_bar` | 1.0 |
| `verlust_hausanschluss_bar` (Zähler, Filter) | 0.6 |
| `verlust_stockwerk_bar` | 0.3 |
| `verlust_wwb_zentral_bar` | 0.2 |
| `verlust_wwb_dezentral_bar` | 0.5 |
| `rohrreibung_mbar_je_m` | 10.0 |
| `zuschlag_einzelwiderstaende` (Anteil) | 0.5 |
| `horizontale_leitungslaenge_m` | 30.0 |
| `entnahme_ueber_fussboden_m` | 1.0 |
| `max_ruhedruck_bar` | 5.0 |

Rechengang je Druckstufe (h = Höhe der obersten Entnahmestelle über dem
Hausanschluss):

```
dp_Leitung = Rohrreibung * (h + horizontale Länge) * (1 + Zuschlag)
p_Stufe    = 0,0981 * h + Mindestfließdruck + Stockwerk + WWB + dp_Leitung
p_erf      = p_Stufe + Verlust Hausanschluss
auto:      Netzdruck, wenn versorgungsdruck_min >= p_erf, sonst DEA
DEA:       Solldruck = p_Stufe, auf 0,1 bar aufgerundet
Netzdruck: Druckminderer, wenn versorgungsdruck_max - 0,0981 * h_unten > max_ruhedruck
```
