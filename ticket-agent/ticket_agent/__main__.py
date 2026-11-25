from __future__ import annotations

import argparse
import logging

from . import config
from .agent import Agent
from .notify import Notifier
from .portals import Kleinanzeigen
from .store import Store


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="ticket_agent", description="Überwacht Ticket-Angebote und schreibt Verkäufer an.")
    ap.add_argument("-c", "--config", default="config.yaml")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="Überwachung starten")
    run.add_argument("--once", action="store_true", help="nur ein Durchlauf")
    run.add_argument("--dry-run", action="store_true", help="nichts senden, nur melden")
    sub.add_parser("login", help="einmalig bei Kleinanzeigen einloggen")
    sub.add_parser("test-notify", help="Testbenachrichtigung senden")
    sub.add_parser("status", help="letzte Treffer anzeigen")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    cfg, searches = config.load(args.config)

    if args.cmd == "login":
        Kleinanzeigen((cfg.get("portals") or {}).get("kleinanzeigen")).login()
    elif args.cmd == "test-notify":
        Notifier(cfg.get("notify")).send("Ticket-Agent", "Testnachricht – Benachrichtigungen funktionieren.")
    elif args.cmd == "status":
        for status, price, title, url in Store((cfg.get("agent") or {}).get("database", "data/agent.db")).recent():
            print(f"{status:10} {price if price is not None else '-':>7}  {title}\n{'':19}{url}")
    else:
        agent = Agent(cfg, searches, dry_run=args.dry_run)
        if args.once:
            try:
                agent.run_once()
            finally:
                agent.close()
        else:
            agent.run_forever()


if __name__ == "__main__":
    main()
