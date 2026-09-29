"""Analiza una ventana, elige el trade y escribe el Pine que lo marca.

    python zonas.py CHZUSDT 15m --dias 3
    python zonas.py BTCUSDT 1h --dias 3 --mercado futuros
    python zonas.py ADAUSDT 4h --dias 10 --salida /tmp/mis_zonas.pine

Es el motor que usa mesa.py, la pantalla del celular: las dos llaman a
analizar(), que devuelve todo en un diccionario. Una sola funcion para los dos
frentes, porque si la pantalla calculara por su lado terminaria diciendo otro
numero y no habria forma de saber cual de las dos tiene razon.

Devuelve, para la ventana pedida: el contexto de las temporalidades mayores, las
zonas ordenadas, y UNA elegida con entrada como orden limite, stop y objetivo ya
redondeados al tick del par. El mercado que se elige decide dos cosas a la vez,
de donde salen las velas y cuanto cuesta operar, y mezclarlas es un error que no
da ninguna senal: leer spot y cobrar comision de futuros da la mitad del piso
del stop que corresponde.

Los precios de cripto salen de Binance. Los futuros de NinjaTrader (ES, NQ,
oro, petroleo y el resto de la lista) salen del contrato continuo de CME, que
es el grafico del frente. No es la conexion de la plataforma de NinjaTrader:
es la misma curva, con el tick del contrato y el VWAP de las 18:00 de Nueva York.

Y resuelve otro problema: yo puedo LEER precios y calcular donde estan las
zonas, pero no puedo DIBUJAR en tu grafico de TradingView. El MCP oficial de
TradingView no tiene ninguna herramienta de dibujo; solo datos, screener, listas
y alertas. Los servidores no oficiales si dibujan, pero manejando tu sesion del
navegador, que va contra los terminos y expone la cuenta entera.

Asi que el camino honesto es al reves: analizo la ventana aca, y escupo un
indicador de Pine con ESAS zonas puestas a mano. Lo pegas una vez y ves en el
grafico exactamente lo que analice, con las etiquetas de LONG y SHORT, el stop y
el objetivo de cada una. No es un indicador que recalcula: es mi analisis de esa
ventana, congelado, que es justo lo que se pide cuando se dice "marcame estos 3
dias".

Lo que marca:

  * ORDER BLOCKS: la vela de origen del movimiento que rompio estructura (BOS),
    con los mismos filtros de calidad que usa el bot (desplazamiento minimo
    medido en ATR y hueco de valor justo).
  * FVG / IMBALANCE: huecos de tres velas sin solapamiento.
  * NIVELES: maximos y minimos del dia, y el VWAP de la sesion UTC.
  * VWAP: si la zona queda entera del lado equivocado del valor del dia, no
    se opera. Si el VWAP cae en la mitad cercana de la caja, la entrada se
    mueve ahi y el stop de la zona no se toca.
  * GATILLO EN LA MEDIA: martillo seguido de envolvente sobre MA 25, 99 o 200,
    o tres soldados / tres cuervos en 5m y 15m sobre esas medias. La entrada
    es la media. La MA 7 se ve en el grafico y no activa sola.

Y las ordena por que tan imponentes son, con un puntaje explicado, para que no
haya que mirar veinte cajas iguales. El puntaje ordena la mirada y nada mas: los
pesos son un criterio, no una medicion, y no son la probabilidad de nada. El
unico numero con sustento es el acierto que hace falta para empatar, que sale de
la aritmetica del costo y no de una opinion.
"""
import argparse
import json
import math
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

import estrategia
import indicadores as ind

ESPEJO = "https://data-api.binance.vision"
MINUTOS = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "2h": 120,
           "4h": 240, "1d": 1440}

# El mercado donde vas a operar decide dos cosas a la vez: de que curva de
# precios salen las zonas, y cuanto cuesta cada operacion. Mezclarlos es un
# error silencioso: leer velas de spot y calcular con comision de futuros da un
# piso de stop la mitad de lo que corresponde.
#
#   futuros USDT-M : taker 0.05% por lado -> 0.10% ida y vuelta
#   spot           : taker 0.10% por lado -> 0.20% ida y vuelta
MERCADOS = {
    "futuros": {"base": "https://fapi.binance.com", "klines": "/fapi/v1/klines",
                "ticker": "/fapi/v1/ticker/price", "costo": 0.10,
                "nombre": "futuros USDT-M"},
    "spot": {"base": ESPEJO, "klines": "/api/v3/klines",
             "ticker": "/api/v3/ticker/price", "costo": 0.20,
             "nombre": "spot (espejo publico)"},
    # Comisión de un futuro CME en una cuenta chica, más un tick de ida y
    # vuelta, medida como porcentaje del precio. No es la de Binance: con
    # 0.10% el piso del stop rechazaría trades que en el futuro sí cierran.
    "ninjatrader": {"base": "", "klines": "", "ticker": "", "costo": 0.02,
                    "nombre": "NinjaTrader · CME continuo"},
}
ORDEN_RESPALDO = ["futuros", "spot"]

# Futuros que se grafican en NinjaTrader. El símbolo de la mesa es el del
# contrato (ES, MNQ) y yahoo es el continuo del frente. El tick es el salto
# mínimo del precio. dolar es lo que paga ese tick, por un contrato. El punto
# es 1.00 del precio: en el Dow el tick ya es un punto; en la plata un punto
# son 200 ticks.
CONTRATOS = {
    "ES": {"yahoo": "ES=F", "tick": 0.25, "dolar": 12.50, "nombre": "ES · S&P 500"},
    "NQ": {"yahoo": "NQ=F", "tick": 0.25, "dolar": 5.00, "nombre": "NQ · Nasdaq"},
    "YM": {"yahoo": "YM=F", "tick": 1, "dolar": 5.00, "nombre": "YM · Dow"},
    "RTY": {"yahoo": "RTY=F", "tick": 0.10, "dolar": 5.00, "nombre": "RTY · Russell"},
    "MES": {"yahoo": "MES=F", "tick": 0.25, "dolar": 1.25, "nombre": "MES · Micro S&P"},
    "MNQ": {"yahoo": "MNQ=F", "tick": 0.25, "dolar": 0.50, "nombre": "MNQ · Micro Nasdaq"},
    "MYM": {"yahoo": "MYM=F", "tick": 1, "dolar": 0.50, "nombre": "MYM · Micro Dow"},
    "M2K": {"yahoo": "M2K=F", "tick": 0.10, "dolar": 0.50, "nombre": "M2K · Micro Russell"},
    "CL": {"yahoo": "CL=F", "tick": 0.01, "dolar": 10.00, "nombre": "CL · Petróleo"},
    "MCL": {"yahoo": "MCL=F", "tick": 0.01, "dolar": 1.00, "nombre": "MCL · Micro petróleo"},
    "GC": {"yahoo": "GC=F", "tick": 0.10, "dolar": 10.00, "nombre": "GC · Oro"},
    "MGC": {"yahoo": "MGC=F", "tick": 0.10, "dolar": 1.00, "nombre": "MGC · Micro oro"},
    "SI": {"yahoo": "SI=F", "tick": 0.005, "dolar": 25.00, "nombre": "SI · Plata"},
    "NG": {"yahoo": "NG=F", "tick": 0.001, "dolar": 10.00, "nombre": "NG · Gas"},
    "6E": {"yahoo": "6E=F", "tick": 0.00005, "dolar": 6.25, "nombre": "6E · Euro"},
    "6B": {"yahoo": "6B=F", "tick": 0.0001, "dolar": 6.25, "nombre": "6B · Libra"},
    "6J": {"yahoo": "6J=F", "tick": 0.0000005, "dolar": 6.25, "nombre": "6J · Yen"},
}


# ─────────────────────────────────────────────────────────────────────────────
#  Datos
# ─────────────────────────────────────────────────────────────────────────────
def _pedido(url):
    return urllib.request.Request(url, headers={
        "User-Agent": ("Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"),
        "Accept": "application/json",
    })


def _pedir(url, timeout=30):
    with urllib.request.urlopen(_pedido(url), timeout=timeout) as r:
        return json.loads(r.read())


_ultimo_yahoo = 0.0


def _pedir_yahoo(url):
    """Yahoo corta con 429 si CALCULAR TODO encadena los contratos."""
    global _ultimo_yahoo
    espera = 0.4 - (time.time() - _ultimo_yahoo)
    if espera > 0:
        time.sleep(espera)
    ultimo = None
    for intento in range(4):
        try:
            datos = _pedir(url)
            _ultimo_yahoo = time.time()
            return datos
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as e:
            ultimo = e
            if "429" not in str(e) or intento == 3:
                raise
            time.sleep(1.5 * (intento + 1))
    raise ultimo


def _klines_de(mercado, simbolo, tf, velas_totales, incluir_abierta=False):
    m = MERCADOS[mercado]
    minutos = MINUTOS.get(tf, 60)
    fin = int(time.time() * 1000)
    cursor = fin - velas_totales * minutos * 60_000
    filas = []
    while cursor < fin and len(filas) < velas_totales + 1000:
        url = (f"{m['base']}{m['klines']}?symbol={simbolo}&interval={tf}"
               f"&startTime={cursor}&limit=1000")
        lote = _pedir(url)
        if not lote:
            break
        filas.extend(lote)
        siguiente = lote[-1][0] + minutos * 60_000
        if siguiente <= cursor:
            break
        cursor = siguiente
        time.sleep(0.05)
    velas = ind.desde_klines(filas)
    # La ultima fila de Binance es la vela que todavia no cerro. El analisis
    # la deja afuera para no medir un indicador con una vela a medias. La
    # bitacora la pide: ahi es donde el precio toca la entrada y el stop.
    if not incluir_abierta:
        velas = velas[:-1]
    return velas


def _rango_yahoo(dias, tope):
    dias = max(1, min(int(math.ceil(dias)), tope))
    if dias <= 5:
        return "5d"
    if dias <= 10:
        return "10d"
    if dias <= 15:
        return "15d"
    if dias <= 30:
        return "1mo"
    if dias <= 90:
        return "3mo"
    if dias <= 180:
        return "6mo"
    if dias <= 365:
        return "1y"
    return "2y"


# Yahoo no tiene 4h. Esas velas se arman con las de 1h. El tope es el máximo
# de días que esa resolución deja bajar.
_YAHOO_INTERVALO = {
    "5m": ("5m", 59),
    "15m": ("15m", 59),
    "30m": ("30m", 59),
    "1h": ("60m", 729),
    "4h": ("60m", 729),
}
_precio_cme = {}


