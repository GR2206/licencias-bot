"""Control por Telegram — operá el bot desde el celular con pantalla apagada."""

from __future__ import annotations

import logging
import threading
import time

import telebot

import config
from risk import today_key
from state import load_state, save_state
from telegram_util import get_bot, polling_enabled, send_message, silence_telebot_logger

log = logging.getLogger("juguer.telegram")


def start_telegram(exchange, get_loop_stats):
    if getattr(config, "TELEGRAM_DISABLED", False):
        log.info("Telegram desactivado")
        return

    silence_telebot_logger()

    if not polling_enabled():
        log.info(
            "Telegram en modo solo-notificaciones (TELEGRAM_POLLING=False). "
            "Comandos /status, /pause desactivados."
        )
        return

    bot = get_bot()

    def authorized(chat_id) -> bool:
        return str(chat_id) == str(config.TELEGRAM_CHAT_ID)

    @bot.message_handler(commands=["start", "help"])
    def help_cmd(message):
        if not authorized(message.chat.id):
            return
        bot.reply_to(
            message,
            "🍊 <b>Juguer 1.1</b>\n\n"
            "/status — estado y balance\n"
            "/stats — winrate y PnL\n"
            "/pause — pausar entradas\n"
            "/resume — reanudar\n"
            "/close — cerrar posición manual\n",
            parse_mode="HTML",
        )

    @bot.message_handler(commands=["status"])
    def status_cmd(message):
        if not authorized(message.chat.id):
            return
        st = load_state()
        bal = exchange.balance_usdt()
        pos = exchange.any_open_position()
        pos_txt = "Sin posición"
        if pos:
            pos_txt = f"{pos['symbol']} {pos['side']} | PnL {pos['pnl']:+.2f}"
        bot.reply_to(
            message,
            f"💰 Balance: <b>{bal:.2f}</b> USDT\n"
            f"📍 {pos_txt}\n"
            f"📅 Hoy: {st.daily_trades} trades | PnL {st.daily_pnl:+.2f}\n"
            f"⏸ Pausado: {'Sí' if st.paused else 'No'}\n"
            f"🔁 Racha SL: {st.consecutive_losses}",
            parse_mode="HTML",
        )

    @bot.message_handler(commands=["stats"])
    def stats_cmd(message):
        if not authorized(message.chat.id):
            return
        st = load_state()
        total = st.wins + st.losses
        wr = (st.wins / total * 100) if total else 0
        bot.reply_to(
            message,
            f"📊 <b>Estadísticas Juguer 1.1</b>\n\n"
            f"Trades: {total} ({st.wins}W / {st.losses}L)\n"
            f"Winrate: {wr:.1f}%\n"
            f"PnL total: {st.total_pnl:+.2f} USDT\n"
            f"Hoy ({today_key()}): {st.daily_pnl:+.2f}",
            parse_mode="HTML",
        )

    @bot.message_handler(commands=["pause"])
    def pause_cmd(message):
        if not authorized(message.chat.id):
            return
        st = load_state()
        st.paused = True
        save_state(st)
        bot.reply_to(message, "⏸ Entradas pausadas.")

    @bot.message_handler(commands=["resume"])
    def resume_cmd(message):
        if not authorized(message.chat.id):
            return
        st = load_state()
        st.paused = False
        st.consecutive_losses = 0
        save_state(st)
        bot.reply_to(message, "▶️ Bot reanudado.")

    @bot.message_handler(commands=["close"])
    def close_cmd(message):
        if not authorized(message.chat.id):
            return
        pos = exchange.any_open_position()
        if not pos:
            bot.reply_to(message, "Sin posición abierta.")
            return
        exchange.close_market(pos["symbol"])
        bot.reply_to(message, f"🛑 Cerrado {pos['symbol']} manualmente.")

    def poll():
        while True:
            try:
                bot.infinity_polling(
                    timeout=30,
                    long_polling_timeout=30,
                    skip_pending=True,
                    allowed_updates=["message"],
                    logger_level=logging.CRITICAL,
                )
            except Exception as e:
                log.warning("Telegram polling cayó: %s — reintento en 15s", e)
                time.sleep(15)

    threading.Thread(target=poll, daemon=True).start()
    log.info("Telegram listener iniciado")
