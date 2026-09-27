"""Filtros duros: se aplican ANTES de investigar."""

from __future__ import annotations

from typing import Optional

from polymarket.config import LIMITS, RiskLimits


def reject_price(price: Optional[float], limits: RiskLimits = LIMITS) -> Optional[str]:
    if price is None:
        return "sin precio público"
    if price < limits.price_min or price > limits.price_max:
        return f"precio extremo ({price:.3f}); comisión y redondeo pesan más que el edge"
    return None


def reject_spread(spread: Optional[float], limits: RiskLimits = LIMITS) -> Optional[str]:
    if spread is None:
        return "sin spread medible"
    if spread > limits.max_spread:
        return f"spread {spread:.3f} > {limits.max_spread:.2f}"
    return None


def reject_resolution(days: Optional[float], limits: RiskLimits = LIMITS) -> Optional[str]:
    if days is None:
        return "fecha de resolución inválida o ausente"
    if days < 0:
        return "fecha de resolución vencida o metadata inconsistente"
    if days > limits.max_resolution_days:
        return f"resuelve en {days:.0f} días (> {limits.max_resolution_days})"
    return None


def top_book_depth(levels, *, best_price: float, side: str = "BUY") -> float:
    """Suma tamaño en los primeros niveles cercanos al mejor precio."""
    if not levels:
        return 0.0
    parsed = []
    for level in levels:
        try:
            price = float(level.get("price"))
            size = float(level.get("size"))
        except (TypeError, ValueError, AttributeError):
            continue
        parsed.append((price, size))
    if side.upper() == "BUY":
        parsed.sort(key=lambda item: item[0])
        window = [item for item in parsed if item[0] <= best_price + 0.02]
    else:
        parsed.sort(key=lambda item: item[0], reverse=True)
        window = [item for item in parsed if item[0] >= best_price - 0.02]
    if not window:
        window = parsed[:3]
    return sum(size for _, size in window[:3])


def reject_book(
    depth_shares: float,
    position_shares: float,
    limits: RiskLimits = LIMITS,
) -> Optional[str]:
    needed = limits.book_depth_multiple * position_shares
    if depth_shares < needed:
        return (
            f"libro flaco: {depth_shares:.1f} shares vs {needed:.1f} "
            f"requeridos (10x la posición)"
        )
    return None


def reject_min_order(stake: float, min_order_size: Optional[float], price: float) -> Optional[str]:
    if not min_order_size:
        return None
    shares = stake / price if price else 0.0
    if shares < float(min_order_size):
        return f"stake {stake:.2f} queda bajo el mínimo operable ({min_order_size} shares)"
    return None
