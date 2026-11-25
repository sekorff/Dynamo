from __future__ import annotations

from pathlib import Path

import yaml

from .models import Search


def load(path: str | Path) -> tuple[dict, list[Search]]:
    cfg = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    searches = []
    for raw in cfg.get("searches") or []:
        raw = dict(raw)
        if isinstance(raw.get("keywords"), str):
            raw["keywords"] = [raw["keywords"]]
        if not raw.get("keywords") or "max_price_per_ticket" not in raw:
            raise ValueError(f"Suche {raw.get('name')!r}: 'keywords' und 'max_price_per_ticket' sind Pflicht")
        raw.setdefault("name", raw["keywords"][0])
        searches.append(Search(**raw))
    if not searches:
        raise ValueError("Keine 'searches' in der Konfiguration")
    return cfg, searches
