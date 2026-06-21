import json
import os

import numpy as np
import pandas as pd

from config import (
    AI_STATE_FILE,
    ATR_PERIOD,
    EMA_FAST,
    EMA_SLOW,
    EMA_TREND,
    MAX_ATR_PCT,
    MAX_CHASE_DISTANCE_PCT,
    MIN_ATR_PCT,
    MIN_CANDLE_BODY_PCT,
    MIN_TP_TO_SL_RATIO,
    MIN_TREND_SLOPE_PCT,
    MIN_VOLUME_RATIO,
)


LONG = "LONG"
SHORT = "SHORT"
FLAT = None


def calc_ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def calc_atr(df: pd.DataFrame, period: int) -> pd.Series:
    prev_close = df["close"].shift(1)
    ranges = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    )
    return ranges.max(axis=1).rolling(period).mean()


def enrich(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["ema_fast"] = calc_ema(df["close"], EMA_FAST)
    df["ema_slow"] = calc_ema(df["close"], EMA_SLOW)
    df["ema_trend"] = calc_ema(df["close"], EMA_TREND)
    df["dist_fast"] = (df["close"] - df["ema_fast"]) / df["ema_fast"]
    df["dist_trend"] = (df["close"] - df["ema_trend"]) / df["ema_trend"]
    df["above_fast"] = df["close"] > df["ema_fast"]
    df["cross"] = df["above_fast"] != df["above_fast"].shift(1)
    df["ema_slope"] = (df["ema_fast"] - df["ema_fast"].shift(3)) / df["ema_fast"].shift(3)
    df["trend_slope"] = (df["ema_trend"] - df["ema_trend"].shift(5)) / df["ema_trend"].shift(5)
    df["atr"] = calc_atr(df, ATR_PERIOD)
    df["atr_pct"] = df["atr"] / df["close"]
    df["vol_ratio"] = df["volume"] / df["volume"].rolling(20).mean()
    df["candle_body"] = (df["close"] - df["open"]).abs() / df["open"]
    df["candle_range"] = (df["high"] - df["low"]) / df["open"]
    return df


class AILearner:
    """Small local learner that only tightens filters from recent outcomes."""

    def __init__(self, base_touch_pct: float):
        self.base_touch = base_touch_pct
        self.touch_pct = base_touch_pct
        self.history: list[dict] = []
        self._load()

    def _load(self) -> None:
        if not os.path.exists(AI_STATE_FILE):
            return
        try:
            with open(AI_STATE_FILE, encoding="utf-8") as fh:
                state = json.load(fh)
            self.history = state.get("history", [])[-80:]
            self.touch_pct = float(state.get("touch_pct", self.base_touch))
            self.touch_pct = self._clamp_touch(self.touch_pct)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            self.history = []
            self.touch_pct = self.base_touch

    def save(self) -> None:
        try:
            with open(AI_STATE_FILE, "w", encoding="utf-8") as fh:
                json.dump(
                    {
                        "history": self.history[-80:],
                        "touch_pct": self.touch_pct,
                    },
                    fh,
                    indent=2,
                )
        except OSError:
            pass

    def _clamp_touch(self, value: float) -> float:
        return min(max(value, self.base_touch * 0.45), self.base_touch * 1.60)

    def extract_features(self, row: pd.Series) -> dict:
        return {
            "dist_pct": round(abs(float(row["dist_fast"])), 6),
            "dist_trend": round(float(row["dist_trend"]), 6),
            "vol_ratio": round(float(row["vol_ratio"]), 3),
            "ema_slope": round(float(row["ema_slope"]), 6),
            "trend_slope": round(float(row["trend_slope"]), 6),
            "atr_pct": round(float(row["atr_pct"]), 6),
            "candle_body": round(float(row["candle_body"]), 6),
            "above_fast": bool(row["above_fast"]),
        }

    def record_trade(self, features: dict, win: bool, pnl: float) -> None:
        if not features:
            return
        self.history.append({**features, "win": win, "pnl": round(float(pnl), 4)})
        self.history = self.history[-80:]
        self._update_threshold()
        self.save()

    def _update_threshold(self) -> None:
        recent = self.history[-24:]
        if len(recent) < 8:
            return

        wr = sum(1 for item in recent if item["win"]) / len(recent)
        old = self.touch_pct
        if wr >= 0.58:
            self.touch_pct = self._clamp_touch(self.touch_pct * 1.06)
        elif wr <= 0.42:
            self.touch_pct = self._clamp_touch(self.touch_pct * 0.92)

        if abs(self.touch_pct - old) > 0.000005:
            print(
                "AI ajuste: touch_pct "
                f"{old:.5f} -> {self.touch_pct:.5f} "
                f"(WR ultimos {len(recent)}: {wr * 100:.1f}%)"
            )

    def should_trade(self, features: dict) -> tuple[bool, str]:
        if len(self.history) < 12:
            return True, "AI_LEARNING"

        recent = self.history[-30:]
        wins = [item for item in recent if item["win"]]
        if len(wins) < 4:
            return True, "AI_NO_REF"

        avg_win_vol = float(np.mean([item["vol_ratio"] for item in wins]))
        if features["vol_ratio"] < max(MIN_VOLUME_RATIO, avg_win_vol * 0.62):
            return False, "AI_LOW_VOL"

        avg_win_atr = float(np.mean([item["atr_pct"] for item in wins]))
        if features["atr_pct"] < avg_win_atr * 0.55:
            return False, "AI_DEAD_ATR"

        return True, "AI_OK"

    def summary(self) -> str:
        if not self.history:
            return "sin historial"
        recent = self.history[-24:]
        wr = sum(1 for item in recent if item["win"]) / max(1, len(recent))
        return (
            f"touch_pct={self.touch_pct:.5f} | "
            f"WR ultimos {len(recent)}: {wr * 100:.1f}% | "
            f"trades aprendidos={len(self.history)}"
        )


class TrendLock:
    def __init__(self) -> None:
        self.direction: str | None = None
        self.cross_price: float | None = None
        self.cross_count = 0

    def update_cross(self, direction: str, price: float) -> None:
        self.direction = direction
        self.cross_price = price
        self.cross_count += 1

    def allows(self, signal: str) -> bool:
        if self.direction is None:
            return True
        return signal.lower() == self.direction

    def __str__(self) -> str:
        if self.direction is None or self.cross_price is None:
            return "SIN_LOCK"
        return f"LOCK_{self.direction.upper()}@{self.cross_price:.1f}"


def _finite_features(row: pd.Series) -> bool:
    required = [
        "ema_fast",
        "ema_slow",
        "ema_trend",
        "ema_slope",
        "trend_slope",
        "atr_pct",
        "vol_ratio",
        "candle_body",
    ]
    return all(np.isfinite(float(row[name])) for name in required)


def _trend_direction(row: pd.Series) -> str | None:
    price = float(row["close"])
    ema_trend = float(row["ema_trend"])
    trend_slope = float(row["trend_slope"])

    if price > ema_trend and trend_slope >= MIN_TREND_SLOPE_PCT:
        return "long"
    if price < ema_trend and trend_slope <= -MIN_TREND_SLOPE_PCT:
        return "short"
    return None


def _base_filters(row: pd.Series) -> tuple[bool, str]:
    if not _finite_features(row):
        return False, "WARMUP"
    if float(row["vol_ratio"]) < MIN_VOLUME_RATIO:
        return False, "LOW_VOL"
    if float(row["atr_pct"]) < MIN_ATR_PCT:
        return False, "LOW_ATR"
    if float(row["atr_pct"]) > MAX_ATR_PCT:
        return False, "HIGH_ATR"
    if float(row["candle_body"]) < MIN_CANDLE_BODY_PCT:
        return False, "DOJI"
    if abs(float(row["dist_trend"])) > MAX_CHASE_DISTANCE_PCT:
        return False, "CHASE"
    return True, "OK"


def get_signal(df: pd.DataFrame, trend_lock: TrendLock, ai: AILearner) -> tuple:
    if len(df) < max(EMA_TREND, EMA_SLOW, ATR_PERIOD) + 5:
        return FLAT, "WARMUP", {}

    row = df.iloc[-1]
    if not _finite_features(row):
        return FLAT, "WARMUP", {}

    features = ai.extract_features(row)
    above = bool(row["above_fast"])
    is_cross = bool(row["cross"])
    dist_pct = abs(float(row["dist_fast"]))
    trend_dir = _trend_direction(row)

    if is_cross:
        direction = "long" if above else "short"
        trend_lock.update_cross(direction, float(row["close"]))

    ok, reason = _base_filters(row)
    if not ok:
        return FLAT, reason, features

    if trend_dir is None:
        return FLAT, "NO_TREND", features

    if is_cross:
        direction = "long" if above else "short"
        if direction != trend_dir:
            return FLAT, f"CROSS_AGAINST_{trend_dir.upper()}", features
        return (LONG if direction == "long" else SHORT), f"CROSS_LOCK_{direction.upper()}", features

    is_touch = dist_pct <= ai.touch_pct
    if not is_touch:
        return FLAT, "NONE", features

    signal = LONG if above else SHORT
    signal_dir = signal.lower()
    if signal_dir != trend_dir:
        return FLAT, f"TOUCH_AGAINST_{trend_dir.upper()}", features
    if not trend_lock.allows(signal):
        return FLAT, f"LOCK_BLOCKED({trend_lock})", features

    ai_ok, ai_reason = ai.should_trade(features)
    if not ai_ok:
        return FLAT, ai_reason, features

    return signal, f"TOUCH({dist_pct:.5f})", features


def compute_levels(
    entry: float,
    side: str,
    tp_pct: float,
    sl_pct: float,
    atr: float | None = None,
) -> dict:
    if atr and np.isfinite(atr) and atr > 0:
        tp_distance = max(entry * tp_pct, atr * 0.65)
        sl_distance = max(entry * sl_pct, atr * 0.42)
    else:
        tp_distance = entry * tp_pct
        sl_distance = entry * sl_pct

    if tp_distance / sl_distance < MIN_TP_TO_SL_RATIO:
        tp_distance = sl_distance * MIN_TP_TO_SL_RATIO

    precision = 2
    if side == "long":
        return {
            "tp": round(entry + tp_distance, precision),
            "sl": round(entry - sl_distance, precision),
        }
    return {
        "tp": round(entry - tp_distance, precision),
        "sl": round(entry + sl_distance, precision),
    }


def calc_position_size(
    capital: float,
    entry: float,
    sl: float,
    leverage: int,
    capital_usage: float,
    risk_per_trade: float,
) -> float:
    stop_distance = abs(entry - sl)
    if stop_distance <= 0:
        return 0.0

    risk_units = (capital * risk_per_trade) / stop_distance
    max_notional = capital * leverage * capital_usage
    margin_units = max_notional / entry
    qty = min(risk_units, margin_units)
    if qty < 0.001:
        return 0.0
    return round(qty, 4)