def _agrupar(velas, minutos, incluir_abierta=False):
    """Junta velas en bloques de `minutos`. El bloque abierto queda afuera, salvo que se pida."""
    paso = minutos * 60_000
    grupos = {}
    orden = []
    for v in velas:
        t0 = v.tiempo - (v.tiempo % paso)
        if t0 not in grupos:
            orden.append(t0)
            grupos[t0] = ind.Vela(t0, v.apertura, v.maximo, v.minimo,
                                  v.cierre, v.volumen or 0.0)
        else:
            g = grupos[t0]
            g.maximo = max(g.maximo, v.maximo)
            g.minimo = min(g.minimo, v.minimo)
            g.cierre = v.cierre
            g.volumen += v.volumen or 0.0
    ahora = int(time.time() * 1000)
    if incluir_abierta:
        return [grupos[t0] for t0 in orden]
    return [grupos[t0] for t0 in orden if t0 + paso <= ahora]


def _velas_yahoo(simbolo, tf, velas_totales, incluir_abierta=False):
    """Velas del continuo CME. El símbolo es el de NinjaTrader (ES, no ES=F)."""
    contrato = CONTRATOS.get(simbolo)
    if not contrato:
        raise RuntimeError(f"{simbolo} no está entre los futuros de NinjaTrader")
    if tf not in _YAHOO_INTERVALO:
        raise RuntimeError(f"NinjaTrader no tiene la temporalidad {tf}")
    intervalo, tope = _YAHOO_INTERVALO[tf]
    minutos = 60 if tf == "4h" else MINUTOS[tf]
    pedidas = velas_totales * (4 if tf == "4h" else 1) + 8
    # La sesión electrónica dura unas 23 horas, no 24.
    dias = pedidas * minutos / 60.0 / 23.0 + 1
    rango = _rango_yahoo(dias, tope)
    yahoo = urllib.parse.quote(contrato["yahoo"], safe="")
    url = ("https://query1.finance.yahoo.com/v8/finance/chart/"
           f"{yahoo}?interval={intervalo}&range={rango}&includePrePost=true")
    datos = _pedir_yahoo(url)
    return _velas_de_respuesta(datos, simbolo, tf, velas_totales, incluir_abierta)


def _velas_de_respuesta(datos, simbolo, tf, velas_totales, incluir_abierta=False):
    result = ((datos or {}).get("chart") or {}).get("result") or []
    if not result:
        raise RuntimeError(f"no vinieron velas de {simbolo}")
    meta = result[0].get("meta") or {}
    px = meta.get("regularMarketPrice")
    if px:
        _precio_cme[simbolo] = (time.time(), float(px))
    ts = result[0].get("timestamp") or []
    quote = ((result[0].get("indicators") or {}).get("quote") or [{}])[0]
    opens = quote.get("open") or []
    highs = quote.get("high") or []
    lows = quote.get("low") or []
    closes = quote.get("close") or []
    vols = quote.get("volume") or []
    velas = []
    for i, t in enumerate(ts):
        if i >= len(opens) or None in (opens[i], highs[i], lows[i], closes[i]):
            continue
        vol = vols[i] if i < len(vols) and vols[i] else 0.0
        velas.append(ind.Vela(
            int(t) * 1000, float(opens[i]), float(highs[i]),
            float(lows[i]), float(closes[i]), float(vol)))
    if tf == "4h":
        velas = _agrupar(velas, 240, incluir_abierta)
    elif not incluir_abierta:
        paso = MINUTOS[tf] * 60_000
        ahora = int(time.time() * 1000)
        if velas and velas[-1].tiempo + paso > ahora:
            velas = velas[:-1]
    if len(velas) > velas_totales:
        velas = velas[-velas_totales:]
    return velas


def bajar(simbolo, tf, velas_totales, mercado="futuros", incluir_abierta=False):
    """Velas del mercado pedido. Devuelve (velas, mercado_usado).

    Intenta el mercado pedido y cae al otro si no responde. Binance bloquea la
    API de futuros por region (HTTP 451), asi que el respaldo importa: sin el,
    desde media Europa esto no corre. Pero el mercado que se termino usando se
    devuelve siempre, porque de ahi sale la comision, y decir "0.10%" cuando en
    realidad se leyo spot es mentirse en el unico numero que decide si la
    operacion cierra.
    """
    if mercado == "ninjatrader":
        try:
            velas = _velas_yahoo(simbolo, tf, velas_totales, incluir_abierta)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError,
                OSError, KeyError, ValueError, TypeError, RuntimeError) as e:
            raise RuntimeError(f"No pude bajar {simbolo} {tf} — {e}") from e
        if not velas:
            raise RuntimeError(f"No pude bajar {simbolo} {tf} — no vino ninguna vela")
        return velas, "ninjatrader"

    intentos = [mercado] + [m for m in ORDEN_RESPALDO if m != mercado]
    ultimo = None
    for candidato in intentos:
        try:
            velas = _klines_de(candidato, simbolo, tf, velas_totales,
                               incluir_abierta)
        except (urllib.error.URLError, urllib.error.HTTPError,
                TimeoutError, OSError) as e:
            ultimo = f"{candidato}: {e}"
            continue
        if velas:
            return velas, candidato
        ultimo = f"{candidato}: no vino ninguna vela"
    raise RuntimeError(f"No pude bajar {simbolo} {tf} — {ultimo}")


_ticks = {}


def tick_de(simbolo, mercado):
    """Tamano minimo de variacion del precio del par, o None si no se pudo saber.

    Sin esto los niveles que calculamos no se pueden cargar: un SL de
    0.015639 en CHZ, cuyo tick es 0.00001, lo rechaza el exchange. Es un
    numero lindo en pantalla y una orden que no entra.
    """
    clave = (simbolo, mercado)
    if clave in _ticks:
        return _ticks[clave]

    if mercado == "ninjatrader":
        contrato = CONTRATOS.get(simbolo)
        tick = contrato["tick"] if contrato else None
        _ticks[clave] = tick if tick and tick > 0 else None
        return _ticks[clave]

    m = MERCADOS[mercado]
    ruta = ("/fapi/v1/exchangeInfo" if mercado == "futuros"
            else f"/api/v3/exchangeInfo?symbol={simbolo}")
    tick = None
    try:
        datos = _pedir(f"{m['base']}{ruta}", 20)
        for s in datos.get("symbols", []):
            if s.get("symbol") != simbolo:
                continue
            for f in s.get("filters", []):
                if f.get("filterType") == "PRICE_FILTER":
                    tick = float(f["tickSize"])
            if tick is None and "pricePrecision" in s:
                tick = 10 ** -int(s["pricePrecision"])
            break
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError,
            OSError, KeyError, ValueError, TypeError):
        tick = None

    _ticks[clave] = tick if tick and tick > 0 else None
    return _ticks[clave]


def _al_tick(valor, tick, hacia=0):
    """Redondea al tick. hacia=1 fuerza hacia arriba, -1 hacia abajo, 0 al mas cercano."""
    if not tick or tick <= 0:
        return valor
    cocientes = valor / tick
    if hacia > 0:
        n = math.ceil(cocientes - 1e-9)
    elif hacia < 0:
        n = math.floor(cocientes + 1e-9)
    else:
        n = round(cocientes)
    return n * tick


def ajustar_al_tick(pl, direccion, tick, costo_pct):
    """Lleva entrada, stop y objetivo a precios que el exchange acepta.

    El stop se redondea SEPARANDOSE de la entrada y el objetivo ACERCANDOSE: asi
    el riesgo real nunca queda mas chico que el calculado ni el premio mas
    grande, y el RR que se informa es el peor de los dos, no el mejor. Redondear
    para el lado conveniente es como se fabrican backtests que no se pueden
    repetir en vivo.
    """
    if not tick or tick <= 0:
        return pl

    entrada = _al_tick(pl["entrada"], tick)
    if not entrada:
        return pl
    largo = direccion == 1
    stop = _al_tick(pl["stop"], tick, -1 if largo else 1)
    objetivo = _al_tick(pl["objetivo"], tick, -1 if largo else 1)

    riesgo = abs(entrada - stop)
    riesgo_pct = riesgo / entrada * 100 if entrada else 0.0
    pl.update({
        "entrada": entrada, "stop": stop, "objetivo": objetivo,
        "riesgo_pct": riesgo_pct,
        "costo_r": costo_pct / riesgo_pct if riesgo_pct else 0.0,
        "rr_real": abs(objetivo - entrada) / riesgo if riesgo else 0.0,
        "tick": tick,
    })
    return pl


def precio_vivo(simbolo, mercado):
    """Ultimo precio negociado, que no es el cierre de la ultima vela cerrada."""
    if mercado == "ninjatrader":
        guardado = _precio_cme.get(simbolo)
        if guardado and time.time() - guardado[0] < 45:
            return guardado[1]
        try:
            _velas_yahoo(simbolo, "1h", 5)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError,
                OSError, RuntimeError, KeyError, ValueError, TypeError):
            return None
        guardado = _precio_cme.get(simbolo)
        return guardado[1] if guardado else None

    m = MERCADOS[mercado]
    try:
        return float(_pedir(f"{m['base']}{m['ticker']}?symbol={simbolo}", 15)
                     ["price"])
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError,
            OSError, KeyError, ValueError):
        return None


# ─────────────────────────────────────────────────────────────────────────────
#  Deteccion
# ─────────────────────────────────────────────────────────────────────────────
def order_blocks(velas, cfg, desde_indice):
    """Order Blocks nacidos a partir de desde_indice, con su puntaje.

    Reusa la deteccion del bot (pivotes, BOS, origen, filtros de calidad) para
    que lo que se marca en el grafico sea lo mismo que el bot operaria, y no una
    segunda version que dice otra cosa.
    """
    atrs = ind.atr(velas, cfg.atr_periodo)
    piv_altos, piv_bajos = ind.pivotes(velas, cfg.pivote, cfg.pivote)
    _, direccion = ind.supertrend(velas, cfg.factor, cfg.atr_periodo)

    encontrados = []
    ult_ph = ult_pl = None
    ph_vivo = pl_vivo = False

    for i in range(len(velas)):
        v, a = velas[i], atrs[i]
        if a is None:
            continue
        if piv_altos[i] is not None:
            ult_ph, ph_vivo = piv_altos[i], True
        if piv_bajos[i] is not None:
            ult_pl, pl_vivo = piv_bajos[i], True

        bos_up = ph_vivo and ult_ph is not None and v.cierre > ult_ph
        bos_dn = pl_vivo and ult_pl is not None and v.cierre < ult_pl
        if bos_up:
            ph_vivo = False
        if bos_dn:
            pl_vivo = False

        for alcista in (True, False):
            if not (bos_up if alcista else bos_dn):
                continue
            k = estrategia._buscar_origen(velas, i, alcista, cfg.origen_max)
            if k is None or not estrategia._validar(velas, i, k, alcista, a, cfg):
                continue
            if i - k < desde_indice:
                continue

            origen = velas[i - k]
            if cfg.zona_cuerpo:
                top = max(origen.apertura, origen.cierre)
                bot = min(origen.apertura, origen.cierre)
            else:
                top, bot = origen.maximo, origen.minimo

            # Desplazamiento: cuanto empujo el precio desde el origen hasta la
            # ruptura, medido en ATR. Es lo que hace a un bloque "imponente".
            if alcista:
                empuje = (v.cierre - bot) / a
            else:
                empuje = (top - v.cierre) / a

            hueco = _hueco_impulso(velas, i, k, alcista)
            hueco_vivo = False
            if hueco:
                piso_h, techo_h = hueco
                hueco_vivo = True
                for w in velas[i + 1:]:
                    if alcista and w.cierre < piso_h:
                        hueco_vivo = False
                        break
                    if not alcista and w.cierre > techo_h:
                        hueco_vivo = False
                        break
            else:
                piso_h = techo_h = None

            encontrados.append({
                "tipo": "OB",
                "direccion": 1 if alcista else -1,
                "top": top, "bot": bot,
                "indice_origen": i - k,
                "indice_ruptura": i,
                "tiempo": origen.tiempo,
                "empuje_atr": empuje,
                "volumen_rel": _volumen_relativo(velas, i - k),
                "atr": a,
                "hueco_bot": piso_h, "hueco_top": techo_h,
                "hueco_vivo": hueco_vivo,
            })
    return encontrados


