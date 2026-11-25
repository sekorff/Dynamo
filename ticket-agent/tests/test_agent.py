from pathlib import Path

import pytest

from ticket_agent.agent import Agent
from ticket_agent.matching import detect_quantity, evaluate, parse_price, render_message
from ticket_agent.models import Listing, Search
from ticket_agent.portals.base import Portal
from ticket_agent.portals.ebay import parse_items
from ticket_agent.portals.kleinanzeigen import parse_search, search_url
from ticket_agent.store import Store

FIX = Path(__file__).parent / "fixtures"
SEARCH = Search(name="cp", keywords=["Coldplay Tickets"], must_contain=["coldplay"], max_price_per_ticket=120, min_price_per_ticket=30, vb_tolerance_percent=15)


def L(title, price, negotiable=False, desc="", id="1", portal="fake"):
    return Listing(portal, id, title, f"https://x/{id}", price, negotiable, desc)


@pytest.mark.parametrize("raw,exp", [
    ("120 €", (120.0, False)), ("1.250 € VB", (1250.0, True)), ("VB", (None, True)),
    ("89,50 €", (89.5, False)), ("Zu verschenken", (0.0, False)), ("", (None, False)),
])
def test_parse_price(raw, exp):
    assert parse_price(raw) == exp


@pytest.mark.parametrize("text,exp", [
    ("2x Coldplay München", 2), ("Verkaufe 3 Tickets", 3), ("zwei Karten Coldplay", 2),
    ("4 Stehplatz Tickets", 4), ("Coldplay 2025 Ticket", None), ("2 Konzertkarten", 2),
    ("Coldplay 15.08.2025 München", None),
])
def test_quantity(text, exp):
    assert detect_quantity(text) == exp


def test_evaluate_rules():
    assert evaluate(L("2x Coldplay", 220), SEARCH).action == "contact"          # 110 pro Ticket
    assert evaluate(L("2x Coldplay", 260), SEARCH).action == "ignore"           # 130, kein VB
    d = evaluate(L("2x Coldplay", 260, True), SEARCH)                           # 130 VB, innerhalb 15 %
    assert (d.action, d.offer) == ("contact", 240)
    assert evaluate(L("2x Coldplay", 300, True), SEARCH).action == "ignore"     # 150 VB, zu weit weg
    assert evaluate(L("Coldplay Ticket", 200), SEARCH).action == "ignore"       # Menge unbekannt -> als 1 gewertet
    assert evaluate(L("2 Tickets Coldplay, 110 € pro Karte", 110), SEARCH).action == "contact"
    assert evaluate(L("2 Tickets Coldplay, 130 € pro Karte", 130), SEARCH).action == "ignore"
    assert evaluate(L("Suche Coldplay Tickets", 50), SEARCH).action == "ignore"
    assert evaluate(L("Coldplay Konzert", 50, desc="Ich suche 1 oder 2 Tickets"), SEARCH).action == "ignore"
    assert evaluate(L("Coldplay Ticket", 10), SEARCH).action == "notify"        # verdächtig billig
    assert evaluate(L("Coldplay Ticket", None, True), SEARCH).action == "notify"
    assert evaluate(L("Oasis Ticket", 50), SEARCH).action == "ignore"
    two = Search(name="x", keywords=["Coldplay"], max_price_per_ticket=120, min_quantity=2)
    assert evaluate(L("Coldplay Ticket", 100), two).action == "notify"          # Menge unklar
    assert evaluate(L("1 Ticket Coldplay", 100), two).action == "ignore"
    assert evaluate(L("Coldplay Ticket", 100, desc="nur PayPal Freunde und Familie"), SEARCH).warnings


def test_message():
    l = L("2x Coldplay", 260, True)
    msg = render_message(l, SEARCH, evaluate(l, SEARCH), None, "Max")
    assert "240 €" in msg and "Max" in msg and "den 2 Tickets" in msg


