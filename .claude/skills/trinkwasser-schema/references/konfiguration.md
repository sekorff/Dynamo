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
| `plankopf` | nein | Angaben für den M&P-Plankopf (Skill mp-plankopf) |

## `projekt`

`name`, `standort`, `datum` (Pflicht, freier Text, z. B. `"18.11.2026"`),
optional `plan_nr`, `index`, `bearbeiter`.

Daraus werden die Plankopffelder vorbelegt: `plan_nr` → `Plannummer`, `index` →
`aktuelle Änderung Index`, `datum` → `erstellt Datum`, `bearbeiter` →
`erstellt von`, `name` + `standort` → Freifeld „Bauvorhaben".

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

## `plankopf` (alle optional)

Wird an den Skill mp-plankopf durchgereicht und dort geprüft; Feldnamen und
Wertebereiche stehen in dessen `references/api.md`. Angaben hier überschreiben
die Vorbelegung aus `projekt`.

| Schlüssel | Inhalt |
|---|---|
| `blatt` | festes Blatt, z. B. `{"format": "A1"}`; ohne Angabe automatische Wahl A3 bis A0 |
| `logo` | `Gruppe` (Vorgabe), `Braunschweig`, `Dresden`, `Duesseldorf`, `Essen`, `Hamburg`, `Magdeburg`, `Mannheim`, `Muenchen`, `Stuttgart`, `null` |
| `felder` | Familienparameter, z. B. `Status`, `MP_400_LB_Text`, `MP_300_V0_Index`, `MP_300_V0_Datum`, `MP_400_V0_Bearbeiter`, `MP_400_V0_Bemerkung` |
| `zellen` | `Maßstab`, `Planart`, `geprüft Datum`, `geprüft von`, `Basisplan Index`, `Basisplan Datum` |
| `freifelder` | `Kopf links`, `Kopf rechts`, `Block 1` bis `Block 4`; vorbelegt sind `Kopf rechts` (Gewerk), `Block 1` (Bauvorhaben), `Block 4` (Planinhalt) |
| `zusatzfelder` | Liste zusätzlicher Felder über dem Änderungsindex |
| `aenderungsindex` | `{"modus": "voll" / "kompakt" / "aus"}` |
| `optionen` | `randtext`, `lochstreifen`, `blattkante` |
| `daemmmatrix` | Dämmmatrix für die Legende - nur angeben, wenn bekannt |

```json
"plankopf": {
  "logo": "Braunschweig",
  "felder": {"Status": "VORABZUG", "MP_400_LB_Text": "Entwurfsplanung",
             "MP_300_V0_Index": "V0", "MP_300_V0_Datum": "18.11.2026",
             "MP_400_V0_Bearbeiter": "TGA", "MP_400_V0_Bemerkung": "Ersterstellung"},
  "freifelder": {"Block 2": {"titel": "Bauherr", "zeilen": ["Muster GmbH"]}}
}
```

Die Legende wird nicht konfiguriert: Linientypen und Symbole ergeben sich aus
dem Planinhalt, Standortvorgaben aus `versorger`.