def decimales(precio, tick=None):
    """Cuantos decimales hacen falta para ver 5 cifras significativas.

    Con un precio fijo se rompe en los dos extremos: CHZ a 0.0162 con 4
    decimales muestra la zona como "0.0158 — 0.0158", que no dice nada.
    Si el tick pide más decimales que el precio (el euro, 0.00005), manda el tick.
    """
    if precio <= 0:
        return 2
    n = max(2, 4 - int(math.floor(math.log10(precio))))
    if tick and tick > 0:
        n = max(n, max(0, -int(math.floor(math.log10(tick) + 1e-12))))
    return n


def _volumen_relativo(velas, i, ventana=20):
    """Volumen de la vela contra la media de las 20 previas."""
    desde = max(0, i - ventana)
    previas = [v.volumen for v in velas[desde:i] if v.volumen]
    if not previas:
        return 1.0
    media = sum(previas) / len(previas)
    return velas[i].volumen / media if media else 1.0


def _hueco_impulso(velas, i, k, alcista):
    """El hueco que dejo el mismo movimiento que creo el order block.

    No es cualquier FVG de la ventana: es el desbalance de esa ruptura. Si
    despues el precio lo cierra, el movimiento ya entrego lo que tenia y el
    bloque deja de ser una entrada.
    """
    for j in range(k, 1, -1):
        if i - j < 0 or i - (j - 2) >= len(velas):
            continue
        origen, destino = velas[i - j], velas[i - (j - 2)]
        if alcista and destino.minimo > origen.maximo:
            return origen.maximo, destino.minimo
        if not alcista and destino.maximo < origen.minimo:
            return destino.maximo, origen.minimo
    return None


def fvgs(velas, desde_indice, atr_min=0.25):
    """Huecos de tres velas sin solapamiento, desde desde_indice."""
    atrs = ind.atr(velas, 14)
    salida = []
    for i in range(max(2, desde_indice), len(velas)):
        v, v2, a = velas[i], velas[i - 2], atrs[i]
        if not a:
            continue
        if v2.maximo < v.minimo:
            hueco = v.minimo - v2.maximo
            if hueco >= a * atr_min:
                salida.append({"tipo": "FVG", "direccion": 1,
                               "top": v.minimo, "bot": v2.maximo,
                               "indice_origen": i, "tiempo": v.tiempo,
                               "tamano_atr": hueco / a})
        elif v2.minimo > v.maximo:
            hueco = v2.minimo - v.maximo
            if hueco >= a * atr_min:
                salida.append({"tipo": "FVG", "direccion": -1,
                               "top": v2.minimo, "bot": v.maximo,
                               "indice_origen": i, "tiempo": v.tiempo,
                               "tamano_atr": hueco / a})
    return salida


def sigue_vivo(zona, velas):
    """Si el precio ya atraveso la zona, dejo de ser un punto de interes."""
    desde = zona.get("indice_ruptura", zona["indice_origen"]) + 1
    for v in velas[desde:]:
        if zona["direccion"] == 1 and v.cierre < zona["bot"]:
            return False
        if zona["direccion"] == -1 and v.cierre > zona["top"]:
            return False
    return True


def lectura_osciladores(velas):
    """RSI(6) y MACD de la temporalidad del trade, en la ultima vela cerrada."""
    cierres = [v.cierre for v in velas]
    rsi_s = ind.rsi(cierres, RSI_TRADE)
    dif, dea, hist = ind.macd(cierres)
    return {
        "rsi": rsi_s[-1] if rsi_s else None,
        "dif": dif[-1] if dif else None,
        "dea": dea[-1] if dea else None,
        "hist": hist[-1] if hist else None,
    }


def valorar_osciladores(direccion, osc):
    """Cuanto suman RSI y MACD a este lado. Puntos, a favor y en contra.

    Un largo suma si el RSI(6) esta en 50 o mas y si el MACD es positivo.
    Un corto suma con el RSI en 50 o menos y el MACD negativo. El cero no vota.
    """
    if not osc:
        return 0.0, [], []
    puntos = 0.0
    favor, contra = [], []
    rsi = osc.get("rsi")
    hist = osc.get("hist")
    if rsi is not None:
        texto = f"RSI(6) {rsi:.1f}"
        a_favor = rsi >= 50 if direccion == 1 else rsi <= 50
        if rsi == 50:
            pass
        elif a_favor:
            puntos += 2
            favor.append(texto + " a favor")
        else:
            puntos -= 2
            contra.append(texto + " en contra")
    if hist is not None and hist != 0:
        dif, dea = osc.get("dif"), osc.get("dea")
        texto = f"MACD {hist:+.4g}"
        if dif is not None and dea is not None:
            texto += f" (DIF {dif:.4g}, DEA {dea:.4g})"
        a_favor = hist > 0 if direccion == 1 else hist < 0
        if a_favor:
            puntos += 2
            favor.append(texto + " a favor")
        else:
            puntos -= 2
            contra.append(texto + " en contra")
    return puntos, favor, contra


def puntaje(zona, velas, precio_actual, osc=None):
    """Que tan imponente es la zona. Devuelve (puntos, motivos).

    Los pesos no salen de una medicion: son un criterio para ordenar la lista y
    no mirar veinte cajas iguales. El puntaje sirve para priorizar la mirada, no
    como probabilidad de nada.
    """
    puntos = 0.0
    motivos = []

    empuje = zona.get("empuje_atr") or zona.get("tamano_atr") or 0
    if empuje >= 4:
        puntos += 3; motivos.append(f"desplazamiento fuerte ({empuje:.1f} ATR)")
    elif empuje >= 2.5:
        puntos += 2; motivos.append(f"buen desplazamiento ({empuje:.1f} ATR)")
    elif empuje >= 1.5:
        puntos += 1; motivos.append(f"desplazamiento normal ({empuje:.1f} ATR)")

    vol = zona.get("volumen_rel", 1.0)
    if vol >= 2.0:
        puntos += 2; motivos.append(f"volumen {vol:.1f}x la media")
    elif vol >= 1.3:
        puntos += 1; motivos.append(f"volumen {vol:.1f}x la media")

    if zona["tipo"] == "OB":
        puntos += 1; motivos.append("rompio estructura (BOS)")
        if zona.get("hueco_vivo"):
            puntos += 1
            motivos.append("el hueco del movimiento sigue abierto")
        elif zona.get("hueco_top") is not None:
            motivos.append("el hueco ya se lleno; el bloque sigue entero")

    if sigue_vivo(zona, velas):
        puntos += 2; motivos.append("sin mitigar")
    else:
        motivos.append("ya fue atravesada")

    # Cerca del precio = operable pronto. Lejos = referencia.
    medio = (zona["top"] + zona["bot"]) / 2
    distancia = abs(precio_actual - medio) / precio_actual * 100
    if distancia <= 1.0:
        puntos += 2; motivos.append(f"a {distancia:.2f}% del precio")
    elif distancia <= 3.0:
        puntos += 1; motivos.append(f"a {distancia:.2f}% del precio")
    else:
        motivos.append(f"lejos, a {distancia:.1f}% del precio")

    # Del lado correcto para que sea una entrada y no una zona ya pasada.
    if zona["direccion"] == 1 and precio_actual < zona["bot"]:
        motivos.append("OJO: el precio esta por DEBAJO de una zona de compra")
        puntos -= 2
    if zona["direccion"] == -1 and precio_actual > zona["top"]:
        motivos.append("OJO: el precio esta por ENCIMA de una zona de venta")
        puntos -= 2

    extra, favor, contra = valorar_osciladores(zona["direccion"], osc)
    puntos += extra
    motivos.extend(favor)
    motivos.extend(contra)

    return puntos, motivos


def plan(zona, atr, rr=3.0, colchon=0.25, costo_pct=0.10, costo_max_r=0.15):
    """Entrada, stop y objetivo si se operara el retroceso a la zona.

    El stop no puede ser tan corto como uno quiera. Con comision de ida y
    vuelta de costo_pct, un stop de riesgo_pct paga costo_pct/riesgo_pct en
    unidades de R: con 0.10% de costo y un stop de 0.20%, la mitad de cada
    operacion se va en comision antes de que el precio haga nada. Asi que hay
    un piso: costo_pct / costo_max_r. Si la zona pide un stop mas corto, se
    ensancha hasta el piso y se avisa, porque el stop de la zona es lindo en el
    grafico y ruinoso en la cuenta.
    """
    if zona["direccion"] == 1:
        entrada = zona["top"]
        stop = zona["bot"] - atr * colchon
    else:
        entrada = zona["bot"]
        stop = zona["top"] + atr * colchon

    riesgo_pct = abs(entrada - stop) / entrada * 100 if entrada else 0
    piso_pct = costo_pct / costo_max_r if costo_max_r > 0 else 0.0
    ensanchado = 0 < riesgo_pct < piso_pct
    if ensanchado:
        riesgo_pct = piso_pct
        delta = entrada * piso_pct / 100
        stop = entrada - delta if zona["direccion"] == 1 else entrada + delta

    riesgo = abs(entrada - stop)
    objetivo = (entrada + riesgo * rr if zona["direccion"] == 1
                else entrada - riesgo * rr)
    return {
        "entrada": entrada, "stop": stop, "objetivo": objetivo,
        "riesgo_pct": riesgo_pct,
        "costo_r": costo_pct / riesgo_pct if riesgo_pct else 0.0,
        "ensanchado": ensanchado,
        "stop_zona_pct": abs(entrada - (zona["bot"] - atr * colchon
                                        if zona["direccion"] == 1
                                        else zona["top"] + atr * colchon))
                         / entrada * 100 if entrada else 0,
    }


