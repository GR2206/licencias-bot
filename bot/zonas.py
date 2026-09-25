"""Analiza una ventana de tiempo y escribe el Pine que la marca en tu grafico.

    python zonas.py BTCUSDT 1h --dias 3
    python zonas.py XAUUSD 1h --dias 3 --fuente tradingview
    python zonas.py ADAUSDT 4h --dias 10 --salida /tmp/mis_zonas.pine

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


# ─────────────────────────────────────────────────────────────────────────────
#  Datos
# ─────────────────────────────────────────────────────────────────────────────
def bajar(simbolo, tf, velas_totales):
    """Velas del espejo publico de Binance, por tramos."""
    minutos = MINUTOS.get(tf, 60)
    fin = int(time.time() * 1000)
    cursor = fin - velas_totales * minutos * 60_000
    filas = []
    while cursor < fin and len(filas) < velas_totales + 1000:
        url = (f"{ESPEJO}/api/v3/klines?symbol={simbolo}&interval={tf}"
               f"&startTime={cursor}&limit=1000")
        pedido = urllib.request.Request(url, headers={"User-Agent": "zonas/1.0"})
        try:
            with urllib.request.urlopen(pedido, timeout=30) as r:
                lote = json.loads(r.read())
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
            if filas:
                break
            raise SystemExit(f"No pude bajar {simbolo} {tf}: {e}")
        if not lote:
            break
        filas.extend(lote)
        siguiente = lote[-1][0] + minutos * 60_000
        if siguiente <= cursor:
            break
        cursor = siguiente
        time.sleep(0.05)
    return ind.desde_klines(filas)[:-1]


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


def plan(zona, atr, rr=3.0, colchon=0.25):
    """Entrada, stop y objetivo si se operara el retroceso a la zona."""
    if zona["direccion"] == 1:
        entrada = zona["top"]
        stop = zona["bot"] - atr * colchon
    else:
        entrada = zona["bot"]
        stop = zona["top"] + atr * colchon
    riesgo = abs(entrada - stop)
    objetivo = (entrada + riesgo * rr if zona["direccion"] == 1
                else entrada - riesgo * rr)
    return {
        "entrada": entrada, "stop": stop, "objetivo": objetivo,
        "riesgo_pct": riesgo / entrada * 100 if entrada else 0,
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
    p.add_argument("--top", type=int, default=8, help="cuantas zonas mostrar")
    p.add_argument("--salida", default=None, help="donde escribir el .pine")
    args = p.parse_args()

    minutos = MINUTOS.get(args.tf, 60)
    en_ventana = int(args.dias * 24 * 60 / minutos)
    # Margen para que pivotes, ATR y SuperTrend lleguen estabilizados.
    velas = bajar(args.simbolo, args.tf, en_ventana + 400)
    if len(velas) < 100:
        raise SystemExit("Vinieron muy pocas velas.")

    desde = max(0, len(velas) - en_ventana)
    cfg = estrategia.Config()
    precio = velas[-1].cierre
    atr_final = ind.atr(velas, cfg.atr_periodo)[-1] or 0.0

    zonas = order_blocks(velas, cfg, desde) + fvgs(velas, desde)
    for z in zonas:
        z["puntos"], z["motivos"] = puntaje(z, velas, precio)
        z["plan"] = plan(z, atr_final, args.rr)
        z["vivo"] = sigue_vivo(z, velas)
    zonas.sort(key=lambda z: z["puntos"], reverse=True)
    zonas = zonas[:args.top]

    d0 = datetime.fromtimestamp(velas[desde].tiempo / 1000, timezone.utc)
    d1 = datetime.fromtimestamp(velas[-1].tiempo / 1000, timezone.utc)
    ancho = 4 if precio < 1 else 2

    print("=" * 78)
    print(f"{args.simbolo} {args.tf} — ventana de {args.dias} dias "
          f"({en_ventana} velas)")
    print(f"desde {d0:%Y-%m-%d %H:%M} hasta {d1:%Y-%m-%d %H:%M} UTC")
    print(f"precio {precio:.{ancho}f}   ATR {atr_final:.{ancho}f} "
          f"({atr_final / precio * 100:.2f}% del precio)")
    print("=" * 78)

    if not zonas:
        print("\nNo salio ninguna zona en esta ventana con los filtros del bot.")
        print("Probá una ventana mas larga o una temporalidad mayor.")
        return

    for n, z in enumerate(zonas, 1):
        lado = "LONG" if z["direccion"] == 1 else "SHORT"
        cuando = datetime.fromtimestamp(z["tiempo"] / 1000, timezone.utc)
        pl = z["plan"]
        print(f"\n{n}. {lado}  {z['tipo']}   {z['puntos']:.1f} puntos"
              f"   {'sin mitigar' if z['vivo'] else 'ya atravesada'}")
        print(f"   zona     {z['bot']:.{ancho}f} — {z['top']:.{ancho}f}"
              f"   (nacio {cuando:%d/%m %H:%M} UTC)")
        print(f"   plan     entrada {pl['entrada']:.{ancho}f}   "
              f"SL {pl['stop']:.{ancho}f}   TP {pl['objetivo']:.{ancho}f}"
              f"   riesgo {pl['riesgo_pct']:.2f}%  a 1:{args.rr:.0f}")
        print(f"   por que  {'; '.join(z['motivos'])}")

    ruta = args.salida or f"/tmp/zonas_{args.simbolo}_{args.tf}.pine"
    with open(ruta, "w", encoding="utf-8") as f:
        f.write(escribir_pine(args.simbolo, args.tf, zonas, velas, args.rr,
                              args.dias))
    print("\n" + "=" * 78)
    print(f"Pine escrito en {ruta}")
    print("Pegalo en el Pine Editor de TradingView y le das 'Add to chart' para")
    print("ver estas mismas zonas dibujadas, con las etiquetas de LONG y SHORT.")
    print("=" * 78)
    print("\nEl puntaje ordena la lista para no mirar veinte cajas iguales. Los")
    print("pesos son un criterio, no una medicion: no es la probabilidad de nada.")


if __name__ == "__main__":
    main()
