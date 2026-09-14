"""Prueba honesta de la estrategia sobre velas reales.

Baja el historial de Binance, corre el motor de confluencia y simula vela por
vela que habria pasado con cada entrada: si toco antes el stop o el objetivo,
descontando comisiones.

Uso:
    python probar.py                        # BTCUSDT 1h, 1000 velas
    python probar.py ETHUSDT 30m 1500
    python probar.py BTCUSDT 1h 1500 --sin-parcial

Usa el historial real (fapi.binance.com). Con --testnet baja las velas del
testnet, pero ojo: ahi los datos tienen saltos y huecos, no sirven para medir
nada. Si tu pais bloquea la API de Binance vas a ver un error 451.

Criterios de la simulacion:
  - Si el stop y el objetivo caen en la misma vela se cuenta como perdida. Es
    el criterio pesimista: no hay forma de saber cual toco primero.
  - Se descuenta 0.10% de comision ida y vuelta (taker en Binance futuros),
    convertido a R segun el riesgo de cada operacion.
  - NO se modela slippage ni el spread. La realidad siempre es un poco peor.
"""

import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

import binance_api as api
import bot
import estrategia
import indicadores as ind
import scalper

COMISION_IDA_VUELTA = 0.10  # % del nocional


def precio(valor):
    """Formatea con decimales utiles: BTC no necesita 8, CHZ sin 5 no se lee."""
    if valor >= 100:
        ancho = 2
    elif valor >= 1:
        ancho = 4
    elif valor >= 0.01:
        ancho = 6
    else:
        ancho = 8
    return f"{valor:>11.{ancho}f}"


def resultado(velas, s, cfg, limite_velas=0):
    """Devuelve (R obtenidas, etiqueta) recorriendo las velas siguientes.

    `limite_velas` es la salida por tiempo del scalper: si no toco ni el stop ni
    el objetivo, se cierra a mercado al precio que haya. Hay que modelarla, si no
    se estaria midiendo una estrategia distinta a la que opera el bot.
    """
    riesgo = abs(s.entrada - s.stop)
    uno_r = s.objetivo_1r
    fraccion = cfg.fraccion_parcial if cfg.parcial_1r else 0.0
    parcial_hecha = False
    stop = s.stop

    for j in range(s.indice + 1, len(velas)):
        v = velas[j]
        if s.es_compra:
            golpe_stop = v.minimo <= stop
            golpe_1r = v.maximo >= uno_r
            golpe_tp = v.maximo >= s.objetivo
        else:
            golpe_stop = v.maximo >= stop
            golpe_1r = v.minimo <= uno_r
            golpe_tp = v.minimo <= s.objetivo

        if golpe_stop:
            if parcial_hecha:
                return fraccion, "parcial + salida a la entrada"
            return -1.0, "perdio"

        if fraccion and not parcial_hecha and golpe_1r:
            parcial_hecha = True
            stop = s.entrada  # de aca en adelante la operacion no puede doler
            if golpe_tp:
                return fraccion + (1 - fraccion) * cfg.rr, "gano completa"
            continue

        if golpe_tp:
            if fraccion and not parcial_hecha:
                return fraccion + (1 - fraccion) * cfg.rr, "gano completa"
            return (fraccion + (1 - fraccion) * cfg.rr) if fraccion else cfg.rr, "gano completa"

        if limite_velas and (j - s.indice) >= limite_velas:
            movido = (v.cierre - s.entrada) if s.es_compra else (s.entrada - v.cierre)
            ganado = movido / riesgo
            if parcial_hecha:
                ganado = fraccion + (1 - fraccion) * ganado
            return ganado, "salio por tiempo"

    return None, "sin cerrar"


ESPEJO = "https://data-api.binance.vision"


def klines_espejo(simbolo, temporalidad, limite):
    """Velas del espejo publico de datos, bajadas por tramos.

    Sirve para dos cosas: cuando Binance responde 451 por geobloqueo, y cuando
    se piden mas de 1500 velas, que es el maximo por pedido.
    """
    minutos = bot.MINUTOS_TF.get(temporalidad, 60)
    paso = 1000
    fin = int(time.time() * 1000)
    cursor = fin - limite * minutos * 60_000
    filas = []
    while cursor < fin and len(filas) < limite:
        url = (f"{ESPEJO}/api/v3/klines?symbol={simbolo}&interval={temporalidad}"
               f"&startTime={cursor}&limit={paso}")
        pedido = urllib.request.Request(url, headers={"User-Agent": "probar/1.0"})
        with urllib.request.urlopen(pedido, timeout=30) as r:
            lote = json.loads(r.read())
        if not lote:
            break
        filas.extend(lote)
        siguiente = lote[-1][0] + minutos * 60_000
        if siguiente <= cursor:
            break
        cursor = siguiente
        time.sleep(0.05)
    return filas[-limite:]


