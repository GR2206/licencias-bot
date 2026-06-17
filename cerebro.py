import pandas as pd
import pandas_ta as ta
from velas import identificar_patrones
import requests
import textwrap
from colorama import Fore, Style, init
init(autoreset=True)
COL_ACTIVO = 12
LOG_MSG_WIDTH = 48


def color_log(mensaje: str):
    mensaje_lower = mensaje.lower()

    if mensaje.startswith(("❌", "🚫")):
        return Style.BRIGHT + Fore.RED

    if any(palabra in mensaje_lower for palabra in [
        "bloqueado",
        "demasiado grande",
        "demasiado pequeño",
        "volumen bajo",
        "volatilidad baja",
        "adx débil",
        "vela indecisión",
    ]):
        return Style.BRIGHT + Fore.RED

    if any(palabra in mensaje_lower for palabra in [
        "confirmado",
        "protegido correctamente",
        "trade abierto",
    ]):
        return Style.BRIGHT + Fore.GREEN

    if mensaje.startswith(("⚠️", "⏳")):
        return Style.BRIGHT + Fore.YELLOW

    return Fore.WHITE


def log_activo(simbolo: str, mensaje: str, primera_linea: bool = False):

    mensaje = str(mensaje)
    color = color_log(mensaje)
    lineas = textwrap.wrap(
        mensaje,
        width=LOG_MSG_WIDTH,
        break_long_words=False,
        replace_whitespace=False
    ) or [""]

    for i, linea in enumerate(lineas):
        etiqueta = simbolo if primera_linea and i == 0 else ""
        print(f"{etiqueta:<{COL_ACTIVO}} | {color}{linea}{Style.RESET_ALL}")

# =================================
# CONFIGURACIÓN GENERAL
# =================================

RR_BASE = 2.8

MODO = "CONSERVADOR"   # "AGRESIVO" o "CONSERVADOR"

SCORE_BASE = 0.8

# ===============================
# CONFIGURACIÓN DEL SISTEMA
# ===============================

RR_MINIMO = 1.5
RR_NORMAL = 2.2
RR_SCALP = 1.7
ATR_MULTIPLICADOR_SL = 2.2
SL_MINIMO_PCT = 0.006
SL_MAXIMO_PCT = 0.025
RIESGO_MAXIMO_PCT = 0.02
OPORTUNIDAD_SCORE_MIN = 2.6
OPORTUNIDAD_VOTOS_MIN = 3
OPORTUNIDAD_SL_MAX_PCT = 0.018
OPORTUNIDAD_RR = 2.5

SIMBOLOS_PREFERENTES = {
    "CHZUSDT",
    "FILUSDT",
    "ETHUSDT",
    "XMRUSDT",
    "INJUSDT",
    "SOLUSDT",
}

SIMBOLOS_EXIGENTES = {
    "OPUSDT",
    "NEARUSDT",
    "LINKUSDT",
    "DOTUSDT",
}

SIMBOLOS_CUIDADO = {
    "BTCUSDT",
}

PATRONES_FUERTES_ALCISTAS = {
    "PINBAR_ALCISTA",
    "MARTILLO_ALCISTA",
    "MARTILLO_INVERTIDO_ALCISTA",
    "ENVOLVENTE_ALCISTA",
    "PIERCING_LINE_ALCISTA",
    "TWEEZER_BOTTOM_ALCISTA",
    "ESTRELLA_MAÑANA",
    "TRES_SOLDADOS",
    "MARUBOZU_ALCISTA",
}

PATRONES_FUERTES_BAJISTAS = {
    "PINBAR_BAJISTA",
    "HOMBRE_COLGADO_BAJISTA",
    "ESTRELLA_FUGAZ_BAJISTA",
    "ENVOLVENTE_BAJISTA",
    "DARK_CLOUD_COVER_BAJISTA",
    "TWEEZER_TOP_BAJISTA",
    "ESTRELLA_ATARDECER",
    "TRES_CUERVOS_NEGROS",
    "MARUBOZU_BAJISTA",
}

PATRONES_FUERTES = PATRONES_FUERTES_ALCISTAS | PATRONES_FUERTES_BAJISTAS


def perfil_simbolo(simbolo):
    if simbolo in SIMBOLOS_PREFERENTES:
        return "PREFERENTE"
    if simbolo in SIMBOLOS_EXIGENTES:
        return "EXIGENTE"
    if simbolo in SIMBOLOS_CUIDADO:
        return "CUIDADO"
    return "NORMAL"


def patrones_fuertes_para_accion(patrones, accion):
    patrones_norm = {str(p).upper() for p in patrones}
    if accion == "LONG":
        return sorted(patrones_norm & PATRONES_FUERTES_ALCISTAS)
    if accion == "SHORT":
        return sorted(patrones_norm & PATRONES_FUERTES_BAJISTAS)
    return []


def rsi_saliendo_zona_extrema(df_m15, accion):
    try:
        if "rsi" in df_m15.columns:
            rsi = df_m15["rsi"]
        else:
            rsi = ta.rsi(df_m15["close"], length=14)

        actual = float(rsi.iloc[-1])
        previa = float(rsi.iloc[-2])
        previa_2 = float(rsi.iloc[-3])

        if accion == "LONG":
            estuvo_extremo = min(previa_2, previa, actual) <= 35
            esta_saliendo = actual > previa and actual >= 30
            return estuvo_extremo and esta_saliendo

        if accion == "SHORT":
            estuvo_extremo = max(previa_2, previa, actual) >= 65
            esta_saliendo = actual < previa and actual <= 70
            return estuvo_extremo and esta_saliendo

    except Exception:
        pass

    return False


