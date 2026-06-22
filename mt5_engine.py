import logging
import os
import time
from datetime import datetime, timedelta, timezone

import pandas as pd

from config import (
    CANDLES_NEEDED,
    CAPITAL_USAGE,
    LEVERAGE,
    MAX_CONSECUTIVE_LOSSES,
    MAX_DAILY_LOSS,
    MAX_DAILY_TRADES,
    MAX_ENTRY_SPREAD_PCT,
    MAX_HOLD_MINUTES,
    MT5_DEVIATION_POINTS,
    MT5_MAGIC_NUMBER,
    MT5_SYMBOL,
    REAL_TRADING_ENABLED,
    RISK_PER_TRADE,
    STOP_LOSS_PCT,
    TAKE_PROFIT_PCT,
    TOUCH_THRESHOLD,
)
from paper_engine import in_session, is_market_open, secs_to_next_candle
from strategy import AILearner, TrendLock, calc_position_size, compute_levels, enrich, get_signal


log = logging.getLogger("XAUBot")


def _load_mt5():
    try:
        import MetaTrader5 as mt5
    except ImportError as exc:
        raise RuntimeError(
            "MetaTrader5 no esta instalado. Instalalo en el entorno donde corre MT5 "
            "y manten PAPER_MODE=True hasta probarlo en demo."
        ) from exc
    return mt5


def _require_real_trading_enabled() -> None:
    if not REAL_TRADING_ENABLED or os.getenv("ALLOW_REAL_TRADING") != "1":
        raise RuntimeError(
            "Trading real bloqueado. Para habilitarlo debes poner "
            "REAL_TRADING_ENABLED=True y export ALLOW_REAL_TRADING=1."
        )


def _initialize(mt5) -> None:
    path = os.getenv("MT5_PATH")
    login = os.getenv("MT5_LOGIN")
    password = os.getenv("MT5_PASSWORD")
    server = os.getenv("MT5_SERVER")

    if path:
        ok = mt5.initialize(path=path)
    else:
        ok = mt5.initialize()
    if not ok:
        raise RuntimeError(f"MT5 initialize fallo: {mt5.last_error()}")

    if login and password and server:
        if not mt5.login(int(login), password=password, server=server):
            raise RuntimeError(f"MT5 login fallo: {mt5.last_error()}")

    if not mt5.symbol_select(MT5_SYMBOL, True):
        raise RuntimeError(f"No se pudo seleccionar simbolo {MT5_SYMBOL}: {mt5.last_error()}")


def _rates_to_df(mt5, count: int) -> pd.DataFrame:
    rates = mt5.copy_rates_from_pos(MT5_SYMBOL, mt5.TIMEFRAME_M1, 0, count + 1)
    if rates is None or len(rates) == 0:
        raise RuntimeError(f"MT5 no devolvio velas: {mt5.last_error()}")
    df = pd.DataFrame(rates)
    df["ts"] = pd.to_datetime(df["time"], unit="s", utc=True)
    df = df.rename(columns={"tick_volume": "volume"})
    df = df[["ts", "open", "high", "low", "close", "volume"]]
    return df.iloc[:-1].tail(count).reset_index(drop=True)


def _spread_pct(mt5) -> float:
    tick = mt5.symbol_info_tick(MT5_SYMBOL)
    if tick is None or not tick.bid or not tick.ask:
        return 1.0
    mid = (tick.ask + tick.bid) / 2
    return (tick.ask - tick.bid) / mid


def _own_positions(mt5):
    positions = mt5.positions_get(symbol=MT5_SYMBOL)
    if positions is None:
        return []
    return [pos for pos in positions if getattr(pos, "magic", None) == MT5_MAGIC_NUMBER]


def _normalize_volume(info, units: float) -> float:
    contract_size = float(getattr(info, "trade_contract_size", 100.0) or 100.0)
    raw_lots = units / contract_size
    min_lot = float(info.volume_min)
    max_lot = float(info.volume_max)
    step = float(info.volume_step)
    lots = max(min_lot, min(max_lot, raw_lots))
    steps = round(lots / step)
    return round(steps * step, 4)


def _send_market_order(mt5, side: str, entry: float, atr: float, capital: float, features: dict) -> bool:
    info = mt5.symbol_info(MT5_SYMBOL)
    tick = mt5.symbol_info_tick(MT5_SYMBOL)
    if info is None or tick is None:
        log.warning("Sin info/tick de MT5")
        return False

    levels = compute_levels(entry, side, TAKE_PROFIT_PCT, STOP_LOSS_PCT, atr)
    units = calc_position_size(capital, entry, levels["sl"], LEVERAGE, CAPITAL_USAGE, RISK_PER_TRADE)
    lots = _normalize_volume(info, units)
    contract_size = float(getattr(info, "trade_contract_size", 100.0) or 100.0)
    normalized_units = lots * contract_size
    planned_risk = abs(entry - levels["sl"]) * normalized_units
    max_risk = capital * RISK_PER_TRADE * 1.20
    if planned_risk > max_risk:
        log.warning(
            "Orden omitida: volumen minimo del broker arriesga %.2f, limite %.2f",
            planned_risk,
            max_risk,
        )
        return False

    if side == "long":
        order_type = mt5.ORDER_TYPE_BUY
        price = tick.ask
    else:
        order_type = mt5.ORDER_TYPE_SELL
        price = tick.bid

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": MT5_SYMBOL,
        "volume": lots,
        "type": order_type,
        "price": price,
        "sl": levels["sl"],
        "tp": levels["tp"],
        "deviation": MT5_DEVIATION_POINTS,
        "magic": MT5_MAGIC_NUMBER,
        "comment": f"xau_scalp {features.get('atr_pct', 0)}",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }
    result = mt5.order_send(request)
    if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
        log.warning("Orden rechazada: %s", result)
        return False

    log.info(
        "MT5 OPEN %s lots=%.4f price=%.2f TP=%.2f SL=%.2f",
        side.upper(),
        lots,
        price,
        levels["tp"],
        levels["sl"],
    )
    return True


