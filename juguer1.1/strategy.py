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

from dataclasses import dataclass, field
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
class Check:
    """Una condición del setup con estado actual."""

    label: str
    ok: bool
    detail: str = ""


@dataclass
class ScanReport:
    trend: str          # UP | DOWN | FLAT
    adx_15: float
    price: float
    rsi: float
    checks: list[Check] = field(default_factory=list)
    score: int = 0
    min_score: int = 4
    signal: Optional["Signal"] = None


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


def _trend_15m(df: pd.DataFrame) -> tuple[str, float, float, float]:
    ema20 = ta.ema(df["close"], length=20)
    ema50 = ta.ema(df["close"], length=50)
    adx = _adx_value(df)
    e20 = float(ema20.iloc[-1])
    e50 = float(ema50.iloc[-1])

    if e20 > e50 and adx >= 22:
        return "UP", adx, e20, e50
    if e20 < e50 and adx >= 22:
        return "DOWN", adx, e20, e50
    return "FLAT", adx, e20, e50


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


def _mark(ok: bool) -> str:
    return "✓" if ok else "✗"


def format_scan(symbol: str, report: ScanReport) -> str:
    """Texto multilínea para consola: ✓ cumple, ✗ no cumple."""
    lines = [f"[{symbol}] análisis @ {report.price:.4f} | RSI {report.rsi:.1f}"]

    if report.trend == "FLAT":
        lines.append(f"  15m: SIN TENDENCIA (ADX {report.adx_15:.1f}) — no opera en rango")
    else:
        lines.append(f"  15m: tendencia {report.trend} (ADX {report.adx_15:.1f})")

    for chk in report.checks:
        extra = f" — {chk.detail}" if chk.detail else ""
        lines.append(f"  {_mark(chk.ok)} {chk.label}{extra}")

    if report.trend != "FLAT":
        m5_total = len(report.checks) - 3
        lines.append(
            f"  Score: {report.score}/{m5_total} (mín {report.min_score})"
            + (" → ENTRADA" if report.signal else " → sin setup")
        )
    else:
        lines.append("  → sin setup (esperar tendencia 15m clara)")

    return "\n".join(lines)