def excepcion_volumen_bajo(
    decision,
    resultados_filtrados,
    df_m15,
    score_ensemble,
    votos_decision,
    distancia_sl,
):
    accion = decision.get("accion")
    patrones = identificar_patrones(df_m15)
    patrones_fuertes = patrones_fuertes_para_accion(patrones, accion)

    tiene_sweep = any(
        r for r in resultados_filtrados
        if r and r.get("modelo") == "LIQUIDITY_SWEEP" and r.get("accion") == accion
    )
    tiene_mss = any(
        r for r in resultados_filtrados
        if r and r.get("modelo") == "MSS" and r.get("accion") == accion
    )

    score_ok = score_ensemble >= 2.4
    votos_ok = votos_decision >= 3
    patron_ok = bool(patrones_fuertes)
    rsi_ok = rsi_saliendo_zona_extrema(df_m15, accion)
    sl_ok = SL_MINIMO_PCT <= distancia_sl <= min(0.018, SL_MAXIMO_PCT)
    confirmaciones_extra = sum([patron_ok, rsi_ok, tiene_sweep, tiene_mss])

    decision["patrones_vela"] = patrones
    decision["patrones_fuertes"] = patrones_fuertes
    decision["volumen_bajo_excepcion"] = (
        score_ok
        and votos_ok
        and sl_ok
        and confirmaciones_extra >= 2
    )

    decision["volumen_bajo_detalle"] = {
        "score_ok": score_ok,
        "votos_ok": votos_ok,
        "patron_ok": patron_ok,
        "rsi_ok": rsi_ok,
        "sweep_ok": tiene_sweep,
        "mss_ok": tiene_mss,
        "sl_ok": sl_ok,
    }

    return decision["volumen_bajo_excepcion"]


def score_y_votos(resultados_filtrados, accion):
    score = sum(
        r["score"]
        for r in resultados_filtrados
        if r and r.get("accion") == accion
    )
    votos = sum(
        1
        for r in resultados_filtrados
        if r and r.get("accion") == accion
    )
    return score, votos


def es_oportunidad_controlada(decision, resultados_filtrados, score_ensemble, votos_decision):
    accion = decision.get("accion")
    modelos = {
        r.get("modelo")
        for r in resultados_filtrados
        if r and r.get("accion") == accion
    }

    tiene_mss = "MSS" in modelos
    modelos_impulso = modelos & {
        "BREAKOUT",
        "VOL_EXPANSION",
        "VWAP_RECLAIM",
        "LIQUIDITY_SWEEP",
        "LIQUIDITY_VOID",
    }
    tiene_impulso = bool(modelos_impulso)

    return (
        score_ensemble >= OPORTUNIDAD_SCORE_MIN
        and votos_decision >= OPORTUNIDAD_VOTOS_MIN
        and (
            (tiene_mss and tiene_impulso)
            or (votos_decision >= 4 and len(modelos_impulso) >= 2)
        )
    )


def activar_oportunidad_controlada(decision, motivo, score_ensemble, votos_decision):
    decision["modo_oportunidad_controlada"] = True
    decision["modo_contra"] = True
    decision["rr_objetivo"] = OPORTUNIDAD_RR
    decision["max_margen_pct"] = 0.08
    decision["riesgo_real_override"] = 0.008
    decision["sl_max_pct_override"] = OPORTUNIDAD_SL_MAX_PCT
    decision["motivos"] = decision.get("motivos", []) + [
        f"oportunidad controlada: {motivo}",
        f"score ensemble {score_ensemble:.2f} / votos {votos_decision}",
    ]

# =================================
# CONFIGURACIÓN SCORE
# =================================

def score_minimo(contexto):

    base = SCORE_BASE

    if MODO == "AGRESIVO":
        base -= 0.05

    if contexto == "RANGO":
        base += 0.05

    if contexto == "TENDENCIA_ALCISTA" or contexto == "TENDENCIA_BAJISTA":
        base -= 0.02

    return round(base, 2)

# ===============================
# OBTENER FUNDING RATE
# ===============================

def obtener_funding_rate(simbolo):

    try:

        url = f"https://fapi.binance.com/fapi/v1/premiumIndex?symbol={simbolo}"

        r = requests.get(url, timeout=3)

        data = r.json()

        funding = float(data["lastFundingRate"])

        return funding

    except:

        return 0

# ===============================
# CONFIGURACIÓN DEL ORDER BLOCK
# ===============================

def detectar_order_block_alcista(df):

    for i in range(-5, -1):
        cuerpo = abs(df['close'].iloc[i] - df['open'].iloc[i])
        rango = df['high'].iloc[i] - df['low'].iloc[i]

        if df['close'].iloc[i] < df['open'].iloc[i] and cuerpo > rango * 0.6:
            return df['low'].iloc[i], df['high'].iloc[i]

    return None


def detectar_order_block_bajista(df):

    for i in range(-5, -1):
        cuerpo = abs(df['close'].iloc[i] - df['open'].iloc[i])
        rango = df['high'].iloc[i] - df['low'].iloc[i]

        if df['close'].iloc[i] > df['open'].iloc[i] and cuerpo > rango * 0.6:
            return df['low'].iloc[i], df['high'].iloc[i]

    return None

# ===============================
# CONFIGURACIÓN MODELO DE ORDER BLOCK
# ===============================

def modelo_orderblock(df_h1, df_m15, simbolo, contexto):

    score = 0
    motivos = []

    ob = detectar_order_block_alcista(df_h1)

    # 🔹 ORDER BLOCK BAJISTA
    ob_bajista = detectar_order_block_bajista(df_h1)

    if ob_bajista:

        low_ob, high_ob = ob_bajista
        precio_actual = df_m15["close"].iloc[-1]

        if low_ob <= precio_actual <= high_ob:
            score = 0.50
            log_activo(simbolo, "🧱 Order Block bajista")

            return {
                "accion": "SHORT",
                "modelo": "ORDER_BLOCK",
                "score": score,
                "motivos": ["order block bajista"]
            }

    if ob and contexto == "TENDENCIA_ALCISTA":

        low_ob, high_ob = ob
        precio_actual = df_m15['close'].iloc[-1]

        if low_ob <= precio_actual <= high_ob:
            score += 0.40
            motivos.append("Retesteo Order Block")

            if df_m15['close'].iloc[-1] > df_m15['open'].iloc[-1]:
                score += 0.30
                motivos.append("Rechazo alcista")

            vol_media = df_m15['volume'].rolling(20).mean().iloc[-1]
            if df_m15['volume'].iloc[-1] > vol_media:
                score += 0.20
                motivos.append("Volumen confirmación")

    if score >= 0.70:
        # dirección según contexto real del mercado
        if contexto == "TENDENCIA_BAJISTA":
            accion_cont = "SHORT"
        else:
            accion_cont = "LONG"
        return {
            "modelo": "CONTINUACION",
            "accion": accion_cont,
            "score": score,
            "motivos": motivos
        }

    return None

