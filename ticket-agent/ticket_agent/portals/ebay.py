"""eBay über die offizielle Browse API (kostenloser Developer-Account nötig).

Zugangsdaten per Umgebungsvariablen EBAY_CLIENT_ID / EBAY_CLIENT_SECRET.
eBay bietet keine Schnittstelle, um als Käufer automatisch Nachrichten zu senden –
Treffer werden deshalb gemeldet, Kauf/Preisvorschlag machst du selbst.
"""
from __future__ import annotations

import os
import time

import requests

from ..models import Listing, Search
from .base import Portal

TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
SEARCH_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"


def parse_items(data: dict) -> list[Listing]:
    out = []
    for item in data.get("itemSummaries") or []:
        price = item.get("price") or item.get("currentBidPrice") or {}
        if price.get("currency", "EUR") != "EUR":
            continue
        options = item.get("buyingOptions") or []
        out.append(
            Listing(
                portal="ebay",
                id=item.get("legacyItemId") or item["itemId"],
                title=item.get("title", ""),
                url=item.get("itemWebUrl", ""),
                price=float(price["value"]) if price.get("value") else None,
                negotiable="BEST_OFFER" in options,
                description=item.get("shortDescription", "") + (" [Auktion]" if "AUCTION" in options else ""),
                location=(item.get("itemLocation") or {}).get("postalCode", ""),
                seller=(item.get("seller") or {}).get("username", ""),
            )
        )
    return out


class Ebay(Portal):
    name = "ebay"
    can_message = False

    def __init__(self, cfg=None):
        super().__init__(cfg)
        self.client_id = os.environ.get("EBAY_CLIENT_ID") or self.cfg.get("client_id")
        self.client_secret = os.environ.get("EBAY_CLIENT_SECRET") or self.cfg.get("client_secret")
        self._token, self._expires = None, 0.0

    def _auth(self) -> str:
        if not (self.client_id and self.client_secret):
            raise RuntimeError("eBay: EBAY_CLIENT_ID / EBAY_CLIENT_SECRET fehlen (siehe README)")
        if not self._token or time.time() > self._expires - 60:
            r = requests.post(
                TOKEN_URL,
                auth=(self.client_id, self.client_secret),
                data={"grant_type": "client_credentials", "scope": "https://api.ebay.com/oauth/api_scope"},
                timeout=20,
            )
            r.raise_for_status()
            body = r.json()
            self._token, self._expires = body["access_token"], time.time() + int(body.get("expires_in", 7200))
        return self._token

    def search(self, search: Search) -> list[Listing]:
        results: dict[str, Listing] = {}
        for kw in search.keywords:
            r = requests.get(
                SEARCH_URL,
                params={
                    "q": kw,
                    "limit": 50,
                    "sort": "newlyListed",
                    "filter": "buyingOptions:{FIXED_PRICE|BEST_OFFER|AUCTION},itemLocationCountry:DE",
                },
                headers={
                    "Authorization": f"Bearer {self._auth()}",
                    "X-EBAY-C-MARKETPLACE-ID": self.cfg.get("marketplace", "EBAY_DE"),
                },
                timeout=20,
            )
            r.raise_for_status()
            for listing in parse_items(r.json()):
                results[listing.id] = listing
        return list(results.values())
