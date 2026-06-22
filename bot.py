# XAUUSD scalping bot entry point.
#
# Safe default: PAPER_MODE=True in config.py. Real MT5 execution requires both
# config.REAL_TRADING_ENABLED=True and ALLOW_REAL_TRADING=1 in the environment.
import logging

from config import LOG_FILE, LOG_LEVEL, PAPER_MODE


logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(),
    ],
)


if __name__ == "__main__":
    if PAPER_MODE:
        from paper_engine import run_paper

        run_paper()
    else:
        from mt5_engine import run_mt5

        run_mt5()
