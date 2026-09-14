"""Cliente minimo para Binance Futures USDT-M (fapi).

Solo lo que el bot necesita, firmado a mano con hmac para no depender de
librerias pesadas. Funciona igual contra la red real y contra el testnet: lo
unico que cambia es la URL base.

  Real    : https://fapi.binance.com
  Testnet : https://testnet.binancefuture.com

En testnet las claves se sacan gratis en https://testnet.binancefuture.com y el
saldo es de juguete. Probar ahi primero no es opcional.
"""

import hashlib
import hmac
import math
import time
import urllib.parse

import requests

REAL = "https://fapi.binance.com"
TESTNET = "https://testnet.binancefuture.com"


class ErrorBinance(Exception):
    pass


def _redondear_abajo(valor: float, paso: float) -> float:
    """Ajusta un valor al multiplo del paso inmediatamente inferior.

    El epsilon evita que un 0.29999999 se convierta en 0.2 por el redondeo
    binario, que despues Binance rechaza con LOT_SIZE o PRICE_FILTER.
    """
    if paso <= 0:
        return valor
    return math.floor(valor / paso + 1e-9) * paso


def _decimales(paso: float) -> int:
    texto = f"{paso:.10f}".rstrip("0")
    if "." not in texto:
        return 0
    return len(texto.split(".")[1])