def main():
    argumentos = [a for a in sys.argv[1:] if not a.startswith("--")]
    simbolo = (argumentos[0] if len(argumentos) > 0 else "BTCUSDT").upper()
    temporalidad = argumentos[1] if len(argumentos) > 1 else "1h"
    limite = int(argumentos[2]) if len(argumentos) > 2 else 1000
    base = api.TESTNET if "--testnet" in sys.argv else api.REAL

    # Se usa el mismo config.env que el bot: si no, se estaria probando una
    # estrategia distinta a la que va a operar.
    bot.cargar_env()
    mod = bot.motor()
    cfg = bot.config_estrategia(mod)
    if "--sin-parcial" in sys.argv:
        cfg.parcial_1r = False

    cliente = api.Cliente(base=base)
    try:
        crudas = cliente.klines(simbolo, temporalidad, limite)
    except api.ErrorBinance as e:
        if "451" not in str(e):
            print(f"No pude bajar el historial: {e}")
            return
        # 451 es geobloqueo. El espejo publico de datos no lo tiene, y sirve
        # igual: son las mismas velas del mercado spot.
        print("Binance devolvio 451 (geobloqueo). Uso el espejo publico de datos.")
        try:
            crudas = klines_espejo(simbolo, temporalidad, limite)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e2:
            print(f"El espejo tampoco respondio: {e2}")
            return
    velas = ind.desde_klines(crudas)[:-1]
    if not velas:
        print("No vinieron velas.")
        return

    desde = datetime.fromtimestamp(velas[0].tiempo / 1000, timezone.utc)
    hasta = datetime.fromtimestamp(velas[-1].tiempo / 1000, timezone.utc)
    lista = mod.senales(velas, cfg)

    print("=" * 78)
    print(f"{simbolo} {temporalidad} — {len(velas)} velas   estrategia: {mod.NOMBRE}")
    print(f"desde {desde:%Y-%m-%d %H:%M} hasta {hasta:%Y-%m-%d %H:%M} (UTC)")
    print(f"RR 1:{cfg.rr}  |  parcial en 1R: {'si' if cfg.parcial_1r else 'no'}")
    if mod is estrategia:
        print(
            f"stop {cfg.sl_modo}  |  riesgo permitido {cfg.riesgo_min_pct}-{cfg.riesgo_max_pct}% "
            f"del precio  |  pivote {cfg.pivote}  |  "
            f"bloques caducan a {cfg.edad_max_bloque or '∞'} velas"
        )
    elif mod is scalper:
        print(
            f"tendencia EMA {cfg.ema_rapida}/{cfg.ema_lenta} en la temporalidad "
            f"x{cfg.factor_mayor}  |  impulso {cfg.impulso_atr} ATR  |  "
            f"FVG {'si' if cfg.exigir_fvg else 'no'}"
        )
        print(
            f"salida por tiempo: {cfg.minutos_max or 'no'} min  |  "
            f"riesgo permitido {cfg.piso_stop:.3f}-{cfg.riesgo_max_pct}% del precio "
            f"(el piso lo impone el costo de {cfg.costo_ida_vuelta_pct}%)"
        )
    else:
        print(
            f"EMA {cfg.ema_periodo}  |  riesgo permitido "
            f"{cfg.riesgo_min_pct}-{cfg.riesgo_max_pct}% del precio  |  "
            f"martillo {'si' if cfg.usar_martillo else 'no'}  |  "
            f"ruptura {'si' if cfg.usar_ruptura else 'no'}"
        )
    print("=" * 78)

    if not lista:
        print("La estrategia no encontro ninguna entrada en este tramo.")
        print("Es normal en muestras cortas: los filtros son exigentes.")
        return

    # La salida por tiempo del scalper, expresada en velas de esta temporalidad.
    minutos_max = getattr(cfg, "minutos_max", 0)
    minutos_vela = bot.MINUTOS_TF.get(temporalidad, 60)
    limite_velas = max(1, minutos_max // minutos_vela) if minutos_max else 0

    erres = 0.0
    positivas = negativas = abiertas = 0
    for s in lista:
        r, etiqueta = resultado(velas, s, cfg, limite_velas)
        momento = datetime.fromtimestamp(s.tiempo / 1000, timezone.utc)
        if r is None:
            abiertas += 1
            texto_r = "    —"
        else:
            r -= COMISION_IDA_VUELTA / s.riesgo_pct  # comision en unidades de R
            erres += r
            if r > 0:
                positivas += 1
            else:
                negativas += 1
            texto_r = f"{r:+5.2f}R"
        print(
            f"{momento:%Y-%m-%d %H:%M}  {s.accion:<4} entrada {precio(s.entrada)}  "
            f"SL {precio(s.stop)}  TP {precio(s.objetivo)}  "
            f"riesgo {s.riesgo_pct:>4.2f}%  {texto_r}  {etiqueta}"
        )

    cerradas = positivas + negativas
    print("-" * 78)
    print(f"Entradas: {len(lista)}  |  en verde {positivas}  en rojo {negativas}  sin cerrar {abiertas}")
    if cerradas:
        print(f"Aciertos: {positivas / cerradas * 100:.1f}%")
        print(f"Resultado: {erres:+.1f}R en total, {erres / cerradas:+.3f}R por operacion")
        print(f"Con 2% de riesgo por operacion: {erres * 2:+.1f}% de la cuenta")
    print("-" * 78)
    print("Esto NO es un backtest serio: muestra corta, sin slippage y sin spread.")
    print("Sirve para ver si la logica respira, no para decidir cuanta plata poner.")
    print("Si el resultado por operacion no es claramente positivo en VARIOS")
    print("activos y periodos, no lo pongas en real. La respuesta honesta es que")
    print("en las pruebas que hicimos queda en el limite del break-even.")


if __name__ == "__main__":
    main()
