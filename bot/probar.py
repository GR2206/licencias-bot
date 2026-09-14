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

import sys
from datetime import datetime, timezone

import binance_api as api
import estrategia
import indicadores as ind

COMISION_IDA_VUELTA = 0.10  # % del nocional


def resultado(velas, s, cfg):
    """Devuelve (R obtenidas, etiqueta) recorriendo las velas siguientes."""
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

    return None, "sin cerrar"


def main():
    argumentos = [a for a in sys.argv[1:] if not a.startswith("--")]
    simbolo = (argumentos[0] if len(argumentos) > 0 else "BTCUSDT").upper()
    temporalidad = argumentos[1] if len(argumentos) > 1 else "1h"
    limite = int(argumentos[2]) if len(argumentos) > 2 else 1000
    base = api.TESTNET if "--testnet" in sys.argv else api.REAL

    cfg = estrategia.Config()
    if "--sin-parcial" in sys.argv:
        cfg.parcial_1r = False

    cliente = api.Cliente(base=base)
    velas = ind.desde_klines(cliente.klines(simbolo, temporalidad, limite))[:-1]
    if not velas:
        print("No vinieron velas.")
        return

    desde = datetime.fromtimestamp(velas[0].tiempo / 1000, timezone.utc)
    hasta = datetime.fromtimestamp(velas[-1].tiempo / 1000, timezone.utc)
    lista = estrategia.senales(velas, cfg)

    print("=" * 78)
    print(f"{simbolo} {temporalidad} — {len(velas)} velas")
    print(f"desde {desde:%Y-%m-%d %H:%M} hasta {hasta:%Y-%m-%d %H:%M} (UTC)")
    print(f"RR 1:{cfg.rr}  |  parcial en 1R: {'si' if cfg.parcial_1r else 'no'}")
    print("=" * 78)

    if not lista:
        print("La estrategia no encontro ninguna entrada en este tramo.")
        print("Es normal en muestras cortas: los filtros son exigentes.")
        return

    erres = 0.0
    positivas = negativas = abiertas = 0
    for s in lista:
        r, etiqueta = resultado(velas, s, cfg)
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
            f"{momento:%Y-%m-%d %H:%M}  {s.accion:<4} entrada {s.entrada:>11.4f}  "
            f"SL {s.stop:>11.4f}  TP {s.objetivo:>11.4f}  "
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
