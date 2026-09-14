"""Indicadores tecnicos en Python puro.

Sin numpy ni pandas a proposito: en Termux esas librerias son un dolor de
cabeza para compilar y aca no hacen falta. Todo trabaja con listas.

Las formulas replican exactamente las de Pine Script (TradingView) para que el
bot y el grafico digan lo mismo:

  - ta.atr  usa RMA (media de Wilder), no SMA.
  - ta.rsi  usa RMA sobre ganancias y perdidas.
  - ta.supertrend se porta linea por linea desde la implementacion de la
    documentacion oficial de Pine.
"""

from dataclasses import dataclass


@dataclass
class Vela:
    """Una vela cerrada."""

    tiempo: int  # milisegundos de apertura
    apertura: float
    maximo: float
    minimo: float
    cierre: float
    volumen: float

    @property
    def hl2(self) -> float:
        return (self.maximo + self.minimo) / 2

    @property
    def alcista(self) -> bool:
        return self.cierre > self.apertura

    @property
    def bajista(self) -> bool:
        return self.cierre < self.apertura


def desde_klines(klines) -> list:
    """Convierte la respuesta de /klines de Binance en una lista de Vela."""
    velas = []
    for k in klines:
        velas.append(
            Vela(
                tiempo=int(k[0]),
                apertura=float(k[1]),
                maximo=float(k[2]),
                minimo=float(k[3]),
                cierre=float(k[4]),
                volumen=float(k[5]),
            )
        )
    return velas


def ema(valores, periodo):
    """EMA con el mismo arranque que Pine: SMA de las primeras `periodo` velas."""
    salida = [None] * len(valores)
    if len(valores) < periodo:
        return salida
    alfa = 2.0 / (periodo + 1)
    previo = sum(valores[:periodo]) / periodo
    salida[periodo - 1] = previo
    for i in range(periodo, len(valores)):
        previo = alfa * valores[i] + (1 - alfa) * previo
        salida[i] = previo
    return salida


def rma(valores, periodo):
    """Media de Wilder (la que usa Pine internamente en atr y rsi)."""
    salida = [None] * len(valores)
    if len(valores) < periodo:
        return salida
    alfa = 1.0 / periodo
    previo = sum(valores[:periodo]) / periodo
    salida[periodo - 1] = previo
    for i in range(periodo, len(valores)):
        previo = alfa * valores[i] + (1 - alfa) * previo
        salida[i] = previo
    return salida


def rango_verdadero(velas):
    tr = [None] * len(velas)
    if velas:
        tr[0] = velas[0].maximo - velas[0].minimo
    for i in range(1, len(velas)):
        v, p = velas[i], velas[i - 1]
        tr[i] = max(
            v.maximo - v.minimo,
            abs(v.maximo - p.cierre),
            abs(v.minimo - p.cierre),
        )
    return tr


def atr(velas, periodo=14):
    return rma(rango_verdadero(velas), periodo)


def rsi(valores, periodo=14):
    salida = [None] * len(valores)
    if len(valores) <= periodo:
        return salida
    subidas, bajadas = [0.0], [0.0]
    for i in range(1, len(valores)):
        cambio = valores[i] - valores[i - 1]
        subidas.append(max(cambio, 0.0))
        bajadas.append(max(-cambio, 0.0))
    media_sub = rma(subidas, periodo)
    media_baj = rma(bajadas, periodo)
    for i in range(len(valores)):
        if media_sub[i] is None or media_baj[i] is None:
            continue
        if media_baj[i] == 0:
            salida[i] = 100.0
        else:
            rs = media_sub[i] / media_baj[i]
            salida[i] = 100 - (100 / (1 + rs))
    return salida


def supertrend(velas, factor=2.6, periodo=14):
    """Port literal de ta.supertrend() de Pine Script.

    Devuelve (linea, direccion) donde direccion es -1 en tendencia alcista y
    1 en tendencia bajista, igual que en TradingView.
    """
    n = len(velas)
    atr_v = atr(velas, periodo)
    linea = [None] * n
    direccion = [None] * n
    banda_sup_prev = None
    banda_inf_prev = None
    st_prev = None

    for i in range(n):
        if atr_v[i] is None:
            continue
        src = velas[i].hl2
        banda_sup = src + factor * atr_v[i]
        banda_inf = src - factor * atr_v[i]

        if banda_inf_prev is not None:
            cierre_previo = velas[i - 1].cierre
            if not (banda_inf > banda_inf_prev or cierre_previo < banda_inf_prev):
                banda_inf = banda_inf_prev
            if not (banda_sup < banda_sup_prev or cierre_previo > banda_sup_prev):
                banda_sup = banda_sup_prev

        if i == 0 or atr_v[i - 1] is None:
            d = 1
        elif st_prev is not None and st_prev == banda_sup_prev:
            d = -1 if velas[i].cierre > banda_sup else 1
        else:
            d = 1 if velas[i].cierre < banda_inf else -1

        st = banda_inf if d == -1 else banda_sup
        linea[i] = st
        direccion[i] = d

        banda_sup_prev = banda_sup
        banda_inf_prev = banda_inf
        st_prev = st

    return linea, direccion


def pivotes(velas, izq, der):
    """Pivotes de maximo y minimo al estilo ta.pivothigh / ta.pivotlow.

    Devuelve dos listas alineadas con `velas`. En el indice i aparece el valor
    del pivote que quedo CONFIRMADO en esa vela, es decir el de la vela i-der.
    Asi se respeta el retardo real: un pivote no existe hasta que pasaron `der`
    velas.
    """
    n = len(velas)
    altos = [None] * n
    bajos = [None] * n
    for centro in range(izq, n - der):
        v = velas[centro]
        es_alto = True
        es_bajo = True
        for j in range(centro - izq, centro + der + 1):
            if j == centro:
                continue
            if velas[j].maximo >= v.maximo:
                es_alto = False
            if velas[j].minimo <= v.minimo:
                es_bajo = False
            if not es_alto and not es_bajo:
                break
        if es_alto:
            altos[centro + der] = v.maximo
        if es_bajo:
            bajos[centro + der] = v.minimo
    return altos, bajos


def niveles_diarios(velas):
    """Maximo/minimo del dia anterior y del dia en curso, vela por vela.

    Devuelve una lista de diccionarios con las claves ayer_max, ayer_min,
    hoy_max y hoy_min. Es el equivalente de las lineas punteadas PDH/PDL y
    HOD/LOD del indicador.
    """
    MS_DIA = 86_400_000
    salida = []
    hoy_max = hoy_min = None
    ayer_max = ayer_min = None
    dia_actual = None
    for v in velas:
        dia = v.tiempo // MS_DIA
        if dia_actual is None:
            dia_actual = dia
            hoy_max, hoy_min = v.maximo, v.minimo
        elif dia != dia_actual:
            ayer_max, ayer_min = hoy_max, hoy_min
            dia_actual = dia
            hoy_max, hoy_min = v.maximo, v.minimo
        else:
            hoy_max = max(hoy_max, v.maximo)
            hoy_min = min(hoy_min, v.minimo)
        salida.append(
            {
                "ayer_max": ayer_max,
                "ayer_min": ayer_min,
                "hoy_max": hoy_max,
                "hoy_min": hoy_min,
            }
        )
    return salida
