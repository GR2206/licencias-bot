"""Motor de confluencia: Tendencia + Order Block, con gestion 1:3.

Es la traduccion linea por linea de tradingview/confluence_engine.pine. Si
cambias uno, cambia el otro, porque la gracia de todo esto es que el grafico y
el bot vean exactamente lo mismo.

Regla de entrada, en palabras:

  1. Una ruptura de estructura (BOS) con desplazamiento real deja un Order
     Block marcado: la ultima vela contraria antes del impulso.
  2. El precio retrocede y vuelve a entrar en ese bloque.
  3. Si la tendencia (SuperTrend + EMA + RSI) sigue a favor, se entra.
     SL en el borde lejano del bloque + colchon ATR. TP a RR veces el riesgo.
  4. Cada bloque se usa una sola vez.
"""

from dataclasses import dataclass, field

import indicadores as ind

NOMBRE = "order_blocks"


@dataclass
class Config:
    # Motor de tendencia
    atr_periodo: int = 14
    factor: float = 2.6
    # La EMA 200 viene apagada a proposito: en H1/M30 son 8 dias de media y en
    # la practica solo llega tarde, porque el SuperTrend ya define el lado.
    # Probala encendida en tu activo antes de decidir.
    usar_ema: bool = False
    ema_periodo: int = 200
    usar_rsi: bool = True
    rsi_periodo: int = 14
    rsi_neutro: float = 50.0
    # Order Blocks
    pivote: int = 5
    origen_max: int = 12
    zona_cuerpo: bool = False  # False = mecha a mecha
    max_bloques: int = 10
    exigir_desplazamiento: bool = True
    desplazamiento_atr: float = 1.5
    exigir_fvg: bool = True
    mitigar_por_cierre: bool = True
    # Entrada y gestion
    tolerancia_atr: float = 0.25
    colchon_atr: float = 0.30
    rr: float = 3.0
    riesgo_min_pct: float = 0.15
    riesgo_max_pct: float = 3.0
    espera_velas: int = 5
    # Confirmacion exigida a la vela que toca el bloque:
    #   "ninguna"      -> entra al cierre, toque y listo
    #   "vela"         -> la vela tiene que cerrar a favor (reaccion visible)
    #   "cierre_fuera" -> ademas tiene que cerrar de vuelta fuera de la zona
    confirmacion: str = "vela"
    # Un bloque viejo ya no es informacion fresca. 0 = sin limite, pero ojo: sin
    # limite un bloque puede ser mas viejo que la ventana de velas que el bot le
    # pide a Binance, y entonces el bot no lo ve aunque el backtest si. Medido en
    # CHZ M30 con 500 velas: se perdia 1 de cada 8 senales, en silencio.
    edad_max_bloque: int = 250
    # El precio tiene que haberse alejado del bloque antes de volver, para que
    # sea un retroceso de verdad y no un arrastre lateral. 0 = no se exige.
    alejarse_atr: float = 1.0
    # Donde va el stop: "borde" = borde lejano del bloque, "atr" = lo que sea
    # mas lejos entre el borde y una distancia fija en ATR. El modo atr es mas
    # ancho y por eso mas dificil de barrer con una mecha.
    sl_modo: str = "atr"
    sl_atr: float = 1.5
    # Cerrar la mitad en 1R y mover el stop a la entrada. No mejora la
    # esperanza matematica pero corta la varianza a la mitad.
    parcial_1r: bool = True
    fraccion_parcial: float = 0.5


@dataclass
class Senal:
    indice: int
    tiempo: int
    accion: str  # "BUY" o "SELL"
    entrada: float
    stop: float
    objetivo: float
    riesgo_pct: float
    zona_top: float
    zona_bot: float

    @property
    def es_compra(self) -> bool:
        return self.accion == "BUY"

    @property
    def riesgo(self) -> float:
        return abs(self.entrada - self.stop)

    @property
    def objetivo_1r(self) -> float:
        """Precio donde se cobra la parcial y el stop pasa a la entrada."""
        return self.entrada + self.riesgo if self.es_compra else self.entrada - self.riesgo