# ===============================
# MODELO FUNDING IMBALANCE
# ===============================

def modelo_funding_imbalance(simbolo):

    try:

        funding = obtener_funding_rate(simbolo)

        # demasiados longs
        if funding > 0.0008:

            log_activo(simbolo, "💰 Funding imbalance short")

            return {
                "modelo": "FUNDING_IMBALANCE",
                "accion": "SHORT",
                "score": 0.75,
                "motivos": ["exceso longs"]
            }

        # demasiados shorts
        if funding < -0.0008:

            log_activo(simbolo, "💰 Funding imbalance long")

            return {
                "modelo": "FUNDING_IMBALANCE",
                "accion": "LONG",
                "score": 0.75,
                "motivos": ["exceso shorts"]
            }

    except:
        pass

    return None

# ===============================
# MODELO MSS
# ===============================

def modelo_mss(simbolo, df_h1, df_m15):

    try:

        score = 0
        motivos = []

        high_prev = df_m15["high"].iloc[-10:-3].max()  # máximo de las últimas 10 velas (excluyendo las 3 más recientes)
        low_prev = df_m15["low"].iloc[-10:-3].min()

        high_now = df_m15["high"].iloc[-1]
        low_now = df_m15["low"].iloc[-1]

        close_now = df_m15["close"].iloc[-1]

        # ruptura alcista de estructura
        if high_now > high_prev and close_now > high_prev:

            score += 0.6
            motivos.append("ruptura estructura alcista")

            # confirmación volumen
            vol_media = df_m15["volume"].rolling(20).mean().iloc[-1]

            if df_m15["volume"].iloc[-1] > vol_media:
                score += 0.25
                motivos.append("volumen confirmación")

            log_activo(simbolo, "🔄 Market Structure Shift alcista")

            return {
                "modelo": "MSS",
                "accion": "LONG",
                "score": score,
                "motivos": motivos
            }

        # ruptura bajista de estructura
        if low_now < low_prev and close_now < low_prev:

            score += 0.6
            motivos.append("ruptura estructura bajista")

            vol_media = df_m15["volume"].rolling(20).mean().iloc[-1]

            if df_m15["volume"].iloc[-1] > vol_media:
                score += 0.25
                motivos.append("volumen confirmación")

            log_activo(simbolo, "🔄 Market Structure Shift bajista")

            return {
                "modelo": "MSS",
                "accion": "SHORT",
                "score": score,
                "motivos": motivos
            }

        return None

    except Exception as e:

        print(f"{simbolo} error modelo MSS: {e}")

        return None
    
# ===============================
# DETECCION DE LIQUIDEZ
# ===============================

def sweep_liquidez_alcista(df):
    return df['high'].iloc[-1] > df['high'].iloc[-2] and \
           df['close'].iloc[-1] < df['high'].iloc[-2]

def sweep_liquidez_bajista(df):
    return df['low'].iloc[-1] < df['low'].iloc[-2] and \
           df['close'].iloc[-1] > df['low'].iloc[-2]

# ===============================
# MODELO DE LIQUIDEZ
# ===============================

def modelo_liquidez(df_h1, df_m15, contexto, simbolo):

    try:

        high_prev = df_m15["high"].iloc[-2]
        high_now = df_m15["high"].iloc[-1]
        close_now = df_m15["close"].iloc[-1]

        low_prev = df_m15["low"].iloc[-2]
        low_now = df_m15["low"].iloc[-1]

        # sweep arriba → short
        if high_now > high_prev and close_now < high_prev:

            log_activo(simbolo, "💧 Liquidity sweep bajista")

            return {
                "modelo": "LIQUIDITY_SWEEP",
                "accion": "SHORT",
                "score": 0.55,
                "motivos": ["barrida liquidez arriba"]
            }

        # sweep abajo → long
        if low_now < low_prev and close_now > low_prev:

            log_activo(simbolo, "💧 Liquidity sweep alcista")

            return {
                "modelo": "LIQUIDITY_SWEEP",
                "accion": "LONG",
                "score": 0.55,
                "motivos": ["barrida liquidez abajo"]
            }

    except:
        pass

    return None

# ===============================
# MODELO DE RANGUE TRAP
# ===============================

def modelo_range_trap(simbolo, df):

    try:

        high_range = df["high"].rolling(20).max().iloc[-2]

        close = df["close"].iloc[-1]

        if df["high"].iloc[-1] > high_range and close < high_range:

            log_activo(simbolo, "🪤 Liquidity trap")

            return {
                "modelo": "RANGE_TRAP",
                "accion": "SHORT",
                "score": 0.8,
                "motivos": ["fake breakout"]
            }

    except:
        pass

    return None

# ===============================
# MODELO DE VOLATILITY EXPANSION
# ===============================

def modelo_volatility_expansion(simbolo, df):

    try:

        rango = (df["high"] - df["low"]).rolling(20).mean().iloc[-1]

        vela = df["high"].iloc[-1] - df["low"].iloc[-1]

        if vela > rango * 2:

            log_activo(simbolo, "⚡️ Volatility expansion")

            es_verde = df["close"].iloc[-1] > df["open"].iloc[-1]
            accion = "LONG" if es_verde else "SHORT"
            return {
                "modelo": "VOL_EXPANSION",
                "accion": accion,
                "score": 0.8,
                "motivos": ["explosión volatilidad"]
            }

    except:
        pass

    return None

# ===============================
# MODELO DE LIQUIDITY VOID
# ===============================

def modelo_liquidity_void(simbolo, df):

    try:

        high_1 = df["high"].iloc[-3]
        low_1 = df["low"].iloc[-3]

        high_3 = df["high"].iloc[-1]
        low_3 = df["low"].iloc[-1]

        fvg = detectar_fvg(df)
        precio = df["close"].iloc[-1]

        if fvg == "ALCISTA":
            gap = low_3 - high_1
            accion = "LONG"
        elif fvg == "BAJISTA":
            gap = low_1 - high_3
            accion = "SHORT"
        else:
            return None

        # Solo aceptar voids reales: gap positivo entre vela 1 y vela 3.
        if gap > 0 and gap > precio * 0.002:

            log_activo(simbolo, f"🌀 Liquidity void {fvg.lower()}")

            return {
                "modelo": "LIQUIDITY_VOID",
                "accion": accion,
                "score": 0.7,
                "motivos": [f"gap liquidez {fvg.lower()}"]
            }

    except:
        pass

    return None

