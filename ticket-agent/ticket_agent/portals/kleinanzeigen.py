"""Kleinanzeigen per Browser-Automatisierung (Playwright) mit deinem eigenen, eingeloggten Profil.

Kleinanzeigen hat keine öffentliche API. Der Agent bedient deshalb einen echten Browser,
dessen Profil lokal gespeichert wird (Login einmalig über `python -m ticket_agent login`).
Die CSS-Selektoren stehen gesammelt in SELECTORS und lassen sich in der config.yaml
unter portals.kleinanzeigen.selectors überschreiben, falls die Seite umgebaut wird.
"""
from __future__ import annotations

import json
import logging
import random
import re
import time
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..matching import parse_price
from ..models import Listing, Search
from .base import Portal

log = logging.getLogger("ticket_agent")
BASE = "https://www.kleinanzeigen.de"

SELECTORS = {
    # jeweils neues Layout (Stand 11/2026) und älteres Layout als Alternative
    "item": "article[data-adid]",
    "title": "h3 a, h2 a, a.ellipsis",
    "price": ".aditem-main--middle--price-shipping--price, h3 ~ div p",
    "description": ".aditem-main--middle--description, h3 ~ p",
    "location": ".aditem-main--top--left",
    "cookie_accept": "#gdpr-banner-accept",
    # Kontaktformular ist ein Dialog, der erst über "Nachricht schreiben" geöffnet wird
    "message_open": "#viewad-contact-button, button:has-text('Nachricht schreiben'), a:has-text('Nachricht schreiben')",
    "message_login_required": ".viewad-contact-button-login",
    "message_box": "#viewad-contact-message, textarea[name='message']",
    "message_submit": "button.viewad-contact-submit",
    "message_success": ".ajaxform-success-msg, .j-viewad-contact-success",
    "login_marker": "#user-email, a#user-logout, .user-profile",
}


def search_url(keyword: str) -> str:
    slug = re.sub(r"[^a-z0-9äöüß]+", "-", keyword.lower()).strip("-")
    return f"{BASE}/s-{slug}/k0"


def parse_search(html: str, selectors: dict | None = None) -> list[Listing]:
    sel = {**SELECTORS, **(selectors or {})}
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for art in soup.select(sel["item"]):
        ad_id = art.get("data-adid")
        link = art.select_one(sel["title"])
        href = art.get("data-href") or (link.get("href") if link else None)
        if not (ad_id and href and link):
            continue
        txt = lambda key: (art.select_one(sel[key]).get_text(" ", strip=True) if art.select_one(sel[key]) else "")
        price, negotiable = parse_price(txt("price"))
        location = txt("location") or next(
            (t for t in (sp.get_text(" ", strip=True) for sp in art.select("span")) if re.match(r"\d{5}\b", t)), ""
        )
        description = txt("description")
        meta = art.select_one("script[type='application/ld+json']")
        if meta and meta.string:  # enthält die längere Beschreibung als die sichtbare Vorschau
            try:
                description = json.loads(meta.string).get("description") or description
            except ValueError:
                pass
        out.append(
            Listing(
                portal="kleinanzeigen",
                id=str(ad_id),
                title=link.get_text(" ", strip=True),
                url=urljoin(BASE, href),
                price=price,
                negotiable=negotiable,
                description=description,
                location=location,
            )
        )
    return out


class Kleinanzeigen(Portal):
    name = "kleinanzeigen"
    can_message = True

    def __init__(self, cfg=None):
        super().__init__(cfg)
        self.sel = {**SELECTORS, **(self.cfg.get("selectors") or {})}
        self.profile = Path(self.cfg.get("profile_dir", "data/browser-profile")).absolute()
        self._pw = self._ctx = self._page = None

    # --- Browser ---------------------------------------------------------
    def _open(self, headless: bool | None = None):
        if self._page:
            return self._page
        from playwright.sync_api import sync_playwright  # erst hier, damit Tests ohne Playwright laufen

        self.profile.mkdir(parents=True, exist_ok=True)
        self._pw = sync_playwright().start()
        self._ctx = self._pw.chromium.launch_persistent_context(
            str(self.profile),
            headless=self.cfg.get("headless", False) if headless is None else headless,
            executable_path=self.cfg.get("executable_path"),  # optional: eigener Chrome/Chromium
            locale="de-DE",
            viewport={"width": 1280, "height": 900},
        )
        self._page = self._ctx.pages[0] if self._ctx.pages else self._ctx.new_page()
        return self._page

    def _goto(self, url: str):
        page = self._open()
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        try:
            page.locator(self.sel["cookie_accept"]).click(timeout=1500)
        except Exception:
            pass
        time.sleep(random.uniform(1.0, 2.5))
        return page

    def close(self) -> None:
        if self._ctx:
            self._ctx.close()
        if self._pw:
            self._pw.stop()
        self._pw = self._ctx = self._page = None

    # --- Aktionen ----------------------------------------------------------
    def login(self) -> None:
        """Öffnet ein sichtbares Browserfenster; du meldest dich selbst an (kein Passwort im Agenten)."""
        page = self._open(headless=False)
        page.goto(f"{BASE}/m-einloggen.html")
        input("Bitte im Browserfenster bei Kleinanzeigen einloggen und danach hier Enter drücken … ")
        self.close()

    def search(self, search: Search) -> list[Listing]:
        urls = [search.urls["kleinanzeigen"]] if search.urls.get("kleinanzeigen") else [search_url(k) for k in search.keywords]
        results: dict[str, Listing] = {}
        for url in urls:
            page = self._goto(url)
            found = parse_search(page.content(), self.sel)
            if not found:
                log.warning("kleinanzeigen: 0 Treffer geparst auf %s (Sperre/Captcha oder geänderte Seite?)", url)
            for listing in found:
                results[listing.id] = listing
        return list(results.values())

    def send_message(self, listing: Listing, text: str) -> None:
        page = self._goto(listing.url)
        box = page.locator(self.sel["message_box"]).first
        if not box.is_visible():
            opener = page.locator(self.sel["message_open"]).first
            if opener.count() == 0 and page.locator(self.sel["message_login_required"]).count():
                raise RuntimeError("nicht eingeloggt – bitte `python -m ticket_agent login` ausführen")
            try:
                opener.click(timeout=8000)
                box.wait_for(state="visible", timeout=10000)
            except Exception as exc:
                raise RuntimeError("Nachrichtenfeld nicht gefunden (Selektoren prüfen, siehe README)") from exc
        box.click()
        box.fill(text)
        time.sleep(random.uniform(1.5, 4.0))
        page.locator(self.sel["message_submit"]).first.click()
        try:
            page.locator(self.sel["message_success"]).first.wait_for(state="visible", timeout=15000)
        except Exception as exc:
            raise RuntimeError("Versand nicht bestätigt – bitte im Postfach prüfen") from exc
