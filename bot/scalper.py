"""Scalping de impulso: tendencia en M15, entrada en M5 por FVG, salida por tiempo.

Es la idea del scalping institucional escrita como reglas exactas, con un contador
de confluencias para que se vea cuantas condiciones se cumplen en cada momento:

  1. TENDENCIA en M15 (armado desde las velas de M5, sin mirar el futuro): la EMA
     rapida de un lado de la lenta y la lenta con pendiente a favor.
  2. IMPULSO en M5: una vela con rango mayor a N veces el ATR, a favor.
  3. IMBALANCE (FVG): el impulso deja un hueco de tres velas sin solapamiento.
     Alcista: maximo[i-2] < minimo[i]. La zona es [maximo[i-2], minimo[i]].
  4. RETROCESO: el precio vuelve y toca el hueco.
  5. CONFIRMACION: la vela del toque cierra a favor.
  6. Stop en el borde lejano del hueco mas un colchon, objetivo a RR veces.
  7. Salida por tiempo: si a los N minutos no toco nada, se cierra a mercado.

-------------------------------------------------------------------------------
LO QUE DIO AL MEDIRLO, Y POR QUE ESTO NO OPERA SOLO

Sobre 71 dias de oro en M5 (que es el mercado con la mejor relacion
costo/movimiento que encontre) y 96 combinaciones de impulso, FVG, punto de
toque, colchon y RR:

    * 90 de 96 configuraciones aciertan MENOS que entrar al azar en el mismo
      activo con el mismo stop. Varias hasta 22 puntos menos.
    * La mejor le gana al azar por 1.6 puntos en 59 operaciones, o sea nada.
    * En R por operacion, con costos: entre -0.66 y +0.10.

El motivo no es mala suerte, es estructural. Entrar en el retroceso a un hueco
significa entrar justo donde el mercado acaba de mostrar que hay ordenes en
contra, y dejar el stop apenas del otro lado de un nivel que todos ven.

Y el marco que hay que tener presente, medido en referencia.py: entrando AL AZAR
en oro M5, el acierto da 33.5% con RR 1:2 y 24.9% con RR 1:3, que es exactamente
1/(1+RR) que predice la teoria para un mercado sin memoria. La esperanza bruta es
CERO en cualquier combinacion de stop y objetivo. Asi que todo el resultado de
una estrategia sale de dos cosas: cuanto le gana al azar, menos lo que cuesta
operar. Si no le gana al azar, no hay nada que automatizar.
-------------------------------------------------------------------------------
"""
from dataclasses import dataclass

import indicadores as ind
from estrategia import Senal

NOMBRE = "scalper"


@dataclass
class Config:
    # Tendencia en la temporalidad mayor
    factor_mayor: int = 3          # 3 velas de M5 = M15
    ema_rapida: int = 20
    ema_lenta: int = 50
    exigir_pendiente: bool = True
    # Impulso e imbalance
    atr_periodo: int = 14
    impulso_atr: float = 1.5       # rango de la vela > esto x ATR
    exigir_fvg: bool = True
    espera_max: int = 12           # velas que el hueco queda vivo
    toque_fraccion: float = 1.0    # 0 = borde cercano, 1 = borde lejano
    exigir_confirmacion: bool = True
    # Gestion
    rr: float = 2.0
    colchon_atr: float = 1.2
    riesgo_min_pct: float = 0.0    # 0 = se deriva del costo
    # El techo tiene que quedar por ARRIBA del piso derivado del costo, si no la
    # estrategia no puede tomar ni una operacion. En Binance el piso sale 0.667%,
    # asi que 1.5% deja aire. En un broker con spread bajo el piso baja mucho y
    # este techo se puede apretar.
    riesgo_max_pct: float = 1.5
    espera_velas: int = 2
    minutos_max: int = 30          # 0 = sin limite de tiempo
    # Costo de ida y vuelta en % del precio. De aca sale el piso del stop.
    costo_ida_vuelta_pct: float = 0.10
    costo_max_r: float = 0.15
    # Horario en hora UTC. (0, 24) = todo el dia.
    hora_desde: int = 0
    hora_hasta: int = 24
    parcial_1r: bool = False
    fraccion_parcial: float = 0.5

    @property
    def piso_stop(self):
        """El stop mas chico que deja el costo por debajo de costo_max_r."""
        if self.riesgo_min_pct > 0:
            return self.riesgo_min_pct
        if self.costo_max_r <= 0:
            return 0.0
        return self.costo_ida_vuelta_pct / self.costo_max_r


