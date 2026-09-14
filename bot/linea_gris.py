"""Estrategia de la linea gris: rebote con martillo en la EMA lenta y ruptura.

Es la version en Python de tradingview/linea_gris.pine, para que el grafico y el
bot cuenten la misma historia. Expone la misma interfaz que estrategia.py
(Config, senales, senal_actual) asi que el bot puede usar cualquiera de las dos
poniendo ESTRATEGIA=linea_gris u ESTRATEGIA=order_blocks en el archivo de
configuracion.

Las dos reglas:

  LONG  : el precio viene arriba de la linea, la toca o la perfora y deja una
          vela martillo que cierra de nuevo arriba. Entrada al cierre, stop
          debajo de la mecha.
  SHORT : el cierre pasa de largo al otro lado de la linea. Entrada al cierre,
          stop arriba del maximo de esa vela.

MEDIDO SOBRE 25 ACTIVOS DE BINANCE, 1 ANIO REAL, M15+M30+H1, CON COMISIONES,
eligiendo parametros en 8 activos y verificando en los otros 17:

    SHORT por ruptura ....  +0.08 R por operacion    (3586 operaciones)
    LONG por martillo ....  -0.11 R por operacion    ( 194 operaciones)

Y las tres razones para no confiarle dinero todavia:

  1. En ese anio 23 de los 25 activos bajaron, mediana -56%. La misma regla al
     alza da -0.18 R. En ZEC, el unico que subio fuerte, se da vuelta: gana el
     largo. Lo que se midio puede ser el mercado y no la regla.
  2. Toda la ventaja cabe dentro de los costos: +0.07 R como esta, +0.01 R con
     0.05% de slippage por lado, negativa si se paga taker en todo.
  3. Se apaga: +0.15 R en la primera mitad del anio, -0.01 R en la segunda.

Por eso el preset viene en MODO=simulacion.
"""
from dataclasses import dataclass

import indicadores as ind
from estrategia import Senal

NOMBRE = "linea_gris"


@dataclass
class Config:
    # La linea
    ema_periodo: int = 200
    atr_periodo: int = 14
    usar_pendiente: bool = True
    pendiente_velas: int = 20
    # El martillo
    usar_martillo: bool = True
    tolerancia_atr: float = 0.05   # que tan cerca de la linea pasa la mecha
    mecha_frac: float = 0.60       # mecha >= esta fraccion del rango de la vela
    mecha_min: float = 3.0         # mecha >= cuerpo x esto
    contra_max: float = 0.20       # mecha opuesta <= esta fraccion del rango
    exigir_color: bool = True      # el martillo cierra verde
    # La ruptura
    usar_ruptura: bool = True      # short cuando el cierre pasa de largo
    ruptura_atr: float = 0.20      # cuanto tiene que pasarse, para no contar roces
    ruptura_largo: bool = False    # espejo al alza. Medido: -0.18 R. Apagado.
    # Gestion
    rr: float = 4.0
    colchon_atr: float = 0.15
    # El piso de stop es el parametro que mas movio el resultado: de -0.07 R a
    # +0.07 R por operacion. No es magia, es aritmetica: la comision es un
    # porcentaje del nocional, asi que sobre un stop del 0.2% se lleva 0.35 R y
    # sobre uno del 1.5% apenas 0.05 R. Las senales con stop diminuto pierden
    # plata por construccion, no por mala suerte.
    riesgo_min_pct: float = 1.0
    riesgo_max_pct: float = 3.0
    espera_velas: int = 3
    # Cerrar la mitad en 1R y mover el stop a la entrada. Con RR 1:4 y 23% de
    # acierto la parcial recorta la ganancia esperada, asi que viene apagada.
    parcial_1r: bool = False
    fraccion_parcial: float = 0.5