# ─────────────────────────────────────────────────────────────────────────────
#  Contexto de la temporalidad mayor
# ─────────────────────────────────────────────────────────────────────────────
def sesgo_de(velas):
    """Alcista, bajista o mixto, por mayoria de tres lecturas.

    Ojo con el SuperTrend: ta.supertrend() de Pine devuelve -1 en tendencia
    ALCISTA y 1 en bajista, y nuestro port respeta esa convencion. Leerlo al
    revés invierte el sesgo de cada activo sin que nada falle.
    """
    cierres = [v.cierre for v in velas]
    precio = cierres[-1]
    e50 = ind.ema(cierres, 50)[-1]
    e200 = ind.ema(cierres, 200)[-1]
    _, direccion = ind.supertrend(velas, 3.0, 10)
    st_alcista = direccion[-1] == -1 if direccion[-1] is not None else None

    votos, lecturas = 0, []
    for nombre, arriba in (("EMA200", None if e200 is None else precio > e200),
                           ("EMA50", None if e50 is None else precio > e50)):
        if arriba is None:
            continue
        votos += 1 if arriba else -1
        lecturas.append(f"precio {'arriba' if arriba else 'abajo'} de {nombre}")
    if st_alcista is not None:
        votos += 1 if st_alcista else -1
        lecturas.append(f"SuperTrend {'alcista' if st_alcista else 'bajista'}")

    if votos > 0:
        etiqueta, signo = "alcista", 1
    elif votos < 0:
        etiqueta, signo = "bajista", -1
    else:
        etiqueta, signo = "mixto", 0
    return {"sesgo": etiqueta, "signo": signo, "lecturas": lecturas,
            "ema50": e50, "ema200": e200,
            "rsi": ind.rsi(cierres, RSI_TRADE)[-1], "precio": precio}


def contexto(simbolo, mayores=("4h", "1h"), mercado="futuros"):
    """Lectura de las temporalidades mayores, para saber si la entrada va a favor."""
    salida = []
    for tf in mayores:
        try:
            velas, _ = bajar(simbolo, tf, 320, mercado)
        except RuntimeError as e:
            salida.append({"tf": tf, "error": str(e)})
            continue
        if len(velas) < 60:
            salida.append({"tf": tf, "error": "muy pocas velas"})
            continue

        s = sesgo_de(velas)
        n24 = max(2, int(24 * 60 / MINUTOS.get(tf, 60)))
        ventana = velas[-n24:]
        alto = max(v.maximo for v in ventana)
        bajo = min(v.minimo for v in ventana)
        s.update({
            "tf": tf,
            "alto_24h": alto, "bajo_24h": bajo,
            "pos_rango": (s["precio"] - bajo) / (alto - bajo) * 100
                         if alto > bajo else 50.0,
        })
        salida.append(s)
    return salida


# ─────────────────────────────────────────────────────────────────────────────
#  La recomendacion
# ─────────────────────────────────────────────────────────────────────────────
def acierto_para_empatar(rr, costo_r):
    """Que porcentaje hay que acertar para no perder plata, y que da el azar.

    En un mercado sin memoria entrar al azar acierta 1/(1+RR) y la esperanza
    bruta es exactamente cero. La comision corre esa vara hacia arriba:
    p*RR - (1-p) - costo_r = 0  =>  p = (1 + costo_r) / (1 + RR)
    """
    azar = 1.0 / (1.0 + rr) * 100
    return (1.0 + costo_r) / (1.0 + rr) * 100, azar


# Premio que tiene que quedar POR DELANTE del precio de ahora, medido en R de
# esa misma operacion. Con menos de 1R el objetivo ya esta en el precio actual
# o atras, y la orden limita pide un retroceso para volver a un nivel que el
# mercado ya negocio.
PREMIO_MIN_R = 1.0
# Si la entrada queda mas lejos que esto, el impulso ya se fue. Volver hasta
# ahi no es un retroceso de esta temporalidad: es devolver gran parte del
# movimiento. RUNE quedo a mas de 2 ATR y la entrada era la reversa, no el
# retroceso. 2 ATR todavia es un retroceso de esta vela; 1.5 dejaba afuera
# zonas que estaban a un suspiro.
DISTANCIA_MAX_ATR = 2.0
# El movimiento se paso cuando el precio TOCO el objetivo, no cuando llevaba
# el 80% del camino. Con 80% una ruptura sana de 1.5 ATR ya quedaba "agotada"
# antes de que existiera el retroceso, y la lista salia vacia.
RECORRIDO_AGOTADO = 1.0
# Las MA del grafico de Binance. La 7 es la rapida: el precio la roza todo el
# tiempo, asi que no activa un trade. 25, 99 y 200 si, cuando ademas hay vela
# gatillo.
MEDIAS = (7, 25, 99, 200)
MEDIAS_GATILLO = (25, 99, 200)
VERSION = 26
# El RSI del grafico de Binance en el celular es 6, no el 14 de los libros.
# El MACD es 12, 26, 9: DIF, DEA, y MACD = DIF - DEA.
RSI_TRADE = 6


def premio_por_delante(zona, precio):
    """Cuantos R quedan entre el precio de ahora y el objetivo.

    Positivo: el objetivo todavia esta adelante. Cero o negativo: el mercado ya
    llego ahi, y entrar en el retroceso es cobrar un premio entregado.
    """
    p = zona["plan"]
    riesgo = abs(p["entrada"] - p["stop"])
    if riesgo <= 0 or not precio:
        return 0.0
    if zona["direccion"] == 1:
        return (p["objetivo"] - precio) / riesgo
    return (precio - p["objetivo"]) / riesgo


def afinar_con_vwap(zona, pl, vwap, atr, precio, rr=3.0,
                    costo_pct=0.10, costo_max_r=0.15, colchon=0.25):
    """Usa el VWAP del dia para certificar la zona y, si cabe, afinar la entrada.

    Un largo con toda la caja debajo del VWAP, o un corto con toda la caja
    arriba, esta del lado equivocado del valor: se marca y operable() lo
    descarta. Tocar el VWAP no es un cuarto punto obligatorio. Mover la entrada
    solo ocurre si el VWAP cae dentro de la zona y en la mitad cercana al borde
    de entrada (arriba del medio en un largo, abajo del medio en un corto). El
    stop sigue siendo el de la zona. Si ese ajuste deja la entrada a mas de
    DISTANCIA_MAX_ATR, no se mueve: se anota que el VWAP toca la zona y el
    limite queda en el borde.
    """
    pl = dict(pl)
    pl["vwap"] = vwap
    pl["vwap_toca"] = False
    pl["vwap_afino"] = False
    pl["contra_vwap"] = False
    if vwap is None or vwap <= 0:
        return pl

    bot, top = zona["bot"], zona["top"]
    medio = (bot + top) / 2.0
    largo = zona["direccion"] == 1
    if (largo and top < vwap) or (not largo and bot > vwap):
        pl["contra_vwap"] = True
        return pl
    if not (bot <= vwap <= top):
        return pl

    pl["vwap_toca"] = True
    cerca = vwap >= medio if largo else vwap <= medio
    if not cerca:
        return pl
    if atr > 0 and DISTANCIA_MAX_ATR > 0 and precio:
        if abs(vwap - precio) / atr > DISTANCIA_MAX_ATR:
            return pl

    if largo:
        stop = zona["bot"] - atr * colchon
    else:
        stop = zona["top"] + atr * colchon
    entrada = vwap
    riesgo_pct = abs(entrada - stop) / entrada * 100 if entrada else 0.0
    piso_pct = costo_pct / costo_max_r if costo_max_r > 0 else 0.0
    ensanchado = 0 < riesgo_pct < piso_pct
    stop_zona_pct = riesgo_pct
    if ensanchado:
        riesgo_pct = piso_pct
        delta = entrada * piso_pct / 100
        stop = entrada - delta if largo else entrada + delta
    riesgo = abs(entrada - stop)
    objetivo = entrada + riesgo * rr if largo else entrada - riesgo * rr
    pl.update({
        "entrada": entrada, "stop": stop, "objetivo": objetivo,
        "riesgo_pct": riesgo_pct,
        "costo_r": costo_pct / riesgo_pct if riesgo_pct else 0.0,
        "ensanchado": ensanchado,
        "stop_zona_pct": stop_zona_pct,
        "vwap_afino": True,
    })
    return pl


def _velas_periodo_cerrado(velas, clave_de):
    """Velas del período anterior al que está en curso. El actual se deja afuera."""
    if not velas:
        return []
    actual = clave_de(velas[-1].tiempo)
    i = len(velas) - 1
    while i >= 0 and clave_de(velas[i].tiempo) == actual:
        i -= 1
    if i < 0:
        return []
    previa = clave_de(velas[i].tiempo)
    grupo = []
    while i >= 0 and clave_de(velas[i].tiempo) == previa:
        grupo.append(velas[i])
        i -= 1
    grupo.reverse()
    return grupo


def _niveles_ancla(rito, valor):
    """Precios de RiTo y del área de valor donde se puede apoyar la entrada."""
    niveles = []
    for s in rito or []:
        niveles.append((f"maximo de {s['nombre']}", s["maximo"]))
        niveles.append((f"minimo de {s['nombre']}", s["minimo"]))
    if valor:
        niveles.append(("VAL", valor.get("val")))
        niveles.append(("POC", valor.get("poc")))
        niveles.append(("VAH", valor.get("vah")))
    return niveles


