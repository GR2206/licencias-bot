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


def _ny_offset_ms(ms):
    """Nueva York contra UTC, con el horario de verano de Estados Unidos.

    El cambio es el segundo domingo de marzo a las 07:00 UTC y el primer
    domingo de noviembre a las 06:00 UTC. Va a mano para no depender de tzdata,
    que en Termux y en la app no siempre está instalado.
    """
    from datetime import datetime, timezone
    dt = datetime.fromtimestamp(ms / 1000, timezone.utc)

    def domingo(year, month, n):
        primero = datetime(year, month, 1, tzinfo=timezone.utc)
        return 1 + (6 - primero.weekday()) % 7 + (n - 1) * 7

    inicio = datetime(dt.year, 3, domingo(dt.year, 3, 2), 7, tzinfo=timezone.utc)
    fin = datetime(dt.year, 11, domingo(dt.year, 11, 1), 6, tzinfo=timezone.utc)
    horas = -4 if inicio <= dt < fin else -5
    return horas * 3_600_000


def clave_sesion_cme(ms):
    """Día de la sesión CME. Arranca a las 18:00 de Nueva York."""
    local = ms + _ny_offset_ms(ms)
    dias = local // 86_400_000
    hora = (local % 86_400_000) // 3_600_000
    if hora < 18:
        dias -= 1
    return dias


def vwap_cme(velas):
    """VWAP de la sesión electrónica, reiniciado a las 18:00 de Nueva York.

    Es el valor que se mira en un futuro de NinjaTrader. El de las 00:00 UTC
    parte la sesión por la mitad y deja el largo y el corto del lado equivocado.
    """
    salida = [None] * len(velas)
    sesion = None
    cum_pv = 0.0
    cum_v = 0.0
    for i, v in enumerate(velas):
        clave = clave_sesion_cme(v.tiempo)
        if clave != sesion:
            sesion = clave
            cum_pv = 0.0
            cum_v = 0.0
        vol = v.volumen or 0.0
        if vol > 0:
            tipico = (v.maximo + v.minimo + v.cierre) / 3.0
            cum_pv += tipico * vol
            cum_v += vol
        if cum_v > 0:
            salida[i] = cum_pv / cum_v
    return salida


def vwap_diario(velas):
    """VWAP que reinicia a las 00:00 UTC.

    Precio tipico (maximo + minimo + cierre) / 3, acumulado por volumen dentro
    del dia. Sin volumen el dia no tiene valor y esa vela queda en None: no se
    inventa un VWAP para no filtrar zonas con un numero falso. El ultimo valor
    es el de la ultima vela cerrada.
    """
    salida = [None] * len(velas)
    dia = None
    cum_pv = 0.0
    cum_v = 0.0
    for i, v in enumerate(velas):
        fecha = v.tiempo // 86_400_000
        if fecha != dia:
            dia = fecha
            cum_pv = 0.0
            cum_v = 0.0
        vol = v.volumen or 0.0
        if vol > 0:
            tipico = (v.maximo + v.minimo + v.cierre) / 3.0
            cum_pv += tipico * vol
            cum_v += vol
        if cum_v > 0:
            salida[i] = cum_pv / cum_v
    return salida