def test_kleinanzeigen_parser():
    items = parse_search((FIX / "kleinanzeigen_search.html").read_text(encoding="utf-8"))
    assert [i.id for i in items] == ["3001", "3002", "3003"]
    a = items[0]
    assert (a.price, a.negotiable, a.location) == (220.0, True, "80331 München")
    assert a.url == "https://www.kleinanzeigen.de/s-anzeige/2x-coldplay-muenchen/3001-231-1"
    assert items[1].price == 1250.0 and items[2].price is None
    assert search_url("Coldplay München") == "https://www.kleinanzeigen.de/s-coldplay-münchen/k0"


def test_kleinanzeigen_parser_layout_2026():
    a, b = parse_search((FIX / "kleinanzeigen_search_2026.html").read_text(encoding="utf-8"))
    assert (a.id, a.title, a.price, a.negotiable, a.location) == ("4001", "2 Tickets Coldplay Dresden", 50.0, True, "01277 Blasewitz")
    assert "pro Karte" in a.description                       # lange Beschreibung aus den Metadaten
    assert (b.price, b.location, b.description) == (95.0, "49377 Vechta", "Biete zwei Karten")


def test_ebay_parser():
    items = parse_items({"itemSummaries": [
        {"itemId": "v1|1|0", "legacyItemId": "1", "title": "2 Tickets Coldplay", "itemWebUrl": "https://ebay.de/itm/1",
         "price": {"value": "199.00", "currency": "EUR"}, "buyingOptions": ["FIXED_PRICE", "BEST_OFFER"]},
        {"itemId": "v1|2|0", "title": "USD", "price": {"value": "5", "currency": "USD"}},
    ]})
    assert len(items) == 1 and items[0].price == 199.0 and items[0].negotiable


class FakePortal(Portal):
    name = "fake"
    can_message = True

    def __init__(self, listings, fail=False):
        self.listings, self.sent, self.fail = listings, [], fail

    def search(self, search):
        return self.listings

    def send_message(self, listing, text):
        if self.fail:
            raise RuntimeError("kaputt")
        self.sent.append((listing.id, text))


class FakeNotifier:
    def __init__(self):
        self.msgs = []

    def send(self, title, body, url=None):
        self.msgs.append((title, body))


def make(tmp_path, listings, cfg=None, **kw):
    portal = FakePortal(listings, fail=kw.pop("fail", False))
    s = Search(name="cp", keywords=["Coldplay"], max_price_per_ticket=120, portals=["fake"])
    n = FakeNotifier()
    agent = Agent(cfg or {}, [s], portals={"fake": portal}, store=Store(tmp_path / "a.db"), notifier=n, sleep=lambda s: None, **kw)
    return agent, portal, n


def test_agent_contacts_once_and_respects_limit(tmp_path):
    ls = [L("2x Coldplay", 200, id="1"), L("2x Coldplay", 500, id="2"), L("Coldplay Ticket", 90, id="3")]
    agent, portal, n = make(tmp_path, ls, {"agent": {"max_messages_per_day": 1}})
    assert agent.run_once() == {"contacted": 1, "ignored": 1, "notified": 1}   # Nr. 3 scheitert am Tageslimit
    assert [s[0] for s in portal.sent] == ["1"]
    assert "Tageslimit" in n.msgs[-1][1]
    assert agent.run_once() == {"seen": 3} and len(portal.sent) == 1          # keine Doppel-Nachricht


def test_agent_dry_run_failure_and_price_drop(tmp_path):
    agent, portal, n = make(tmp_path, [L("2x Coldplay", 200)], dry_run=True)
    agent.run_once()
    assert not portal.sent and "Nachrichtenentwurf" in n.msgs[0][1]

    agent, portal, n = make(tmp_path / "f", [L("2x Coldplay", 200)], fail=True)
    assert agent.run_once() == {"notified": 1} and "fehlgeschlagen" in n.msgs[0][1]

    agent, portal, n = make(tmp_path / "d", [L("2x Coldplay", 400)])
    assert agent.run_once() == {"ignored": 1}
    portal.listings = [L("2x Coldplay", 230)]                                  # Preis gesenkt
    assert agent.run_once() == {"contacted": 1}


def test_scam_warning_blocks_auto_contact(tmp_path):
    agent, portal, n = make(tmp_path, [L("Coldplay Ticket", 100, desc="Zahlung nur Vorkasse")])
    assert agent.run_once() == {"notified": 1} and not portal.sent