def afinar_con_anclas(zona, pl, niveles, atr, precio, rr=3.0,
                      costo_pct=0.10, costo_max_r=0.15, colchon=0.25):
    """Apoya el límite en RiTo o en el área de valor, si caen en la mitad cercana.

    No prenden ni apagan el trade. Entre los precios que están en la mitad de
    la zona que toca el mercado, se elige el más pegado al borde: es el primero
    que el retroceso encuentra. La mitad lejana no se usa, para no hundir el
    límite. Si ese precio deja el stop más corto que la comisión, la entrada
    se queda donde estaba.
    """
    pl = dict(pl)
    pl["ancla_afino"] = False
    pl["ancla_nombre"] = None
    pl["anclas_en_zona"] = []
    if not niveles:
        return pl

    bot, top = zona["bot"], zona["top"]
    if top <= bot:
        return pl
    medio = (bot + top) / 2.0
    largo = zona["direccion"] == 1
    en_zona = []
    cerca = []
    for nombre, nivel in niveles:
        if nivel is None:
            continue
        if not (bot <= nivel <= top):
            continue
        en_zona.append(nombre)
        if largo and medio <= nivel <= top and (not precio or nivel < precio):
            cerca.append((float(nivel), nombre))
        elif ((not largo) and bot <= nivel <= medio
              and (not precio or nivel > precio)):
            cerca.append((float(nivel), nombre))
    pl["anclas_en_zona"] = en_zona
    if not cerca:
        return pl

    opciones = list(cerca)
    if pl.get("vwap_afino"):
        opciones.append((float(pl["entrada"]), "VWAP"))
    nivel, nombre = max(opciones) if largo else min(opciones)
    if nombre == "VWAP" or abs(nivel - pl["entrada"]) <= 1e-9:
        if nombre != "VWAP":
            pl["ancla_nombre"] = nombre
        return pl
    if atr and atr > 0 and DISTANCIA_MAX_ATR > 0 and precio:
        if abs(nivel - precio) / atr > DISTANCIA_MAX_ATR:
            return pl

    if largo:
        stop = zona["bot"] - (atr or 0.0) * colchon
    else:
        stop = zona["top"] + (atr or 0.0) * colchon
    entrada = nivel
    riesgo_pct = abs(entrada - stop) / entrada * 100 if entrada else 0.0
    piso_pct = costo_pct / costo_max_r if costo_max_r > 0 else 0.0
    if 0 < riesgo_pct < piso_pct:
        return pl
    riesgo = abs(entrada - stop)
    objetivo = entrada + riesgo * rr if largo else entrada - riesgo * rr
    pl.update({
        "entrada": entrada, "stop": stop, "objetivo": objetivo,
        "riesgo_pct": riesgo_pct,
        "costo_r": costo_pct / riesgo_pct if riesgo_pct else 0.0,
        "ensanchado": False,
        "stop_zona_pct": riesgo_pct,
        "ancla_afino": True,
        "ancla_nombre": nombre,
    })
    return pl


def operable(zona, precio, atr=0.0, stop_max_atr=3.0):
    """Si la zona todavia se puede tomar con una orden limite, y por que no.

    Cinco filtros, y los cinco son por algo que se puede explicar:

    1. La zona no puede estar ya atravesada.
    2. Una zona de venta se toma con un limite ARRIBA del precio y una de compra
       con un limite ABAJO. Si el precio ya paso de largo, la orden nunca se
       llena: no es una entrada pendiente, es una que se fue. Sin esto el panel
       ofrece precios que no se pueden poner.
    3. El stop no puede pasar de stop_max_atr veces el ATR. Una zona muy ancha
       da un stop enorme, y con RR 1:3 el objetivo se va tan lejos que deja de
       ser una operacion de esta temporalidad: es un swing de varios dias
       disfrazado de entrada de 15 minutos.
    4. El stop de la zona tiene que cubrir la comision sin moverlo. Si hay que
       ensancharlo hasta el piso de costo, la salida queda en un precio que la
       zona no invalida: el nivel de la idea y el que saca de la operacion
       dejan de ser el mismo. Eso se descarta, no se opera con el stop corrido.
    5. Entre el precio de ahora y el objetivo tiene que quedar al menos 1R.
       Si no, el TP ya esta donde el mercado cotiza y la orden pide un
       retroceso para volver ahi.
    6. La entrada no puede quedar a mas de DISTANCIA_MAX_ATR. Mas lejos, el
       impulso ya corrio y el limite espera un retroceso que devuelve gran
       parte del movimiento.
    """
    entrada = zona["plan"]["entrada"]
    if not zona["vivo"]:
        return False, "la zona ya fue atravesada"

    if atr > 0 and stop_max_atr > 0:
        veces = abs(entrada - zona["plan"]["stop"]) / atr
        if veces > stop_max_atr:
            return False, (f"stop de {veces:.1f} ATR: la zona es demasiado ancha "
                           f"para esta temporalidad")

    if zona["direccion"] == -1 and entrada <= precio:
        return False, "el precio ya esta debajo de la entrada: el corto se fue"
    if zona["direccion"] == 1 and entrada >= precio:
        return False, "el precio ya esta arriba de la entrada: el largo se fue"

    if zona["plan"].get("ensanchado"):
        return False, ("el stop de la zona no cubre la comision: ensancharlo "
                       "lo saca del nivel que invalida la idea")

    if zona["plan"].get("agotado"):
        return False, ("el movimiento ya se negocio: el precio llego al "
                       "objetivo antes de que la entrada se llene")

    premio = premio_por_delante(zona, precio)
    if premio < PREMIO_MIN_R:
        return False, (f"el objetivo ya quedo atras: desde aca quedan "
                       f"{premio:.1f}R y hacen falta {PREMIO_MIN_R:.0f}R "
                       f"por delante")

    if atr > 0 and DISTANCIA_MAX_ATR > 0:
        lejos = abs(entrada - precio) / atr
        if lejos > DISTANCIA_MAX_ATR:
            return False, (f"el impulso ya se fue: la entrada queda a "
                           f"{lejos:.1f} ATR y el retroceso devolveria gran "
                           f"parte del movimiento")

    if zona["direccion"] == -1:
        return True, "limite de venta, esperando que el precio suba a la zona"
    return True, "limite de compra, esperando que el precio baje a la zona"


def congruencia(zona, ctx, osc=None):
    """Los tres puntos que tienen que coincidir para que haya un trade.

    1. Un movimiento de verdad: order block nacido de una ruptura con al menos
       1.5 ATR de desplazamiento, que dejo un hueco. Un hueco suelto no alcanza.
    2. 4h y 1h del mismo lado. Si una empuja para el otro lado, no hay
       congruencia: hay una pelea de temporalidades.

    El hueco abierto suma certeza, pero ya no traba la entrada. En la practica
    el retroceso que llena el limite tambien cierra el hueco, y exigirlo vivo
    dejaba la lista vacia justo cuando el precio llegaba a la zona. Si el
    bloque sigue entero y el objetivo no se toco, el retest vale.

    El largo o el corto no se activa por el puntaje. Se activa cuando el precio
    toca la entrada de una zona que ya junta los tres.
    """
    activos, faltan = [], []
    if zona.get("tipo") != "OB":
        return activos, ["un FVG suelto no activa: hace falta el order block del movimiento"]

    empuje = zona.get("empuje_atr") or 0
    if empuje >= 1.5:
        activos.append(f"movimiento de {empuje:.1f} ATR que rompio estructura")
    else:
        faltan.append(f"el movimiento fue chico ({empuje:.1f} ATR, hace falta 1.5)")

    if zona.get("hueco_vivo"):
        activos.append("el hueco de ese movimiento sigue abierto")
    elif zona.get("hueco_top") is None:
        faltan.append("el movimiento no dejo hueco")

    mayores = [c for c in ctx if "signo" in c]
    favor = [c["tf"] for c in mayores if c.get("signo") == zona["direccion"]]
    contra = [c["tf"] for c in mayores
              if c.get("signo") and c.get("signo") != zona["direccion"]]
    if favor and not contra:
        activos.append("a favor de " + " y ".join(favor))
    elif not mayores:
        faltan.append("no pude leer 4h ni 1h")
    elif contra and favor:
        faltan.append("la temporalidad mayor no coincide: "
                      + " y ".join(favor) + " a favor, "
                      + " y ".join(contra) + " en contra")
    elif contra:
        faltan.append("va contra " + " y ".join(contra))
    else:
        faltan.append("4h y 1h estan mixtos, no confirman el lado")

    # El VWAP y los osciladores suman o restan. No son una llave mas: un
    # retroceso sano deja el RSI y el MACD en contra, y la demanda debajo
    # del valor. Si ademas 4h o 1h empujan al otro lado, ahi si no hay trade.
    if zona.get("plan", {}).get("vwap_toca"):
        activos.append("la zona toca el VWAP del dia")
    if zona.get("plan", {}).get("ancla_afino"):
        activos.append("entrada apoyada en "
                       + zona["plan"].get("ancla_nombre", "un nivel"))

    _, favor, contra = valorar_osciladores(zona["direccion"], osc)
    activos.extend(favor)
    return activos, faltan


def recomendar(zonas, precio, ctx, rr):
    """Elige la zona que se puede operar ahora y explica la eleccion.

    Solo entra una zona operable que ademas junta los tres puntos de
    congruencia(). El puntaje desempata entre esas. Una zona con el stop
    ensanchado ni siquiera llega aca: operable() la descarta.
    """
    candidatas = []
    for z in zonas:
        if not z.get("operable"):
            continue
        if z.get("falta_confluencia", ["sin congruencia"]):
            continue
        candidatas.append((z, z["motivo_operable"]))
    if not candidatas:
        return None

    candidatas.sort(key=lambda par: (
        -par[0]["puntos"],
        -par[0]["indice_origen"],
    ))
    z, motivo = candidatas[0]
    p = z["plan"]

    mayores = [c for c in ctx if "signo" in c]
    contra = [c["tf"] for c in mayores if c["signo"] and c["signo"] != z["direccion"]]
    favor = [c["tf"] for c in mayores if c["signo"] == z["direccion"]]

    empate, azar = acierto_para_empatar(rr, p["costo_r"])
    distancia = abs(p["entrada"] - precio) / precio * 100

    return {
        "zona": z,
        "accion": "BUY" if z["direccion"] == 1 else "SELL",
        "tipo_orden": ("limite de compra" if z["direccion"] == 1
                       else "limite de venta"),
        "motivo_operable": motivo,
        "distancia_pct": distancia,
        "invalida": z["top"] if z["direccion"] == -1 else z["bot"],
        "contra_tf": contra, "favor_tf": favor,
        "contracorriente": bool(contra) and not favor,
        "confluencia": z.get("confluencia") or [],
        "acierto_empate": empate,
        "acierto_azar": azar,
    }


def _martillo(v, alcista):
    """Martillo alcista o estrella fugaz. Misma vara que la linea gris."""
    rango = v.maximo - v.minimo
    if rango <= 0:
        return False
    cuerpo = abs(v.cierre - v.apertura)
    techo = max(v.apertura, v.cierre)
    piso = min(v.apertura, v.cierre)
    mecha = piso - v.minimo if alcista else v.maximo - techo
    contra = v.maximo - techo if alcista else piso - v.minimo
    if mecha < rango * 0.60 or contra > rango * 0.20:
        return False
    if cuerpo <= 0 or mecha < cuerpo * 3:
        return False
    if alcista and v.cierre < v.apertura:
        return False
    if not alcista and v.cierre > v.apertura:
        return False
    return True


def _envolvente(prev, v, alcista):
    """El cuerpo de v tapa entero el cuerpo de la vela anterior, a favor."""
    if alcista and v.cierre <= v.apertura:
        return False
    if not alcista and v.cierre >= v.apertura:
        return False
    piso_p = min(prev.apertura, prev.cierre)
    techo_p = max(prev.apertura, prev.cierre)
    if techo_p <= piso_p:
        return False
    return (min(v.apertura, v.cierre) <= piso_p
            and max(v.apertura, v.cierre) >= techo_p)