def clave_rito(ms):
    """Sesión de RiTo en hora de Nueva York, o None entre 17:00 y 18:00.

    Asia 18:00–03:00, Europa 03:00–09:30, Nueva York 09:30–17:00. La de Asia
    cruza la medianoche: las velas de la madrugada siguen siendo de la Asia
    que abrió a las 18:00 del día anterior.
    """
    local = ms + _ny_offset_ms(ms)
    dias = int(local // 86_400_000)
    minuto = int((local % 86_400_000) // 60_000)
    if minuto >= 18 * 60:
        return ("Asia", dias)
    if minuto < 3 * 60:
        return ("Asia", dias - 1)
    if minuto < 9 * 60 + 30:
        return ("Europa", dias)
    if minuto < 17 * 60:
        return ("Nueva York", dias)
    return None


def sesiones_rito(velas):
    """Máximo y mínimo de la última Asia, Europa y Nueva York ya cerradas.

    La sesión que todavía está en curso no entra: su máximo se mueve y una
    entrada apoyada ahí cambiaría con cada vela.
    """
    if not velas:
        return []
    grupos = {}
    for v in velas:
        clave = clave_rito(v.tiempo)
        if clave is None:
            continue
        g = grupos.get(clave)
        if g is None:
            grupos[clave] = {
                "nombre": clave[0],
                "dia": clave[1],
                "maximo": v.maximo,
                "minimo": v.minimo,
            }
        else:
            g["maximo"] = max(g["maximo"], v.maximo)
            g["minimo"] = min(g["minimo"], v.minimo)
    ultima = clave_rito(velas[-1].tiempo)
    por_nombre = {}
    for clave, g in grupos.items():
        if clave == ultima:
            continue
        previo = por_nombre.get(g["nombre"])
        if previo is None or g["dia"] > previo["dia"]:
            por_nombre[g["nombre"]] = g
    return [por_nombre[n] for n in ("Asia", "Europa", "Nueva York")
            if n in por_nombre]


def area_de_valor(velas, tick=None, porcentaje=0.70):
    """POC, VAL y VAH: el rango donde se negoció el 70% del volumen.

    Cada vela reparte su volumen parejo entre el mínimo y el máximo. Sin
    volumen, la vela cuenta uno: queda un perfil por tiempo, que es lo que
    se mira cuando el dato no trae contratos. El porcentaje es el del Value
    Area de NinjaTrader, 70.
    """
    if not velas:
        return None
    lo = min(v.minimo for v in velas)
    hi = max(v.maximo for v in velas)
    if hi < lo:
        return None
    span = hi - lo
    if span <= 0:
        return {"poc": lo, "val": lo, "vah": hi}
    if tick and tick > 0 and span / tick <= 400:
        paso = tick
    else:
        paso = span / 80.0
    n = int(span / paso) + 1
    if n < 1:
        return None
    if n > 400:
        n = 400
        paso = span / (n - 1) if n > 1 else span
    vols = [0.0] * n
    for v in velas:
        vol = v.volumen if v.volumen and v.volumen > 0 else 1.0
        a = int((v.minimo - lo) / paso)
        b = int((v.maximo - lo) / paso)
        if a < 0:
            a = 0
        if b >= n:
            b = n - 1
        if b < a:
            b = a
        parte = vol / (b - a + 1)
        for i in range(a, b + 1):
            vols[i] += parte
    total = sum(vols)
    if total <= 0:
        return None
    poc_i = max(range(n), key=lambda i: vols[i])
    acum = vols[poc_i]
    lo_i = hi_i = poc_i
    meta = total * porcentaje
    while acum < meta and (lo_i > 0 or hi_i < n - 1):
        abajo = vols[lo_i - 1] if lo_i > 0 else -1.0
        arriba = vols[hi_i + 1] if hi_i < n - 1 else -1.0
        if arriba >= abajo:
            hi_i += 1
            acum += vols[hi_i]
        else:
            lo_i -= 1
            acum += vols[lo_i]
    return {
        "poc": lo + (poc_i + 0.5) * paso,
        "val": lo + lo_i * paso,
        "vah": lo + (hi_i + 1) * paso,
    }


def sma(valores, periodo):
    """Media simple. Es la MA del grafico de Binance (MA 7, 25, 99, 200)."""
    salida = [None] * len(valores)
    if len(valores) < periodo or periodo <= 0:
        return salida
    acum = sum(valores[:periodo])
    salida[periodo - 1] = acum / periodo
    for i in range(periodo, len(valores)):
        acum += valores[i] - valores[i - periodo]
        salida[i] = acum / periodo
    return salida


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


def macd(valores, rapida=12, lenta=26, senal=9):
    """MACD de Binance: DIF, DEA y el histograma MACD.

    DIF es EMA rapida menos EMA lenta. DEA es la EMA del DIF. El valor que
    Binance rotula MACD es DIF menos DEA, sin multiplicar por dos.
    """
    n = len(valores)
    dif = [None] * n
    dea = [None] * n
    hist = [None] * n
    ema_rapida = ema(valores, rapida)
    ema_lenta = ema(valores, lenta)
    crudo, indices = [], []
    for i in range(n):
        if ema_rapida[i] is None or ema_lenta[i] is None:
            continue
        dif[i] = ema_rapida[i] - ema_lenta[i]
        crudo.append(dif[i])
        indices.append(i)
    dea_cruda = ema(crudo, senal)
    for j, i in enumerate(indices):
        if dea_cruda[j] is None:
            continue
        dea[i] = dea_cruda[j]
        hist[i] = dif[i] - dea[i]
    return dif, dea, hist


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
