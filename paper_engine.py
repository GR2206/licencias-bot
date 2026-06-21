import json
import logging
import time
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests

from config import (
    BYBIT_SYMBOL,
    CANDLES_NEEDED,
    CAPITAL_USAGE,
    COMMISSION,
    COOLDOWN_AFTER_SL,
    FILTER_SESSIONS,
    HTTP_TIMEOUT_SECONDS,
    INTRABAR_POLICY,
    LEVERAGE,
    MAX_CONSECUTIVE_LOSSES,
    MAX_DAILY_LOSS,
    MAX_DAILY_TRADES,
    MAX_HOLD_MINUTES,
    PAPER_CAPITAL,
    RISK_PER_TRADE,
    SESSION_RANGES,
    SLIPPAGE_PCT,
    SPREAD_PCT,
    STOP_LOSS_PCT,
    TAKE_PROFIT_PCT,
    TRADES_FILE,
    TOUCH_THRESHOLD,
    XAU_MAX,
    XAU_MIN,
)
from strategy import AILearner, TrendLock, calc_position_size, compute_levels, enrich, get_signal


log = logging.getLogger("XAUBot")

BYBIT_BASE = "https://api.bybit.com"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def is_market_open() -> tuple[bool, str]:
    now = utc_now()
    wday = now.weekday()
    if wday == 5:
        return False, "MERCADO_CERRADO_SABADO"
    if wday == 6 and now.hour < 22:
        return False, "MERCADO_CERRADO_DOMINGO"
    if wday == 4 and now.hour >= 22:
        return False, "MERCADO_CERRADO_VIERNES"
    return True, "abierto"


def seconds_to_next_open() -> float:
    now = utc_now()
    wday = now.weekday()
    if wday == 4 and now.hour >= 22:
        target = (now + timedelta(days=2)).replace(hour=22, minute=5, second=0, microsecond=0)
    elif wday == 5:
        target = (now + timedelta(days=1)).replace(hour=22, minute=5, second=0, microsecond=0)
    elif wday == 6 and now.hour < 22:
        target = now.replace(hour=22, minute=5, second=0, microsecond=0)
    else:
        return 0.0
    return max((target - now).total_seconds(), 0.0)


def validate_price(price: float) -> bool:
    return XAU_MIN <= price <= XAU_MAX


def in_session(ts: datetime | None = None) -> bool:
    if not FILTER_SESSIONS:
        return True
    now = ts or utc_now()
    hour = now.hour
    return any(start <= hour < end for start, end in SESSION_RANGES)


def fetch_candles(limit: int = 120) -> pd.DataFrame:
    url = f"{BYBIT_BASE}/v5/market/kline"
    params = {
        "category": "linear",
        "symbol": BYBIT_SYMBOL,
        "interval": "1",
        "limit": limit + 1,
    }
    response = requests.get(url, params=params, timeout=HTTP_TIMEOUT_SECONDS)
    response.raise_for_status()
    payload = response.json()
    if payload.get("retCode", -1) != 0:
        raise ValueError(f"Bybit error: {payload.get('retMsg', 'unknown')}")

    rows = []
    for candle in reversed(payload["result"]["list"]):
        rows.append(
            {
                "ts": pd.to_datetime(int(candle[0]), unit="ms", utc=True),
                "open": float(candle[1]),
                "high": float(candle[2]),
                "low": float(candle[3]),
                "close": float(candle[4]),
                "volume": float(candle[5]),
            }
        )

    df = pd.DataFrame(rows).dropna(subset=["open", "high", "low", "close"])
    df = df.iloc[:-1].tail(limit).reset_index(drop=True)
    if df.empty:
        return df

    last_price = float(df.iloc[-1]["close"])
    if not validate_price(last_price):
        log.warning("Precio fuera de rango: %.2f", last_price)
        return pd.DataFrame()
    return df


def secs_to_next_candle() -> float:
    return 60.0 - (time.time() % 60.0)


def _entry_fill(mid_price: float, side: str) -> float:
    spread = mid_price * SPREAD_PCT
    slippage = mid_price * SLIPPAGE_PCT
    if side == "long":
        return mid_price + spread / 2 + slippage
    return mid_price - spread / 2 - slippage


def _exit_fill(mid_price: float, side: str) -> float:
    spread = mid_price * SPREAD_PCT
    slippage = mid_price * SLIPPAGE_PCT
    if side == "long":
        return mid_price - spread / 2 - slippage
    return mid_price + spread / 2 + slippage