def _close_position(mt5, position, reason: str) -> bool:
    tick = mt5.symbol_info_tick(MT5_SYMBOL)
    if tick is None:
        return False

    if position.type == mt5.POSITION_TYPE_BUY:
        order_type = mt5.ORDER_TYPE_SELL
        price = tick.bid
    else:
        order_type = mt5.ORDER_TYPE_BUY
        price = tick.ask

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": MT5_SYMBOL,
        "position": position.ticket,
        "volume": position.volume,
        "type": order_type,
        "price": price,
        "deviation": MT5_DEVIATION_POINTS,
        "magic": MT5_MAGIC_NUMBER,
        "comment": f"xau_scalp close {reason}",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }
    result = mt5.order_send(request)
    ok = result is not None and result.retcode == mt5.TRADE_RETCODE_DONE
    if ok:
        log.info("MT5 CLOSE ticket=%s reason=%s price=%.2f", position.ticket, reason, price)
    else:
        log.warning("Cierre rechazado: %s", result)
    return ok


def _close_expired_positions(mt5) -> None:
    now = datetime.now(timezone.utc)
    for position in _own_positions(mt5):
        opened = datetime.fromtimestamp(position.time, tz=timezone.utc)
        if now - opened >= timedelta(minutes=MAX_HOLD_MINUTES):
            _close_position(mt5, position, "TIME_EXIT")


def _daily_stats(mt5) -> dict:
    now = datetime.now(timezone.utc)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    deals = mt5.history_deals_get(start, now)
    if deals is None:
        deals = []

    exits = []
    for deal in deals:
        if getattr(deal, "symbol", "") != MT5_SYMBOL:
            continue
        if getattr(deal, "magic", None) != MT5_MAGIC_NUMBER:
            continue
        if getattr(deal, "entry", None) in (mt5.DEAL_ENTRY_OUT, mt5.DEAL_ENTRY_OUT_BY):
            exits.append(deal)

    def deal_pnl(deal) -> float:
        return float(deal.profit) + float(deal.commission) + float(deal.swap)

    exits.sort(key=lambda item: item.time)
    pnl = sum(deal_pnl(deal) for deal in exits)
    consecutive_losses = 0
    for deal in reversed(exits):
        if deal_pnl(deal) < 0:
            consecutive_losses += 1
        else:
            break
    return {
        "pnl": pnl,
        "closed_trades": len(exits),
        "consecutive_losses": consecutive_losses,
    }


def _can_trade_live(mt5, ts: datetime) -> tuple[bool, str]:
    if not in_session(ts):
        return False, "FUERA_SESION"
    stats = _daily_stats(mt5)
    account = mt5.account_info()
    equity = float(account.equity if account else 0.0)
    if stats["closed_trades"] >= MAX_DAILY_TRADES:
        return False, "MAX_TRADES"
    if stats["consecutive_losses"] >= MAX_CONSECUTIVE_LOSSES:
        return False, "MAX_LOSSES_CONSECUTIVE"
    if equity > 0 and stats["pnl"] <= -(equity * MAX_DAILY_LOSS):
        return False, "STOP_DIARIO"
    return True, "OK"


def run_mt5() -> None:
    _require_real_trading_enabled()
    mt5 = _load_mt5()
    _initialize(mt5)

    trend_lock = TrendLock()
    ai = AILearner(base_touch_pct=TOUCH_THRESHOLD)
    last_ts = None
    log.info("XAUUSD SCALP BOT - MT5 MODE | symbol=%s | AI=%s", MT5_SYMBOL, ai.summary())

    while True:
        try:
            open_ok, open_msg = is_market_open()
            if not open_ok:
                log.info("%s - pausa 5m", open_msg)
                time.sleep(300)
                continue

            time.sleep(secs_to_next_candle() + 2.0)
            _close_expired_positions(mt5)

            df = _rates_to_df(mt5, CANDLES_NEEDED)
            if df.empty or len(df) < 60:
                continue
            ts = df.iloc[-1]["ts"]
            if last_ts is not None and ts == last_ts:
                continue
            last_ts = ts

            df_e = enrich(df)
            row = df_e.iloc[-1]
            signal, reason, features = get_signal(df_e, trend_lock, ai)
            ok, block_reason = _can_trade_live(mt5, ts.to_pydatetime())
            spread = _spread_pct(mt5)
            if spread > MAX_ENTRY_SPREAD_PCT:
                ok = False
                block_reason = f"SPREAD_{spread * 100:.3f}%"

            log.info(
                "[%s] XAU=%.2f spread=%.4f%% | %s (%s) | %s | %s",
                ts.strftime("%H:%M"),
                float(row["close"]),
                spread * 100,
                signal or "NONE",
                reason,
                trend_lock,
                block_reason if not ok else "OK",
            )

            if not ok or signal is None or _own_positions(mt5):
                continue

            account = mt5.account_info()
            capital = float(account.equity if account else 0.0)
            if capital <= 0:
                log.warning("Equity no valida, se omite orden")
                continue
            _send_market_order(mt5, signal.lower(), float(row["close"]), float(row["atr"]), capital, features)

        except KeyboardInterrupt:
            log.info("MT5 bot detenido por usuario")
            break
        except Exception as exc:
            log.exception("MT5 error: %s", exc)
            time.sleep(10)
