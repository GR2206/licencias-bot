"""Recorrido por varios activos: donde funciona la estrategia y donde no.

Baja historia real de Binance, corre la estrategia activo por activo y arma un
ranking. Ademas parte el periodo en dos mitades y compara, que es la unica forma
de distinguir un activo que de verdad le sienta bien de uno que tuvo suerte.

Uso:
    python explorar.py                          # los 25 mas operados, 30m, 180 dias
    python explorar.py --tf 15m --dias 365
    python explorar.py --simbolos BTCUSDT,ETHUSDT,SOLUSDT --tf 15m
    python explorar.py --estrategia linea_gris --tf 15m --dias 365

Lee config.env, asi que mide con la misma configuracion con la que va a operar
el bot. Si cambias RIESGO_MAX_PCT o RR en el env, esto lo refleja.

Ojo con el tiempo: un anio de 15m son 35.000 velas por activo y unos 40 segundos
de descarga cada uno. Para una primera pasada, 180 dias alcanza.
"""

import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

import bot
import estrategia
import indicadores as ind

# Los 25 perpetuos USDT de Binance con mas volumen sostenido (mediana de 90
# dias, no el pico de un dia suelto). Se puede pisar con --simbolos.
POR_DEFECTO = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "ZECUSDT", "XRPUSDT",
    "BNBUSDT", "DOGEUSDT", "TRXUSDT", "NEARUSDT", "SUIUSDT",
    "ADAUSDT", "WLDUSDT", "ENAUSDT", "UNIUSDT", "LINKUSDT",
    "AAVEUSDT", "TAOUSDT", "AVAXUSDT", "LTCUSDT", "XLMUSDT",
    "PAXGUSDT", "XPLUSDT", "ONDOUSDT", "FETUSDT", "TRUMPUSDT",
]

# Comisiones de Binance futuros. El objetivo se cobra con orden limite (maker) y
# el stop sale a mercado (taker). Con stops cortos esto define el resultado: un
# 0.10% de ida y vuelta sobre un stop del 0.5% son 0.20R por operacion.
TAKER = 0.05
MAKER = 0.02

MINUTOS = bot.MINUTOS_TF
ESPEJO = "https://data-api.binance.vision"


def bajar(simbolo, tf, dias):
    """Historia de Binance. Usa el espejo publico, que no tiene geobloqueo."""
    ms = MINUTOS[tf] * 60_000
    fin = int(time.time() * 1000)
    cursor = fin - dias * 86_400_000
    filas = []
    while cursor < fin:
        url = (f"{ESPEJO}/api/v3/klines?symbol={simbolo}&interval={tf}"
               f"&startTime={cursor}&limit=1000")
        pedido = urllib.request.Request(url, headers={"User-Agent": "bot-ob/1.0"})
        try:
            with urllib.request.urlopen(pedido, timeout=30) as r:
                lote = json.loads(r.read())
        except (urllib.error.URLError, urllib.error.HTTPError) as e:
            raise SystemExit(f"No pude bajar {simbolo}: {e}")
        if not lote:
            break
        filas.extend(lote)
        siguiente = lote[-1][0] + ms
        if siguiente <= cursor:
            break
        cursor = siguiente
        time.sleep(0.05)
    return ind.desde_klines(filas)


def resultado(velas, s, cfg):
    """R netas de comision, o None si la operacion no llego a cerrar."""
    riesgo = abs(s.entrada - s.stop)
    riesgo_pct = riesgo / s.entrada * 100
    if riesgo_pct <= 0:
        return None, 0
    uno_r = s.objetivo_1r
    frac = cfg.fraccion_parcial if cfg.parcial_1r else 0.0
    costo = TAKER / riesgo_pct  # la entrada va a mercado
    hecha = False
    stop = s.stop

    for j in range(s.indice + 1, len(velas)):
        v = velas[j]
        if s.es_compra:
            gs, g1, gt = v.minimo <= stop, v.maximo >= uno_r, v.maximo >= s.objetivo
        else:
            gs, g1, gt = v.maximo >= stop, v.minimo <= uno_r, v.minimo <= s.objetivo

        if gs:
            resto = (1 - frac) if hecha else 1.0
            base = frac if hecha else -1.0
            return base - costo - TAKER / riesgo_pct * resto, j - s.indice
        if frac and not hecha and g1:
            hecha = True
            stop = s.entrada
            costo += MAKER / riesgo_pct * frac
            if gt:
                return frac + (1 - frac) * cfg.rr - costo - MAKER / riesgo_pct * (1 - frac), j - s.indice
            continue
        if gt:
            ganado = (frac + (1 - frac) * cfg.rr) if frac else cfg.rr
            resto = (1 - frac) if frac else 1.0
            return ganado - costo - MAKER / riesgo_pct * resto, j - s.indice
    return None, 0


def operaciones(velas, cfg, mod=None):
    """Una posicion por vez, como opera el bot de verdad."""
    ops = []
    libre_desde = -1
    for s in (mod or estrategia).senales(velas, cfg):
        if s.indice <= libre_desde:
            continue
        r, duracion = resultado(velas, s, cfg)
        if r is None:
            continue
        libre_desde = s.indice + duracion
        ops.append((velas[s.indice].tiempo, r, abs(s.entrada - s.stop) / s.entrada * 100))
    return ops


