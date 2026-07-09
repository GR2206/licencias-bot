"""
Juguer 1.1 — Pulse Scalp

Diseño para winrate alto (setups selectivos) con R:R 1:1.5 (3 ganancias ≈ 2 pérdidas).

Reglas:
- 15m define tendencia (EMA20/50 + ADX)
- 5m busca pullback a zona de valor (Bollinger + RSI)
- Solo opera a favor de tendencia 15m
- Mínimo 3 confluencias para disparar
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd
import pandas_ta as ta

try:
    import config
except ImportError:
    RR_TARGET = 1.5
    SL_MIN_PCT = 0.0025
    SL_MAX_PCT = 0.006


@dataclass
class Signal:
    side: str          # LONG | SHORT
    price: float
    sl: float
    tp: float
    sl_pct: float
    score: int
    reasons: list[str]


def _bbands(close: pd.Series, length: int = 20, std_mult: float = 2.0) -> tuple[float, float]:
    """Bollinger inferior/superior — sin depender del nombre de columnas de pandas_ta."""
    mid = close.rolling(length).mean()
    std = close.rolling(length).std()
    lower = mid - std_mult * std
    upper = mid + std_mult * std
    bbl = float(lower.iloc[-1])
    bbu = float(upper.iloc[-1])
    if pd.isna(bbl) or pd.isna(bbu):
        raise ValueError("Bollinger no disponible (datos insuficientes)")
    return bbl, bbu


def _adx_value(df: pd.DataFrame, length: int = 14) -> float:
    adx_df = ta.adx(df["high"], df["low"], df["close"], length=length)
    if adx_df is None or adx_df.empty:
        return 0.0
    col = next((c for c in adx_df.columns if str(c).upper().startswith("ADX")), None)
    if col is None:
        return float(adx_df.iloc[-1, 0])
    return float(adx_df[col].iloc[-1])


def _trend_15m(df: pd.DataFrame) -> tuple[str, float]:
    ema20 = ta.ema(df["close"], length=20)
    ema50 = ta.ema(df["close"], length=50)
    adx = _adx_value(df)

    if ema20.iloc[-1] > ema50.iloc[-1] and adx >= 22:
        return "UP", adx
    if ema20.iloc[-1] < ema50.iloc[-1] and adx >= 22:
        return "DOWN", adx
    return "FLAT", adx


def _sl_tp(price: float, side: str, atr_pct: float) -> tuple[float, float, float]:
    sl_pct = max(config.SL_MIN_PCT, min(config.SL_MAX_PCT, atr_pct * 0.9))
    dist = price * sl_pct
    rr = config.RR_TARGET

    if side == "LONG":
        sl = price - dist
        tp = price + dist * rr
    else:
        sl = price + dist
        tp = price - dist * rr

    return sl, tp, sl_pct * 100


def analyze(df_5m: pd.DataFrame, df_15m: pd.DataFrame) -> Optional[Signal]:
    if len(df_5m) < 60 or len(df_15m) < 60:
        return None

    trend, adx_15 = _trend_15m(df_15m)
    if trend == "FLAT":
        return None

    close = df_5m["close"]
    high = df_5m["high"]
    low = df_5m["low"]

    rsi = ta.rsi(close, length=14)
    ema9 = ta.ema(close, length=9)
    ema21 = ta.ema(close, length=21)
    atr = ta.atr(high, low, close, length=14)
    vol_ma = df_5m["volume"].rolling(20).mean()

    price = float(close.iloc[-1])
    atr_pct = float(atr.iloc[-1] / price) if price else 0.003

    rsi_now = float(rsi.iloc[-1])
    rsi_prev = float(rsi.iloc[-2])
    bbl, bbu = _bbands(close)
    vol_ok = float(df_5m["volume"].iloc[-1]) > float(vol_ma.iloc[-1]) * 0.9

    reasons: list[str] = []

    if trend == "UP":
        reasons.append(f"tendencia 15m UP (ADX {adx_15:.0f})")
        score = 0

        if ema9.iloc[-1] > ema21.iloc[-1]:
            score += 1
            reasons.append("EMA9>EMA21 M5")

        if low.iloc[-1] <= bbl * 1.002 or close.iloc[-2] <= bbl * 1.002:
            score += 1
            reasons.append("toque banda inferior")

        if 32 <= rsi_prev <= 48 and rsi_now > rsi_prev and rsi_now >= 38:
            score += 1
            reasons.append("RSI girando desde sobreventa")

        if close.iloc[-1] > close.iloc[-2]:
            score += 1
            reasons.append("vela M5 alcista")

        if vol_ok:
            score += 1
            reasons.append("volumen ok")

        if score < 4:
            return None

        sl, tp, sl_pct = _sl_tp(price, "LONG", atr_pct)
        return Signal("LONG", price, sl, tp, sl_pct, score, reasons)

    if trend == "DOWN":
        reasons.append(f"tendencia 15m DOWN (ADX {adx_15:.0f})")
        score = 0

        if ema9.iloc[-1] < ema21.iloc[-1]:
            score += 1
            reasons.append("EMA9<EMA21 M5")

        if high.iloc[-1] >= bbu * 0.998 or close.iloc[-2] >= bbu * 0.998:
            score += 1
            reasons.append("toque banda superior")

        if 52 <= rsi_prev <= 68 and rsi_now < rsi_prev and rsi_now <= 62:
            score += 1
            reasons.append("RSI girando desde sobrecompra")

        if close.iloc[-1] < close.iloc[-2]:
            score += 1
            reasons.append("vela M5 bajista")

        if vol_ok:
            score += 1
            reasons.append("volumen ok")

        if score < 4:
            return None

        sl, tp, sl_pct = _sl_tp(price, "SHORT", atr_pct)
        return Signal("SHORT", price, sl, tp, sl_pct, score, reasons)

    return None