def _soldados(a, b, c, alcista):
    """Tres soldados blancos o tres cuervos. Cuerpos grandes, sin mecha en contra."""
    trio = (a, b, c)
    if alcista:
        if not all(v.cierre > v.apertura for v in trio):
            return False
        if not (b.cierre > a.cierre and c.cierre > b.cierre):
            return False
    else:
        if not all(v.cierre < v.apertura for v in trio):
            return False
        if not (b.cierre < a.cierre and c.cierre < b.cierre):
            return False
    for prev, v in ((a, b), (b, c)):
        piso = min(prev.apertura, prev.cierre)
        techo = max(prev.apertura, prev.cierre)
        if not (piso <= v.apertura <= techo):
            return False
    for v in trio:
        rango = v.maximo - v.minimo
        if rango <= 0:
            return False
        if abs(v.cierre - v.apertura) < rango * 0.50:
            return False
        mecha = ((min(v.apertura, v.cierre) - v.minimo) if alcista
                 else (v.maximo - max(v.apertura, v.cierre)))
        if mecha > rango * 0.25:
            return False
    return True


def _media_en_vela(vela, medias):
    """La media mas lenta que la vela toca o atraviesa. medias: (periodo, valor)."""
    tocadas = [par for par in medias
               if par[1] is not None and vela.minimo <= par[1] <= vela.maximo]
    if not tocadas:
        return None
    return max(tocadas)


def proponer_gatillo(velas, precio, atr, tf, ctx, rr, costo_pct, costo_max_r,
                     tick, stop_max_atr=3.0):
    """Vela gatillo sobre MA 25, 99 o 200. La entrada queda en esa media.

    Alta confianza, y nada mas que eso: martillo y envolvente en la vela que
    acaba de cerrar, o tres soldados / tres cuervos en 5m y 15m. Si 4h o 1h
    van para el otro lado, no activa.
    """
    if len(velas) < 210 or not atr or not precio:
        return None
    mayores = [c for c in ctx if "signo" in c]
    if not mayores:
        return None

    cierres = [v.cierre for v in velas]
    series = {p: ind.sma(cierres, p) for p in MEDIAS_GATILLO}
    candidatos = []

    martillo, confirma = velas[-2], velas[-1]
    for alcista in (True, False):
        if _martillo(martillo, alcista) and _envolvente(martillo, confirma, alcista):
            medias = [(p, series[p][-2]) for p in MEDIAS_GATILLO]
            nivel = _media_en_vela(martillo, medias)
            if nivel and ((alcista and martillo.cierre >= nivel[1])
                          or (not alcista and martillo.cierre <= nivel[1])):
                candidatos.append((0, nivel[0], 1 if alcista else -1, nivel[1],
                                   martillo.minimo if alcista else martillo.maximo,
                                   "martillo y envolvente"))
        if tf in ("5m", "15m") and _soldados(velas[-3], velas[-2], velas[-1], alcista):
            nivel = None
            for k in (-3, -2, -1):
                medias = [(p, series[p][k]) for p in MEDIAS_GATILLO]
                toca = _media_en_vela(velas[k], medias)
                if toca and (nivel is None or toca[0] > nivel[0]):
                    nivel = toca
            if nivel:
                nombre = "tres soldados" if alcista else "tres cuervos"
                extremo = (min(v.minimo for v in velas[-3:]) if alcista
                           else max(v.maximo for v in velas[-3:]))
                candidatos.append((1, nivel[0], 1 if alcista else -1, nivel[1],
                                   extremo, nombre))

    candidatos.sort(key=lambda c: (c[0], -c[1]))
    for _, periodo, direccion, nivel, extremo, nombre in candidatos:
        contra = [c["tf"] for c in mayores
                  if c.get("signo") and c["signo"] != direccion]
        if contra:
            continue
        if direccion == 1:
            if not (extremo < nivel):
                continue
            zona_top, zona_bot = nivel, extremo
        else:
            if not (extremo > nivel):
                continue
            zona_top, zona_bot = extremo, nivel
        favor = [c["tf"] for c in mayores if c.get("signo") == direccion]
        texto = f"{nombre} en MA{periodo}"
        zona = {
            "tipo": nombre,
            "direccion": direccion,
            "top": zona_top, "bot": zona_bot,
            "indice_origen": len(velas) - 2,
            "indice_ruptura": len(velas) - 1,
            "tiempo": velas[-1].tiempo,
            "empuje_atr": 0, "hueco_vivo": False,
            "puntos": 6,
            "motivos": [texto],
            "gatillo": texto,
            "confluencia": [texto],
            "falta_confluencia": [],
        }
        zona["plan"] = ajustar_al_tick(
            plan(zona, atr, rr, costo_pct=costo_pct, costo_max_r=costo_max_r),
            direccion, tick, costo_pct)
        marcar_agotado(zona, velas)
        zona["vivo"] = sigue_vivo(zona, velas)
        ok, motivo = operable(zona, precio, atr, stop_max_atr)
        zona["operable"], zona["motivo_operable"] = ok, motivo
        if not ok:
            continue
        osc = lectura_osciladores(velas)
        extra, a_favor, en_contra = valorar_osciladores(direccion, osc)
        zona["puntos"] += extra
        zona["motivos"] = [texto] + a_favor + en_contra
        zona["confluencia"] = [texto] + a_favor
        p = zona["plan"]
        empate, azar = acierto_para_empatar(rr, p["costo_r"])
        return {
            "zona": zona,
            "accion": "BUY" if direccion == 1 else "SELL",
            "tipo_orden": ("limite de compra" if direccion == 1
                           else "limite de venta"),
            "motivo_operable": motivo,
            "distancia_pct": abs(p["entrada"] - precio) / precio * 100,
            "invalida": zona["bot"] if direccion == 1 else zona["top"],
            "contra_tf": [], "favor_tf": favor,
            "contracorriente": False,
            "confluencia": [texto] + a_favor,
            "acierto_empate": empate,
            "acierto_azar": azar,
            "gatillo": texto,
        }
    return None


def marcar_agotado(zona, velas):
    """Anota cuanto del objetivo ya se negoceo despues de que nacio la zona.

    El premio que importa no es el que queda por delante del precio de ahora:
    es el que el mercado ya toco. RUNE llego a 0.845, que era el TP, y recien
    despues bajo a llenar el limite. Esa entrada es la reversa, no el retroceso.
    """
    pl = zona["plan"]
    riesgo = abs(pl["entrada"] - pl["stop"])
    premio = abs(pl["objetivo"] - pl["entrada"])
    desde = zona.get("indice_ruptura", zona.get("indice_origen", 0)) + 1
    if zona["direccion"] == 1:
        extremo = max((v.maximo for v in velas[desde:]), default=pl["entrada"])
        viajado = max(0.0, extremo - pl["entrada"])
    else:
        extremo = min((v.minimo for v in velas[desde:]), default=pl["entrada"])
        viajado = max(0.0, pl["entrada"] - extremo)
    pl["recorrido_r"] = viajado / riesgo if riesgo else 0.0
    pl["agotado"] = bool(premio) and viajado / premio >= RECORRIDO_AGOTADO


def diagnostico(zonas, costo_pct, piso_pct, stop_max_atr, atr_pct, tf):
    """Por que no hay nada para operar. Sin esto, el panel solo dice "nada" y
    deja al que mira sin saber si el problema es el mercado, la ventana o el
    costo, que son tres cosas con tres soluciones distintas.
    """
    if not zonas:
        return ("No salio ninguna zona en esta ventana con los filtros del bot. "
                "Probá una ventana mas larga o una temporalidad mayor.")

    operables = [z for z in zonas if z.get("operable")]
    completas = [z for z in operables if not z.get("falta_confluencia")]
    if operables and not completas:
        z = max(operables, key=lambda x: x.get("puntos", 0))
        lado = "LONG" if z["direccion"] == 1 else "SHORT"
        falta = z.get("falta_confluencia") or ["no junta los tres puntos"]
        return (
            f"Ningun trade cierra la valoracion. La mas cerca es {z['tipo']} "
            f"{lado}. Falta: " + "; ".join(falta) + ".")

    gastados = [z for z in zonas if "ya se negocio" in z.get("motivo_operable", "")]
    if gastados and not operables:
        return (
            "El movimiento ya se negocio. El precio llego al objetivo antes de "
            "llenar la entrada, y volver a esa zona es entrar tarde: el premio "
            "ya se entrego. No se deja el limite puesto esperando la vuelta.")

    lejos = [z for z in zonas if "impulso ya se fue" in z.get("motivo_operable", "")]
    if lejos and not operables:
        return (
            "El movimiento ya corrio. La entrada queda a mas de "
            f"{DISTANCIA_MAX_ATR:.1f} ATR del precio, y volver hasta ahi es "
            "devolver gran parte del impulso, no un retroceso corto. No se "
            "entra a mercado para alcanzarla: si no vuelve cerca, no hay trade.")

    mal_vwap = [z for z in zonas
                if "lado equivocado del VWAP" in z.get("motivo_operable", "")]
    if mal_vwap and not operables:
        return (
            "Las zonas quedan del lado equivocado del VWAP del dia. Un largo "
            "con toda la caja debajo del valor, o un corto con toda la caja "
            "arriba, no es un retroceso hacia el precio medio de la sesion.")

    anchas = [z for z in zonas if "demasiado ancha" in z["motivo_operable"]]
    if anchas and all(z["plan"]["ensanchado"] for z in anchas):
        veces = piso_pct / atr_pct if atr_pct else 0
        return (
            f"Ninguna zona cierra, y el motivo es el costo, no el grafico. "
            f"Con {costo_pct:.2f}% de comision ida y vuelta el stop no puede "
            f"bajar de {piso_pct:.2f}% del precio, y eso son {veces:.1f} ATR en "
            f"{tf}: mas ancho que el tope de {stop_max_atr:.0f} ATR. Un stop tan "
            f"grande con 1:3 manda el objetivo a varios dias de distancia, asi "
            f"que ya no es una operacion de {tf}. Dos salidas reales: operar en "
            f"futuros, donde la comision es la mitad, o subir de temporalidad "
            f"para que el ATR crezca y el mismo porcentaje entre en el tope.")

    if all(not z["vivo"] for z in zonas):
        return ("Todas las zonas de la ventana ya fueron atravesadas. No queda "
                "ningun nivel sin usar: esperar a que se forme uno nuevo.")

    sin_piso = [z for z in zonas if "no cubre la comision" in z["motivo_operable"]]
    sin_premio = [z for z in zonas if "quedo atras" in z["motivo_operable"]]
    if sin_piso or sin_premio:
        partes = []
        if sin_piso:
            partes.append(
                f"{len(sin_piso)} zona{'s' if len(sin_piso) != 1 else ''} "
                f"{'piden' if len(sin_piso) != 1 else 'pide'} un stop mas corto "
                f"que el piso de la comision ({piso_pct:.2f}%). Correr ese stop "
                f"hasta el piso lo deja en un precio que la zona no invalida, "
                f"asi que se descartan en lugar de operarse con la salida corrida.")
        if sin_premio:
            partes.append(
                f"{len(sin_premio)} zona{'s' if len(sin_premio) != 1 else ''} "
                f"{'apuntan' if len(sin_premio) != 1 else 'apunta'} a un objetivo "
                f"que el precio ya alcanzo: entre el precio de ahora y el TP "
                f"queda menos de {PREMIO_MIN_R:.0f}R. Esa entrada pide un "
                f"retroceso para cobrar un premio que el mercado ya entrego.")
        return " ".join(partes)

    pasadas = [z for z in zonas if "se fue" in z["motivo_operable"]]
    if pasadas:
        return ("Las zonas vivas quedaron del lado equivocado del precio: con una "
                "orden limite ya no se pueden tomar. Perseguirlas a mercado es "
                "justo lo que la cuenta del costo no perdona.")

    return ("Ninguna zona operable ahora. Mirá el motivo de cada una en la lista "
            "de abajo.")