def medir(ops, dias):
    if not ops:
        return None
    erres = [o[1] for o in ops]
    curva = pico = caida = 0.0
    racha = mejor = peor = 0
    for r in erres:
        curva += r
        pico = max(pico, curva)
        caida = min(caida, curva - pico)
        if r > 0:
            racha = racha + 1 if racha > 0 else 1
            mejor = max(mejor, racha)
        else:
            racha = racha - 1 if racha < 0 else -1
            peor = min(peor, racha)
    return {
        "ops": len(ops),
        "por_dia": len(ops) / dias if dias else 0,
        "acierto": sum(1 for r in erres if r > 0) / len(erres) * 100,
        "total_r": sum(erres),
        "r_op": sum(erres) / len(erres),
        "caida": caida,
        "racha_g": mejor,
        "racha_p": -peor,
        "stop": sum(o[2] for o in ops) / len(ops),
    }


def argumento(nombre, defecto=None):
    if nombre in sys.argv:
        i = sys.argv.index(nombre)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return defecto


def main():
    tf = argumento("--tf", "30m")
    dias = int(argumento("--dias", "180"))
    crudos = argumento("--simbolos")
    simbolos = [s.strip().upper() for s in crudos.split(",")] if crudos else POR_DEFECTO

    bot.cargar_env()
    mod = bot.motor(argumento("--estrategia"))
    cfg = bot.config_estrategia(mod)

    print("=" * 96)
    print(f"RECORRIDO POR {len(simbolos)} ACTIVOS — {tf}, {dias} dias de historia real de Binance")
    print(f"estrategia {mod.NOMBRE} | RR 1:{cfg.rr} | riesgo permitido "
          f"{cfg.riesgo_min_pct}-{cfg.riesgo_max_pct}% del precio | "
          f"parcial {'si' if cfg.parcial_1r else 'no'}")
    print(f"comisiones incluidas: {TAKER}% taker por lado, {MAKER}% maker en el objetivo")
    print("=" * 96)
    print(f"{'activo':<12} {'ops':>5} {'op/dia':>7} {'stop':>6} {'acierto':>8} "
          f"{'R tot':>8} {'R/op':>7} {'caida':>7} {'rachas':>8} {'1a/2a mitad':>14}")
    print("-" * 96)

    filas = []
    for s in simbolos:
        try:
            velas = bajar(s, tf, dias)
        except SystemExit as e:
            print(f"{s:<12} {e}")
            continue
        if len(velas) < 400:
            print(f"{s:<12} historia insuficiente ({len(velas)} velas)")
            continue
        velas = velas[:-1]
        dias_reales = (velas[-1].tiempo - velas[0].tiempo) / 86_400_000
        ops = operaciones(velas, cfg, mod)
        m = medir(ops, dias_reales)
        if not m:
            print(f"{s:<12} {'0':>5}  sin senales en este tramo")
            continue

        corte = velas[len(velas) // 2].tiempo
        a = medir([o for o in ops if o[0] < corte], dias_reales / 2)
        b = medir([o for o in ops if o[0] >= corte], dias_reales / 2)
        mitades = (f"{a['r_op']:+.2f}/{b['r_op']:+.2f}" if a and b else "-")
        filas.append((m, s, a, b))
        print(f"{s:<12} {m['ops']:>5} {m['por_dia']:>7.2f} {m['stop']:>5.2f}% "
              f"{m['acierto']:>7.1f}% {m['total_r']:>+8.1f} {m['r_op']:>+7.3f} "
              f"{m['caida']:>6.1f}R {m['racha_g']:>3}/{m['racha_p']:<4} {mitades:>14}",
              flush=True)

    if not filas:
        return

    print("-" * 96)
    ops = sum(m["ops"] for m, *_ in filas)
    total = sum(m["total_r"] for m, *_ in filas)
    por_dia = sum(m["por_dia"] for m, *_ in filas)
    print(f"CARTERA COMPLETA: {ops} operaciones, {por_dia:.1f} por dia, "
          f"{total:+.1f}R en total, {total / ops:+.4f}R por operacion")

    duros = [(m, s) for m, s, a, b in filas
             if a and b and a["r_op"] > 0 and b["r_op"] > 0]
    print()
    print(f"Positivos en LAS DOS mitades: {len(duros)} de {len(filas)}")
    if duros:
        print("   " + ", ".join(s for _, s in sorted(duros, key=lambda x: -x[0]["r_op"])))
    print("   Con ~25 activos, que 5 o 6 den positivo en las dos mitades es lo que")
    print("   sale por azar. Para creerle a uno hace falta que siga estando ahi")
    print("   cuando repitas esto dentro de unos meses.")

    orden = sorted(filas, key=lambda f: -(f[2]["r_op"] if f[2] else -9))
    mitad = len(orden) // 2
    if mitad:
        arriba = [f for f in orden[:mitad] if f[3]]
        abajo = [f for f in orden[mitad:] if f[3]]
        if arriba and abajo:
            ra = sum(f[3]["total_r"] for f in arriba) / sum(f[3]["ops"] for f in arriba)
            rb = sum(f[3]["total_r"] for f in abajo) / sum(f[3]["ops"] for f in abajo)
            print()
            print("¿Sirve elegir activos por su historial?")
            print(f"   los mejores de la 1a mitad rindieron {ra:+.4f}R en la 2a")
            print(f"   los peores  de la 1a mitad rindieron {rb:+.4f}R en la 2a")
            print("   Si el primero no es claramente mayor, el ranking no predice nada.")


if __name__ == "__main__":
    main()
