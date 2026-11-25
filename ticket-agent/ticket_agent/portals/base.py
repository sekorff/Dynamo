from __future__ import annotations

from ..models import Listing, Search


class Portal:
    """Basisklasse. Für ein weiteres Portal: ableiten, search() implementieren, in REGISTRY eintragen."""

    name = "portal"
    can_message = False

    def __init__(self, cfg: dict | None = None):
        self.cfg = cfg or {}

    def search(self, search: Search) -> list[Listing]:
        raise NotImplementedError

    def send_message(self, listing: Listing, text: str) -> None:
        raise NotImplementedError(f"{self.name}: automatische Nachrichten werden nicht unterstützt")

    def close(self) -> None:
        pass