# ─────────────────────────────────────────────────────────────────────────────
#  El analisis completo, en datos
# ─────────────────────────────────────────────────────────────────────────────
def analizar(simbolo, tf="15m", dias=3.0, rr=3.0, costo_max_r=0.15,
             mercado="futuros", top=8, mayores=("4h", "1h"), costo_pct=None,
             stop_max_atr=3.0):
    """Todo el analisis como diccionario, para la consola y para el panel.

    Una sola funcion para los dos frentes: si la pantalla calculara por su lado
    terminaria diciendo algo distinto de la consola, y no habria forma de saber
    cual de las dos tiene razon.
    """
    minutos = MINUTOS.get(tf, 15)
    en_ventana = int(dias * 24 * 60 / minutos)
    velas, usado = bajar(simbolo, tf, en_ventana + 400, mercado)
    if len(velas) < 100:
        raise RuntimeError(f"{simbolo} {tf}: vinieron {len(velas)} velas, "
                           "no alcanza para medir nada")

    if costo_pct is None:
        costo_pct = MERCADOS[usado]["costo"]

    desde = max(0, len(velas) - en_ventana)
    cfg = estrategia.Config()
    ultimo_cierre = velas[-1].cierre
    precio = precio_vivo(simbolo, usado) or ultimo_cierre
    atr_final = ind.atr(velas, cfg.atr_periodo)[-1] or 0.0

    tick = tick_de(simbolo, usado)
    if usado == "ninjatrader":
        vwap = ind.vwap_cme(velas)[-1]
        periodo_valor = ind.clave_sesion_cme
        nombre_valor = "la sesion anterior"
    else:
        vwap = ind.vwap_diario(velas)[-1]
        periodo_valor = lambda ms: ms // 86_400_000
        nombre_valor = "el dia anterior"
    rito = ind.sesiones_rito(velas)
    valor = ind.area_de_valor(
        _velas_periodo_cerrado(velas, periodo_valor), tick)
    if valor:
        valor["periodo"] = nombre_valor
    niveles = _niveles_ancla(rito, valor)
    osc = lectura_osciladores(velas)
    todas = order_blocks(velas, cfg, desde) + fvgs(velas, desde)
    for z in todas:
        z["puntos"], z["motivos"] = puntaje(z, velas, precio, osc)
        z["plan"] = ajustar_al_tick(
            afinar_con_anclas(
                z, afinar_con_vwap(
                    z, plan(z, atr_final, rr, costo_pct=costo_pct,
                            costo_max_r=costo_max_r),
                    vwap, atr_final, precio, rr, costo_pct, costo_max_r),
                niveles, atr_final, precio, rr, costo_pct, costo_max_r),
            z["direccion"], tick, costo_pct)
        if z["plan"].get("contra_vwap"):
            z["puntos"] -= 2
            z["motivos"].append("la zona queda del lado de atras del VWAP")
        elif z["plan"].get("vwap_toca"):
            z["puntos"] += 1
            z["motivos"].append("la zona toca el VWAP")
        if z["plan"].get("ancla_afino"):
            z["puntos"] += 1
            z["motivos"].append("la entrada se apoya en "
                                + z["plan"]["ancla_nombre"])
        elif z["plan"].get("anclas_en_zona"):
            z["puntos"] += 1
            z["motivos"].append("la zona toca "
                                + " y ".join(z["plan"]["anclas_en_zona"]))
        marcar_agotado(z, velas)
        z["vivo"] = sigue_vivo(z, velas)
        z["operable"], z["motivo_operable"] = operable(z, precio, atr_final,
                                                       stop_max_atr)
    todas.sort(key=lambda z: z["puntos"], reverse=True)

    # Elegir y diagnosticar sobre TODAS, no sobre las que se muestran: si la
    # unica zona operable quedaba en el puesto nueve, recortar primero la hacia
    # desaparecer y el panel decia "nada para operar" teniendo un trade.
    ctx = contexto(simbolo, mayores, usado)
    for z in todas:
        activos, faltan = congruencia(z, ctx, osc)
        z["confluencia"] = activos
        z["falta_confluencia"] = faltan
    rec = recomendar(todas, precio, ctx, rr)
    gatillo = proponer_gatillo(
        velas, precio, atr_final, tf, ctx, rr, costo_pct, costo_max_r,
        tick, stop_max_atr)
    if gatillo and (rec is None
                    or gatillo["zona"]["direccion"] == rec["zona"]["direccion"]):
        rec = gatillo

    zonas = todas[:top]
    if rec and rec["zona"] not in zonas:
        zonas = [rec["zona"]] + zonas[:max(0, top - 1)]
    piso_pct = costo_pct / costo_max_r if costo_max_r else 0.0
    atr_pct = atr_final / precio * 100 if precio else 0.0
    return {
        "simbolo": simbolo, "tf": tf, "dias": dias, "rr": rr,
        "mercado": usado, "mercado_nombre": MERCADOS[usado]["nombre"],
        "mercado_pedido": mercado,
        "respaldo": usado != mercado,
        "costo_pct": costo_pct, "costo_max_r": costo_max_r,
        "costo_forzado": costo_pct != MERCADOS[usado]["costo"],
        "stop_max_atr": stop_max_atr, "tick": tick,
        "piso_stop_pct": piso_pct,
        "precio": precio, "ultimo_cierre": ultimo_cierre,
        "vwap": vwap,
        "osciladores": osc,
        "atr": atr_final, "atr_pct": atr_pct,
        "decimales": decimales(precio, tick),
        "vwap_nombre": ("VWAP de la sesión" if usado == "ninjatrader"
                        else "VWAP del día"),
        "rito": rito,
        "valor": valor,
        "velas": len(velas), "en_ventana": en_ventana,
        "desde_ms": velas[desde].tiempo, "hasta_ms": velas[-1].tiempo,
        "contexto": ctx,
        "zonas": zonas,
        "recomendacion": rec,
        "zonas_totales": len(todas),
        "diagnostico": None if rec else diagnostico(
            todas, costo_pct, piso_pct, stop_max_atr, atr_pct, tf),
        "_velas": velas,
    }


# ─────────────────────────────────────────────────────────────────────────────
#  Salida: el Pine que dibuja el analisis
# ─────────────────────────────────────────────────────────────────────────────
def escribir_pine(simbolo, tf, zonas, velas, rr, dias):
    """Un indicador con las zonas puestas a mano, listo para pegar."""
    ahora = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    lineas = [
        "// " + "=" * 74,
        f"//  ZONAS MARCADAS — {simbolo} {tf}",
        "// " + "=" * 74,
        f"//  Analisis de los ultimos {dias} dias, generado el {ahora} UTC.",
        "//",
        "//  Esto NO es un indicador que recalcula: son las zonas concretas que",
        "//  salieron de analizar esa ventana, escritas a mano. Si pasan varios dias",
        "//  van a quedar viejas; volvé a correr zonas.py y pegá la version nueva.",
        "//",
        "//  Cada caja trae su puntaje y el motivo. El puntaje ordena la mirada, no",
        "//  es una probabilidad: los pesos son un criterio, no una medicion.",
        "// " + "=" * 74,
        "",
        "//@version=5",
        f'indicator("Zonas {simbolo} {tf}", overlay=true, '
        "max_boxes_count=100, max_labels_count=100, max_lines_count=200)",
        "",
        'verPlan  = input.bool(true,  "Dibujar entrada / SL / TP")',
        'verFVG   = input.bool(true,  "Dibujar los FVG")',
        'puntMin  = input.float(0,    "Mostrar solo puntaje >=", step=0.5)',
        'largoCaja = input.int(40,    "Extender las cajas (velas)", minval=0)',
        "",
        'colCompra = input.color(color.new(#00e5a0, 80), "Zona de compra")',
        'colVenta  = input.color(color.new(#ff3c5f, 80), "Zona de venta")',
        'colFvg    = input.color(color.new(#9598a1, 85), "FVG")',
        "",
        "// Todo se ubica por TIEMPO y no por numero de vela. Contar velas hacia",
        "// atras se desfasa en cuanto hay un hueco de fin de semana o un feriado,",
        "// que es exactamente el caso del oro, el forex y las acciones.",
        "der = time + largoCaja * timeframe.in_seconds() * 1000",
        "",
        "dibujar(int t0, float techo, float piso, int lado, string tipo, "
        "float punt, string nota, float sl, float tp) =>",
        "    if punt >= puntMin and (tipo != \"FVG\" or verFVG)",
        "        col = tipo == \"FVG\" ? colFvg : (lado == 1 ? colCompra : colVenta)",
        "        borde = color.new(lado == 1 ? #00e5a0 : #ff3c5f, 40)",
        "        box.new(t0, techo, der, piso, xloc=xloc.bar_time, bgcolor=col, "
        "border_color=borde)",
        "        etiqueta = (lado == 1 ? \"LONG \" : \"SHORT \") + tipo + \"  \" + "
        "str.tostring(punt, \"#.#\") + \"pts\\n\" + nota",
        "        label.new(der, lado == 1 ? techo : piso, etiqueta, "
        "xloc=xloc.bar_time,",
        "                  style=lado == 1 ? label.style_label_down : "
        "label.style_label_up,",
        "                  color=color.new(lado == 1 ? #00e5a0 : #ff3c5f, 20), "
        "textcolor=color.white, size=size.small)",
        "        if verPlan and tipo != \"FVG\"",
        "            line.new(t0, sl, der, sl, xloc=xloc.bar_time, "
        "color=color.new(#ff3c5f, 20), style=line.style_dashed)",
        "            line.new(t0, tp, der, tp, xloc=xloc.bar_time, "
        "color=color.new(#00e5a0, 20), style=line.style_dashed)",
        "            label.new(der, tp, \"TP \" + str.tostring(tp, format.mintick), "
        "xloc=xloc.bar_time, style=label.style_label_left, "
        "color=color.new(#00e5a0, 30), textcolor=color.white, size=size.tiny)",
        "            label.new(der, sl, \"SL \" + str.tostring(sl, format.mintick), "
        "xloc=xloc.bar_time, style=label.style_label_left, "
        "color=color.new(#ff3c5f, 30), textcolor=color.white, size=size.tiny)",
        "",
        "if barstate.islast",
    ]

    if not zonas:
        lineas.append("    // No salio ninguna zona en esta ventana.")
    for z in zonas:
        nota = z["motivos"][0].replace('"', "'") if z["motivos"] else ""
        p = z["plan"]
        # La entrada no se pasa: es justo el borde de la caja, ya esta dibujada.
        lineas.append(
            f'    dibujar({z["tiempo"]}, {z["top"]:.10g}, {z["bot"]:.10g}, '
            f'{z["direccion"]}, "{z["tipo"]}", {z["puntos"]:.1f}, "{nota}", '
            f'{p["stop"]:.10g}, {p["objetivo"]:.10g})'
        )

    # Maximo y minimo del dia, que es el otro pedido de siempre.
    hoy = [v for v in velas
           if datetime.fromtimestamp(v.tiempo / 1000, timezone.utc).date()
           == datetime.fromtimestamp(velas[-1].tiempo / 1000, timezone.utc).date()]
    if hoy:
        desde_hoy = hoy[0].tiempo
        techo, piso = max(v.maximo for v in hoy), min(v.minimo for v in hoy)
        lineas += [
            "",
            "    // Maximo y minimo del dia, tambien por tiempo",
            f"    line.new({desde_hoy}, {techo:.10g}, der, {techo:.10g}, "
            "xloc=xloc.bar_time, color=color.new(#9598a1, 30), "
            "style=line.style_dotted, width=1)",
            f"    line.new({desde_hoy}, {piso:.10g}, der, {piso:.10g}, "
            "xloc=xloc.bar_time, color=color.new(#9598a1, 30), "
            "style=line.style_dotted, width=1)",
        ]
        vwap = ind.vwap_diario(velas)[-1]
        cierres = [v.cierre for v in velas]
        for per in MEDIAS:
            media = ind.sma(cierres, per)[-1]
            if not media:
                continue
            lineas += [
                f"    line.new({desde_hoy}, {media:.10g}, der, {media:.10g}, "
                "xloc=xloc.bar_time, color=color.new(#c58bff, 20), width=1)",
                f"    label.new(der, {media:.10g}, \"MA{per}\", xloc=xloc.bar_time, "
                "style=label.style_label_left, color=color.new(#c58bff, 40), "
                "textcolor=color.white, size=size.tiny)",
            ]
        if vwap:
            lineas += [
                "    // VWAP del dia UTC, desde la primera vela de la sesion",
                f"    line.new({desde_hoy}, {vwap:.10g}, der, {vwap:.10g}, "
                "xloc=xloc.bar_time, color=color.new(#f0b90b, 0), width=2)",
                f"    label.new(der, {vwap:.10g}, \"VWAP\", xloc=xloc.bar_time, "
                "style=label.style_label_left, color=color.new(#f0b90b, 20), "
                "textcolor=color.white, size=size.tiny)",
            ]

    return "\n".join(lineas) + "\n"


