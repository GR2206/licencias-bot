"""Cálculos de fee, edge, Kelly y redondeo. Números, no opiniones."""

from __future__ import annotations

import math
import re
from typing import Optional

from polymarket.config import (
    FEE_RATE_BY_CATEGORY,
    FEE_TYPE_TO_CATEGORY,
    LIMITS,
    TAG_TO_CATEGORY,
    RiskLimits,
    assert_limits_intact,
)


def normalize_category(raw: Optional[str]) -> str:
    if not raw:
        return "other"
    key = raw.strip().lower()
    key = key.replace("á", "a").replace("é", "e").replace("í", "i")
    key = key.replace("ó", "o").replace("ú", "u")
    if key in FEE_RATE_BY_CATEGORY:
        return "geopolitics" if key.startswith("geopolitic") else (
            "politics" if key.startswith("politic") else (
                "economics" if key.startswith("econom") else key
            )
        )
    if key in FEE_TYPE_TO_CATEGORY:
        return FEE_TYPE_TO_CATEGORY[key]
    for needle, category in TAG_TO_CATEGORY.items():
        if needle in key:
            return category
    return "other"


def infer_category(
    fee_type: Optional[str] = None,
    tags: Optional[list] = None,
    slug: Optional[str] = None,
    question: Optional[str] = None,
) -> str:
    if fee_type:
        mapped = FEE_TYPE_TO_CATEGORY.get(fee_type.strip().lower())
        if mapped:
            return mapped
        guessed = normalize_category(fee_type)
        if guessed != "other":
            return guessed
    for tag in tags or []:
        if isinstance(tag, dict):
            text = " ".join(
                str(tag.get(k) or "") for k in ("slug", "ticker", "title")
            )
        else:
            text = str(tag)
        guessed = normalize_category(text)
        if guessed != "other":
            return guessed
    for text in (slug, question):
        guessed = normalize_category(text)
        if guessed != "other":
            return guessed
    return "other"


def fee_rate_for(
    category: str,
    published_rate: Optional[float] = None,
) -> float:
    if published_rate is not None:
        try:
            rate = float(published_rate)
        except (TypeError, ValueError):
            rate = None
        else:
            if 0.0 <= rate <= 0.2:
                return rate
    return FEE_RATE_BY_CATEGORY.get(normalize_category(category), 0.05)


def fee_per_share(price: float, fee_rate: float) -> float:
    """fee = C × feeRate × p × (1 − p) con C = 1."""
    return float(fee_rate) * float(price) * (1.0 - float(price))


def fee_pp(price: float, fee_rate: float) -> float:
    return fee_per_share(price, fee_rate) * 100.0


def round_probability(p: float, step: float = LIMITS.prob_step) -> float:
    if p <= 0 or p >= 1:
        raise ValueError("la probabilidad tiene que estar entre 0 y 1")
    rounded = round(float(p) / step) * step
    return min(1.0 - step, max(step, rounded))


def kelly_full(p: float, cost: float) -> float:
    if cost <= 0 or cost >= 1:
        return 0.0
    return (float(p) - float(cost)) / (1.0 - float(cost))


def position_fraction(
    p: float,
    cost: float,
    limits: RiskLimits = LIMITS,
) -> float:
    assert_limits_intact(limits)
    raw = kelly_full(p, cost)
    if raw <= 0:
        return 0.0
    return min(limits.kelly_fraction * raw, limits.position_cap)


def edge_bruto_pp(p: float, cost: float) -> float:
    return (float(p) - float(cost)) * 100.0


def edge_neto_pp(
    p: float,
    cost: float,
    spread: float,
    fee_rate: float,
) -> float:
    return edge_bruto_pp(p, cost) - fee_pp(cost, fee_rate) - (float(spread) * 100.0)


def choose_side(p_yes: float, price_yes: float, price_no: Optional[float]) -> str:
    """Elige YES o NO según dónde hay más desvío bruto."""
    no_cost = 1.0 - float(price_yes) if price_no is None else float(price_no)
    yes_edge = float(p_yes) - float(price_yes)
    no_edge = (1.0 - float(p_yes)) - no_cost
    return "YES" if yes_edge >= no_edge else "NO"


ISO_DATE = re.compile(r"(20\d{2}-\d{2}-\d{2})")


def extract_source_date(source) -> Optional[str]:
    if isinstance(source, dict):
        for key in ("date", "fecha", "published", "published_at"):
            value = source.get(key)
            if value:
                match = ISO_DATE.search(str(value))
                if match:
                    return match.group(1)
        blob = " ".join(str(v) for v in source.values())
    else:
        blob = str(source)
    match = ISO_DATE.search(blob)
    return match.group(1) if match else None


def source_label(source) -> str:
    if isinstance(source, dict):
        return str(
            source.get("url")
            or source.get("href")
            or source.get("nombre")
            or source.get("name")
            or source
        )
    return str(source)


def parse_iso_datetime(value: Optional[str]):
    if not value:
        return None
    from datetime import datetime, timezone

    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def days_until(end_date: Optional[str], now=None) -> Optional[float]:
    from datetime import datetime, timezone

    parsed = parse_iso_datetime(end_date)
    if parsed is None:
        return None
    now = now or datetime.now(timezone.utc)
    return (parsed - now).total_seconds() / 86400.0


def is_fast_market(category: str, days_to_resolution: Optional[float]) -> bool:
    if category in {"sports", "crypto"}:
        return True
    if days_to_resolution is not None and days_to_resolution <= LIMITS.fast_resolution_days:
        return True
    return False


def hours_since(date_text: str, now=None) -> Optional[float]:
    from datetime import datetime, timezone

    parsed = parse_iso_datetime(date_text)
    if parsed is None:
        parsed = parse_iso_datetime(date_text + "T00:00:00+00:00")
    if parsed is None:
        return None
    now = now or datetime.now(timezone.utc)
    return (now - parsed).total_seconds() / 3600.0


def valid_sources(
    sources,
    *,
    fast: bool,
    now=None,
    limits: RiskLimits = LIMITS,
) -> list:
    kept = []
    seen = set()
    for source in sources or []:
        date = extract_source_date(source)
        if not date:
            continue
        label = source_label(source).strip().lower()
        if not label or label in seen:
            continue
        if fast:
            age = hours_since(date, now=now)
            if age is None or age > limits.fast_source_max_hours:
                continue
        seen.add(label)
        kept.append(source)
    return kept


def almost_equal(a: float, b: float, digits: int = 6) -> bool:
    return math.isclose(float(a), float(b), rel_tol=0.0, abs_tol=10 ** (-digits))
