"""Límites de riesgo inmutables.

El proceso no tiene una función para cambiarlos. Si algún día se ajustan,
el cambio es un diff revisado por una persona, y solo hacia el lado
conservador. Ver `polymarket_agent.audit.revisar`.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class RiskLimits:
    min_edge_pp: float = 8.0
    kelly_multiplier: float = 0.25
    max_position_fraction: float = 0.06
    max_spread: float = 0.03
    min_price: float = 0.05
    max_price: float = 0.95
    max_resolution_days: int = 90
    fast_market_days: int = 7
    min_sources: int = 2
    max_source_age_hours: int = 48
    divergence_review_pp: float = 25.0
    probability_step: float = 0.05
    book_depth_multiple: float = 10.0
    drawdown_halt: float = 0.25
    book_levels: int = 3
    # Si la API no trae feeSchedule, se usa la tasa más alta de la guía
    # para no inflar el edge.
    unknown_fee_rate: float = 0.07


LIMITS = RiskLimits()


def fee_rate_from_type(fee_type: str) -> float | None:
    """Tasas de la guía, solo como respaldo cuando Gamma no manda rate."""
    name = (fee_type or "").lower()
    if not name:
        return None
    if "geopolit" in name:
        return 0.0
    if "crypto" in name:
        return 0.07
    if any(token in name for token in ("sport", "econ", "cultur", "weather", "climate", "clima")):
        return 0.05
    if any(token in name for token in ("politic", "financ", "tech")):
        return 0.04
    return None