# ─────────────────────────────────────────────────────────────────────────────
def main():
    p = argparse.ArgumentParser(description="Analiza una ventana y escribe el Pine")
    p.add_argument("simbolo", nargs="?", default="BTCUSDT")
    p.add_argument("tf", nargs="?", default="1h")
    p.add_argument("--dias", type=float, default=3)
    p.add_argument("--rr", type=float, default=3.0)
    p.add_argument("--costo", type=float, default=0.10,
                   help="costo de ida y vuelta en %% (0.10 = futuros de Binance "
                        "a mercado; 0.008 = oro en Exness Raw)")
    p.add_argument("--costo-max-r", type=float, default=0.15, dest="costo_max_r",
                   help="cuanto costo se tolera por operacion, en R")
    p.add_argument("--top", type=int, default=8, help="cuantas zonas mostrar")
    p.add_argument("--mercado", default="futuros", choices=sorted(MERCADOS),
                   help="de donde salen las velas y que comision se aplica")
    p.add_argument("--salida", default=None, help="donde escribir el .pine")
    args = p.parse_args()

    costo = args.costo if "--costo" in sys.argv else None
    try:
        a = analizar(args.simbolo, args.tf, args.dias, args.rr,
                     args.costo_max_r, args.mercado, args.top,
                     costo_pct=costo)
    except RuntimeError as e:
        raise SystemExit(str(e))

    velas = a["_velas"]
    ancho = a["decimales"]
    d0 = datetime.fromtimestamp(a["desde_ms"] / 1000, timezone.utc)
    d1 = datetime.fromtimestamp(a["hasta_ms"] / 1000, timezone.utc)

    print("=" * 78)
    print(f"{a['simbolo']} {a['tf']} — ventana de {a['dias']} dias "
          f"({a['en_ventana']} velas)")
    print(f"desde {d0:%Y-%m-%d %H:%M} hasta {d1:%Y-%m-%d %H:%M} UTC")
    print(f"precio {a['precio']:.{ancho}f}   ATR {a['atr']:.{ancho}f} "
          f"({a['atr_pct']:.2f}% del precio)")
    if a.get("vwap"):
        print(f"VWAP del dia {a['vwap']:.{ancho}f}")
    print(f"mercado {a['mercado_nombre']}   costo {a['costo_pct']:.3f}% ida y "
          f"vuelta   piso del stop {a['piso_stop_pct']:.2f}%")
    if a["respaldo"]:
        print(f"OJO: pediste {a['mercado_pedido']} y no respondio; esto salio de "
              f"{a['mercado']}. Los precios y la comision son de ahi.")
    print("=" * 78)

    print("\nTEMPORALIDAD MAYOR")
    for c in a["contexto"]:
        if "error" in c:
            print(f"  {c['tf']:>4}  {c['error']}")
            continue
        print(f"  {c['tf']:>4}  {c['sesgo'].upper():<8} RSI {c['rsi']:.0f}"
              f"   rango 24h {c['bajo_24h']:.{ancho}f} — {c['alto_24h']:.{ancho}f}"
              f"   precio en el {c['pos_rango']:.0f}%")
        print(f"        {'; '.join(c['lecturas'])}")

    r = a["recomendacion"]
    print("\n" + "=" * 78)
    if not r:
        print("NINGUNA ZONA OPERABLE AHORA")
        print("=" * 78)
        texto = a["diagnostico"] or ""
        linea = ""
        for palabra in texto.split():
            if len(linea) + len(palabra) + 1 > 76:
                print(linea)
                linea = palabra
            else:
                linea = f"{linea} {palabra}".strip()
        if linea:
            print(linea)
    else:
        z, pl = r["zona"], r["zona"]["plan"]
        print(f"EL TRADE: {r['accion']}  {a['simbolo']}  {a['tf']}")
        if r.get("gatillo"):
            print(f"  gatillo   {r['gatillo']}")
        print("=" * 78)
        print(f"  entrada   {pl['entrada']:.{ancho}f}   ({r['tipo_orden']}, "
              f"a {r['distancia_pct']:.2f}% del precio)")
        if pl.get("vwap_afino"):
            print("            afinada al VWAP: cae en la mitad cercana de la zona")
        print(f"  stop loss {pl['stop']:.{ancho}f}")
        print(f"  take prof {pl['objetivo']:.{ancho}f}")
        print(f"  riesgo    {pl['riesgo_pct']:.2f}% del precio, a "
              f"1:{pl.get('rr_real', a['rr']):.2f}")
        print(f"  costo     {pl['costo_r']:.3f} R por operacion")
        if a["tick"]:
            print(f"  (precios ya redondeados al tick de {a['tick']:.10g})")
        print(f"  invalida  cierre de {a['tf']} pasando "
              f"{r['invalida']:.{ancho}f}")
        print(f"\n  acierto para empatar {r['acierto_empate']:.1f}%   "
              f"(entrando al azar da {r['acierto_azar']:.1f}%)")
        if r["contracorriente"]:
            print(f"\n  CONTRACORRIENTE: {', '.join(r['contra_tf'])} va para el otro "
                  "lado.\n  Scalp hasta el objetivo y afuera, no lo dejes correr.")
        elif r["favor_tf"]:
            print(f"\n  A favor de {', '.join(r['favor_tf'])}.")
        print(f"\n  por que   {'; '.join(z['motivos'])}")

    print("\n" + "=" * 78)
    print("TODAS LAS ZONAS")
    if not a["zonas"]:
        print("No salio ninguna en esta ventana con los filtros del bot.")
    for n, z in enumerate(a["zonas"], 1):
        lado = "LONG" if z["direccion"] == 1 else "SHORT"
        cuando = datetime.fromtimestamp(z["tiempo"] / 1000, timezone.utc)
        pl = z["plan"]
        marca = "operable" if z["operable"] else z["motivo_operable"]
        print(f"\n{n}. {lado}  {z['tipo']}   {z['puntos']:.1f} puntos   {marca}")
        print(f"   zona     {z['bot']:.{ancho}f} — {z['top']:.{ancho}f}"
              f"   (nacio {cuando:%d/%m %H:%M} UTC)")
        print(f"   plan     entrada {pl['entrada']:.{ancho}f}   "
              f"SL {pl['stop']:.{ancho}f}   TP {pl['objetivo']:.{ancho}f}"
              f"   riesgo {pl['riesgo_pct']:.2f}%  a 1:{a['rr']:.0f}")
        print(f"   costo    {pl['costo_r']:.3f} R por operacion "
              f"({a['costo_pct']:.3f}% de ida y vuelta sobre un stop de "
              f"{pl['riesgo_pct']:.2f}%)")
        if pl["ensanchado"]:
            print(f"   OJO      el stop de la zona era {pl['stop_zona_pct']:.2f}%"
                  f" y pagaba {a['costo_pct'] / pl['stop_zona_pct']:.2f} R de "
                  f"costo. Ensanchado al piso de {pl['riesgo_pct']:.2f}%")
        print(f"   por que  {'; '.join(z['motivos'])}")

    ruta = args.salida or f"/tmp/zonas_{args.simbolo}_{args.tf}.pine"
    with open(ruta, "w", encoding="utf-8") as f:
        f.write(escribir_pine(args.simbolo, args.tf, a["zonas"], velas,
                              args.rr, args.dias))
    print("\n" + "=" * 78)
    print(f"Pine escrito en {ruta}")
    print("Pegalo en el Pine Editor de TradingView y le das 'Add to chart' para")
    print("ver estas mismas zonas dibujadas, con las etiquetas de LONG y SHORT.")
    print("=" * 78)
    print("\nEl puntaje ordena la lista para no mirar veinte cajas iguales. Los")
    print("pesos son un criterio, no una medicion: no es la probabilidad de nada.")


if __name__ == "__main__":
    main()