# ===============================
# MODELO DE VWAP RECLAIM
# ===============================

def modelo_vwap_reclaim(simbolo, df):

    try:

        vwap = ta.vwap(df["high"], df["low"], df["close"], df["volume"])

        precio = df["close"].iloc[-1]

        if precio > vwap.iloc[-1] and df["close"].iloc[-2] < vwap.iloc[-2]:

            log_activo(simbolo, "📈 VWAP reclaim")

            return {
                "modelo": "VWAP_RECLAIM",
                "accion": "LONG",
                "score": 0.8,
                "motivos": ["recuperación VWAP"]
            }

    except:
        pass

    return None


# ===============================
# DETECTAR CONTEXTO
# ===============================

def detectar_contexto(df_h1):

    ema50 = ta.ema(df_h1['close'], length=50)
    ema200 = ta.ema(df_h1['close'], length=200)
    atr = ta.atr(df_h1['high'], df_h1['low'], df_h1['close'], length=14)

    atr_media = atr.rolling(20).mean()

    if ema50.iloc[-1] > ema200.iloc[-1] and atr.iloc[-1] > atr_media.iloc[-1]:
        return "TENDENCIA_ALCISTA"

    if ema50.iloc[-1] < ema200.iloc[-1] and atr.iloc[-1] > atr_media.iloc[-1]:
        return "TENDENCIA_BAJISTA"

    return "RANGO"

# ===============================
# MODELO DE MEAN REVERSION
# ===============================

def modelo_mean_reversion(simbolo, df, contexto):

    try:

        ema50 = ta.ema(df["close"], length=50)

        precio = df["close"].iloc[-1]

        desviacion = (precio - ema50.iloc[-1]) / ema50.iloc[-1]

        rsi = ta.rsi(df["close"], length=14).iloc[-1]

        # sobrecompra
        if desviacion > 0.02 and rsi > 75 and contexto != "TENDENCIA_ALCISTA":

            log_activo(simbolo, "🔁 Mean reversion short")

            return {
                "modelo": "MEAN_REVERSION",
                "accion": "SHORT",
                "score": 0.75,
                "motivos": ["sobre extensión alcista"]
            }

        # sobreventa
        if desviacion < -0.02 and rsi < 25 and contexto != "TENDENCIA_BAJISTA":

            log_activo(simbolo, "🔁 Mean reversion long")

            return {
                "modelo": "MEAN_REVERSION",
                "accion": "LONG",
                "score": 0.75,
                "motivos": ["sobre extensión bajista"]
            }

    except:
        pass

    return None

# ===============================
# FAIR VALUE GAP / LIQUIDITY VOID
# ===============================

def detectar_fvg(df):

    try:

        high_1 = df["high"].iloc[-3]
        low_1 = df["low"].iloc[-3]

        high_3 = df["high"].iloc[-1]
        low_3 = df["low"].iloc[-1]

        # FVG alcista
        if low_3 > high_1:
            return "ALCISTA"

        # FVG bajista
        if high_3 < low_1:
            return "BAJISTA"

        return None

    except:
        return None

# ===============================
#  DETECCIÓN DE DIVERGENCIAS
# ===============================

def detectar_divergencia_bajista(df):

    rsi = ta.rsi(df['close'], length=14)
    macd = ta.macd(df['close'])['MACDh_12_26_9']

    precio_hh = df['high'].iloc[-1] > df['high'].iloc[-5]
    rsi_lh = rsi.iloc[-1] < rsi.iloc[-5]
    macd_lh = macd.iloc[-1] < macd.iloc[-5]

    if precio_hh and (rsi_lh or macd_lh):
        return True

    return False


def detectar_divergencia_alcista(df):

    rsi = ta.rsi(df['close'], length=14)
    macd = ta.macd(df['close'])['MACDh_12_26_9']

    precio_ll = df['low'].iloc[-1] < df['low'].iloc[-5]
    rsi_hl = rsi.iloc[-1] > rsi.iloc[-5]
    macd_hl = macd.iloc[-1] > macd.iloc[-5]

    if precio_ll and (rsi_hl or macd_hl):
        return True

    return False

# ===============================
#  MODELO REVERSAL (DIVERGENCIA)
# ===============================

def modelo_reversal(simbolo, df_h1, contexto):

    score = 0
    motivos = []

    if detectar_divergencia_bajista(df_h1):
        score += 0.20
        motivos.append("Divergencia bajista")

        if df_h1['close'].iloc[-1] < df_h1['low'].iloc[-2]:
            score += 0.30
            motivos.append("Ruptura estructura")

        if contexto != "TENDENCIA_ALCISTA":
            score += 0.20
            motivos.append("Contexto no alcista")

    # 🔹 DIVERGENCIA ALCISTA
    if detectar_divergencia_alcista(df_h1):
        score += 0.35
        accion = "LONG"
        motivos.append("divergencia alcista")
        log_activo(simbolo, "🔁 Divergencia alcista")

    if score >= 0.70:

        accion = "SHORT"

        if detectar_divergencia_alcista(df_h1):
            accion = "LONG"

        return {
            "modelo": "REVERSAL",
            "accion": accion,
            "score": score,
            "motivos": motivos
        }

    return None

# ===============================
# VOLUMEN INSTITUCIONAL
# ===============================

def volumen_institucional(df):

    media_vol = df['volume'].rolling(20).mean().iloc[-1]
    vol_actual = df['volume'].iloc[-1]

    cuerpo = abs(df['close'].iloc[-1] - df['open'].iloc[-1])
    rango = df['high'].iloc[-1] - df['low'].iloc[-1]

    desplazamiento_fuerte = rango > (df['high'].rolling(20).max().iloc[-1] * 0.005)

    if vol_actual > media_vol * 1.2 and desplazamiento_fuerte:
        return True

    return False
    
