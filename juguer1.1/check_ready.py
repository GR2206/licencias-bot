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
    telegram_optional = getattr(config, "TELEGRAM_OPTIONAL", True)
    telegram_disabled = getattr(config, "TELEGRAM_DISABLED", False)

    for name in ("BINANCE_API_KEY", "BINANCE_API_SECRET"):
        val = str(getattr(config, name, ""))
        if not val or any(p in val.lower() for p in PLACEHOLDERS):
            print(f"❌ {name} no configurado")
            ok = False

    if not telegram_disabled:
        for name in ("TELEGRAM_TOKEN", "TELEGRAM_CHAT_ID"):
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

    if telegram_disabled:
        print("ℹ️  Telegram desactivado (TELEGRAM_DISABLED=True)")
        print("\n🟢 BINANCE LISTO — ejecutá: python bot.py")
        return

    from telegram_util import polling_enabled

    if not polling_enabled():
        print("ℹ️  Telegram solo-notificaciones (TELEGRAM_POLLING=False)")
        print("   Sin comandos /status — el bot avisa entradas y cierres si la red lo permite")

    print("🔔 Probando Telegram (hasta 5 reintentos)...")
    from telegram_util import test_connection
    tg_ok, tg_msg = test_connection(retries=5)

    if tg_ok:
        print(f"✅ {tg_msg}")
        print("\n🟢 TODO LISTO — ejecutá: python bot.py")
        return

    print(f"⚠️  {tg_msg}")

    if telegram_optional:
        print("\n🟡 BINANCE OK — podés arrancar sin Telegram:")
        print("   python bot.py")
        print("\n   Para arreglar Telegram después:")
        print("   • Probá con datos móviles o VPN")
        print("   • TELEGRAM_PROXY = 'socks5://127.0.0.1:1080' en config.py")
        print("   • TELEGRAM_POLLING = False (evita errores de polling en Termux)")
        print("   • TELEGRAM_DISABLED = True (sin Telegram, solo consola)")
        return

    print("\n❌ Telegram obligatorio y no conecta. No arranques hasta resolverlo.")
    sys.exit(1)


if __name__ == "__main__":
    main()
