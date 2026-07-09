# Copiá este archivo como config.py y completá tus credenciales.

BINANCE_API_KEY = "tu_api_key"
BINANCE_API_SECRET = "tu_api_secret"

# Telegram (control desde el celular)
TELEGRAM_TOKEN = "tu_token_botfather"
TELEGRAM_CHAT_ID = "tu_chat_id"
TELEGRAM_OPTIONAL = True   # True = arranca aunque Telegram falle
TELEGRAM_DISABLED = False  # True = sin Telegram (solo consola)
TELEGRAM_PROXY = ""        # ej: "socks5://127.0.0.1:1080" si tu red bloquea Telegram

# Futures
LEVERAGE = 5
TESTNET = False  # True para paper en testnet.binancefuture.com

# Activos líquidos — empezá con uno solo
SYMBOLS = ["BTCUSDT"]

# Riesgo conservador: muchas operaciones chicas, pérdidas acotadas
RISK_PER_TRADE = 0.0075      # 0.75% del balance por SL
MAX_DAILY_LOSS = 0.02         # 2% → pausa hasta mañana
MAX_TRADES_PER_DAY = 6
MAX_CONSECUTIVE_LOSSES = 3
MAX_OPEN_POSITIONS = 1

# Objetivo 3:2 (TP = 1.5 × SL)
RR_TARGET = 1.5
SL_MIN_PCT = 0.0025           # 0.25%
SL_MAX_PCT = 0.006            # 0.60%

# Loop
SCAN_SECONDS = 45
