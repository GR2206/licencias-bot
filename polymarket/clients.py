"""Clientes de solo lectura para Gamma y CLOB. Sin autenticación."""

from __future__ import annotations

import json
import time
from typing import Optional
from urllib.parse import urlencode

import requests

from polymarket.config import CLOB_BASE, GAMMA_BASE, USER_AGENT


class PublicClient:
    def __init__(self, timeout: float = 20.0, pause: float = 0.12):
        self.timeout = timeout
        self.pause = pause
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})

    def get(self, url: str, params: Optional[dict] = None):
        response = self.session.get(url, params=params, timeout=self.timeout)
        response.raise_for_status()
        return response.json()

    def post(self, url: str, payload):
        response = self.session.post(url, json=payload, timeout=self.timeout)
        response.raise_for_status()
        return response.json()

    def iter_open_markets(
        self,
        limit: int = 200,
        page_size: int = 100,
        order: str = "volume24hr",
        ascending: bool = False,
    ):
        fetched = 0
        after = None
        seen_cursors = set()
        page_size = min(100, max(1, page_size))
        while fetched < limit:
            params = {
                "closed": "false",
                "limit": min(page_size, limit - fetched),
                "order": order,
                "ascending": "true" if ascending else "false",
            }
            if after:
                params["after_cursor"] = after
            data = self.get(f"{GAMMA_BASE}/markets/keyset", params=params)
            markets = data.get("markets") or []
            if not markets:
                break
            for market in markets:
                yield market
                fetched += 1
                if fetched >= limit:
                    return
            cursor = data.get("next_cursor")
            if not cursor or cursor in seen_cursors:
                break
            seen_cursors.add(cursor)
            after = cursor
            time.sleep(self.pause)

    def market_by_slug(self, slug: str) -> Optional[dict]:
        data = self.get(f"{GAMMA_BASE}/markets/keyset", params={"slug": slug, "limit": 1})
        markets = data.get("markets") or []
        return markets[0] if markets else None

    def prices(self, token_ids: list[str], side: str = "BUY") -> dict:
        out = {}
        chunk = 100
        for i in range(0, len(token_ids), chunk):
            batch = token_ids[i : i + chunk]
            payload = [{"token_id": token_id, "side": side} for token_id in batch]
            data = self.post(f"{CLOB_BASE}/prices", payload)
            out.update(data or {})
            time.sleep(self.pause)
        return out

    def book(self, token_id: str) -> dict:
        return self.get(f"{CLOB_BASE}/book", params={"token_id": token_id})

    def spread(self, token_id: str) -> float:
        data = self.get(f"{CLOB_BASE}/spread", params={"token_id": token_id})
        return float(data.get("spread"))


def parse_json_list(value) -> list:
    if value in (None, ""):
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def token_ids(market: dict) -> list[str]:
    return [str(x) for x in parse_json_list(market.get("clobTokenIds"))]


def outcome_prices(market: dict) -> list[float]:
    values = parse_json_list(market.get("outcomePrices"))
    prices = []
    for value in values:
        try:
            prices.append(float(value))
        except (TypeError, ValueError):
            prices.append(None)
    return prices


def price_from_map(price_map: dict, token_id: str, side: str = "BUY") -> Optional[float]:
    entry = price_map.get(token_id)
    if entry is None:
        return None
    if isinstance(entry, dict):
        raw = entry.get(side) or entry.get(side.lower()) or entry.get(side.upper())
    else:
        raw = entry
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def event_meta(market: dict) -> dict:
    events = market.get("events") or []
    event = events[0] if events else {}
    tags = event.get("tags") or []
    return {
        "event_id": str(event.get("id") or event.get("slug") or ""),
        "event_slug": event.get("slug") or "",
        "event_title": event.get("title") or "",
        "tags": tags,
        "resolution_source": event.get("resolutionSource") or market.get("resolutionSource") or "",
    }


def published_fee_rate(market: dict) -> Optional[float]:
    schedule = market.get("feeSchedule") or {}
    if isinstance(schedule, dict) and schedule.get("rate") is not None:
        try:
            return float(schedule["rate"])
        except (TypeError, ValueError):
            return None
    return None


def query_string(params: dict) -> str:
    return urlencode({k: v for k, v in params.items() if v is not None})
