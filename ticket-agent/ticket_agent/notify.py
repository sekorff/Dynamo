"""Benachrichtigungen: Konsole immer, optional Telegram und ntfy."""
from __future__ import annotations

import logging
import os

import requests

log = logging.getLogger("ticket_agent")


class Notifier:
    def __init__(self, cfg: dict | None):
        cfg = cfg or {}
        tg = cfg.get("telegram") or {}
        self.tg_token = os.environ.get("TELEGRAM_BOT_TOKEN") or tg.get("bot_token")
        self.tg_chat = os.environ.get("TELEGRAM_CHAT_ID") or tg.get("chat_id")
        ntfy = cfg.get("ntfy") or {}
        self.ntfy_url = ntfy.get("url") or (f"https://ntfy.sh/{ntfy['topic']}" if ntfy.get("topic") else None)

    def send(self, title: str, body: str, url: str | None = None) -> None:
        log.info("%s\n%s%s", title, body, f"\n{url}" if url else "")
        text = f"{title}\n\n{body}" + (f"\n\n{url}" if url else "")
        try:
            if self.tg_token and self.tg_chat:
                requests.post(
                    f"https://api.telegram.org/bot{self.tg_token}/sendMessage",
                    json={"chat_id": self.tg_chat, "text": text[:4000], "disable_web_page_preview": True},
                    timeout=15,
                ).raise_for_status()
            if self.ntfy_url:
                headers = {"Title": title.encode("utf-8"), "Tags": "ticket"}
                if url:
                    headers["Click"] = url
                requests.post(self.ntfy_url, data=body.encode("utf-8"), headers=headers, timeout=15).raise_for_status()
        except requests.RequestException as exc:
            log.warning("Benachrichtigung fehlgeschlagen: %s", exc)
