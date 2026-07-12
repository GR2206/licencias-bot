"""Estado persistente — sobrevive reinicios y bloqueo de pantalla."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field

STATE_FILE = os.path.join(os.path.dirname(__file__), "juguer_state.json")


@dataclass
class BotState:
    paused: bool = False
    day_key: str = ""
    daily_pnl: float = 0.0
    daily_trades: int = 0
    consecutive_losses: int = 0
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    total_pnl: float = 0.0
    active_symbol: str = ""
    active_side: str = ""
    entry_price: float = 0.0
    sl: float = 0.0
    tp: float = 0.0


def load_state() -> BotState:
    if not os.path.exists(STATE_FILE):
        return BotState()
    try:
        with open(STATE_FILE) as f:
            data = json.load(f)
        return BotState(**{k: v for k, v in data.items() if k in BotState.__dataclass_fields__})
    except Exception:
        return BotState()


def save_state(state: BotState):
    with open(STATE_FILE, "w") as f:
        json.dump(asdict(state), f, indent=2)