def agregar(velas, n):
    """Junta las velas de n en n para armar la temporalidad mayor.

    Devuelve (velas_mayores, indice) donde indice[i] dice cual es la ultima vela
    mayor YA CERRADA en el momento de la vela chica i. Sin eso se estaria usando
    informacion del futuro.
    """
    if n <= 1:
        return list(velas), list(range(len(velas)))
    mayores = []
    indice = []
    for i, v in enumerate(velas):
        if i % n == 0:
            mayores.append(ind.Vela(tiempo=v.tiempo, apertura=v.apertura,
                                    maximo=v.maximo, minimo=v.minimo,
                                    cierre=v.cierre, volumen=v.volumen))
        else:
            g = mayores[-1]
            g.maximo = max(g.maximo, v.maximo)
            g.minimo = min(g.minimo, v.minimo)
            g.cierre = v.cierre
            g.volumen += v.volumen
        indice.append(len(mayores) - 2)
    return mayores, indice


def tendencia_mayor(velas, cfg: Config):
    """Lado permitido en cada vela chica: 1 alcista, -1 bajista, 0 sin tendencia."""
    mayores, indice = agregar(velas, cfg.factor_mayor)
    cierres = [v.cierre for v in mayores]
    rapida = ind.ema(cierres, cfg.ema_rapida)
    lenta = ind.ema(cierres, cfg.ema_lenta)

    lados = []
    for i in range(len(mayores)):
        r, l = rapida[i], lenta[i]
        previa = lenta[i - 3] if i >= 3 else None
        if r is None or l is None:
            lados.append(0)
        elif r > l and (not cfg.exigir_pendiente or (previa is not None and l > previa)):
            lados.append(1)
        elif r < l and (not cfg.exigir_pendiente or (previa is not None and l < previa)):
            lados.append(-1)
        else:
            lados.append(0)

    return [lados[j] if 0 <= j < len(lados) else 0 for j in indice]


def _hora_utc(ms):
    return int(ms / 3_600_000) % 24


def senales(velas, cfg: Config = None):
    """Todas las entradas que la estrategia habria tomado."""
    cfg = cfg or Config()
    arranque = max(cfg.atr_periodo, cfg.ema_lenta * cfg.factor_mayor) + 5
    if len(velas) < arranque + 5:
        return []

    lados = tendencia_mayor(velas, cfg)
    atrs = ind.atr(velas, cfg.atr_periodo)
    piso = cfg.piso_stop

    huecos = []   # [direccion, borde_cercano, borde_lejano, nacimiento]
    salida = []
    ultima = -10**9

    for i in range(arranque, len(velas)):
        v = velas[i]
        atr = atrs[i]
        if not atr:
            continue

        # ── Impulso que deja un hueco ───────────────────────────────────────
        if (v.maximo - v.minimo) > atr * cfg.impulso_atr:
            v2 = velas[i - 2]
            alcista = v.cierre > v.apertura
            if alcista and (not cfg.exigir_fvg or v2.maximo < v.minimo):
                lejos = v2.maximo if cfg.exigir_fvg else v.apertura
                huecos.append([1, v.minimo, lejos, i])
            elif not alcista and (not cfg.exigir_fvg or v2.minimo > v.maximo):
                lejos = v2.minimo if cfg.exigir_fvg else v.apertura
                huecos.append([-1, v.maximo, lejos, i])

        huecos = [h for h in huecos if i - h[3] <= cfg.espera_max]

        lado = lados[i]
        if not huecos or lado == 0 or i - ultima <= cfg.espera_velas:
            continue
        if not (cfg.hora_desde <= _hora_utc(v.tiempo) < cfg.hora_hasta):
            continue

        # ── Retroceso a un hueco a favor de la tendencia ────────────────────
        for h in list(huecos):
            direccion, cerca, lejos = h[0], h[1], h[2]
            if direccion != lado:
                continue
            nivel = cerca + (lejos - cerca) * cfg.toque_fraccion
            tocado = v.minimo <= nivel if direccion == 1 else v.maximo >= nivel
            if not tocado:
                continue
            if cfg.exigir_confirmacion:
                if direccion == 1 and v.cierre < v.apertura:
                    continue
                if direccion == -1 and v.cierre > v.apertura:
                    continue

            stop = (lejos - atr * cfg.colchon_atr if direccion == 1
                    else lejos + atr * cfg.colchon_atr)
            riesgo = abs(v.cierre - stop)
            if riesgo <= 0:
                continue
            riesgo_pct = riesgo / v.cierre * 100
            if riesgo_pct < piso or riesgo_pct > cfg.riesgo_max_pct:
                continue

            objetivo = (v.cierre + riesgo * cfg.rr if direccion == 1
                        else v.cierre - riesgo * cfg.rr)
            salida.append(Senal(
                indice=i, tiempo=v.tiempo,
                accion="BUY" if direccion == 1 else "SELL",
                entrada=v.cierre, stop=stop, objetivo=objetivo,
                riesgo_pct=riesgo_pct, zona_top=max(cerca, lejos),
                zona_bot=min(cerca, lejos),
            ))
            huecos.remove(h)
            ultima = i
            break

    return salida