def scan(df_5m: pd.DataFrame, df_15m: pd.DataFrame) -> ScanReport:
    """Evalúa todas las condiciones y devuelve informe con ✓/✗."""
    if len(df_5m) < 60 or len(df_15m) < 60:
        return ScanReport("FLAT", 0.0, 0.0, 0.0, [Check("datos suficientes", False, "menos de 60 velas")])

    trend, adx_15, ema20_15, ema50_15 = _trend_15m(df_15m)
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
    vol_now = float(df_5m["volume"].iloc[-1])
    vol_avg = float(vol_ma.iloc[-1])
    vol_ok = vol_now > vol_avg * 0.9

    checks: list[Check] = []

    ema_bull_15 = ema20_15 > ema50_15
    ema_bear_15 = ema20_15 < ema50_15
    adx_ok = adx_15 >= 22

    checks.append(Check(
        "EMA20>EMA50 en 15m",
        ema_bull_15,
        f"EMA20={ema20_15:.2f} EMA50={ema50_15:.2f}",
    ))
    checks.append(Check(
        "EMA20<EMA50 en 15m",
        ema_bear_15,
        f"EMA20={ema20_15:.2f} EMA50={ema50_15:.2f}",
    ))
    checks.append(Check(
        "ADX≥22 en 15m (tendencia fuerte)",
        adx_ok,
        f"ADX={adx_15:.1f}",
    ))

    if trend == "FLAT":
        return ScanReport(trend, adx_15, price, rsi_now, checks)

    score = 0
    min_score = 4
    signal: Optional[Signal] = None

    if trend == "UP":
        e9, e21 = float(ema9.iloc[-1]), float(ema21.iloc[-1])
        ema_ok = e9 > e21
        if ema_ok:
            score += 1
        checks.append(Check("EMA9>EMA21 en 5m", ema_ok, f"EMA9={e9:.2f} EMA21={e21:.2f}"))

        bb_touch = low.iloc[-1] <= bbl * 1.002 or close.iloc[-2] <= bbl * 1.002
        if bb_touch:
            score += 1
        checks.append(Check(
            "toque banda inferior Bollinger 5m",
            bool(bb_touch),
            f"low={float(low.iloc[-1]):.2f} BBL={bbl:.2f}",
        ))

        rsi_ok = 32 <= rsi_prev <= 48 and rsi_now > rsi_prev and rsi_now >= 38
        if rsi_ok:
            score += 1
        checks.append(Check(
            "RSI girando desde sobreventa",
            rsi_ok,
            f"RSI {rsi_prev:.1f}→{rsi_now:.1f} (necesita 32-48 subiendo, ≥38)",
        ))

        candle_ok = close.iloc[-1] > close.iloc[-2]
        if candle_ok:
            score += 1
        checks.append(Check(
            "vela 5m alcista (cierre > anterior)",
            bool(candle_ok),
            f"{float(close.iloc[-2]):.2f} → {float(close.iloc[-1]):.2f}",
        ))

        if vol_ok:
            score += 1
        checks.append(Check(
            "volumen ≥ 90% media 20 velas",
            vol_ok,
            f"vol={vol_now:.0f} media={vol_avg:.0f}",
        ))

        if score >= min_score:
            sl, tp, sl_pct = _sl_tp(price, "LONG", atr_pct)
            reasons = [c.label for c in checks if c.ok and not c.label.startswith("EMA20<")]
            signal = Signal("LONG", price, sl, tp, sl_pct, score, reasons)

    elif trend == "DOWN":
        e9, e21 = float(ema9.iloc[-1]), float(ema21.iloc[-1])
        ema_ok = e9 < e21
        if ema_ok:
            score += 1
        checks.append(Check("EMA9<EMA21 en 5m", ema_ok, f"EMA9={e9:.2f} EMA21={e21:.2f}"))

        bb_touch = high.iloc[-1] >= bbu * 0.998 or close.iloc[-2] >= bbu * 0.998
        if bb_touch:
            score += 1
        checks.append(Check(
            "toque banda superior Bollinger 5m",
            bool(bb_touch),
            f"high={float(high.iloc[-1]):.2f} BBU={bbu:.2f}",
        ))

        rsi_ok = 52 <= rsi_prev <= 68 and rsi_now < rsi_prev and rsi_now <= 62
        if rsi_ok:
            score += 1
        checks.append(Check(
            "RSI girando desde sobrecompra",
            rsi_ok,
            f"RSI {rsi_prev:.1f}→{rsi_now:.1f} (necesita 52-68 bajando, ≤62)",
        ))

        candle_ok = close.iloc[-1] < close.iloc[-2]
        if candle_ok:
            score += 1
        checks.append(Check(
            "vela 5m bajista (cierre < anterior)",
            bool(candle_ok),
            f"{float(close.iloc[-2]):.2f} → {float(close.iloc[-1]):.2f}",
        ))

        if vol_ok:
            score += 1
        checks.append(Check(
            "volumen ≥ 90% media 20 velas",
            vol_ok,
            f"vol={vol_now:.0f} media={vol_avg:.0f}",
        ))

        if score >= min_score:
            sl, tp, sl_pct = _sl_tp(price, "SHORT", atr_pct)
            reasons = [c.label for c in checks if c.ok and not c.label.startswith("EMA20>")]
            signal = Signal("SHORT", price, sl, tp, sl_pct, score, reasons)

    return ScanReport(trend, adx_15, price, rsi_now, checks, score, min_score, signal)


def analyze(df_5m: pd.DataFrame, df_15m: pd.DataFrame) -> Optional[Signal]:
    return scan(df_5m, df_15m).signal