def _es_martillo(v, cfg: Config, alcista: bool) -> bool:
    """Martillo (alcista) o estrella fugaz (bajista): mecha larga, cuerpo chico."""
    rango = v.maximo - v.minimo
    if rango <= 0:
        return False
    cuerpo = abs(v.cierre - v.apertura)
    techo = max(v.apertura, v.cierre)
    piso = min(v.apertura, v.cierre)
    mecha = piso - v.minimo if alcista else v.maximo - techo
    contra = v.maximo - techo if alcista else piso - v.minimo
    if mecha < rango * cfg.mecha_frac or contra > rango * cfg.contra_max:
        return False
    if cuerpo > 0 and mecha < cuerpo * cfg.mecha_min:
        return False
    if cfg.exigir_color:
        if alcista and v.cierre < v.apertura:
            return False
        if not alcista and v.cierre > v.apertura:
            return False
    return True


def senales(velas, cfg: Config = None):
    """Todas las entradas que la estrategia habria tomado, en orden."""
    cfg = cfg or Config()
    arranque = max(cfg.ema_periodo, cfg.atr_periodo, cfg.pendiente_velas) + 2
    if len(velas) < arranque + 5:
        return []

    lineas = ind.ema([v.cierre for v in velas], cfg.ema_periodo)
    atrs = ind.atr(velas, cfg.atr_periodo)

    salida = []
    ultima = -10**9

    for i in range(arranque, len(velas)):
        if i - ultima <= cfg.espera_velas:
            continue
        v = velas[i]
        linea = lineas[i]
        atr = atrs[i]
        previa = lineas[i - 1]
        if linea is None or previa is None or not atr:
            continue

        anclaje = lineas[i - cfg.pendiente_velas]
        subiendo = anclaje is not None and linea > anclaje
        tol = atr * cfg.tolerancia_atr
        senal = None

        # ── LONG: rebote con martillo apoyado en la linea ────────────────────
        if cfg.usar_martillo and v.cierre > linea and (subiendo or not cfg.usar_pendiente):
            if v.minimo <= linea + tol and _es_martillo(v, cfg, alcista=True):
                stop = v.minimo - atr * cfg.colchon_atr
                if stop < v.cierre:
                    senal = ("BUY", stop)

        # ── LONG por ruptura al alza: espejo del short, apagado por defecto ──
        if senal is None and cfg.ruptura_largo:
            if velas[i - 1].cierre < previa and v.cierre > linea + atr * cfg.ruptura_atr:
                stop = v.minimo - atr * cfg.colchon_atr
                if stop < v.cierre:
                    senal = ("BUY", stop)

        # ── SHORT: el precio pasa de largo y cierra del otro lado ────────────
        if senal is None and cfg.usar_ruptura:
            if velas[i - 1].cierre > previa and v.cierre < linea - atr * cfg.ruptura_atr:
                stop = v.maximo + atr * cfg.colchon_atr
                if stop > v.cierre:
                    senal = ("SELL", stop)

        if senal is None:
            continue

        accion, stop = senal
        riesgo = abs(v.cierre - stop)
        riesgo_pct = riesgo / v.cierre * 100
        if not (cfg.riesgo_min_pct <= riesgo_pct <= cfg.riesgo_max_pct):
            continue
        objetivo = (v.cierre + riesgo * cfg.rr if accion == "BUY"
                    else v.cierre - riesgo * cfg.rr)
        salida.append(Senal(
            indice=i, tiempo=v.tiempo, accion=accion, entrada=v.cierre,
            stop=stop, objetivo=objetivo, riesgo_pct=riesgo_pct,
            # La "zona" de esta estrategia es la vela que disparo la senal.
            zona_top=v.maximo, zona_bot=v.minimo,
        ))
        ultima = i

    return salida


def senal_actual(velas, cfg: Config = None):
    """La senal de la ultima vela cerrada, o None. Es lo que mira el bot."""
    todas = senales(velas, cfg)
    if not todas:
        return None
    ultima = todas[-1]
    return ultima if ultima.indice == len(velas) - 1 else None
