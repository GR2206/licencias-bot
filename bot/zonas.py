"""Analiza una ventana de tiempo y escribe el Pine que la marca en tu grafico.

    python zonas.py BTCUSDT 1h --dias 3
    python zonas.py ADAUSDT 4h --dias 10 --salida /tmp/mis_zonas.pine
    python zonas.py SOLUSDT 15m --dias 1 --top 3

Los precios salen del espejo publico de Binance, asi que por ahora esto anda con
cripto y nada mas. Para oro, forex o acciones hace falta una fuente de velas que
todavia no tengo: Yahoo devuelve el forex redondeado al pip y Dukascopy tarda
demasiado. Es justo el agujero que taparia conectar el MCP de TradingView.

Resuelve un problema concreto: yo puedo LEER precios y calcular donde estan las
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
  * NIVELES: maximos y minimos del dia.

Y las ordena por que tan imponentes son, con un puntaje explicado, para que no
haya que mirar veinte cajas iguales.
"""
import argparse
import json
import math
import sys
import time
import urllib.error
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
}
ORDEN_RESPALDO = ["futuros", "spot"]


# ─────────────────────────────────────────────────────────────────────────────
#  Datos
# ─────────────────────────────────────────────────────────────────────────────
def _pedir(url, timeout=30):
    pedido = urllib.request.Request(url, headers={"User-Agent": "zonas/1.0"})
    with urllib.request.urlopen(pedido, timeout=timeout) as r:
        return json.loads(r.read())


def _klines_de(mercado, simbolo, tf, velas_totales):
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
    return ind.desde_klines(filas)[:-1]


def bajar(simbolo, tf, velas_totales, mercado="futuros"):
    """Velas de Binance. Devuelve (velas, mercado_usado).

    Intenta el mercado pedido y cae al otro si no responde. Binance bloquea la
    API de futuros por region (HTTP 451), asi que el respaldo importa: sin el,
    desde media Europa esto no corre. Pero el mercado que se termino usando se
    devuelve siempre, porque de ahi sale la comision, y decir "0.10%" cuando en
    realidad se leyo spot es mentirse en el unico numero que decide si la
    operacion cierra.
    """
    intentos = [mercado] + [m for m in ORDEN_RESPALDO if m != mercado]
    ultimo = None
    for candidato in intentos:
        try:
            velas = _klines_de(candidato, simbolo, tf, velas_totales)
        except (urllib.error.URLError, urllib.error.HTTPError,
                TimeoutError, OSError) as e:
            ultimo = f"{candidato}: {e}"
            continue
        if velas:
            return velas, candidato
        ultimo = f"{candidato}: no vino ninguna vela"
    raise RuntimeError(f"No pude bajar {simbolo} {tf} — {ultimo}")


def precio_vivo(simbolo, mercado):
    """Ultimo precio negociado, que no es el cierre de la ultima vela cerrada."""
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
            })
    return encontrados


def decimales(precio):
    """Cuantos decimales hacen falta para ver 5 cifras significativas.

    Con un precio fijo se rompe en los dos extremos: CHZ a 0.0162 con 4
    decimales muestra la zona como "0.0158 — 0.0158", que no dice nada.
    """
    if precio <= 0:
        return 2
    return max(2, 4 - int(math.floor(math.log10(precio))))


def _volumen_relativo(velas, i, ventana=20):
    """Volumen de la vela contra la media de las 20 previas."""
    desde = max(0, i - ventana)
    previas = [v.volumen for v in velas[desde:i] if v.volumen]
    if not previas:
        return 1.0
    media = sum(previas) / len(previas)
    return velas[i].volumen / media if media else 1.0


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


def puntaje(zona, velas, precio_actual):
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
            "rsi": ind.rsi(cierres, 14)[-1], "precio": precio}


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