# ===============================
# UTILIDADES ESTRUCTURA
# ===============================

def estructura_alcista(df):
    return df['high'].iloc[-1] > df['high'].iloc[-3] and \
           df['low'].iloc[-1] > df['low'].iloc[-3]

def estructura_bajista(df):
    return df['low'].iloc[-1] < df['low'].iloc[-3] and \
           df['high'].iloc[-1] < df['high'].iloc[-3]

def ruptura_alcista(df):
    return df['close'].iloc[-1] > df['high'].iloc[-2]

def ruptura_bajista(df):
    return df['close'].iloc[-1] < df['low'].iloc[-2]

# ===============================
# FRACTALES DE ESTRUCTURA
# ===============================

def fractal_high(df):

    for i in range(-5, -30, -1):
        if df["high"].iloc[i] > df["high"].iloc[i-1] and \
           df["high"].iloc[i] > df["high"].iloc[i+1]:
            return df["high"].iloc[i]

    return df["high"].rolling(10).max().iloc[-1]


def fractal_low(df):

    for i in range(-5, -30, -1):
        if df["low"].iloc[i] < df["low"].iloc[i-1] and \
           df["low"].iloc[i] < df["low"].iloc[i+1]:
            return df["low"].iloc[i]

    return df["low"].rolling(10).min().iloc[-1]

# ===============================
# DETECTOR ACUMULACIÓN INSTITUCIONAL
# ===============================

def detectar_acumulacion(simbolo, df):

    try:

        # rango últimas velas
        rangos = (df["high"] - df["low"]).tail(8)

        rango_prom = rangos.mean()

        # rango total del bloque
        maximo = df["high"].tail(8).max()
        minimo = df["low"].tail(8).min()

        rango_total = maximo - minimo

        # volumen
        vol_media = df["volume"].rolling(20).mean().iloc[-1]
        vol_actual = df["volume"].tail(8).mean()

        # condiciones acumulación
        compresion = rango_total < rango_prom * 6
        volumen_estable = vol_actual >= vol_media * 0.8

        if compresion and volumen_estable:
            log_activo(simbolo, "🏦 Posible acumulación institucional")
            return True

        return False

    except Exception as e:
        print(f"❌Error detector acumulación:", e)
        return False

# ===============================
#  CONFIRMACION MTF
# ===============================

def confirmacion_mtf(df_h1):

    try:
        ema20 = df_h1["EMA20"].iloc[-1]
        ema50 = df_h1["EMA50"].iloc[-1]

        if ema20 > ema50:
            return "ALCISTA"

        if ema20 < ema50:
            return "BAJISTA"

        return "NEUTRO"

    except:
        return "NEUTRO"
    
# ===============================
# FILTRO BTC MACRO
# ===============================

def filtro_btc(df_btc_h4):

    df_btc_h4['ema50'] = ta.ema(df_btc_h4['close'], length=50)
    df_btc_h4['ema200'] = ta.ema(df_btc_h4['close'], length=200)
    rsi = ta.rsi(df_btc_h4['close'], length=14)

    if df_btc_h4['ema50'].iloc[-1] > df_btc_h4['ema200'].iloc[-1] and rsi.iloc[-1] > 50:
        return "ALCISTA"

    if df_btc_h4['ema50'].iloc[-1] < df_btc_h4['ema200'].iloc[-1] and rsi.iloc[-1] < 50:
        return "BAJISTA"

    return "NEUTRO"

# ===============================
# CÁLCULO SL DINÁMICO
# ===============================

def calcular_sl(simbolo, df_h1, accion):

    try:

        atr = ta.atr(
            df_h1['high'],
            df_h1['low'],
            df_h1['close'],
            length=14
        ).iloc[-1]

        precio = df_h1["close"].iloc[-1]

        if accion == "LONG":

            swing_low = fractal_low(df_h1)

            sl = swing_low - (atr * ATR_MULTIPLICADOR_SL)

        elif accion == "SHORT":

            swing_high = fractal_high(df_h1)

            sl = swing_high + (atr * ATR_MULTIPLICADOR_SL)

        else:
            return None

        distancia = abs(precio - sl) / precio

        # SL mínimo 1%
        if distancia < 0.01:
            sl = precio * (0.99 if accion == "LONG" else 1.01)
            distancia = abs(precio - sl) / precio

        log_activo(simbolo, f"🛑 SL calculado: {sl:.6f} ({distancia*100:.2f}%)")

        return sl

    except:

        return None

# ===============================
# CÁLCULO SL M15 DE RESCATE
# ===============================

def calcular_sl_m15_rescate(simbolo, df_m15, accion):

    try:

        atr = ta.atr(
            df_m15['high'],
            df_m15['low'],
            df_m15['close'],
            length=14
        ).iloc[-1]

        precio = df_m15["close"].iloc[-1]

        if accion == "LONG":
            swing = df_m15["low"].tail(12).min()
            sl = swing - (atr * 1.2)
        elif accion == "SHORT":
            swing = df_m15["high"].tail(12).max()
            sl = swing + (atr * 1.2)
        else:
            return None

        distancia = abs(precio - sl) / precio

        if distancia < SL_MINIMO_PCT:
            sl = precio * (
                1 - SL_MINIMO_PCT if accion == "LONG" else 1 + SL_MINIMO_PCT
            )
            distancia = abs(precio - sl) / precio

        log_activo(simbolo, f"🛟 SL M15 rescate: {sl:.6f} ({distancia*100:.2f}%)")

        return sl

    except Exception as e:

        print(f"{simbolo} error SL M15 rescate: {e}")

        return None

# ===============================
#  MODELO BREAKEOUT
# ===============================

