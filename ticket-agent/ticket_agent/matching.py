"""Preis-/Mengenerkennung und die Entscheidung: kontaktieren, nur melden oder ignorieren."""
from __future__ import annotations

import re

from .models import Decision, Listing, Search

_NUM_WORDS = {"ein": 1, "eine": 1, "einen": 1, "zwei": 2, "drei": 3, "vier": 4, "fünf": 5, "sechs": 6}
_TICKET = (
    r"(?:konzert|eintritts|steh|sitz|front\s*of\s*stage\s*|fos[\s-]*)?"
    r"(?:tickets?|karten?|plätze|platz|stehplätze|sitzplätze|e-?tickets?)"
)
_QTY_PATTERNS = [
    re.compile(rf"\b(\d{{1,2}})\s*(?:x|×|stück|stk\.?|st\.)?\s*{_TICKET}\b", re.I),
    re.compile(r"\b(\d{1,2})\s*(?:x|×)\s", re.I),
    re.compile(rf"\b({'|'.join(_NUM_WORDS)})\s+{_TICKET}\b", re.I),
]
_PER_TICKET = re.compile(
    r"(?:pro|je|per)\s+(?:ticket|karte|stück|stk)|/\s*(?:ticket|karte|stück|stk)|"
    r"preis\s+(?:gilt\s+)?(?:pro|je)|\bp\.\s?p\.|pro\s+person",
    re.I,
)
_WANTED = re.compile(r"^\s*(?:ich\s+suche|suche|gesucht|gesuch|\[suche\]|tausche|biete\s+tausch)\b", re.I)
_SCAM_HINTS = {
    r"freunde\s*(?:&|und|\+)?\s*familie|friends\s*(?:&|and)?\s*family|\bf\s*&\s*f\b": "Zahlung per PayPal Freunde & Familie verlangt (kein Käuferschutz)",
    r"whats\s*app|telegram|\+?\d[\d\s/-]{9,}": "Verkäufer lenkt auf WhatsApp/Telefon um",
    r"nur\s+überweisung|vorkasse|western\s*union|paysafe|gutschein(?:karte|code)": "Vorkasse/unsichere Zahlungsart",
}


def parse_price(raw: str) -> tuple[float | None, bool]:
    """'1.250 € VB' -> (1250.0, True); 'VB' -> (None, True); 'Zu verschenken' -> (0.0, False)."""
    raw = (raw or "").strip()
    negotiable = bool(re.search(r"\bVB\b|verhandlungsbasis", raw, re.I))
    if re.search(r"verschenken", raw, re.I):
        return 0.0, negotiable
    m = re.search(r"(\d{1,3}(?:\.\d{3})+|\d+)(?:,(\d{1,2}))?", raw)
    if not m:
        return None, negotiable
    value = float(m.group(1).replace(".", ""))
    if m.group(2):
        value += float(m.group(2)) / 10 ** len(m.group(2))
    return value, negotiable


def detect_quantity(text: str) -> int | None:
    for pat in _QTY_PATTERNS:
        m = pat.search(text)
        if m:
            g = m.group(1).lower()
            n = _NUM_WORDS.get(g) or int(g)
            if 1 <= n <= 20:
                return n
    return None


def _contains(text: str, needle: str) -> bool:
    return needle.lower() in text.lower()


def evaluate(listing: Listing, search: Search) -> Decision:
    text = listing.text
    # keywords sind nur die Suchanfragen an das Portal; lokal gefiltert wird über must_contain/exclude.
    for word in search.must_contain:
        if not _contains(text, word):
            return Decision("ignore", f"Pflichtbegriff fehlt: {word}")
    for word in search.exclude:
        if _contains(text, word):
            return Decision("ignore", f"Ausschlussbegriff: {word}")
    if _WANTED.search(listing.title) or _WANTED.search(listing.description):
        return Decision("ignore", "Gesuch/Tausch, kein Angebot")

    qty = detect_quantity(text)
    known = qty is not None
    qty = qty or 1
    warnings = [msg for pat, msg in _SCAM_HINTS.items() if re.search(pat, text, re.I)]
    base = dict(quantity=qty, quantity_known=known, warnings=warnings)

    if qty < search.min_quantity and known:
        return Decision("ignore", f"nur {qty} Ticket(s), gesucht mind. {search.min_quantity}", **base)
    if search.max_quantity and qty > search.max_quantity:
        return Decision("ignore", f"{qty} Tickets, gesucht max. {search.max_quantity}", **base)

    if listing.price is None:
        return Decision("notify", "kein Preis angegeben – bitte selbst prüfen", **base)

    # Unbekannte Menge wird als 1 Ticket gewertet: so wird das Limit nie überschritten.
    per_ticket = listing.price if _PER_TICKET.search(text) else listing.price / qty
    base["price_per_ticket"] = round(per_ticket, 2)
    limit = search.max_price_per_ticket

    if per_ticket < search.min_price_per_ticket:
        warnings.append(f"Preis auffällig niedrig ({per_ticket:.0f} € pro Ticket) – möglicher Betrug")
        return Decision("notify", "unter Mindestpreis, nur Meldung", **base)
    if not known and search.min_quantity > 1:
        return Decision("notify", "Anzahl der Tickets nicht erkennbar – bitte selbst prüfen", **base)
    if per_ticket <= limit:
        return Decision("contact", f"{per_ticket:.2f} € pro Ticket ≤ Limit {limit:.2f} €", **base)
    if listing.negotiable and per_ticket <= limit * (1 + search.vb_tolerance_percent / 100):
        return Decision(
            "contact",
            f"VB, {per_ticket:.2f} € pro Ticket – Gegenangebot zum Limit {limit:.2f} €",
            offer=round(limit * qty, 2),
            **base,
        )
    return Decision("ignore", f"{per_ticket:.2f} € pro Ticket > Limit {limit:.2f} €", **base)


DEFAULT_MESSAGE = (
    "Hallo,\n\nich habe Interesse an {anzahl_text} für \"{titel}\". "
    "Sind die Tickets noch verfügbar? {preis_text}\n"
    "Ich würde gern per PayPal (Waren & Dienstleistungen) zahlen oder die Tickets persönlich abholen.\n\n"
    "Viele Grüße\n{name}"
)


def render_message(listing: Listing, search: Search, decision: Decision, template: str, name: str) -> str:
    qty = decision.quantity
    anzahl_text = f"den {qty} Tickets" if qty > 1 else ("dem Ticket" if decision.quantity_known else "Ihrem Angebot")
    if decision.offer is not None:
        preis_text = f"Ich könnte Ihnen {decision.offer:.0f} € anbieten – wäre das für Sie in Ordnung?"
    else:
        preis_text = f"Der Preis von {listing.price:.0f} € passt für mich."
    return (search.message or template or DEFAULT_MESSAGE).format(
        titel=listing.title.strip(),
        preis=f"{listing.price:.0f}" if listing.price is not None else "?",
        anzahl=qty,
        anzahl_text=anzahl_text,
        preis_text=preis_text,
        angebot=f"{decision.offer:.0f}" if decision.offer is not None else "",
        limit=f"{search.max_price_per_ticket:.0f}",
        name=name,
    ).strip()
