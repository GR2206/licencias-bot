"""Límites de riesgo que el agente no puede cambiar."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RiskLimits:
    edge_min_pp: float = 8.0
    kelly_fraction: float = 0.25
    position_cap: float = 0.06
    max_spread: float = 0.03
    price_min: float = 0.05
    price_max: float = 0.95
    max_resolution_days: int = 90
    book_depth_multiple: float = 10.0
    book_levels: int = 3
    drawdown_halt: float = 0.25
    alarm_edge_pp: float = 25.0
    prob_step: float = 0.05
    min_sources: int = 2
    fast_source_max_hours: int = 48
    fast_resolution_days: int = 7
    initial_bankroll: float = 1000.0


LIMITS = RiskLimits()

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LEDGER = REPO_ROOT / "data" / "polymarket" / "registro.csv"
DEFAULT_SECRETS = REPO_ROOT / "data" / "polymarket" / "telegram.env"

GAMMA_BASE = "https://gamma-api.polymarket.com"
CLOB_BASE = "https://clob.polymarket.com"
USER_AGENT = "nodestudio-paper-agent/1.0"

LEDGER_COLUMNS = [
    "timestamp_utc",
    "mercado",
    "precio_yes",
    "prob_estimada",
    "edge_bruto",
    "edge_neto",
    "fraccion",
    "stake_simulado",
    "bankroll_pre",
    "resultado",
    "pnl",
    "bankroll_post",
    "razonamiento",
    "fuentes",
    "decision",
    "motivo",
    "categoria",
    "slug",
    "event_id",
    "lado",
    "confianza",
    "run_id",
    "condition_id",
]

MUTABLE_LEDGER_FIELDS = frozenset({"resultado", "pnl", "bankroll_post"})

FEE_RATE_BY_CATEGORY = {
    "geopolitics": 0.0,
    "geopolitica": 0.0,
    "geopolítica": 0.0,
    "crypto": 0.07,
    "cryptocurrency": 0.07,
    "finance": 0.04,
    "finanzas": 0.04,
    "politics": 0.04,
    "politica": 0.04,
    "política": 0.04,
    "tech": 0.04,
    "technology": 0.04,
    "mentions": 0.04,
    "sports": 0.05,
    "deportes": 0.05,
    "economics": 0.05,
    "economia": 0.05,
    "economía": 0.05,
    "culture": 0.05,
    "cultura": 0.05,
    "weather": 0.05,
    "clima": 0.05,
    "other": 0.05,
    "general": 0.05,
}

FEE_TYPE_TO_CATEGORY = {
    "politics_fees": "politics",
    "crypto_fees": "crypto",
    "sports_fees": "sports",
    "finance_fees": "finance",
    "tech_fees": "tech",
    "geopolitics_fees": "geopolitics",
    "economics_fees": "economics",
    "culture_fees": "culture",
    "weather_fees": "weather",
}

TAG_TO_CATEGORY = {
    "crypto": "crypto",
    "bitcoin": "crypto",
    "btc": "crypto",
    "ethereum": "crypto",
    "sports": "sports",
    "nba": "sports",
    "nfl": "sports",
    "mlb": "sports",
    "soccer": "sports",
    "football": "sports",
    "tennis": "sports",
    "politics": "politics",
    "elections": "politics",
    "finance": "finance",
    "economics": "economics",
    "tech": "tech",
    "ai": "tech",
    "culture": "culture",
    "weather": "weather",
    "geopolitics": "geopolitics",
}


def assert_limits_intact(limits: RiskLimits = LIMITS) -> None:
    """Los números de la guía no se pueden relajar en runtime."""
    if limits.edge_min_pp < 8.0:
        raise PermissionError("el umbral de 8% no se puede bajar")
    if limits.kelly_fraction > 0.25:
        raise PermissionError("el multiplicador de Kelly no se puede subir")
    if limits.position_cap > 0.06:
        raise PermissionError("el techo del 6% no se puede subir")
    if limits.drawdown_halt > 0.25:
        raise PermissionError("el freno de drawdown no se puede relajar")
