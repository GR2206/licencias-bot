# XAUUSD scalp bot configuration.
#
# The defaults favor survival and repeatability over trade count. For a bot
# that compounds gains, keeping drawdown shallow matters more than forcing
# entries in noisy candles.

# Runtime mode
PAPER_MODE = True
REAL_TRADING_ENABLED = False

# Account and symbol
PAPER_CAPITAL = 100.0
INSTRUMENT = "XAUUSD"
BYBIT_SYMBOL = "XAUUSDT"
MT5_SYMBOL = "XAUUSD"
GRANULARITY = "M1"
MT5_MAGIC_NUMBER = 8607001
MT5_DEVIATION_POINTS = 30

# Position constraints
LEVERAGE = 10
CAPITAL_USAGE = 0.55
RISK_PER_TRADE = 0.006
MAX_DAILY_LOSS = 0.03
MAX_CONSECUTIVE_LOSSES = 3
MAX_DAILY_TRADES = 40
COOLDOWN_AFTER_SL = 5

# Scalping targets. The engine can widen these slightly when ATR is higher.
TAKE_PROFIT_PCT = 0.0010
STOP_LOSS_PCT = 0.00065
MIN_TP_TO_SL_RATIO = 1.35
MAX_HOLD_MINUTES = 2

# Execution model for paper/live checks
COMMISSION = 0.00003
SPREAD_PCT = 0.00005
SLIPPAGE_PCT = 0.00002
MAX_ENTRY_SPREAD_PCT = 0.00018
INTRABAR_POLICY = "conservative"

# Indicators
EMA_FAST = 9
EMA_SLOW = 21
EMA_TREND = 50
ATR_PERIOD = 14
CANDLES_NEEDED = 120
TOUCH_THRESHOLD = 0.00055
MIN_VOLUME_RATIO = 0.85
MIN_ATR_PCT = 0.00022
MAX_ATR_PCT = 0.00200
MIN_TREND_SLOPE_PCT = 0.000035
MAX_CHASE_DISTANCE_PCT = 0.00120
MIN_CANDLE_BODY_PCT = 0.000035

# Sessions in UTC. London plus New York overlap are generally cleaner for XAU.
FILTER_SESSIONS = True
SESSION_RANGES = [
    (7, 12),
    (12, 17),
]

# Data validation
XAU_MIN = 2000.0
XAU_MAX = 7000.0
HTTP_TIMEOUT_SECONDS = 15

# Logging and state
LOG_FILE = "xauusd_bot.log"
LOG_LEVEL = "INFO"
AI_STATE_FILE = "ai_state.json"
TRADES_FILE = "xauusd_trades.json"