class Cliente:
    def __init__(self, api_key="", api_secret="", base=TESTNET, timeout=15):
        self.api_key = api_key
        self.api_secret = api_secret
        self.base = base.rstrip("/")
        self.timeout = timeout
        self.sesion = requests.Session()
        if api_key:
            self.sesion.headers.update({"X-MBX-APIKEY": api_key})
        self._desfase_ms = 0
        self._filtros = {}

    # ── Plomeria ────────────────────────────────────────────────────────────
    def _pedir(self, metodo, ruta, params=None, firmado=False):
        params = dict(params or {})
        if firmado:
            if not self.api_key or not self.api_secret:
                raise ErrorBinance("Faltan las claves de API para una llamada firmada")
            params["timestamp"] = int(time.time() * 1000) + self._desfase_ms
            params.setdefault("recvWindow", 5000)
            consulta = urllib.parse.urlencode(params, doseq=True)
            firma = hmac.new(
                self.api_secret.encode(), consulta.encode(), hashlib.sha256
            ).hexdigest()
            params["signature"] = firma

        url = f"{self.base}{ruta}"
        try:
            r = self.sesion.request(metodo, url, params=params, timeout=self.timeout)
        except requests.RequestException as e:
            raise ErrorBinance(f"Fallo de red en {ruta}: {e}") from e

        if r.status_code != 200:
            raise ErrorBinance(f"{ruta} -> HTTP {r.status_code}: {r.text[:300]}")
        datos = r.json()
        if isinstance(datos, dict) and datos.get("code", 0) not in (0, 200) and "msg" in datos:
            raise ErrorBinance(f"{ruta} -> {datos['code']}: {datos['msg']}")
        return datos

    def sincronizar_hora(self):
        """Alinea el reloj con el del servidor. En celulares el drift es comun
        y Binance rechaza cualquier firma con timestamp corrido."""
        antes = int(time.time() * 1000)
        datos = self._pedir("GET", "/fapi/v1/time")
        self._desfase_ms = int(datos["serverTime"]) - antes
        return self._desfase_ms

    # ── Datos publicos ──────────────────────────────────────────────────────
    def klines(self, simbolo, intervalo, limite=500):
        return self._pedir(
            "GET",
            "/fapi/v1/klines",
            {"symbol": simbolo, "interval": intervalo, "limit": limite},
        )

    def filtros(self, simbolo):
        """tickSize, stepSize, minQty y notional minimo del simbolo (con cache)."""
        if simbolo in self._filtros:
            return self._filtros[simbolo]
        info = self._pedir("GET", "/fapi/v1/exchangeInfo")
        for s in info.get("symbols", []):
            datos = {"tick": 0.01, "paso": 0.001, "min_qty": 0.0, "min_notional": 5.0}
            for f in s.get("filters", []):
                if f["filterType"] == "PRICE_FILTER":
                    datos["tick"] = float(f["tickSize"])
                elif f["filterType"] == "LOT_SIZE":
                    datos["paso"] = float(f["stepSize"])
                    datos["min_qty"] = float(f["minQty"])
                elif f["filterType"] == "MIN_NOTIONAL":
                    datos["min_notional"] = float(f.get("notional", 5.0))
            self._filtros[s["symbol"]] = datos
        if simbolo not in self._filtros:
            raise ErrorBinance(f"El simbolo {simbolo} no existe en futuros USDT-M")
        return self._filtros[simbolo]

    def ajustar_precio(self, simbolo, precio):
        f = self.filtros(simbolo)
        return round(_redondear_abajo(precio, f["tick"]), _decimales(f["tick"]))

    def ajustar_cantidad(self, simbolo, cantidad):
        f = self.filtros(simbolo)
        return round(_redondear_abajo(cantidad, f["paso"]), _decimales(f["paso"]))

    # ── Cuenta ──────────────────────────────────────────────────────────────
    def saldo_usdt(self):
        for activo in self._pedir("GET", "/fapi/v2/balance", firmado=True):
            if activo["asset"] == "USDT":
                return float(activo["availableBalance"])
        return 0.0

    def posicion(self, simbolo):
        """Cantidad neta en posicion. Positiva = long, negativa = short, 0 = plano."""
        datos = self._pedir(
            "GET", "/fapi/v2/positionRisk", {"symbol": simbolo}, firmado=True
        )
        total = 0.0
        for p in datos:
            total += float(p.get("positionAmt", 0) or 0)
        return total

    def posiciones(self):
        """Todas las posiciones abiertas en un solo pedido: {simbolo: cantidad}.

        Con muchos simbolos preguntar de a uno sale carisimo en peso de API.
        """
        datos = self._pedir("GET", "/fapi/v2/positionRisk", firmado=True)
        abiertas = {}
        for p in datos:
            cantidad = float(p.get("positionAmt", 0) or 0)
            if cantidad:
                abiertas[p["symbol"]] = abiertas.get(p["symbol"], 0.0) + cantidad
        return {s: c for s, c in abiertas.items() if c}

    def apalancamiento(self, simbolo, x):
        return self._pedir(
            "POST",
            "/fapi/v1/leverage",
            {"symbol": simbolo, "leverage": int(x)},
            firmado=True,
        )

    # ── Ordenes ─────────────────────────────────────────────────────────────
    def orden_mercado(self, simbolo, lado, cantidad):
        return self._pedir(
            "POST",
            "/fapi/v1/order",
            {
                "symbol": simbolo,
                "side": lado,  # BUY / SELL
                "type": "MARKET",
                "quantity": self.ajustar_cantidad(simbolo, cantidad),
            },
            firmado=True,
        )

    def orden_stop(self, simbolo, lado_cierre, precio):
        """Stop loss que cierra toda la posicion (closePosition)."""
        return self._pedir(
            "POST",
            "/fapi/v1/order",
            {
                "symbol": simbolo,
                "side": lado_cierre,
                "type": "STOP_MARKET",
                "stopPrice": self.ajustar_precio(simbolo, precio),
                "closePosition": "true",
                "workingType": "MARK_PRICE",
                "priceProtect": "true",
            },
            firmado=True,
        )

    def orden_objetivo(self, simbolo, lado_cierre, precio):
        """Take profit que cierra toda la posicion."""
        return self._pedir(
            "POST",
            "/fapi/v1/order",
            {
                "symbol": simbolo,
                "side": lado_cierre,
                "type": "TAKE_PROFIT_MARKET",
                "stopPrice": self.ajustar_precio(simbolo, precio),
                "closePosition": "true",
                "workingType": "MARK_PRICE",
                "priceProtect": "true",
            },
            firmado=True,
        )

    def orden_objetivo_parcial(self, simbolo, lado_cierre, precio, cantidad):
        """Take profit por una parte de la posicion (reduceOnly)."""
        return self._pedir(
            "POST",
            "/fapi/v1/order",
            {
                "symbol": simbolo,
                "side": lado_cierre,
                "type": "TAKE_PROFIT_MARKET",
                "stopPrice": self.ajustar_precio(simbolo, precio),
                "quantity": self.ajustar_cantidad(simbolo, cantidad),
                "reduceOnly": "true",
                "workingType": "MARK_PRICE",
                "priceProtect": "true",
            },
            firmado=True,
        )

    def cancelar_ordenes(self, simbolo):
        return self._pedir(
            "DELETE", "/fapi/v1/allOpenOrders", {"symbol": simbolo}, firmado=True
        )