def operable(zona, precio, atr=0.0, stop_max_atr=3.0):
    """Si la zona todavia se puede tomar con una orden limite, y por que no.

    Tres filtros, y los tres son por algo que se puede explicar:

    1. La zona no puede estar ya atravesada.
    2. Una zona de venta se toma con un limite ARRIBA del precio y una de compra
       con un limite ABAJO. Si el precio ya paso de largo, la orden nunca se
       llena: no es una entrada pendiente, es una que se fue. Sin esto el panel
       ofrece precios que no se pueden poner.
    3. El stop no puede pasar de stop_max_atr veces el ATR. Una zona muy ancha
       da un stop enorme, y con RR 1:3 el objetivo se va tan lejos que deja de
       ser una operacion de esta temporalidad: es un swing de varios dias
       disfrazado de entrada de 15 minutos. El riesgo por operacion sigue
       controlado, pero el tiempo de exposicion y la lectura no son las que se
       pidieron.
    """
    entrada = zona["plan"]["entrada"]
    if not zona["vivo"]:
        return False, "la zona ya fue atravesada"

    if atr > 0 and stop_max_atr > 0:
        veces = abs(entrada - zona["plan"]["stop"]) / atr
        if veces > stop_max_atr:
            return False, (f"stop de {veces:.1f} ATR: la zona es demasiado ancha "
                           f"para esta temporalidad")

    if zona["direccion"] == -1:
        if entrada <= precio:
            return False, "el precio ya esta debajo de la entrada: el corto se fue"
        return True, "limite de venta, esperando que el precio suba a la zona"
    if entrada >= precio:
        return False, "el precio ya esta arriba de la entrada: el largo se fue"
    return True, "limite de compra, esperando que el precio baje a la zona"


def recomendar(zonas, precio, ctx, rr):
    """Elige la zona que se puede operar ahora y explica la eleccion.

    Prioriza, en este orden: que el stop de la zona aguante el costo sin que
    haya que ensancharlo, el puntaje, y que sea reciente. Un stop ensanchado
    significa que el nivel que define la idea y el nivel que paga las cuentas no
    son el mismo, y ahi la zona dejo de ser el motivo de la operacion.
    """
    candidatas = [(z, z["motivo_operable"]) for z in zonas if z["operable"]]
    if not candidatas:
        return None

    candidatas.sort(key=lambda par: (
        par[0]["plan"]["ensanchado"],
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
        "acierto_empate": empate,
        "acierto_azar": azar,
    }


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

    zonas = order_blocks(velas, cfg, desde) + fvgs(velas, desde)
    for z in zonas:
        z["puntos"], z["motivos"] = puntaje(z, velas, precio)
        z["plan"] = plan(z, atr_final, rr, costo_pct=costo_pct,
                         costo_max_r=costo_max_r)
        z["vivo"] = sigue_vivo(z, velas)
        z["operable"], z["motivo_operable"] = operable(z, precio, atr_final,
                                                       stop_max_atr)
    zonas.sort(key=lambda z: z["puntos"], reverse=True)
    zonas = zonas[:top]

    ctx = contexto(simbolo, mayores, usado)
    return {
        "simbolo": simbolo, "tf": tf, "dias": dias, "rr": rr,
        "mercado": usado, "mercado_nombre": MERCADOS[usado]["nombre"],
        "mercado_pedido": mercado,
        "respaldo": usado != mercado,
        "costo_pct": costo_pct, "costo_max_r": costo_max_r,
        "costo_forzado": costo_pct != MERCADOS[usado]["costo"],
        "stop_max_atr": stop_max_atr,
        "piso_stop_pct": costo_pct / costo_max_r if costo_max_r else 0.0,
        "precio": precio, "ultimo_cierre": ultimo_cierre,
        "atr": atr_final,
        "atr_pct": atr_final / precio * 100 if precio else 0.0,
        "decimales": decimales(precio),
        "velas": len(velas), "en_ventana": en_ventana,
        "desde_ms": velas[desde].tiempo, "hasta_ms": velas[-1].tiempo,
        "contexto": ctx,
        "zonas": zonas,
        "recomendacion": recomendar(zonas, precio, ctx, rr),
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
        print("Todas estan atravesadas o el precio ya paso de largo la entrada.")
        print("Con una orden limite no se puede tomar ninguna: esperar.")
    else:
        z, pl = r["zona"], r["zona"]["plan"]
        print(f"EL TRADE: {r['accion']}  {a['simbolo']}  {a['tf']}")
        print("=" * 78)
        print(f"  entrada   {pl['entrada']:.{ancho}f}   ({r['tipo_orden']}, "
              f"a {r['distancia_pct']:.2f}% del precio)")
        print(f"  stop loss {pl['stop']:.{ancho}f}")
        print(f"  take prof {pl['objetivo']:.{ancho}f}")
        print(f"  riesgo    {pl['riesgo_pct']:.2f}% del precio, a 1:{a['rr']:.0f}")
        print(f"  costo     {pl['costo_r']:.3f} R por operacion")
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
