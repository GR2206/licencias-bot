"""Cliente Binance Futures — órdenes, velas, posiciones."""

from __future__ import annotations

import logging
import time
from typing import Callable, Optional, TypeVar

import pandas as pd
from binance.client import Client
from binance.enums import *
from requests.exceptions import ConnectionError, ReadTimeout, Timeout

try:
    import config
except ImportError:
    raise SystemExit("Creá config.py desde config.example.py")

log = logging.getLogger("juguer.exchange")

T = TypeVar("T")

# Errores típicos en Termux: WiFi/datos cortan la conexión a fapi.binance.com
_TRANSIENT = (ConnectionError, Timeout, ReadTimeout, OSError)


def _is_transient(exc: BaseException) -> bool:
    if isinstance(exc, _TRANSIENT):
        return True
    msg = str(exc).lower()
    return any(
        s in msg
        for s in (
            "connection aborted",
            "remote end closed",
            "connection reset",
            "broken pipe",
            "timed out",
        )
    )


class Exchange:
    def __init__(self):
        req = {"timeout": getattr(config, "BINANCE_TIMEOUT", 30)}
        if config.TESTNET:
            self.client = Client(
                config.BINANCE_API_KEY,
                config.BINANCE_API_SECRET,
                testnet=True,
                requests_params=req,
            )
            self.client.FUTURES_URL = "https://testnet.binancefuture.com/fapi"
        else:
            self.client = Client(
                config.BINANCE_API_KEY,
                config.BINANCE_API_SECRET,
                requests_params=req,
            )

        self._retries = int(getattr(config, "BINANCE_RETRIES", 5))
        self._sync_time()
        self._precision: dict[str, dict] = {}

    def _call(self, label: str, fn: Callable[..., T], *args, **kwargs) -> T:
        last_err: Optional[BaseException] = None
        for attempt in range(1, self._retries + 1):
            try:
                return fn(*args, **kwargs)
            except Exception as e:
                if not _is_transient(e):
                    raise
                last_err = e
                wait = min(2 ** attempt, 30)
                log.warning(
                    "Binance %s — red inestable (%s/%s): %s — reintento en %ss",
                    label,
                    attempt,
                    self._retries,
                    e,
                    wait,
                )
                time.sleep(wait)
                self._sync_time()
        assert last_err is not None
        raise last_err

    def _sync_time(self):
        try:
            offset = self.client.get_server_time()["serverTime"] - int(time.time() * 1000)
            self.client._timestamp_offset = offset
        except Exception:
            pass

    def balance_usdt(self) -> float:
        acc = self._call("balance", self.client.futures_account)
        return float(acc["totalWalletBalance"])

    def klines(self, symbol: str, interval: str, limit: int = 200) -> pd.DataFrame:
        raw = self._call(
            f"klines {symbol}",
            self.client.futures_klines,
            symbol=symbol,
            interval=interval,
            limit=limit,
        )
        df = pd.DataFrame(
            raw,
            columns=[
                "time", "open", "high", "low", "close", "volume",
                "close_time", "qav", "trades", "tb", "tq", "ignore",
            ],
        )
        for col in ("open", "high", "low", "close", "volume"):
            df[col] = pd.to_numeric(df[col])
        df["time"] = pd.to_datetime(df["time"], unit="ms")
        return df

    def _symbol_info(self, symbol: str) -> dict:
        if symbol not in self._precision:
            info = self._call("exchange_info", self.client.futures_exchange_info)
            s = next(x for x in info["symbols"] if x["symbol"] == symbol)
            min_notional = 5.0
            for f in s["filters"]:
                if f["filterType"] in ("MIN_NOTIONAL", "NOTIONAL"):
                    min_notional = float(f.get("notional", f.get("minNotional", 5)))
            self._precision[symbol] = {
                "price": int(s["pricePrecision"]),
                "qty": int(s["quantityPrecision"]),
                "min_notional": min_notional,
            }
        return self._precision[symbol]

    def set_leverage(self, symbol: str, leverage: int):
        self._call(
            f"leverage {symbol}",
            self.client.futures_change_leverage,
            symbol=symbol,
            leverage=leverage,
        )

    def open_position(self, symbol: str, side: str, qty: float, sl: float, tp: float) -> dict:
        meta = self._symbol_info(symbol)
        qty = round(qty, meta["qty"])
        sl = round(sl, meta["price"])
        tp = round(tp, meta["price"])

        entry_side = SIDE_BUY if side == "LONG" else SIDE_SELL
        exit_side = SIDE_SELL if side == "LONG" else SIDE_BUY

        order = self._call(
            f"entrada {symbol}",
            self.client.futures_create_order,
            symbol=symbol,
            side=entry_side,
            type=ORDER_TYPE_MARKET,
            quantity=qty,
        )

        self._call(
            f"SL {symbol}",
            self.client.futures_create_order,
            symbol=symbol,
            side=exit_side,
            type="STOP_MARKET",
            stopPrice=sl,
            closePosition=True,
            workingType="CONTRACT_PRICE",
        )
        self._call(
            f"TP {symbol}",
            self.client.futures_create_order,
            symbol=symbol,
            side=exit_side,
            type="TAKE_PROFIT_MARKET",
            stopPrice=tp,
            closePosition=True,
            workingType="CONTRACT_PRICE",
        )

        return {
            "order_id": order.get("orderId"),
            "qty": qty,
            "sl": sl,
            "tp": tp,
        }

    def position(self, symbol: str) -> Optional[dict]:
        rows = self._call(
            f"posición {symbol}",
            self.client.futures_position_information,
            symbol=symbol,
        )
        for p in rows:
            amt = float(p["positionAmt"])
            if abs(amt) < 1e-12:
                continue
            return {
                "symbol": symbol,
                "side": "LONG" if amt > 0 else "SHORT",
                "qty": abs(amt),
                "entry": float(p["entryPrice"]),
                "pnl": float(p["unRealizedProfit"]),
                "margin": float(p["positionInitialMargin"]),
            }
        return None

    def any_open_position(self) -> Optional[dict]:
        for sym in config.SYMBOLS:
            pos = self.position(sym)
            if pos:
                return pos
        return None

    def cancel_all(self, symbol: str):
        self._call(f"cancel {symbol}", self.client.futures_cancel_all_open_orders, symbol=symbol)

    def close_market(self, symbol: str):
        pos = self.position(symbol)
        if not pos:
            return
        side = SIDE_SELL if pos["side"] == "LONG" else SIDE_BUY
        self.cancel_all(symbol)
        self._call(
            f"cierre {symbol}",
            self.client.futures_create_order,
            symbol=symbol,
            side=side,
            type=ORDER_TYPE_MARKET,
            quantity=pos["qty"],
            reduceOnly=True,
        )

    def last_realized_pnl(self, symbol: str) -> tuple[float, float]:
        """Último cierre: (pnl, precio salida)."""
        trades = self._call(
            f"trades {symbol}",
            self.client.futures_account_trades,
            symbol=symbol,
            limit=5,
        )
        if not trades:
            return 0.0, 0.0
        t = trades[-1]
        return float(t.get("realizedPnl", 0)), float(t.get("price", 0))
