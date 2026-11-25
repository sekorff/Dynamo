# Ticket-Agent

Persönlicher Agent, der Kleinanzeigen und eBay nach Konzertkarten durchsucht und Verkäufer
automatisch anschreibt, sobald ein Angebot dein Preislimit pro Ticket nicht übersteigt.

## Was er tut

1. Sucht alle `interval_minutes` nach deinen Suchbegriffen.
2. Erkennt Preis, „VB“ und Ticketanzahl („2x“, „3 Tickets“, „zwei Karten“, „pro Karte“) und rechnet den Preis pro Ticket aus.
3. Entscheidet je Inserat:
   - **≤ Limit** → Verkäufer wird angeschrieben (Kleinanzeigen) bzw. du wirst benachrichtigt (eBay).
   - **VB und knapp über Limit** (`vb_tolerance_percent`) → Gegenangebot in Höhe deines Limits.
   - **Kein Preis, Anzahl unklar, auffällig billig oder Betrugsmerkmale** (Freunde & Familie, Vorkasse, WhatsApp-Umleitung) → nur Meldung, nie automatische Nachricht.
   - Gesuche, Ausschlussbegriffe, zu teuer → ignoriert (bei späterer Preissenkung erneut geprüft).
4. Jedes Inserat wird nur einmal bearbeitet (SQLite unter `data/agent.db`), mit Tageslimit für Nachrichten.
5. Du bekommst jede Aktion per Telegram/ntfy inklusive Link und gesendetem Text.

Der Agent **bezahlt nie** und führt nach der ersten Nachricht kein Gespräch weiter – das übernimmst du.
Ist die Ticketanzahl nicht erkennbar, wird der Gesamtpreis als Preis für ein Ticket gewertet, damit dein Limit nie überschritten wird.

## Einrichtung

```bash
cd ticket-agent
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
cp config.example.yaml config.yaml      # Suchen und Limits eintragen

python -m ticket_agent login            # einmalig: im geöffneten Browser bei Kleinanzeigen einloggen
python -m ticket_agent test-notify
python -m ticket_agent run --once --dry-run   # Testlauf, sendet nichts
python -m ticket_agent run              # Dauerbetrieb
python -m ticket_agent status           # letzte Treffer
```

Der Agent muss auf deinem eigenen Rechner (oder z. B. einem Raspberry Pi mit Desktop) laufen: Er nutzt dein
eingeloggtes Browserprofil (`data/browser-profile`), ein Passwort wird nirgends gespeichert.

**eBay:** kostenlosen Account auf developer.ebay.com anlegen, Production-Keyset erzeugen und setzen:
`export EBAY_CLIENT_ID=… EBAY_CLIENT_SECRET=…`. eBay bietet Käufern keine Schnittstelle für automatische
Nachrichten, daher werden eBay-Treffer nur gemeldet.

**Benachrichtigung:** `TELEGRAM_BOT_TOKEN` und `TELEGRAM_CHAT_ID` setzen (Bot über @BotFather) oder ein
ntfy-Topic in der `config.yaml` eintragen.

## Wichtige Hinweise

- **Nutzungsbedingungen:** Kleinanzeigen untersagt automatisierte Zugriffe. Bei Erkennung drohen Captchas oder
  eine Kontosperre. Halte Intervall und Tageslimit moderat oder setze `auto_contact: false` – dann erhältst du
  den fertigen Nachrichtenentwurf und sendest selbst.
- **Selektoren:** Die Suche ist gegen die Live-Seite getestet (Layout Stand 11/2026). Der Nachrichtenversand ist
  nicht live getestet, weil er ein eingeloggtes Konto braucht. Meldet der Agent „0 Treffer geparst“ oder
  „Nachrichtenfeld nicht gefunden“, die Selektoren unter `portals.kleinanzeigen.selectors` anpassen
  (Standardwerte in `ticket_agent/portals/kleinanzeigen.py`).
- **Suchbegriffe:** `keywords` sind die Suchanfragen an das Portal. Gefiltert wird lokal über `must_contain` und
  `exclude` – z. B. `exclude: ["Tribute", "Candlelight", "Kerzenschein"]`, sonst landen Tribute-Konzerte im Ergebnis.
- **Betrug:** Tickets auf Kleinanzeigen sind ein häufiges Betrugsziel. Nur mit Käuferschutz zahlen oder persönlich übergeben.
- **Weitere Portale:** Klasse von `Portal` ableiten (`ticket_agent/portals/base.py`), `search()` implementieren und in
  `portals/__init__.py` registrieren.

## Tests

```bash
pip install pytest && python -m pytest -q
```
