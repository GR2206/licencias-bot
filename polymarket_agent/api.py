"""Lectura pública de Gamma y CLOB. Sin autenticación y sin órdenes."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone

import requests

from polymarket_agent.risk import LIMITS, fee_rate_from_type

GAMMA_BASE = "https://gamma-api.polymarket.com"
CLOB_BASE = "https://clob.polymarket.com"
USER_AGENT = "polymarket-paper-agent/1.0 (simulacion; no envia ordenes)"


@dataclass(frozen=True)
class Market:
    slug: str
    question: str
    description: str
    category: str
    end: datetime | None
    fee_rate: float
    fee_exponent: float
    event_id: str
    outcomes: tuple[str, str]
    token_ids: tuple[str, str]
    gamma_prices: tuple[float, float]
    min_order_size: float
    tick_size: float
    accepting: bool
    closed: bool

    @property
    def yes_price(self) -> float:
        return self.gamma_prices[0]

    def label(self) -> str:
        return f"{self.slug} | {self.question} | {self.category}"


@dataclass(frozen=True)
class Book:
    bid: float
    ask: float
    ask_depth: float
    min_order_size: float
    tick_size: float

    @property
    def spread(self) -> float:
        return self.ask - self.bid

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2


class MarketDataError(RuntimeError):
    pass


def _as_list(value) -> list:
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value:
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _fees(raw: dict) -> tuple[float, float]:
    schedule = raw.get("feeSchedule") or {}
    if isinstance(schedule, dict) and schedule.get("rate") is not None:
        exponent = schedule.get("exponent")
        return float(schedule["rate"]), float(exponent if exponent is not None else 1)
    fallback = fee_rate_from_type(str(raw.get("feeType") or ""))
    if fallback is None:
        return LIMITS.unknown_fee_rate, 1.0
    return fallback, 1.0


def parse_market(raw: dict) -> Market | None:
    outcomes = [str(item) for item in _as_list(raw.get("outcomes"))]
    tokens = [str(item) for item in _as_list(raw.get("clobTokenIds"))]
    prices_raw = _as_list(raw.get("outcomePrices"))
    if len(outcomes) != 2 or len(tokens) != 2 or len(prices_raw) < 2:
        return None
    try:
        prices = (float(prices_raw[0]), float(prices_raw[1]))
    except (TypeError, ValueError):
        return None
    events = raw.get("events") or []
    event_id = ""
    if events and isinstance(events[0], dict):
        event_id = str(events[0].get("id") or "")
    fee_rate, fee_exponent = _fees(raw)
    category = str(raw.get("feeType") or "sin_categoria")
    return Market(
        slug=str(raw.get("slug") or ""),
        question=str(raw.get("question") or ""),
        description=str(raw.get("description") or ""),
        category=category,
        end=_parse_time(raw.get("endDate") or raw.get("endDateIso")),
        fee_rate=fee_rate,
        fee_exponent=fee_exponent,
        event_id=event_id or str(raw.get("slug") or ""),
        outcomes=(outcomes[0], outcomes[1]),
        token_ids=(tokens[0], tokens[1]),
        gamma_prices=prices,
        min_order_size=float(raw.get("orderMinSize") or 0),
        tick_size=float(raw.get("orderPriceMinTickSize") or 0.01),
        accepting=bool(raw.get("acceptingOrders", True)),
        closed=bool(raw.get("closed", False)),
    )


class HttpMarketData:
    def __init__(self, session: requests.Session | None = None, timeout: float = 20):
        self.timeout = timeout
        self.session = session or requests.Session()
        self.session.headers.setdefault("User-Agent", USER_AGENT)

    def _get(self, base: str, path: str, params: dict) -> dict | list:
        try:
            response = self.session.get(f"{base}{path}", params=params, timeout=self.timeout)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as exc:
            raise MarketDataError(str(exc)) from exc

    def _post(self, path: str, payload) -> dict:
        try:
            response = self.session.post(
                f"{CLOB_BASE}{path}",
                json=payload,
                timeout=self.timeout,
            )
            response.raise_for_status()
            body = response.json()
        except requests.RequestException as exc:
            raise MarketDataError(str(exc)) from exc
        if not isinstance(body, dict):
            raise MarketDataError(f"respuesta inesperada de {path}")
        return body

    def list_open(self, limit: int = 100, pages: int = 1) -> list[Market]:
        markets: list[Market] = []
        cursor = None
        page_size = max(1, min(int(limit), 100))
        for _ in range(max(1, pages)):
            params = {
                "closed": "false",
                "limit": str(page_size),
                "order": "volume24hr",
                "ascending": "false",
            }
            if cursor:
                params["after_cursor"] = cursor
            payload = self._get(GAMMA_BASE, "/markets/keyset", params)
            if not isinstance(payload, dict):
                break
            for raw in payload.get("markets") or []:
                parsed = parse_market(raw)
                if parsed and parsed.slug:
                    markets.append(parsed)
            cursor = payload.get("next_cursor")
            if not cursor:
                break
        return markets

    def by_slug(self, slug: str) -> Market:
        payload = self._get(GAMMA_BASE, "/markets", {"slug": slug})
        raw_list = payload if isinstance(payload, list) else []
        if not raw_list:
            raise MarketDataError(f"no encontré el mercado {slug}")
        parsed = parse_market(raw_list[0])
        if parsed is None:
            raise MarketDataError(f"{slug} no es un mercado binario")
        return parsed

    def spreads(self, token_ids: list[str]) -> dict[str, float]:
        found: dict[str, float] = {}
        for start in range(0, len(token_ids), 100):
            chunk = token_ids[start : start + 100]
            if not chunk:
                continue
            body = self._post("/spreads", [{"token_id": token_id} for token_id in chunk])
            for token_id, spread in body.items():
                try:
                    found[str(token_id)] = float(spread)
                except (TypeError, ValueError):
                    continue
        return found

    def book(self, token_id: str) -> Book:
        payload = self._get(CLOB_BASE, "/book", {"token_id": token_id})
        if not isinstance(payload, dict):
            raise MarketDataError("libro vacío")
        bids = _levels(payload.get("bids"))
        asks = _levels(payload.get("asks"))
        if not bids or not asks:
            raise MarketDataError("libro sin puntas")
        bid = max(price for price, _size in bids)
        ask = min(price for price, _size in asks)
        depth = sum(size for _price, size in sorted(asks)[: LIMITS.book_levels])
        min_size = float(payload.get("min_order_size") or 0)
        tick = float(payload.get("tick_size") or 0.01)
        return Book(bid=bid, ask=ask, ask_depth=depth, min_order_size=min_size, tick_size=tick)


def _levels(raw) -> list[tuple[float, float]]:
    levels = []
    for level in raw or []:
        try:
            levels.append((float(level["price"]), float(level["size"])))
        except (KeyError, TypeError, ValueError):
            continue
    return levels


def infer_resolution(market: Market) -> str | None:
    """YES si ganó el primer outcome, NO si ganó el segundo.

    Solo cuando el mercado está cerrado y el precio ya está en el extremo.
    """
    if not market.closed:
        return None
    yes, no = market.gamma_prices
    if yes >= 0.99 and no <= 0.01:
        return "YES"
    if no >= 0.99 and yes <= 0.01:
        return "NO"
    return None