def modelo_breakout(simbolo, df):

    score = 0
    motivos = []

    try:

        rango = df["high"].rolling(10).max().iloc[-2] - df["low"].rolling(10).min().iloc[-2]

        vela_actual = df["high"].iloc[-1] - df["low"].iloc[-1]

        if vela_actual > rango * 0.25:

            score += 0.4
            motivos.append("expansión volatilidad")

        if df["close"].iloc[-1] > df["high"].iloc[-2]:

            score += 0.35
            motivos.append("ruptura resistencia")

        vol_media = df["volume"].rolling(20).mean().iloc[-1]

        if df["volume"].iloc[-1] > vol_media * 1.5:

            score += 0.25
            motivos.append("volumen explosivo")

        if score >= 0.70:

            log_activo(simbolo, "🚀 Breakout institucional")

            return {
                "modelo": "BREAKOUT",
                "accion": "LONG",
                "score": score,
                "motivos": motivos
            }

    except:
        pass

    return None

# ===============================
#  MODELO CONTINUACIÓN
# ===============================

def modelo_continuacion(simbolo, df_h1, df_m15, contexto):

    score = 0
    motivos = []

    # 🔹 DETECTOR ACUMULACIÓN
    try:
        if detectar_acumulacion(simbolo, df_m15):
            score += 0.20
            motivos.append("acumulación institucional")
    except:
        pass

    # 🔹 CONFIRMACIÓN PATRONES DE VELAS
    try:
        patrones = identificar_patrones(df_m15)

        if patrones:

            patrones_norm = {str(p).upper() for p in patrones}

            if patrones_norm & PATRONES_FUERTES_ALCISTAS:
                score += 0.20
                motivos.append("patron vela alcista")
                log_activo(simbolo, "✅ Patrón vela alcista")

            if patrones_norm & PATRONES_FUERTES_BAJISTAS:
                score += 0.20
                motivos.append("patron vela bajista")
                log_activo(simbolo, "✅ Patrón vela bajista")

    except Exception as e:
        print(f"{simbolo} ❌ Error patrones velas:", e)

    # 🔹 CONFIRMACIÓN VOLUMEN INSTITUCIONAL
    try:
        if volumen_institucional(df_m15):
            score += 0.15
            motivos.append("volumen institucional")
            log_activo(simbolo, "🏦 Volumen institucional")
    except:
        pass

    ema20 = ta.ema(df_h1['close'], length=20)
    ema50 = ta.ema(df_h1['close'], length=50)
    rsi = ta.rsi(df_h1['close'], length=14)

    if ema20.iloc[-1] > ema50.iloc[-1]:
        score += 0.25
        motivos.append("EMA alcistas")

    if df_h1['close'].iloc[-1] > df_h1['high'].iloc[-2]:
        score += 0.25
        motivos.append("Ruptura confirmada")

    vol_media = df_m15['volume'].rolling(20).mean().iloc[-1]
    if df_m15['volume'].iloc[-1] > vol_media * 1.2:
        score += 0.20
        motivos.append("Volumen fuerte")

    if rsi.iloc[-1] > 55:
        score += 0.15
        motivos.append("RSI fuerte")

    # 🔹 CONFIRMACIÓN ESTRUCTURA MERCADO
    try:
        if estructura_alcista(df_m15):
            score += 0.15
            motivos.append("estructura alcista")
            log_activo(simbolo, "📈 Estructura alcista")
    except:
        pass

    if contexto == "TENDENCIA_ALCISTA":
        score += 0.15
        motivos.append("Contexto favorable")

    # ===============================
    # CONFIRMACION FVG
    # ===============================

    fvg = detectar_fvg(df_m15)

    if fvg == "ALCISTA":
        score += 0.15
        motivos.append("Fair Value Gap alcista")
        log_activo(simbolo, "💠 FVG alcista detectado")

    if score >= 0.70:
        return {
            "modelo": "CONTINUACION",
            "accion": "LONG",
            "score": score,
            "motivos": motivos
        }

    return None

# ===============================
# COMBINADOR DE MODELOS
# ===============================

def combinar_modelos(simbolo, resultados, df_h1):

    validos = [r for r in resultados if r is not None]

    if not validos:
        return {"accion": "ESPERAR"}

    # matriz de scores
    score_long = 0
    score_short = 0

    for r in validos:

        if r["accion"] == "LONG":
            score_long += r["score"]

        if r["accion"] == "SHORT":
            score_short += r["score"]

    # ── FILTRO CONFLUENCIA MÍNIMA ──────────────────
    votos_long = sum(1 for r in validos if r["accion"] == "LONG")
    votos_short = sum(1 for r in validos if r["accion"] == "SHORT")

    if score_long > score_short and score_long > 1.35:        
        candidatos = [r for r in validos if r["accion"] == "LONG"]
        mejor = max(candidatos, key=lambda x: x["score"])
        log_activo(simbolo, f"🧠 Ensemble LONG | Score {round(score_long,3)} | Votos {votos_long}")
        return mejor

    if score_short > score_long and score_short > 1.35:
        candidatos = [r for r in validos if r["accion"] == "SHORT"]
        mejor = max(candidatos, key=lambda x: x["score"])
        log_activo(simbolo, f"🧠 Ensemble SHORT | Score {round(score_short,3)} | Votos {votos_short}")
        return mejor

    return {"accion": "ESPERAR"}

# ===============================
# FUNCIÓN PRINCIPAL
# ===============================