class PaperState:
    def __init__(self) -> None:
        self.capital = PAPER_CAPITAL
        self.initial = PAPER_CAPITAL
        self.day_start_capital = PAPER_CAPITAL
        self.position: dict | None = None
        self.trades: list[dict] = []
        self.daily_pnl = 0.0
        self.daily_trades = 0
        self.last_day = utc_now().date()
        self.cooldown_until = 0.0
        self.last_ts = None
        self.wins = 0
        self.losses = 0
        self.consecutive_losses = 0
        self.total_pnl = 0.0

    def reset_day(self) -> None:
        today = utc_now().date()
        if today == self.last_day:
            return
        log.info(
            "Nuevo dia: PnL=%+.2f | trades=%s | capital=%.2f",
            self.daily_pnl,
            self.daily_trades,
            self.capital,
        )
        self.daily_pnl = 0.0
        self.daily_trades = 0
        self.consecutive_losses = 0
        self.day_start_capital = self.capital
        self.last_day = today

    def can_trade(self, ts: datetime) -> tuple[bool, str]:
        if not in_session(ts):
            return False, "FUERA_SESION"
        if time.time() < self.cooldown_until:
            return False, f"COOLDOWN_{int(self.cooldown_until - time.time())}s"
        if self.daily_trades >= MAX_DAILY_TRADES:
            return False, "MAX_TRADES"
        if self.consecutive_losses >= MAX_CONSECUTIVE_LOSSES:
            return False, "MAX_LOSSES_CONSECUTIVE"
        if self.daily_pnl <= -(self.day_start_capital * MAX_DAILY_LOSS):
            return False, "STOP_DIARIO"
        return True, "OK"

    def open_pos(self, side: str, mid_entry: float, atr: float, features: dict) -> bool:
        entry = _entry_fill(mid_entry, side)
        levels = compute_levels(entry, side, TAKE_PROFIT_PCT, STOP_LOSS_PCT, atr)
        units = calc_position_size(
            self.capital,
            entry,
            levels["sl"],
            LEVERAGE,
            CAPITAL_USAGE,
            RISK_PER_TRADE,
        )
        if units <= 0:
            log.warning("No se pudo calcular posicion valida")
            return False

        notional = units * entry
        tp_usd = abs(levels["tp"] - entry) * units
        sl_usd = abs(levels["sl"] - entry) * units
        self.position = {
            "side": side,
            "entry_mid": mid_entry,
            "entry": entry,
            "tp": levels["tp"],
            "sl": levels["sl"],
            "units": units,
            "notional": notional,
            "ts": utc_now(),
            "features": features,
        }
        log.info(
            "OPEN %s | mid=%.2f fill=%.2f | %.4f oz notional=%.2f | "
            "TP=%.2f (+%.2f) SL=%.2f (-%.2f)",
            side.upper(),
            mid_entry,
            entry,
            units,
            notional,
            levels["tp"],
            tp_usd,
            levels["sl"],
            sl_usd,
        )
        return True

    def close_pos(self, exit_mid: float, reason: str, ai: AILearner) -> None:
        if not self.position:
            return

        pos = self.position
        side = pos["side"]
        exit_price = _exit_fill(exit_mid, side)
        points = exit_price - pos["entry"] if side == "long" else pos["entry"] - exit_price
        fee = (pos["entry"] + exit_price) * COMMISSION * pos["units"]
        pnl = round(points * pos["units"] - fee, 4)
        win = pnl > 0

        self.capital += pnl
        self.daily_pnl += pnl
        self.daily_trades += 1
        self.total_pnl += pnl
        self.wins += int(win)
        self.losses += int(not win)
        self.consecutive_losses = 0 if win else self.consecutive_losses + 1

        if not win:
            self.cooldown_until = time.time() + COOLDOWN_AFTER_SL * 60

        total = self.wins + self.losses
        win_rate = self.wins / max(1, total) * 100
        ret = (self.capital - self.initial) / self.initial * 100
        log.info(
            "CLOSE %s %s | mid=%.2f fill=%.2f | PnL=%+.4f | "
            "capital=%.2f (%+.1f%%) | WR=%.1f%% (%sW/%sL) | PnL_total=%+.2f",
            side.upper(),
            reason,
            exit_mid,
            exit_price,
            pnl,
            self.capital,
            ret,
            win_rate,
            self.wins,
            self.losses,
            self.total_pnl,
        )

        ai.record_trade(pos.get("features", {}), win, pnl)
        log.info("AI: %s", ai.summary())

        self.trades.append(
            {
                "ts": pos["ts"].isoformat(),
                "side": side,
                "entry_mid": pos["entry_mid"],
                "entry": pos["entry"],
                "exit_mid": exit_mid,
                "exit": exit_price,
                "tp": pos["tp"],
                "sl": pos["sl"],
                "units": pos["units"],
                "pnl": pnl,
                "win": win,
                "reason": reason,
                "capital": round(self.capital, 4),
            }
        )
        self.position = None

    def check_exit(self, row: pd.Series, ai: AILearner) -> None:
        if not self.position:
            return

        pos = self.position
        high = float(row["high"])
        low = float(row["low"])
        close = float(row["close"])
        side = pos["side"]

        if side == "long":
            hit_tp = high >= pos["tp"]
            hit_sl = low <= pos["sl"]
        else:
            hit_tp = low <= pos["tp"]
            hit_sl = high >= pos["sl"]

        if hit_tp and hit_sl:
            if INTRABAR_POLICY == "optimistic":
                self.close_pos(pos["tp"], "TP_BOTH_HIT", ai)
            else:
                self.close_pos(pos["sl"], "SL_BOTH_HIT", ai)
            return
        if hit_tp:
            self.close_pos(pos["tp"], "TP", ai)
            return
        if hit_sl:
            self.close_pos(pos["sl"], "SL", ai)
            return

        held_for = utc_now() - pos["ts"]
        if held_for >= timedelta(minutes=MAX_HOLD_MINUTES):
            self.close_pos(close, "TIME_EXIT", ai)


