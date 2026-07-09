"""Cliente Binance Futures — órdenes, velas, posiciones."""

from __future__ import annotations

import os
import time
from typing import Optional

import pandas as pd
from binance.client import Client
from binance.enums import *

try:
    import config
except ImportError:
    raise SystemExit("Creá config.py desde config.example.py")


class Exchange:
    def __init__(self):
        if config.TESTNET:
            self.client = Client(
                config.BINANCE_API_KEY,
                config.BINANCE_API_SECRET,
                testnet=True,
            )
            self.client.FUTURES_URL = "https://testnet.binancefuture.com/fapi"
        else:
            self.client = Client(config.BINANCE_API_KEY, config.BINANCE_API_SECRET)

        self._sync_time()
        self._precision: dict[str, dict] = {}

    def _sync_time(self):
        try:
            offset = self.client.get_server_time()["serverTime"] - int(time.time() * 1000)
            self.client._timestamp_offset = offset
        except Exception:
            pass

    def balance_usdt(self) -> float:
        acc = self.client.futures_account()
        return float(acc["totalWalletBalance"])

    def klines(self, symbol: str, interval: str, limit: int = 200) -> pd.DataFrame:
        raw = self.client.futures_klines(symbol=symbol, interval=interval, limit=limit)
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
            info = self.client.futures_exchange_info()
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
        self.client.futures_change_leverage(symbol=symbol, leverage=leverage)

    def open_position(self, symbol: str, side: str, qty: float, sl: float, tp: float) -> dict:
        meta = self._symbol_info(symbol)
        qty = round(qty, meta["qty"])
        sl = round(sl, meta["price"])
        tp = round(tp, meta["price"])

        entry_side = SIDE_BUY if side == "LONG" else SIDE_SELL
        exit_side = SIDE_SELL if side == "LONG" else SIDE_BUY

        order = self.client.futures_create_order(
            symbol=symbol,
            side=entry_side,
            type=ORDER_TYPE_MARKET,
            quantity=qty,
        )

        self.client.futures_create_order(
            symbol=symbol,
            side=exit_side,
            type="STOP_MARKET",
            stopPrice=sl,
            closePosition=True,
            workingType="CONTRACT_PRICE",
        )
        self.client.futures_create_order(
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
        for p in self.client.futures_position_information(symbol=symbol):
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
        self.client.futures_cancel_all_open_orders(symbol=symbol)

    def close_market(self, symbol: str):
        pos = self.position(symbol)
        if not pos:
            return
        side = SIDE_SELL if pos["side"] == "LONG" else SIDE_BUY
        self.cancel_all(symbol)
        self.client.futures_create_order(
            symbol=symbol,
            side=side,
            type=ORDER_TYPE_MARKET,
            quantity=pos["qty"],
            reduceOnly=True,
        )

    def last_realized_pnl(self, symbol: str) -> tuple[float, float]:
        """Último cierre: (pnl, precio salida)."""
        trades = self.client.futures_account_trades(symbol=symbol, limit=5)
        if not trades:
            return 0.0, 0.0
        t = trades[-1]
        return float(t.get("realizedPnl", 0)), float(t.get("price", 0))
