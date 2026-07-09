"""Telegram con reintentos — tolera cortes de red temporales."""

from __future__ import annotations

import logging
import time
from typing import Optional

import telebot
from telebot import apihelper

import config

log = logging.getLogger("juguer.telegram")

# Timeouts más largos (Termux / redes inestables)
apihelper.CONNECT_TIMEOUT = 30
apihelper.READ_TIMEOUT = 60

_bot: Optional[telebot.TeleBot] = None


def _proxy_url() -> Optional[str]:
    proxy = getattr(config, "TELEGRAM_PROXY", "") or ""
    return proxy.strip() or None


def get_bot() -> telebot.TeleBot:
    global _bot
    if _bot is None:
        _bot = telebot.TeleBot(config.TELEGRAM_TOKEN, threaded=False)
        proxy = _proxy_url()
        if proxy:
            apihelper.proxy = {"https": proxy, "http": proxy}
            log.info("Telegram proxy configurado")
    return _bot


def send_message(text: str, retries: int = 5) -> bool:
    if getattr(config, "TELEGRAM_DISABLED", False):
        return False

    chat_id = config.TELEGRAM_CHAT_ID
    bot = get_bot()

    for attempt in range(1, retries + 1):
        try:
            bot.send_message(chat_id, text, parse_mode="HTML")
            return True
        except Exception as e:
            wait = min(2 ** attempt, 30)
            log.warning("Telegram intento %s/%s: %s", attempt, retries, e)
            if attempt < retries:
                time.sleep(wait)

    return False


def test_connection(retries: int = 5) -> tuple[bool, str]:
    if getattr(config, "TELEGRAM_DISABLED", False):
        return True, "Telegram desactivado (TELEGRAM_DISABLED=True)"

    ok = send_message("🍊 Juguer 1.1 — test de conexión OK", retries=retries)
    if ok:
        return True, "Telegram OK"

    return False, (
        "No se pudo conectar a api.telegram.org tras varios intentos. "
        "Probá: otra red (datos móviles), VPN, o TELEGRAM_PROXY en config.py. "
        "Podés arrancar igual con TELEGRAM_OPTIONAL=True."
    )