def analizar(simbolo, df_h1, estado_mercado, df_m15, df_btc):

    try:

        contexto = detectar_contexto(df_h1)

        resultados = []

        # ===============================
        # MODELOS PRINCIPALES
        # ===============================

        resultados.append(modelo_continuacion(simbolo, df_h1, df_m15, contexto))

        resultados.append(modelo_reversal(simbolo, df_h1, contexto))

        resultados.append(modelo_liquidez(df_h1, df_m15, contexto, simbolo))

        resultados.append(modelo_orderblock(df_h1, df_m15, simbolo, contexto))

        # MODELOS DE EXPANSIÓN

        resultados.append(modelo_breakout(simbolo, df_m15))

        resultados.append(modelo_volatility_expansion(simbolo, df_m15))

        # MODELOS DE REVERSIÓN

        resultados.append(modelo_mean_reversion(simbolo, df_m15, contexto))

        resultados.append(modelo_range_trap(simbolo, df_m15))

        # MODELOS DE LIQUIDEZ

        resultados.append(modelo_liquidity_void(simbolo, df_m15))

        resultados.append(modelo_vwap_reclaim(simbolo, df_m15))

        # MODELO MACRO DERIVADOS

        resultados.append(modelo_funding_imbalance(simbolo))

        resultados.append(modelo_mss(simbolo, df_h1, df_m15))

        # =============================
        # ELEGIR MEJOR MODELO
        # =============================

        # =================================
        # FILTRO POR CONTEXTO (CLAVE)
        # =================================

        resultados_filtrados = []

        for r in resultados:
            if r is None:
                continue

            # En tendencia alcista → evitar shorts débiles
            if contexto == "TENDENCIA_ALCISTA" and r["accion"] == "SHORT":
                if r["score"] < 0.9:
                    continue

            # En tendencia bajista → evitar longs débiles
            if contexto == "TENDENCIA_BAJISTA" and r["accion"] == "LONG":
                if r["score"] < 0.9:
                    continue

            resultados_filtrados.append(r)

        decision = combinar_modelos(simbolo, resultados_filtrados, df_h1)

        if decision["accion"] == "ESPERAR":
            return decision
        
        # =================================
        # FILTRO SOPORTE / RESISTENCIA INTELIGENTE
        # =================================
        
        soporte = df_h1["low"].rolling(20).min().iloc[-1]
        resistencia = df_h1["high"].rolling(20).max().iloc[-1]
        precio = df_h1["close"].iloc[-1]

        # detectar breakout real
        breakout = precio > resistencia * 1.002

        # evitar vender en soporte
        if decision["accion"] == "SHORT" and precio <= soporte * 1.01:
            log_activo(simbolo, "❌ SHORT en soporte")
            return {"accion": "ESPERAR"}

        # evitar comprar en resistencia SIN breakout
        if decision["accion"] == "LONG" and not breakout:

            if precio >= resistencia * 0.995 and decision["score"] < 3.0:

                tiene_breakout_model = any(

                    r for r in resultados_filtrados

                    if r and r.get("modelo") in [
                        "BREAKOUT",
                        "MSS",
                        "VOL_EXPANSION"
                    ]
                )

                if not tiene_breakout_model:

                    log_activo(
                        simbolo,
                        "❌ LONG en resistencia sin confirmación"
                    )

                    return {"accion": "ESPERAR"}

        # =============================
        # FILTROS CONFIRMACIÓN
        # =============================

        accion_decision = decision.get("accion")
        score_ensemble, votos_decision = score_y_votos(
            resultados_filtrados,
            accion_decision
        )

        decision["score_ensemble"] = round(score_ensemble, 4)
        decision["votos_decision"] = votos_decision
        perfil_activo = perfil_simbolo(simbolo)
        decision["perfil_simbolo"] = perfil_activo

        # ============================
        # FILTRO BTC INTELIGENTE
        # ============================

        correccion_valida = False

        try:

            ema20_btc = ta.ema(df_btc["close"], length=20).iloc[-1]
            ema50_btc = ta.ema(df_btc["close"], length=50).iloc[-1]

            macro_btc = "ALCISTA" if ema20_btc > ema50_btc else "BAJISTA"

            # ====================================
            # DETECCIÓN DE CORRECCIÓN REAL
            # ====================================

            if decision["accion"] == "SHORT":

                if (
                    any(r for r in resultados_filtrados if r and r["modelo"] == "LIQUIDITY_SWEEP")
                    and any(r for r in resultados_filtrados if r and r["modelo"] == "MSS")
                ):

                    correccion_valida = True

            if decision["accion"] == "LONG":

                if (
                    any(r for r in resultados_filtrados if r and r["modelo"] == "LIQUIDITY_SWEEP")
                    and any(r for r in resultados_filtrados if r and r["modelo"] == "MSS")
                ):

                    correccion_valida = True

            # ====================================
            # BLOQUEO POR MACRO BTC
            # ====================================

            if macro_btc == "ALCISTA" and decision["accion"] == "SHORT":

                if not correccion_valida:

                    if es_oportunidad_controlada(
                        decision,
                        resultados_filtrados,
                        score_ensemble,
                        votos_decision
                    ):
                        activar_oportunidad_controlada(
                            decision,
                            "SHORT contra BTC alcista sin corrección clásica",
                            score_ensemble,
                            votos_decision
                        )
                        log_activo(simbolo, "⚠️ SHORT permitido como oportunidad controlada")
                    else:
                        log_activo(simbolo, "🚫 SHORT bloqueado (no corrección real)")
                        return {"accion": "ESPERAR"}

                else:

                    decision["modo_contra"] = True
                    log_activo(simbolo, "⚡ SHORT permitido (corrección detectada)")

            if macro_btc == "BAJISTA" and decision["accion"] == "LONG":

                if not correccion_valida:

                    if es_oportunidad_controlada(
                        decision,
                        resultados_filtrados,
                        score_ensemble,
                        votos_decision
                    ):
                        activar_oportunidad_controlada(
                            decision,
                            "LONG contra BTC bajista sin corrección clásica",
                            score_ensemble,
                            votos_decision
                        )
                        log_activo(simbolo, "⚠️ LONG permitido como oportunidad controlada")
                    else:
                        log_activo(simbolo, "🚫 LONG bloqueado (no corrección real)")
                        return {"accion": "ESPERAR"}

                else:

                    decision["modo_contra"] = True
                    log_activo(simbolo, "⚡ LONG permitido (corrección detectada)")

        except Exception as e:

            print(f"Error filtro BTC: {e}")

        # FILTRO ADX
        try:

            if "ADX" in df_m15.columns and not df_m15["ADX"].isna().all():

                adx_actual = df_m15["ADX"].iloc[-1]

            else:

                adx_actual = ta.adx(
                    df_m15["high"],
                    df_m15["low"],
                    df_m15["close"]
                )["ADX_14"].iloc[-1]

            decision["adx_actual"] = round(float(adx_actual), 2)

            if (
                adx_actual < 18
                and score_ensemble < 1.5
                and votos_decision < 2
            ):

                log_activo(
                    simbolo,
                    f"❌ ADX débil ({adx_actual:.1f}) | Score ensemble {score_ensemble:.2f} | Votos {votos_decision}"
                )

                return {"accion": "ESPERAR"}

        except Exception as e:

            print(f"{simbolo} ❌ Error filtro ADX: {e}")

        precio_actual = df_m15["close"].iloc[-1]

        sl = calcular_sl(simbolo, df_h1, decision["accion"])

        if sl is None:
            return {"accion": "ESPERAR"}

        distancia_sl = abs(precio_actual - sl) / precio_actual
        decision["distancia_sl_pct"] = round(distancia_sl * 100, 3)

        log_activo(simbolo, f"🛑 SL distancia: {distancia_sl*100:.2f}%")

        if distancia_sl > SL_MAXIMO_PCT:
            sl_rescate = calcular_sl_m15_rescate(
                simbolo,
                df_m15,
                decision["accion"]
            )
            if sl_rescate is not None:
                distancia_rescate = abs(precio_actual - sl_rescate) / precio_actual
                if SL_MINIMO_PCT <= distancia_rescate <= SL_MAXIMO_PCT:
                    sl = sl_rescate
                    distancia_sl = distancia_rescate
                    decision["sl_rescate_m15"] = True
                    decision["distancia_sl_pct"] = round(distancia_sl * 100, 3)
                    log_activo(simbolo, f"✅ SL H1 reemplazado por M15 ({distancia_sl*100:.2f}%)")

        if perfil_activo == "EXIGENTE":
            if score_ensemble < 2.2 or votos_decision < 3:
                log_activo(
                    simbolo,
                    f"🚫 Símbolo exigente requiere más confluencia ({score_ensemble:.2f}/{votos_decision})"
                )
                return {"accion": "ESPERAR"}

            if distancia_sl > 0.020:
                log_activo(
                    simbolo,
                    f"🚫 Símbolo exigente requiere SL <= 2.0% ({distancia_sl*100:.2f}%)"
                )
                return {"accion": "ESPERAR"}

        if perfil_activo == "CUIDADO":
            if score_ensemble < 2.6 or votos_decision < 3:
                log_activo(
                    simbolo,
                    f"🚫 BTC en modo cuidado requiere score/votos ({score_ensemble:.2f}/{votos_decision})"
                )
                return {"accion": "ESPERAR"}

            if distancia_sl > 0.018:
                log_activo(
                    simbolo,
                    f"🚫 BTC exige SL corto <= 1.8% ({distancia_sl*100:.2f}%)"
                )
                return {"accion": "ESPERAR"}

        # SL demasiado pequeño o demasiado grande para operar con control.
        if distancia_sl < SL_MINIMO_PCT:

            log_activo(simbolo, f"❌ SL demasiado pequeño ({distancia_sl*100:.2f}%)")

            return {"accion": "ESPERAR"}

        if distancia_sl > SL_MAXIMO_PCT:

            log_activo(simbolo, f"❌ SL demasiado grande ({distancia_sl*100:.2f}% > {SL_MAXIMO_PCT*100:.2f}%)")

            return {"accion": "ESPERAR"}

        if (
            decision.get("modo_oportunidad_controlada")
            and distancia_sl > decision.get("sl_max_pct_override", OPORTUNIDAD_SL_MAX_PCT)
        ):

            log_activo(
                simbolo,
                f"❌ oportunidad controlada exige SL corto ({distancia_sl*100:.2f}% > {OPORTUNIDAD_SL_MAX_PCT*100:.2f}%)"
            )

            return {"accion": "ESPERAR"}

        # FILTRO VOLUMEN
        try:

            vol_actual = df_m15["volume"].iloc[-1]
            vol_media = df_m15["volume"].rolling(30).mean().iloc[-1]

            if vol_actual < vol_media * 0.7:
                if excepcion_volumen_bajo(
                    decision,
                    resultados_filtrados,
                    df_m15,
                    score_ensemble,
                    votos_decision,
                    distancia_sl,
                ):
                    log_activo(simbolo, "⚠️ Volumen bajo permitido por confluencia")
                else:
                    detalle = decision.get("volumen_bajo_detalle", {})
                    nombres_faltantes = {
                        "score_ok": "score",
                        "votos_ok": "votos",
                        "patron_ok": "patrón",
                        "rsi_ok": "RSI",
                        "sweep_ok": "sweep",
                        "mss_ok": "MSS",
                        "sl_ok": "SL",
                    }
                    faltantes = [
                        nombres_faltantes.get(nombre, nombre)
                        for nombre, ok in detalle.items()
                        if not ok
                    ]
                    log_activo(
                        simbolo,
                        f"❌ volumen bajo | falta: {', '.join(faltantes[:3])}"
                    )
                    return {"accion": "ESPERAR"}

        except:
            pass

        # FILTRO VOLATILIDAD
        try:

            atr = ta.atr(
                df_m15["high"],
                df_m15["low"],
                df_m15["close"],
                length=14
            ).iloc[-1]

            precio = df_m15["close"].iloc[-1]

            if atr / precio < 0.002:
                log_activo(simbolo, "❌ volatilidad baja")
                return {"accion": "ESPERAR"}

        except:
            pass

        # =============================
        # 🎯 TP DINÁMICO (SCALP vs NORMAL)
        # =============================

        if decision.get("modo_oportunidad_controlada"):

            rr = decision.get("rr_objetivo", OPORTUNIDAD_RR)
            log_activo(simbolo, "🎯 Oportunidad controlada (RR 1:2.5)")

        elif decision.get("modo_contra"):

            rr = RR_SCALP
            log_activo(simbolo, "🎯 Trade de CORRECCIÓN (TP corto)")

        else:

            rr = RR_NORMAL
            log_activo(simbolo, "📈 Trade de TENDENCIA")

        dist = abs(precio_actual - sl)

        if decision["accion"] == "LONG":
            tp = precio_actual + dist * rr
        else:
            tp = precio_actual - dist * rr


        if "patrones_vela" not in decision:
            patrones = identificar_patrones(df_m15)
            decision["patrones_vela"] = patrones
            decision["patrones_fuertes"] = patrones_fuertes_para_accion(
                patrones,
                decision["accion"]
            )

        decision["precio"] = precio_actual
        decision["sl_precio"] = sl
        decision["tp_precio"] = tp

        return decision

    except Exception as e:

        print(f"{simbolo} ❌ Error análisis {simbolo}: {e}")
        
        return {"accion": "ESPERAR"}