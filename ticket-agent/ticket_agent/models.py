from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Listing:
    portal: str
    id: str
    title: str
    url: str
    price: float | None = None  # Gesamtpreis des Inserats in EUR, None = keine Angabe
    negotiable: bool = False  # "VB"
    description: str = ""
    location: str = ""
    seller: str = ""

    @property
    def key(self) -> str:
        return f"{self.portal}:{self.id}"

    @property
    def text(self) -> str:
        return f"{self.title}\n{self.description}"


@dataclass
class Search:
    name: str
    keywords: list[str]
    max_price_per_ticket: float
    min_quantity: int = 1
    max_quantity: int | None = None
    must_contain: list[str] = field(default_factory=list)  # alle müssen vorkommen
    exclude: list[str] = field(default_factory=list)
    min_price_per_ticket: float = 0.0  # darunter: Betrugsverdacht, nur melden
    vb_tolerance_percent: float = 0.0  # bei "VB" bis x % über Limit -> Gegenangebot
    portals: list[str] = field(default_factory=lambda: ["kleinanzeigen", "ebay"])
    urls: dict[str, str] = field(default_factory=dict)  # optionale eigene Such-URL je Portal
    message: str | None = None  # eigene Nachrichtenvorlage


@dataclass
class Decision:
    action: str  # "contact" | "notify" | "ignore"
    reason: str
    quantity: int = 1
    quantity_known: bool = False
    price_per_ticket: float | None = None
    offer: float | None = None  # Gegenangebot (Gesamtpreis), falls über Limit aber VB
    warnings: list[str] = field(default_factory=list)