def run_paper() -> None:
    log.info("=" * 72)
    log.info("XAUUSD SCALP BOT - PAPER MODE")
    log.info("Data: Bybit %s M1 | capital=%.2f | risk/trade=%.2f%%", BYBIT_SYMBOL, PAPER_CAPITAL, RISK_PER_TRADE * 100)
    log.info("Limits: daily loss=%.2f%% | max hold=%sm | max trades/day=%s", MAX_DAILY_LOSS * 100, MAX_HOLD_MINUTES, MAX_DAILY_TRADES)
    log.info("Costs: spread=%.4f%% | slippage=%.4f%% | commission=%.4f%%", SPREAD_PCT * 100, SLIPPAGE_PCT * 100, COMMISSION * 100)
    log.info("=" * 72)

    state = PaperState()
    trend_lock = TrendLock()
    ai = AILearner(base_touch_pct=TOUCH_THRESHOLD)
    log.info("AI: %s", ai.summary())

    while True:
        try:
            open_ok, open_msg = is_market_open()
            if not open_ok:
                wait_seconds = seconds_to_next_open()
                wait_minutes = int(wait_seconds / 60)
                log.info("%s | proxima revision en 5m | apertura aprox en %sm", open_msg, wait_minutes)
                time.sleep(300)
                continue

            state.reset_day()
            time.sleep(secs_to_next_candle() + 2.0)

            df = fetch_candles(limit=CANDLES_NEEDED)
            if df.empty or len(df) < 60:
                log.warning("Datos insuficientes")
                time.sleep(30)
                continue

            ts = df.iloc[-1]["ts"]
            if state.last_ts is not None and ts == state.last_ts:
                continue
            state.last_ts = ts

            row = df.iloc[-1]
            price = float(row["close"])
            if not validate_price(price):
                log.warning("Precio invalido: %.2f", price)
                continue

            df_e = enrich(df)
            row_e = df_e.iloc[-1]
            state.check_exit(row_e, ai)

            signal, reason, features = get_signal(df_e, trend_lock, ai)
            ok, block_reason = state.can_trade(ts.to_pydatetime())
            session = "ON" if in_session(ts.to_pydatetime()) else "OFF"

            log.info(
                "[%s %s] XAU=%.2f | EMA9=%.2f EMA50=%.2f ATR%%=%.4f | "
                "%s (%s) | %s | %s",
                session,
                ts.strftime("%H:%M"),
                price,
                float(row_e["ema_fast"]),
                float(row_e["ema_trend"]),
                float(row_e["atr_pct"]) * 100,
                signal or "NONE",
                reason,
                trend_lock,
                block_reason if not ok else "OK",
            )

            if state.position or not ok or signal is None:
                continue

            state.open_pos(signal.lower(), price, float(row_e["atr"]), features)

        except requests.exceptions.ConnectionError:
            log.warning("Sin conexion - reintento en 30s")
            time.sleep(30)
        except requests.exceptions.Timeout:
            log.warning("Timeout - reintento en 15s")
            time.sleep(15)
        except KeyboardInterrupt:
            log.info("Resumen final")
            log.info("Capital: %.2f -> %.2f", state.initial, state.capital)
            log.info("PnL: %+.2f", state.total_pnl)
            total = state.wins + state.losses
            log.info("Trades: %s (%sW/%sL)", total, state.wins, state.losses)
            if state.trades:
                with open(TRADES_FILE, "w", encoding="utf-8") as fh:
                    json.dump(state.trades, fh, indent=2)
                log.info("Guardado: %s", TRADES_FILE)
            break
        except Exception as exc:
            log.exception("Error: %s", exc)
            time.sleep(10)
