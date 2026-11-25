from __future__ import annotations

import logging
import random
import time

from .matching import evaluate, render_message
from .models import Listing, Search
from .notify import Notifier
from .portals import REGISTRY
from .store import Store

log = logging.getLogger("ticket_agent")


class Agent:
    def __init__(self, cfg: dict, searches: list[Search], dry_run: bool = False, portals: dict | None = None,
                 store: Store | None = None, notifier: Notifier | None = None, sleep=time.sleep):
        self.cfg, self.searches, self.dry_run, self.sleep = cfg, searches, dry_run, sleep
        a = cfg.get("agent") or {}
        self.auto_contact = a.get("auto_contact", True)
        self.max_per_day = a.get("max_messages_per_day", 10)
        self.delay = a.get("message_delay_seconds", [30, 90])
        self.interval = a.get("interval_minutes", 10)
        self.name = a.get("name", "")
        self.template = a.get("message")
        self.store = store or Store(a.get("database", "data/agent.db"))
        self.notifier = notifier or Notifier(cfg.get("notify"))
        pcfg = cfg.get("portals") or {}
        wanted = {p for s in searches for p in s.portals}
        unknown = wanted - set(REGISTRY) if portals is None else set()
        if unknown:
            raise ValueError(f"Unbekannte Portale: {', '.join(sorted(unknown))}")
        self.portals = portals if portals is not None else {n: REGISTRY[n](pcfg.get(n)) for n in wanted}

    # ------------------------------------------------------------------
    def handle(self, listing: Listing, search: Search) -> str:
        seen = self.store.get(listing.key)
        if seen:
            old_price, status = seen
            dropped = listing.price is not None and old_price is not None and listing.price < old_price
            if status != "ignored" or not dropped:
                return "seen"  # schon bearbeitet; nur Preissenkungen ignorierter Inserate neu prüfen

        d = evaluate(listing, search)
        if d.action == "ignore":
            self.store.save(listing, "ignored")
            log.debug("ignoriert: %s (%s)", listing.title, d.reason)
            return "ignored"

        portal = self.portals[listing.portal]
        price = f"{listing.price:.0f} €" if listing.price is not None else "ohne Preis"
        head = f"{listing.title}\n{price}{' VB' if listing.negotiable else ''} · {listing.location}\n{d.reason}"
        if d.warnings:
            head += "\n⚠️ " + "\n⚠️ ".join(d.warnings)

        if d.action == "notify" or d.warnings:
            # Bei Betrugsverdacht nie automatisch schreiben.
            self.store.save(listing, "notified")
            self.notifier.send(f"🔎 Bitte prüfen [{search.name}]", head, listing.url)
            return "notified"

        text = render_message(listing, search, d, self.template, self.name)
        blocked = None
        if not portal.can_message:
            blocked = f"{listing.portal} erlaubt keine automatischen Nachrichten"
        elif not self.auto_contact:
            blocked = "auto_contact ist aus"
        elif self.dry_run:
            blocked = "Testlauf (--dry-run)"
        elif self.store.messages_last_24h() >= self.max_per_day:
            blocked = f"Tageslimit von {self.max_per_day} Nachrichten erreicht"

        if blocked is None:
            try:
                self.sleep(random.uniform(*self.delay))
                portal.send_message(listing, text)
            except Exception as exc:  # Versand gescheitert -> du bekommst den Text zum manuellen Senden
                blocked = f"Versand fehlgeschlagen: {exc}"
            else:
                self.store.record_message(listing.key)
                self.store.save(listing, "contacted")
                self.notifier.send(f"✅ Verkäufer angeschrieben [{search.name}]", f"{head}\n\nGesendet:\n{text}", listing.url)
                return "contacted"

        self.store.save(listing, "notified")
        body = f"{head}\n\nNicht automatisch gesendet: {blocked}"
        if portal.can_message:
            body += f"\n\nNachrichtenentwurf:\n{text}"
        self.notifier.send(f"🎫 Treffer unter Limit [{search.name}]", body, listing.url)
        return "notified"

    def run_once(self) -> dict[str, int]:
        stats: dict[str, int] = {}
        for search in self.searches:
            for pname in search.portals:
                try:
                    listings = self.portals[pname].search(search)
                except Exception as exc:
                    log.error("%s / %s: Suche fehlgeschlagen: %s", pname, search.name, exc)
                    stats["errors"] = stats.get("errors", 0) + 1
                    continue
                for listing in listings:
                    result = self.handle(listing, search)
                    stats[result] = stats.get(result, 0) + 1
        log.info("Durchlauf fertig: %s", stats or "keine Inserate")
        return stats

    def run_forever(self) -> None:
        try:
            while True:
                self.run_once()
                self.sleep(self.interval * 60 * random.uniform(0.8, 1.2))
        except KeyboardInterrupt:
            log.info("Beendet.")
        finally:
            self.close()

    def close(self) -> None:
        for p in self.portals.values():
            p.close()
