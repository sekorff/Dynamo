# Newsletter-Assistent für Gmail

Ein Google Apps Script, das Newsletter im Gmail-Postfach findet, als Tabelle mit Checkboxen auflistet
und die ausgewählten abmeldet und deren bisherige Mails löscht. Es läuft komplett im eigenen Google-Konto.

## Einrichtung (einmalig, ca. 2 Minuten)

1. Neues Google Sheet anlegen: <https://sheets.new>
2. Menü **Erweiterungen → Apps Script** öffnen.
3. Den vorhandenen Inhalt löschen, den Inhalt von [`Code.gs`](Code.gs) einfügen und speichern.
4. Das Sheet neu laden. Oben erscheint das Menü **Newsletter-Assistent**.
5. Beim ersten Klick fragt Google nach der Berechtigung für Gmail, Sheets und externe Aufrufe.
   Da das Script nicht von Google geprüft ist: **Erweitert → Zu … wechseln (unsicher) → Zulassen**.

## Benutzung

1. **Newsletter scannen (neu)**: füllt das Tabellenblatt `Newsletter`, sortiert nach Anzahl der Mails.
   Bei großen Postfächern stoppt der Scan nach ca. 4,5 Minuten; dann **Scan fortsetzen** wählen.
2. In **Spalte A** die Newsletter anhaken, die weg sollen.
3. **Ausgewählte abmelden + alte Mails löschen** wählen und bestätigen.
   Alternativ gibt es *nur abmelden* und *nur löschen*.
4. Das Ergebnis steht je Zeile in der Spalte **Status**.

## Wie es arbeitet

| Schritt | Verhalten |
| --- | --- |
| Erkennung | Als Newsletter gilt ein Absender, dessen Mail einen `List-Unsubscribe`-Header hat. |
| Abmeldung „One-Click“ | Automatischer POST nach RFC 8058, zuverlässigste Variante. |
| Abmeldung „E-Mail“ | Es wird eine Abmelde-Mail an die vom Absender angegebene Adresse geschickt. |
| Abmeldung „Link“ | Der Link wird aufgerufen. Manche Anbieter verlangen noch einen Klick im Browser, der Link steht in Spalte I. |
| Löschen | Alle Konversationen `from:<Absender>` wandern in den **Papierkorb** (30 Tage wiederherstellbar). |

Hinweise:

- Gelöscht wird nach Absenderadresse. Verschickt ein Absender unter derselben Adresse auch Wichtiges
  (z. B. Rechnungen), dafür *nur abmelden* verwenden.
- Bricht ein Lauf am Zeitlimit ab, bleiben die offenen Zeilen angehakt; den Menüpunkt einfach erneut ausführen.
