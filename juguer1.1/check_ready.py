#!/usr/bin/env python3
"""Verifica que Juguer 1.1 esté listo para Binance REAL antes de arrancar."""

import sys

PLACEHOLDERS = ("tu_api", "tu_token", "tu_chat", "changeme", "example")


def main():
    try:
        import config
    except ImportError:
        print("❌ Falta config.py")
        print("   cp config.example.py config.py")
        print("   Editá API keys y Telegram.")
        sys.exit(1)

    ok = True

    for name in ("BINANCE_API_KEY", "BINANCE_API_SECRET", "TELEGRAM_TOKEN", "TELEGRAM_CHAT_ID"):
        val = str(getattr(config, name, ""))
        if not val or any(p in val.lower() for p in PLACEHOLDERS):
            print(f"❌ {name} no configurado")
            ok = False

    if getattr(config, "TESTNET", True):
        print("⚠️  TESTNET = True — cambiá a False para Binance real")
        ok = False
    else:
        print("✅ TESTNET = False (Binance real)")

    if not ok:
        sys.exit(1)

    print("🔌 Conectando a Binance Futures...")
    try:
        from exchange import Exchange
        ex = Exchange()
        bal = ex.balance_usdt()
        print(f"✅ Conexión OK | Balance: {bal:.2f} USDT")

        if bal < 10:
            print("⚠️  Balance bajo — recomendado mínimo ~15-20 USDT para margen + fees")

        for sym in config.SYMBOLS:
            df = ex.klines(sym, "5m", 10)
            print(f"✅ {sym} — velas OK ({len(df)} barras)")

    except Exception as e:
        print(f"❌ Error Binance: {e}")
        print("   Revisá: API con permiso Futures, IP whitelist, keys correctas")
        sys.exit(1)

    print("🔔 Probando Telegram...")
    try:
        import telebot
        bot = telebot.TeleBot(config.TELEGRAM_TOKEN)
        bot.send_message(
            config.TELEGRAM_CHAT_ID,
            "🍊 Juguer 1.1 — check OK. Listo para <code>python bot.py</code>",
            parse_mode="HTML",
        )
        print("✅ Telegram OK")
    except Exception as e:
        print(f"❌ Telegram: {e}")
        sys.exit(1)

    print("\n🟢 TODO LISTO — ejecutá: python bot.py")


if __name__ == "__main__":
    main()
