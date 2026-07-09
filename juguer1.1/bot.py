#!/usr/bin/env python3
"""
Juguer 1.1 — Bot de scalping conservador Binance Futures

Diseño: pocas operaciones, alta selectividad, R:R 1:1.5, riesgo fijo por trade.
Corre en VPS, PC o Termux (Android) con pantalla apagada.
Control: Telegram (/status, /pause, /resume).
"""

from __future__ import annotations

import logging
import sys
import time
from datetime import datetime

import config
from exchange import Exchange, _is_transient
from risk import can_trade, position_size, register_close, reset_day_if_needed
from state import load_state, save_state
from strategy import analyze
from telegram_ctl import start_telegram
from telegram_util import polling_enabled, send_message, silence_telebot_logger

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("juguer")


def notify(msg: str):
    if not send_message(msg):
        log.warning("No se pudo enviar a Telegram (el bot sigue operando)")


def check_closed(exchange: Exchange, state):
    """Detecta cierre de posición y registra PnL."""
    if not state.active_symbol:
        return

    pos = exchange.position(state.active_symbol)
    if pos:
        return

    pnl, exit_price = exchange.last_realized_pnl(state.active_symbol)
    register_close(state, pnl)

    tag = "✅ TP" if pnl > 0 else "🛑 SL"
    wr = state.wins / max(1, state.wins + state.losses) * 100
    msg = (
        f"{tag} <b>{state.active_symbol}</b> {state.active_side}\n"
        f"PnL: <b>{pnl:+.2f}</b> USDT\n"
        f"Salida: {exit_price}\n"
        f"WR: {wr:.0f}% ({state.wins}W/{state.losses}L)"
    )
    log.info("%s %s PnL %+.2f", tag, state.active_symbol, pnl)
    notify(msg)

    state.active_symbol = ""
    state.active_side = ""
    state.entry_price = 0.0
    state.sl = 0.0
    state.tp = 0.0
    save_state(state)


def try_entry(exchange: Exchange, state, symbol: str):
    ok, reason = can_trade(state, exchange.balance_usdt())
    if not ok:
        log.info("[%s] skip: %s", symbol, reason)
        return

    if exchange.any_open_position():
        return

    df_5m = exchange.klines(symbol, "5m", 120)
    df_15m = exchange.klines(symbol, "15m", 120)
    signal = analyze(df_5m, df_15m)

    if not signal:
        log.info("[%s] sin setup", symbol)
        return

    balance = exchange.balance_usdt()
    qty = position_size(balance, signal.price, signal.sl, config.LEVERAGE)
    if qty <= 0:
        log.info("[%s] qty inválida", symbol)
        return

    exchange.set_leverage(symbol, config.LEVERAGE)
    exchange.open_position(symbol, signal.side, qty, signal.sl, signal.tp)

    state.active_symbol = symbol
    state.active_side = signal.side
    state.entry_price = signal.price
    state.sl = signal.sl
    state.tp = signal.tp
    save_state(state)

    reasons = "\n".join(f"• {r}" for r in signal.reasons)
    msg = (
        f"🍊 <b>ENTRADA {signal.side}</b> {symbol}\n"
        f"Score: {signal.score}/5\n"
        f"Entrada: {signal.price:.4f}\n"
        f"SL: {signal.sl:.4f} ({signal.sl_pct:.2f}%)\n"
        f"TP: {signal.tp:.4f} (RR 1:{config.RR_TARGET})\n"
        f"Riesgo: {config.RISK_PER_TRADE*100:.2f}%\n\n"
        f"{reasons}"
    )
    log.info("ENTRADA %s %s @ %.4f", signal.side, symbol, signal.price)
    notify(msg)


def main():
    if not hasattr(config, "BINANCE_API_KEY"):
        print("Creá config.py desde config.example.py")
        sys.exit(1)

    if getattr(config, "TESTNET", False):
        log.warning("TESTNET activo — no es Binance real")
    else:
        log.info("Modo REAL — Binance Futures")

    for name in ("BINANCE_API_KEY", "BINANCE_API_SECRET"):
        val = str(getattr(config, name, ""))
        if "tu_api" in val.lower():
            print("Completá config.py con tus API keys reales")
            sys.exit(1)

    exchange = Exchange()
    state = load_state()
    reset_day_if_needed(state)
    save_state(state)

    silence_telebot_logger()
    start_telegram(exchange, lambda: state)

    log.info("Juguer 1.1 iniciado | símbolos: %s | testnet: %s", config.SYMBOLS, config.TESTNET)
    if not polling_enabled():
        log.info("Telegram: solo notificaciones (sin comandos /status)")
    if send_message("🍊 <b>Juguer 1.1</b> en línea — Binance real"):
        log.info("Telegram conectado")
    else:
        log.warning("Telegram no disponible — trading activo igual")

    while True:
        try:
            state = load_state()
            check_closed(exchange, state)

            if not state.paused:
                for symbol in config.SYMBOLS:
                    try_entry(exchange, state, symbol)
                    state = load_state()

            time.sleep(config.SCAN_SECONDS)

        except KeyboardInterrupt:
            log.info("Detenido por usuario.")
            break
        except Exception as e:
            if _is_transient(e):
                log.warning("Red Binance inestable: %s — esperando 20s", e)
                time.sleep(20)
            else:
                log.exception("Error loop: %s", e)
                time.sleep(10)


if __name__ == "__main__":
    main()