@dataclass
class _Bloque:
    top: float
    bot: float
    direccion: int  # 1 alcista (demanda), -1 bajista (oferta)
    usado: bool = False
    nacido: int = 0
    # Hasta donde llego el precio a favor despues de crearse el bloque. Sirve
    # para saber si el precio realmente se alejo antes de volver.
    extremo: float = 0.0


def senales(velas, cfg: Config = None):
    """Recorre las velas y devuelve todas las entradas que la estrategia habria
    tomado. El bot solo mira si la ultima corresponde a la vela recien cerrada;
    probar.py las usa todas para simular.
    """
    cfg = cfg or Config()
    n = len(velas)
    if n < max(cfg.ema_periodo, cfg.atr_periodo, cfg.pivote * 2) + 5:
        return []

    cierres = [v.cierre for v in velas]
    atr_v = ind.atr(velas, cfg.atr_periodo)
    ema_v = ind.ema(cierres, cfg.ema_periodo)
    rsi_v = ind.rsi(cierres, cfg.rsi_periodo)
    _, direccion = ind.supertrend(velas, cfg.factor, cfg.atr_periodo)
    piv_altos, piv_bajos = ind.pivotes(velas, cfg.pivote, cfg.pivote)

    bloques = []
    ult_ph = ult_pl = None
    ph_vivo = pl_vivo = False
    ultima_entrada = -10**9
    salida = []

    for i in range(n):
        v = velas[i]
        a = atr_v[i]
        if a is None or direccion[i] is None:
            continue

        # ── Estructura y rupturas ────────────────────────────────────────────
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

        # ── Paso 1: mitigacion de los bloques vivos ──────────────────────────
        vivos = []
        for b in bloques:
            if b.direccion == 1:
                anulado = v.cierre < b.bot if cfg.mitigar_por_cierre else v.minimo < b.bot
                b.extremo = max(b.extremo, v.maximo)
            else:
                anulado = v.cierre > b.top if cfg.mitigar_por_cierre else v.maximo > b.top
                b.extremo = min(b.extremo, v.minimo)
            if anulado:
                continue
            if cfg.edad_max_bloque and i - b.nacido > cfg.edad_max_bloque:
                continue
            vivos.append(b)
        bloques = vivos

        # ── Paso 2: crear el bloque que deja la ruptura ───────────────────────
        for alcista in (True, False):
            if not (bos_up if alcista else bos_dn):
                continue
            k = _buscar_origen(velas, i, alcista, cfg.origen_max)
            if k is not None and _validar(velas, i, k, alcista, a, cfg):
                origen = velas[i - k]
                if cfg.zona_cuerpo:
                    top = max(origen.apertura, origen.cierre)
                    bot = min(origen.apertura, origen.cierre)
                else:
                    top, bot = origen.maximo, origen.minimo
                bloques.append(
                    _Bloque(
                        top=top,
                        bot=bot,
                        direccion=1 if alcista else -1,
                        nacido=i,
                        extremo=v.maximo if alcista else v.minimo,
                    )
                )

        while len(bloques) > cfg.max_bloques:
            bloques.pop(0)

        # ── Tendencia ────────────────────────────────────────────────────────
        alcista_tendencia = direccion[i] == -1
        ema_i = ema_v[i]
        rsi_i = rsi_v[i]
        if cfg.usar_ema and ema_i is None:
            continue
        if cfg.usar_rsi and rsi_i is None:
            continue

        long_ok = alcista_tendencia
        short_ok = not alcista_tendencia
        if cfg.usar_ema:
            long_ok = long_ok and v.cierre > ema_i
            short_ok = short_ok and v.cierre < ema_i
        if cfg.usar_rsi:
            long_ok = long_ok and rsi_i > cfg.rsi_neutro
            short_ok = short_ok and rsi_i < cfg.rsi_neutro

        if not (long_ok or short_ok):
            continue

        # ── Confluencia: retroceso a un bloque virgen del lado correcto ───────
        lado = 1 if long_ok else -1
        tol = a * cfg.tolerancia_atr
        elegido = None
        for b in reversed(bloques):  # del mas nuevo al mas viejo
            if b.direccion != lado or b.usado:
                continue
            if cfg.alejarse_atr > 0:
                alejamiento = b.extremo - b.top if lado == 1 else b.bot - b.extremo
                if alejamiento < a * cfg.alejarse_atr:
                    continue
            if v.minimo <= b.top + tol and v.maximo >= b.bot - tol:
                elegido = b
                break
        if elegido is None:
            continue

        # El precio esta en la zona, pero entrar aca sin mas es comprar un
        # cuchillo cayendo. Se le pide que la vela muestre la reaccion.
        if cfg.confirmacion != "ninguna":
            if long_ok:
                if not v.alcista:
                    continue
                if cfg.confirmacion == "cierre_fuera" and v.cierre < elegido.top:
                    continue
            else:
                if not v.bajista:
                    continue
                if cfg.confirmacion == "cierre_fuera" and v.cierre > elegido.bot:
                    continue

        if i - ultima_entrada <= cfg.espera_velas:
            continue

        entrada = v.cierre
        if long_ok:
            stop = elegido.bot - a * cfg.colchon_atr
            if cfg.sl_modo == "atr":
                stop = min(stop, entrada - a * cfg.sl_atr)
            objetivo = entrada + (entrada - stop) * cfg.rr
            coherente = stop < entrada < objetivo
        else:
            stop = elegido.top + a * cfg.colchon_atr
            if cfg.sl_modo == "atr":
                stop = max(stop, entrada + a * cfg.sl_atr)
            objetivo = entrada - (stop - entrada) * cfg.rr
            coherente = objetivo < entrada < stop

        riesgo_pct = abs(entrada - stop) / entrada * 100
        if not coherente or not (cfg.riesgo_min_pct <= riesgo_pct <= cfg.riesgo_max_pct):
            continue

        elegido.usado = True
        ultima_entrada = i
        salida.append(
            Senal(
                indice=i,
                tiempo=v.tiempo,
                accion="BUY" if long_ok else "SELL",
                entrada=entrada,
                stop=stop,
                objetivo=objetivo,
                riesgo_pct=riesgo_pct,
                zona_top=elegido.top,
                zona_bot=elegido.bot,
            )
        )

    return salida