def senal_actual(velas, cfg: Config = None):
    """La senal de la ultima vela cerrada, o None."""
    todas = senales(velas, cfg)
    if not todas:
        return None
    ultima = todas[-1]
    return ultima if ultima.indice == len(velas) - 1 else None


def confluencias(velas, cfg: Config = None):
    """Que condiciones se cumplen ahora mismo. Para el panel.

    Devuelve una lista de (nombre, se_cumple, detalle) y el conteo. Es lo que
    pedias ver: cuantas caracteristicas coinciden en este momento.
    """
    cfg = cfg or Config()
    arranque = max(cfg.atr_periodo, cfg.ema_lenta * cfg.factor_mayor) + 5
    if len(velas) < arranque + 5:
        return [], 0

    lados = tendencia_mayor(velas, cfg)
    atrs = ind.atr(velas, cfg.atr_periodo)
    i = len(velas) - 1
    v = velas[i]
    atr = atrs[i] or 0.0
    lado = lados[i]

    # Hueco vivo mas reciente a favor de la tendencia
    hueco = None
    for j in range(max(arranque, i - cfg.espera_max), i + 1):
        vj, aj = velas[j], atrs[j]
        if not aj or (vj.maximo - vj.minimo) <= aj * cfg.impulso_atr:
            continue
        v2 = velas[j - 2]
        if vj.cierre > vj.apertura and (not cfg.exigir_fvg or v2.maximo < vj.minimo):
            hueco = (1, vj.minimo, v2.maximo if cfg.exigir_fvg else vj.apertura)
        elif vj.cierre < vj.apertura and (not cfg.exigir_fvg or v2.minimo > vj.maximo):
            hueco = (-1, vj.maximo, v2.minimo if cfg.exigir_fvg else vj.apertura)

    tocando = False
    if hueco and hueco[0] == lado:
        nivel = hueco[1] + (hueco[2] - hueco[1]) * cfg.toque_fraccion
        tocando = v.minimo <= nivel if lado == 1 else v.maximo >= nivel

    confirma = (v.cierre > v.apertura) if lado == 1 else (v.cierre < v.apertura)
    en_horario = cfg.hora_desde <= _hora_utc(v.tiempo) < cfg.hora_hasta
    volatil = bool(atr and (atr / v.cierre * 100) >= cfg.piso_stop)

    lista = [
        ("Tendencia mayor definida", lado != 0,
         "alcista" if lado == 1 else "bajista" if lado == -1 else "sin definir"),
        ("Hay un imbalance vivo a favor", bool(hueco and hueco[0] == lado),
         f"zona {min(hueco[1], hueco[2]):.6g} a {max(hueco[1], hueco[2]):.6g}"
         if hueco else "no hay"),
        ("El precio esta tocando la zona", tocando, "si" if tocando else "no"),
        ("La vela confirma a favor", bool(lado) and confirma,
         "si" if confirma else "no"),
        ("Volatilidad suficiente para el costo", volatil,
         f"ATR {atr / v.cierre * 100:.3f}% vs piso {cfg.piso_stop:.3f}%"
         if atr else "sin dato"),
        ("Dentro del horario", en_horario, f"{_hora_utc(v.tiempo)}h UTC"),
    ]
    return lista, sum(1 for _, ok, _ in lista if ok)
