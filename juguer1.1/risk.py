"""Gestión de riesgo — tamaño, límites diarios, pausas."""

from __future__ import annotations

from datetime import datetime

import config
from state import BotState


def today_key() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d")


def reset_day_if_needed(state: BotState):
    if state.day_key != today_key():
        state.day_key = today_key()
        state.daily_pnl = 0.0
        state.daily_trades = 0
        state.consecutive_losses = 0


def can_trade(state: BotState, balance: float) -> tuple[bool, str]:
    reset_day_if_needed(state)

    if state.paused:
        return False, "bot pausado"

    if state.consecutive_losses >= config.MAX_CONSECUTIVE_LOSSES:
        return False, f"{config.MAX_CONSECUTIVE_LOSSES} pérdidas seguidas"

    if state.daily_trades >= config.MAX_TRADES_PER_DAY:
        return False, "límite diario de trades"

    if balance > 0 and state.daily_pnl <= -balance * config.MAX_DAILY_LOSS:
        return False, "pérdida diaria máxima alcanzada"

    return True, "ok"


def position_size(balance: float, price: float, sl: float, leverage: int) -> float:
    risk_usdt = balance * config.RISK_PER_TRADE
    sl_dist = abs(price - sl)
    if sl_dist <= 0 or price <= 0:
        return 0.0
    qty = risk_usdt / sl_dist
    # Tope por margen: no más del 12% del balance en margen
    max_notional = balance * 0.12 * leverage
    qty_cap = max_notional / price
    return min(qty, qty_cap)


def register_close(state: BotState, pnl: float):
    reset_day_if_needed(state)
    state.daily_pnl = round(state.daily_pnl + pnl, 4)
    state.daily_trades += 1
    state.total_trades += 1

    if pnl > 0:
        state.wins += 1
        state.consecutive_losses = 0
    else:
        state.losses += 1
        state.consecutive_losses += 1

    state.total_pnl = round(state.total_pnl + pnl, 4)