def senal_actual(velas, cfg: Config = None):
    """Devuelve la senal de la ULTIMA vela cerrada, o None."""
    todas = senales(velas, cfg)
    if not todas:
        return None
    ultima = todas[-1]
    return ultima if ultima.indice == len(velas) - 1 else None


def _buscar_origen(velas, i, alcista, maximo):
    """Offset de la ultima vela contraria al impulso (el Order Block)."""
    for off in range(1, maximo + 1):
        j = i - off
        if j < 0:
            return None
        if alcista and velas[j].bajista:
            return off
        if not alcista and velas[j].alcista:
            return off
    return None


def _validar(velas, i, k, alcista, atr_i, cfg: Config):
    """Filtros de calidad del bloque: desplazamiento e imbalance."""
    if cfg.exigir_desplazamiento:
        apertura_impulso = velas[i - (k - 1)].apertura
        impulso = (
            velas[i].cierre - apertura_impulso
            if alcista
            else apertura_impulso - velas[i].cierre
        )
        if impulso < atr_i * cfg.desplazamiento_atr:
            return False

    if cfg.exigir_fvg:
        if k < 2:
            return False
        hay = False
        for j in range(k, 1, -1):  # j = k .. 2
            if i - j < 0 or i - (j - 2) >= len(velas):
                continue
            a_, b_ = velas[i - j], velas[i - (j - 2)]
            if alcista and b_.minimo > a_.maximo:
                hay = True
                break
            if not alcista and b_.maximo < a_.minimo:
                hay = True
                break
        if not hay:
            return False

    return True
