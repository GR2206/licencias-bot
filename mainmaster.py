import time
import threading
import os
import textwrap
from datetime import datetime
import pandas as pd
import cerebro
import config
from binance.client import Client
from binance.enums import *
import requests
import telebot
import json
import pandas_ta as ta
from colorama import Fore, Style, init
init(autoreset=True)
from cerebro import filtro_btc
from sniper_ai import sniper_ai
from binance.enums import KLINE_INTERVAL_4HOUR


COL_ACTIVO = 12
LOG_MSG_WIDTH = 48

REGIMEN_CORTO = {
    "TENDENCIA_ALCISTA_FUERTE": "ALCISTA FUERTE",
    "TENDENCIA_BAJISTA_FUERTE": "BAJISTA FUERTE",
    "TENDENCIA_ALCISTA": "ALCISTA",
    "TENDENCIA_BAJISTA": "BAJISTA",
    "VOLATIL_EXPANSION": "VOLATIL",
    "RANGO_COMPRIMIDO": "RANGO COMP.",
    "RANGO": "RANGO",
    "DESCONOCIDO": "DESCONOCIDO",
}

def log_activo(simbolo: str, mensaje: str, primera_linea: bool = False, tipo="info"):

    color = {
        "ok": Fore.GREEN,
        "error": Fore.RED,
        "wait": Fore.YELLOW,
        "info": Fore.CYAN
    }.get(tipo, Fore.WHITE)

    lineas = textwrap.wrap(
        str(mensaje),
        width=LOG_MSG_WIDTH,
        break_long_words=False,
        replace_whitespace=False
    ) or [""]

    for i, linea in enumerate(lineas):
        etiqueta = simbolo if primera_linea and i == 0 else ""
        texto = color + linea + Style.RESET_ALL
        print(f"{etiqueta:<{COL_ACTIVO}} | {texto}")


def log_contexto_ia(simbolo: str, contexto: dict):
    regimen = REGIMEN_CORTO.get(
        contexto.get("regimen", "DESCONOCIDO"),
        contexto.get("regimen", "DESCONOCIDO")
    )
    tendencia = contexto.get("tendencia_pct", 0)
    confianza = contexto.get("confianza", 0)

    log_activo(simbolo, f"🧠 Régimen: {regimen}")
    log_activo(simbolo, f"📊 Tend: {tendencia:+.2f}% | Conf: {confianza:.2f}")

bot = telebot.TeleBot(config.TELEGRAM_TOKEN)
client = Client(config.BINANCE_API_KEY, config.BINANCE_API_SECRET, {"timeout": 30})

trades_activos = []
trades_info = {}
ultimo_intento = {}
balance_total = 0.0
inicio_bot = datetime.now()
BOT_PAUSADO = False

# Gestión de riesgo real: PORCENTAJE_POR_TRADE queda como límite de margen,
# no como pérdida máxima. La pérdida máxima se calcula contra la distancia al SL.
RIESGO_REAL_BASE = 0.01        # 1% del balance si el stop loss se ejecuta
RIESGO_REAL_MAXIMO = 0.015     # límite absoluto del 1.5% tras ajuste por score
MAX_MARGEN_POR_TRADE = getattr(config, "PORCENTAJE_POR_TRADE", 0.10)

client = Client(config.BINANCE_API_KEY, config.BINANCE_API_SECRET)
client._timestamp_offset = client.get_server_time()['serverTime'] - int(time.time() * 1000)

# ======================================
# OBTENER DATOS
# ======================================

client = Client(config.BINANCE_API_KEY, config.BINANCE_API_SECRET)

def obtener_datos(simbolo, intervalo, limite=200):

    klines = client.get_klines(
        symbol=simbolo,
        interval=intervalo,
        limit=limite
    )

    df = pd.DataFrame(klines, columns=[
        "time","open","high","low","close","volume",
        "close_time","qav","trades",
        "taker_base","taker_quote","ignore"
    ])

    df["time"] = pd.to_datetime(df["time"], unit="ms")

    df = df[["time","open","high","low","close","volume"]]

    df[["open","high","low","close","volume"]] = df[[
        "open","high","low","close","volume"
    ]].astype(float)

    df.set_index("time", inplace=True)

    return df

# ======================================
# FUNCIONES SINCRONZACION
# ======================================

def sincronizar_trades_activos():

    global trades_activos

    try:
        posiciones = client.futures_position_information()
        trades_activos = []

        for pos in posiciones:
            cantidad = float(pos['positionAmt'])
            if cantidad != 0:
                trades_activos.append(pos['symbol'])

        print(f"🔄 Sincronización completada. Trades activos: {trades_activos}")

    except Exception as e:
        print(f"⚠️ Error sincronizando trades: {e}")
        
def sincronizar_con_binance():

    global trades_activos, trades_info

    try:
        posiciones = client.futures_position_information()

        activos_reales = [
            p['symbol']
            for p in posiciones
            if abs(float(p['positionAmt'])) > 0.00001
        ]

        # La lista interna debe reflejar Binance, no solo cancelar órdenes huérfanas.
        trades_activos = activos_reales
        trades_info = {
            simbolo: info
            for simbolo, info in trades_info.items()
            if simbolo in activos_reales
        }

        # Detectar símbolos sin posición pero con órdenes abiertas
        ordenes = client.futures_get_open_orders()
        simbolos_con_ordenes = set(o['symbol'] for o in ordenes)

        for simbolo in simbolos_con_ordenes:
            if simbolo not in activos_reales:
                try:
                    client.futures_cancel_all_open_orders(symbol=simbolo)
                    print(f"🧹 Órdenes huérfanas eliminadas en {simbolo}")
                except Exception as e:
                    print(f"⚠️ No se pudieron cancelar órdenes huérfanas en {simbolo}: {e}")

        guardar_estado()

    except Exception as e:
        print(f"⚠️ Error sincronizando con Binance: {e}")

# ======================================
# FUNCIONES BASE
# ======================================

def actualizar_balance():
    global balance_total
    try:
        info = client.futures_account()
        balance_total = float(info['totalWalletBalance'])
    except:
        print("⚠️ Error al actualizar balance")

# ======================================

def enviar_telegram_privado(msg):
    try:
        bot.send_message(config.TELEGRAM_CHAT_ID, msg, parse_mode="HTML")
    except Exception as e:
        print(f"⚠️ Telegram privado error: {e}")


def enviar_senal_canal(msg):
    try:
        bot.send_message(config.TELEGRAM_CHANNEL_ID, msg, parse_mode="HTML")
    except Exception as e:
        print(f"⚠️ Telegram canal error: {e}")
# ======================================

def sentimiento_mercado(df_btc):
    try:
        estado = cerebro.filtro_btc(df_btc)
        return estado
    except:
        return "ERROR"
    
# ======================================

def redondear_precio(simbolo, precio):
    try:
        info = client.futures_exchange_info()
        simbolo_info = next(s for s in info['symbols'] if s['symbol'] == simbolo)
        precision = int(simbolo_info['pricePrecision'])
        return round(precio, precision)
    except:
        return round(precio, 4) # Fallback por si falla la API

# ======================================

def guardar_en_bitacora(simbolo, direccion, entrada, salida, pnl):

    archivo = os.path.join(os.getcwd(), "bitacora_trading.xlsx")

    nueva_fila = {
        "Fecha": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "Simbolo": simbolo,
        "Direccion": direccion,
        "Entrada": entrada,
        "Salida": salida,
        "PNL": pnl,
        "Balance": balance_total
    }

    df_nuevo = pd.DataFrame([nueva_fila])

    if not os.path.exists(archivo):
        df_nuevo.to_excel(archivo, index=False)
    else:
        df_existente = pd.read_excel(archivo)
        df_total = pd.concat([df_existente, df_nuevo], ignore_index=True)
        df_total.to_excel(archivo, index=False)

    print("Bitácora actualizada correctamente.")

# ======================================

def guardar_estado():
    data = {
        "trades_activos": trades_activos,
        "trades_info": trades_info
    }

    with open("estado_trades.json", "w") as f:
        json.dump(data, f)

# ======================================

def cargar_estado():
    global trades_activos, trades_info

    if os.path.exists("estado_trades.json"):
        with open("estado_trades.json", "r") as f:
            data = json.load(f)

            trades_activos = data.get("trades_activos", [])
            trades_info = data.get("trades_info", {})

# ======================================
# MICROTENDENCIA
# ======================================

def detectar_micro_tendencia(df_m15):

    ema20 = ta.ema(df_m15["close"], length=20)
    ema50 = ta.ema(df_m15["close"], length=50)

    if ema20.iloc[-1] > ema50.iloc[-1]:
        return "ALCISTA_MICRO"

    elif ema20.iloc[-1] < ema50.iloc[-1]:
        return "BAJISTA_MICRO"

    else:
        return "RANGO_MICRO"

# ======================================
# DIVERGENCIA
# ======================================

def detectar_divergencia(df):

    # últimos 3 mínimos y máximos
    lows = df['low'].tail(20)
    highs = df['high'].tail(20)
    rsi = df['rsi'].tail(20)

    # encontrar 2 últimos mínimos
    low1 = lows.iloc[-5]
    low2 = lows.iloc[-1]

    rsi1 = rsi.iloc[-5]
    rsi2 = rsi.iloc[-1]

    # encontrar 2 últimos máximos
    high1 = highs.iloc[-5]
    high2 = highs.iloc[-1]

    rsi_high1 = rsi.iloc[-5]
    rsi_high2 = rsi.iloc[-1]

    divergencia = None

    # 🟢 Bullish divergence
    if low2 < low1 and rsi2 > rsi1:
        divergencia = "bull"

    # 🔴 Bearish divergence
    elif high2 > high1 and rsi_high2 < rsi_high1:
        divergencia = "bear"

    return divergencia

# ======================================
# REVISAR CIERRE
# ======================================

def revisar_cierres():
    global trades_activos, trades_info

    try:
        # 1. Obtener posiciones reales actuales
        posiciones = client.futures_position_information()
        activos_reales = [
            p['symbol'] for p in posiciones 
            if abs(float(p['positionAmt'])) > 0.00001
        ]

        # 2. Detectar cuál de nuestros trades ya no está en Binance
        for simbolo in trades_activos.copy():
            if simbolo not in activos_reales:
                print(f"🔍 Detectado cierre en {simbolo}. Buscando detalles...")

                # 3. Obtener el último trade finalizado para ese símbolo
                historial = client.futures_account_trades(symbol=simbolo, limit=5)
                if not historial:
                    continue
                
                ultimo_movimiento = historial[-1]
                precio_salida = float(ultimo_movimiento['price'])
                pnl_realizado = float(ultimo_movimiento['realizedPnl'])
                                
                info = trades_info.get(simbolo, {})
                margen_usado = info.get("margen", 0)

                if margen_usado > 0:
                    pnl_pct = (pnl_realizado / margen_usado) * 100
                else:
                    pnl_pct = 0
                
                # Obtener info guardada al abrir
                info = trades_info.get(simbolo, {})
                precio_entrada = info.get("entrada", 0)
                direccion = info.get("direccion", "N/A")
                tp_objetivo = info.get("tp", 0)

                # Determinar si fue TP o SL
                # Si el PnL es positivo, asumimos TP (o BE). 
                # Si el precio de salida está cerca del TP guardado, es TP.
                resultado_tag = "🎯 TAKE PROFIT" if pnl_realizado > 0 else "🛑 STOP LOSS"
                
                # 4. Limpiar órdenes huérfanas (Si salió por TP, borra el SL)
                try:
                    client.futures_cancel_all_open_orders(symbol=simbolo)
                    print(f"🧹 Órdenes residuales de {simbolo} canceladas.")
                except:
                    pass

                # 5. Guardar en Bitácora
                guardar_en_bitacora(simbolo, direccion, precio_entrada, precio_salida, pnl_realizado)

                # 6. Notificar (IMAGEN + SONIDO)
                TELEGRAM_CHANNEL_ID = '-1003793634988'

                caption = f"""
                {resultado_tag}

                📊 <b>{simbolo}</b>
                📈 Resultado: {pnl_pct:.2f}%
                💵 PnL: {pnl_realizado:.2f} USDT
                📍 Salida: {precio_salida:.4f}
                🏁 Trade finalizado
                ==================
                🔥 Sniper Pro
                """
                enviar_senal_canal(caption.strip())

                # ← IA: aprender del resultado
                hash_fp     = info.get("hash_fp", "")
                features_fp = info.get("features_fp", {})
                if hash_fp:
                    sniper_ai.registrar_resultado(
                        hash_fp, features_fp,
                        win=(pnl_realizado > 0),
                        pnl=pnl_realizado
                    )

                # 7. Limpiar memoria interna
                if simbolo in trades_activos: trades_activos.remove(simbolo)
                trades_info.pop(simbolo, None)
                guardar_estado()

    except Exception as e:
        print(f"❌ Error en revisión de cierres: {e}")

# ======================================
# FUNCIÓN BREAK-EVEN — 2 NIVELES
# ======================================

def revisar_break_even():
    """
    Nivel 1 → ROI 30% (apalancado): SL sube a precio de entrada exacto
    Nivel 2 → ROI 40% (apalancado): SL sube a entrada + 0.20% (trade gratis)
    
    Usa be_nivel en trades_info: 0=sin activar, 1=BE entrada, 2=BE+0.20%
    """
    try:
        posiciones = client.futures_position_information()

        for pos in posiciones:
            cantidad = float(pos['positionAmt'])
            if cantidad == 0:
                continue

            simbolo   = pos['symbol']
            info      = trades_info.get(simbolo)
            if not info:
                continue

            # Migración: si tiene be_activado viejo, convertir a be_nivel
            if "be_activado" in info and "be_nivel" not in info:
                info["be_nivel"] = 1 if info["be_activado"] else 0

            be_nivel = info.get("be_nivel", 0)

            # Nivel 2 ya activado → nada más que hacer
            if be_nivel >= 2:
                continue

            entry         = float(pos['entryPrice'])
            pnl           = float(pos['unRealizedProfit'])
            margen        = float(pos['positionInitialMargin'])
            direccion     = info["direccion"]
            precio_actual = float(pos['markPrice'])

            if margen == 0:
                continue

            # ROI sobre el margen (apalancado)
            roi = (pnl / margen) * 100

            # ── NIVEL 1: ROI ≥ 30% → SL a entrada exacta ──────────────
            if be_nivel < 1 and roi >= 30.0:

                nuevo_sl = entry   # exactamente en entrada
                side     = SIDE_SELL if direccion == "LONG" else SIDE_BUY

                try:
                    client.futures_cancel_all_open_orders(symbol=simbolo)

                    # Reponer TP que acabamos de cancelar
                    tp_guardado = info.get("tp")
                    if tp_guardado:
                        client.futures_create_order(
                            symbol=simbolo,
                            side=side,
                            type="TAKE_PROFIT_MARKET",
                            stopPrice=round(tp_guardado, 6),
                            closePosition=True,
                            workingType="CONTRACT_PRICE"
                        )

                    # Nuevo SL en entrada
                    client.futures_create_order(
                        symbol=simbolo,
                        side=side,
                        type="STOP_MARKET",
                        stopPrice=round(nuevo_sl, 6),
                        closePosition=True,
                        workingType="CONTRACT_PRICE"
                    )

                    trades_info[simbolo]["be_nivel"] = 1
                    guardar_estado()
                    log_activo(simbolo, f"🔒 BE Nivel 1 activado (ROI {roi:.1f}%) → SL en entrada")

                    enviar_senal_canal(
                        f"🔒 <b>BREAK EVEN ACTIVADO</b>\n\n"
                        f"📊 {simbolo}\n"
                        f"📈 ROI: {roi:.1f}%\n"
                        f"🛑 SL movido a entrada: {nuevo_sl:.4f}\n"
                        f"<i>Trade ahora en zona segura</i>"
                    )
                except Exception as e:
                    print(f"Error BE Nivel 1 {simbolo}: {e}")

            # ── NIVEL 2: ROI ≥ 40% → SL a entrada + 0.20% ────────────
            elif be_nivel == 1 and roi >= 40.0:

                buffer_pct = 0.002   # 0.20% de ganancia protegida en precio
                if direccion == "LONG":
                    nuevo_sl = entry * (1 + buffer_pct)
                    side     = SIDE_SELL
                else:
                    nuevo_sl = entry * (1 - buffer_pct)
                    side     = SIDE_BUY

                try:
                    client.futures_cancel_all_open_orders(symbol=simbolo)

                    # Reponer TP
                    tp_guardado = info.get("tp")
                    if tp_guardado:
                        client.futures_create_order(
                            symbol=simbolo,
                            side=side,
                            type="TAKE_PROFIT_MARKET",
                            stopPrice=round(tp_guardado, 6),
                            closePosition=True,
                            workingType="CONTRACT_PRICE"
                        )

                    # SL con ganancia garantizada
                    client.futures_create_order(
                        symbol=simbolo,
                        side=side,
                        type="STOP_MARKET",
                        stopPrice=round(nuevo_sl, 6),
                        closePosition=True,
                        workingType="CONTRACT_PRICE"
                    )

                    trades_info[simbolo]["be_nivel"] = 2
                    guardar_estado()
                    log_activo(simbolo, f"🔐 BE Nivel 2 activado (ROI {roi:.1f}%) → SL +0.20% protegido")

                    enviar_senal_canal(
                        f"🔐 <b>BREAK EVEN NIVEL 2</b>\n\n"
                        f"📊 {simbolo}\n"
                        f"📈 ROI: {roi:.1f}%\n"
                        f"✅ SL con ganancia protegida: {nuevo_sl:.4f}\n"
                        f"<i>Trade sale en ganancia garantizada</i>"
                    )
                except Exception as e:
                    print(f"Error BE Nivel 2 {simbolo}: {e}")

    except Exception as e:
        print(f"Error break-even: {e}")

# ======================================
# FUNCION VALIDACION FINAL
# ======================================

def validacion_final(simbolo, resultado, df_h1, df_m15):

    if resultado.get("accion") not in ["LONG", "SHORT"]:
        return False

    accion = resultado["accion"]

    # ==============================
    # 1️⃣ FILTRO RSI EXTREMO
    # ==============================

    if "rsi" in df_m15.columns:
        rsi_actual = df_m15['rsi'].iloc[-1]

        if accion == "LONG" and rsi_actual > 70:
            log_activo(simbolo, f"❌ RSI sobrecompra ({rsi_actual:.2f})")
            return False

        if accion == "SHORT" and rsi_actual < 30:
            log_activo(simbolo, f"❌ RSI sobrecompra ({rsi_actual:.2f})")
            return False


    # ==============================
    # 2️⃣ FILTRO MOMENTUM MACD
    # ==============================

    if "macd_hist" in df_m15.columns:

        macd_actual = df_m15['macd_hist'].iloc[-1]
        macd_prev = df_m15['macd_hist'].iloc[-2]
        macd_prev2 = df_m15['macd_hist'].iloc[-3]
        # Solo bloquear si hay 2 velas consecutivas debilitándose (no solo 1)

        if accion == "LONG" and macd_actual < macd_prev < macd_prev2:
            log_activo(simbolo, "❌ Momentum debilitándose 2v consecutivas LONG")
            return False

        if accion == "SHORT" and macd_actual > macd_prev > macd_prev2:
            log_activo(simbolo, "❌ Momentum debilitándose 2v consecutivas SHORT")
            return False

    # ==============================
    # 4️⃣ FILTRO SOPORTE / RESISTENCIA
    # ==============================

    try:

        resistencia = df_h1["high"].rolling(20).max().iloc[-1]
        soporte = df_h1["low"].rolling(20).min().iloc[-1]

        precio = df_m15["close"].iloc[-1]

        distancia_res = abs(resistencia - precio) / precio
        distancia_sup = abs(precio - soporte) / precio

        # si está demasiado cerca del nivel → bloquear

        if accion == "LONG" and distancia_res < 0.004:
            log_activo(simbolo, "❌ Cerca de RESISTENCIA")
            return False

        if accion == "SHORT" and distancia_sup < 0.004:
            log_activo(simbolo, "❌ Cerca de SOPORTE")
            return False

    except Exception as e:
        print(f"{simbolo} Error filtro soporte/resistencia:", e)

    # ==============================
    # 5 FILTRO VWAP INSTITUCIONAL
    # ==============================

    try:

        if "vwap" in df_m15.columns:

            precio = df_m15["close"].iloc[-1]
            vwap = df_m15["vwap"].iloc[-1]
            diferencia_vwap = (precio - vwap) / vwap

            if accion == "LONG" and diferencia_vwap < -0.012:  # tolera hasta 1.2% bajo VWAP
                log_activo(simbolo, f"❌ Muy debajo VWAP ({diferencia_vwap*100:.2f}%)")
                return False

            if accion == "SHORT" and diferencia_vwap > 0.012:  # tolera hasta 1.2% sobre VWAP
                log_activo(simbolo, f"❌ Muy encima VWAP ({diferencia_vwap*100:.2f}%)")
                return False

    except Exception as e:
        print(f"{simbolo} error filtro VWAP:", e)


    # ==============================
    # ✔️ PASA TODO
    # ==============================

    log_activo(simbolo, "✅ Validación final OK")
    return True


# ======================================
# FUNCION ESTADISTICA
# ======================================

def obtener_estadisticas():

    archivo = os.path.join(os.getcwd(), "bitacora_trading.xlsx")

    if not os.path.exists(archivo):
        return {
            "total": 0,
            "ganadas": 0,
            "perdidas": 0,
            "winrate": 0,
            "pnl_total": 0
        }

    df = pd.read_excel(archivo)

    if df.empty:
        return {
            "total": 0,
            "ganadas": 0,
            "perdidas": 0,
            "winrate": 0,
            "pnl_total": 0
        }

    total = len(df)
    ganadas = len(df[df["PNL"] > 0])
    perdidas = len(df[df["PNL"] <= 0])
    pnl_total = df["PNL"].sum()

    winrate = (ganadas / total) * 100 if total > 0 else 0

    return {
        "total": total,
        "ganadas": ganadas,
        "perdidas": perdidas,
        "winrate": winrate,
        "pnl_total": pnl_total
    }

# ======================================
# MENSAJE DISCLAIMER
# ======================================

def enviar_mensaje_disclaimer():

    mensaje = """
============================
🛡️ SNIPER PRO OFICIAL

⚠️ IMPORTANTE

Sniper Pro NUNCA te pedirá dinero.
Sniper Pro NO vende señales privadas.

📊 Todas las señales que ves aquí:
son 100% GRATUITAS para la comunidad.

============================

🚫 Evitá estafas:
• No envíes dinero a terceros
• No confíes en mensajes privados

============================

🔥 Sniper Pro
Trading automatizado en tiempo real
============================
"""

    enviar_senal_canal(mensaje)

# ======================================

def mensajes_automaticos():

    while True:
        ahora = datetime.now()

        hora = ahora.hour
        minuto = ahora.minute

        # 🕛 12:00
        if hora == 12 and minuto == 0:
            enviar_mensaje_disclaimer()

        # 🕖 19:00
        if hora == 19 and minuto == 0:
            enviar_mensaje_disclaimer()

        time.sleep(60)

# ======================================
# EJECUCIÓN DE TRADE PROFESIONAL
# ======================================

def ejecutar_trade(simbolo, analisis):

    global trades_activos

    try:
        actualizar_balance()

        precio = analisis['precio']
        sl = analisis['sl_precio']
        tp = analisis['tp_precio']
        accion = analisis['accion']

        # ==========================================
        # CÁLCULO POR RIESGO REAL CONTRA STOP LOSS
        # ==========================================

        score = float(analisis.get("score", 0.8))
        distancia_sl_precio = abs(precio - sl)

        if precio <= 0 or distancia_sl_precio <= 0:
            log_activo(simbolo, "❌ Precio o SL inválido para calcular riesgo", True, "error")
            return

        # El score del cerebro trabaja en escala ~0.5 a 1.5, no 70/80/90.
        if score >= 1.4:
            porcentaje_riesgo_real = RIESGO_REAL_BASE * 1.10
        elif score >= 1.1:
            porcentaje_riesgo_real = RIESGO_REAL_BASE
        elif score >= 0.8:
            porcentaje_riesgo_real = RIESGO_REAL_BASE * 0.80
        else:
            porcentaje_riesgo_real = RIESGO_REAL_BASE * 0.60

        porcentaje_riesgo_real = min(porcentaje_riesgo_real, RIESGO_REAL_MAXIMO)
        riesgo_usdt_objetivo = balance_total * porcentaje_riesgo_real

        if riesgo_usdt_objetivo <= 0:
            return

        leverage = config.LEVERAGE

        # Cantidad calculada para que, si toca SL, la pérdida sea el riesgo objetivo.
        cantidad = riesgo_usdt_objetivo / distancia_sl_precio
        notional = cantidad * precio

        margen_maximo = balance_total * MAX_MARGEN_POR_TRADE
        notional_maximo = margen_maximo * leverage
        posicion_capada = False

        if notional > notional_maximo:
            notional = notional_maximo
            cantidad = notional / precio
            posicion_capada = True

        margen_usar = notional / leverage
        riesgo_usdt_estimado = cantidad * distancia_sl_precio

        if margen_usar <= 0 or cantidad <= 0:
            return

        log_activo(
            simbolo,
            f"📊 Score {score:.2f} → Riesgo real {porcentaje_riesgo_real*100:.2f}% "
            f"(${riesgo_usdt_estimado:.2f}) → Margen ${margen_usar:.2f}"
            f"{' | CAP margen' if posicion_capada else ''}",
            True
        )

        info = client.futures_exchange_info()
        simbolo_info = next(s for s in info['symbols'] if s['symbol'] == simbolo)

        price_precision = int(simbolo_info['pricePrecision'])
        precision = int(simbolo_info['quantityPrecision'])

        cantidad = round(cantidad, precision)
        notional = cantidad * precio
        margen_usar = notional / leverage
        riesgo_usdt_estimado = cantidad * distancia_sl_precio

        if cantidad <= 0 or notional <= 0:
            return
        
        # Verificar min notional
        min_notional = None

        for f in simbolo_info['filters']:
            if f['filterType'] in ['MIN_NOTIONAL', 'NOTIONAL']:
                min_notional = float(f.get('notional', f.get('minNotional', 0)))

        if min_notional and notional < min_notional:
            log_activo(simbolo, "❌ Bloqueado por min notional")
            return

        # ================= CONTROL DE LEVERAGE =================

        client.futures_change_leverage(
            symbol=simbolo,
            leverage=config.LEVERAGE
        )

        # ================= CONTROL DE MARGEN MAXIMO =================

        margen_requerido = margen_usar

        # ================= CONTROL MIN NOTIONAL =================

        min_notional = None

        for f in simbolo_info['filters']:
            if f['filterType'] in ['MIN_NOTIONAL', 'NOTIONAL']:
                min_notional = float(f.get('notional', f.get('minNotional', 0)))

        if min_notional and notional < min_notional:
            log_activo(simbolo, f"❌ Bloqueado por min notional ({notional:.2f} < {min_notional})")
            return  

            
        lado = SIDE_BUY if accion=="LONG" else SIDE_SELL

                
        try:
            client.futures_change_margin_type(
                symbol=simbolo,
                marginType="ISOLATED"
            )
        except Exception as e:
            if "No need to change margin type" not in str(e):
                raise e
        
        if simbolo in trades_activos:
            return    

        #======== MARKET ORDER ========#
        order = client.futures_create_order(
            symbol=simbolo,
            side=lado,
            type=ORDER_TYPE_MARKET,
            quantity=cantidad
        )
        
        time.sleep(0.8)

        posiciones = client.futures_position_information(symbol=simbolo)
        posicion = next((p for p in posiciones if float(p['positionAmt']) != 0), None)

        if posicion is None:
            log_activo(simbolo, "❌ MARKET no abrió posición real.")
            if simbolo in trades_activos:
                trades_activos.remove(simbolo)
            return

        precio_real = float(posicion['entryPrice'])
        cantidad_real = abs(float(posicion['positionAmt']))

        trades_activos.append(simbolo)
        guardar_estado()
        
        sl_real = sl
        tp_real = tp

        salida_side = SIDE_SELL if accion == "LONG" else SIDE_BUY

        try:
            # STOP LOSS
            client.futures_create_order(
                symbol=simbolo,
                side=salida_side,
                type="STOP_MARKET",
                stopPrice=round(sl_real, price_precision),
                quantity=cantidad_real,
                reduceOnly=True,
                workingType="CONTRACT_PRICE"
            )

            # TAKE PROFIT
            client.futures_create_order(
                symbol=simbolo,
                side=salida_side,
                type="TAKE_PROFIT_MARKET",
                stopPrice=round(tp_real, price_precision),
                quantity=cantidad_real,
                reduceOnly=True,
                workingType="CONTRACT_PRICE"
            )

        except Exception as proteccion_error:
            log_activo(
                simbolo,
                f"❌ Falló la protección SL/TP: {proteccion_error}. Cerrando posición.",
                True,
                "error"
            )

            try:
                client.futures_cancel_all_open_orders(symbol=simbolo)
            except Exception as cancel_error:
                print(f"⚠️ No se pudieron cancelar órdenes de {simbolo}: {cancel_error}")

            try:
                client.futures_create_order(
                    symbol=simbolo,
                    side=salida_side,
                    type=ORDER_TYPE_MARKET,
                    quantity=cantidad_real,
                    reduceOnly=True
                )
            except Exception as cierre_error:
                print(f"🚨 ERROR CRÍTICO cerrando {simbolo} sin protección: {cierre_error}")
                enviar_telegram_privado(
                    f"🚨 ERROR CRÍTICO\n{simbolo} quedó sin protección y no pudo cerrarse: {cierre_error}"
                )

            if simbolo in trades_activos:
                trades_activos.remove(simbolo)
            guardar_estado()
            return

        # SOLO SI TODO SALIÓ BIEN

        trades_info[simbolo] = {
            "sl": sl,
            "tp": tp,
            "entrada": precio_real,
            "direccion": accion,
            "margen": margen_usar,
            "riesgo_usdt": riesgo_usdt_estimado,
            "riesgo_pct": porcentaje_riesgo_real,
            "hash_fp"    : analisis.get("hash_fp", ""),
            "features_fp": analisis.get("features_fp", {}),
        }
        print(f"  {sniper_ai.estado()}")

        print(f"{simbolo} protegido correctamente con SL y TP.")

        enviar_senal_canal(
            f"🚀 *SNIPER PRO 3.0*\n\n"
            f"📊 Activo: *{simbolo}*\n"
            f"📈 Dirección: {accion}\n\n"
            f"💰 Margen usado: ${margen_requerido:.2f}\n"
            f"⚙️ Leverage: x{config.LEVERAGE}\n"            
            f"⚠️ Riesgo real: {porcentaje_riesgo_real*100:.2f}% (${riesgo_usdt_estimado:.2f})\n"
            f"🧠 Modelo: {analisis.get('modelo')}\n"
            f"🎯 Score: {analisis.get('score'):.2f}\n"
            f"📍 Entrada: {precio:.4f}\n"
            f"🛑 Stop Loss: {sl:.4f}\n"
            f"🎯 Take Profit: {tp:.4f}\n\n"
            f"📋 Motivos:\n- " + "\n- ".join(analisis.get("motivos", [])) +
            "\n\n<i>Sniper Pro 3.0</i>"
        )

    except Exception as e:
        ultimo_intento[simbolo] = time.time()
        print(f"ERROR COMPLETO {simbolo}: {repr(e)}")

        if simbolo in trades_activos:
            trades_activos.remove(simbolo)
# ======================================
# LOOP PRINCIPAL
# ======================================

def loop_principal():

    while True:

        try:

            if BOT_PAUSADO:
                time.sleep(5)
                continue

            cargar_estado()
            actualizar_balance()
            revisar_cierres()
            sincronizar_con_binance()
            revisar_break_even()

            ahora = datetime.now().strftime("%H:%M:%S")

            print("\n========================================")
            print(f"⏰ {ahora}")

            # ===== BTC H4 (macro) =====
            k_btc_h4 = client.futures_klines(
                symbol='BTCUSDT',
                interval="4h",
                limit=250
            )

            df_btc_h4 = pd.DataFrame(
                k_btc_h4,
                columns=['t','o','h','l','c','v','ct','q','n','tq','tb','ig']
            )

            df_btc_h4['close'] = pd.to_numeric(df_btc_h4['c'])


            # ===== BTC H1 (micro tendencia real) =====
            k_btc_h1 = client.futures_klines(
                symbol='BTCUSDT',
                interval="1h",
                limit=250
            )

            df_btc_h1 = pd.DataFrame(
                k_btc_h1,
                columns=['t','o','h','l','c','v','ct','q','n','tq','tb','ig']
            )

            df_btc_h1['close'] = pd.to_numeric(df_btc_h1['c'])


            # ===== USAR H1 COMO FILTRO PRINCIPAL =====
            estado_mercado = sentimiento_mercado(df_btc_h1)

            print(f"📊 BTC H4 (macro): {sentimiento_mercado(df_btc_h4)}")
            print(f"📊 BTC H1 (micro): {estado_mercado}")

            print(f"📊 Sentimiento BTC: {estado_mercado}")
            print("----------------------------------------")

            # ===== RECORRER FAVORITOS =====

            for simbolo in config.FAVORITOS:

                if simbolo in trades_activos:
                    print("\n----------------------------------------")  
                    log_activo(simbolo, "✅ YA EN TRADE", True)
                    print()
                    continue

                if len(trades_activos) >= config.MAX_TRADES_ACTIVOS:
                    print("⚠️ Límite de trades activos alcanzado")
                    break

                print("\n----------------------------------------")
                log_activo(simbolo, "🔍 Analizando", True)
                print("")
                

                k_h1 = client.futures_klines(
                    symbol=simbolo,
                    interval="1h",
                    limit=200
                )

                df_h1 = pd.DataFrame(
                    k_h1,
                    columns=['t','o','h','l','c','v','ct','q','n','tq','tb','ig']
                )

                k_m15 = client.futures_klines(
                    symbol=simbolo,
                    interval="15m",
                    limit=200
                )

                df_m15 = pd.DataFrame(
                    k_m15,
                    columns=['t','o','h','l','c','v','ct','q','n','tq','tb','ig']
                )

                for df in [df_h1, df_m15]:
                    df['open'] = pd.to_numeric(df['o'])
                    df['high'] = pd.to_numeric(df['h'])
                    df['low'] = pd.to_numeric(df['l'])
                    df['close'] = pd.to_numeric(df['c'])
                    df['volume'] = pd.to_numeric(df['v'])

                # ==============================
                # INDICADORES BASE
                # ==============================

                df_m15["ATR"] = ta.atr(
                    df_m15["high"],
                    df_m15["low"],
                    df_m15["close"],
                    length=14
                )

                adx = ta.adx(
                    df_m15["high"],
                    df_m15["low"],
                    df_m15["close"]
                )

                df_m15["ADX"] = adx["ADX_14"]

                # RSI y MACD para validaciones finales

                df_m15["rsi"] = ta.rsi(df_m15["close"], length=14)

                macd = ta.macd(df_m15["close"])
                df_m15["macd_hist"] = macd["MACDh_12_26_9"]

                # convertir timestamp a datetime
                df_m15['t'] = pd.to_datetime(df_m15['t'], unit='ms')
                df_m15.set_index('t', inplace=True)

                # ==============================
                # VWAP INSTITUCIONAL
                # ==============================

                df_m15["vwap"] = ta.vwap(
                    df_m15["high"],
                    df_m15["low"],
                    df_m15["close"],
                    df_m15["volume"]
                )

                df_btc = df_btc_h1

                divergencia = detectar_divergencia(df_m15)
                
                # IA: analizar contexto de mercado con 200 velas
                contexto_ia = sniper_ai.analizar_contexto_mercado(df_m15)
                log_contexto_ia(simbolo, contexto_ia)

                resultado = cerebro.analizar(
                    simbolo,
                    df_h1,
                    estado_mercado,
                    df_m15,
                    df_btc
                )

                # ==============================
                # 🧠 MACRO BTC + MICRO (SNIPER)
                # ==============================

                df_btc_h4 = obtener_datos("BTCUSDT", KLINE_INTERVAL_4HOUR, 200)
                macro = filtro_btc(df_btc_h4)

                micro = detectar_micro_tendencia(df_m15)

                modo_contra = False

                # 🎯 BTC ALCISTA
                if macro == "ALCISTA":

                    if micro == "BAJISTA_MICRO":
                        # Corrección en tendencia alcista → permitir LONG si score es alto
                        if resultado["accion"] == "SHORT":
                            log_activo(simbolo, "🔻 Corrección BTC → SHORT SCALP")
                            modo_contra = True
                        elif resultado["accion"] == "LONG" and resultado.get("score", 0) >= 1.4:
                            log_activo(simbolo, "📉 Compra en corrección BTC (score suficiente)")
                            # permitir, no hacer continue
                        else:
                            log_activo(simbolo, "⏳ Corrección BTC, score insuficiente")
                            continue

                    elif micro == "ALCISTA_MICRO":
                        
                        if resultado["accion"] != "LONG":

                             if resultado.get("score", 0) < 1.8:
                                  continue
                        

                # 🎯 BTC BAJISTA
                elif macro == "BAJISTA":

                    if micro == "ALCISTA_MICRO":

                        if resultado["accion"] == "LONG":
                            log_activo(simbolo, "🔺 Corrección BTC → LONG SCALP")
                            modo_contra = True
                        else:
                            continue

                    elif micro == "BAJISTA_MICRO":

                        
                        if resultado["accion"] != "SHORT":
                             if resultado.get("score", 0) < 1.8:
                                  continue

                resultado["modo_contra"] = modo_contra

                
                if simbolo in ultimo_intento and time.time() - ultimo_intento[simbolo] < 60:
                    continue
                
                vela = df_m15.iloc[-1]

                cuerpo = abs(vela["close"] - vela["open"])
                mecha = vela["high"] - vela["low"]

                if cuerpo < mecha * 0.35:
                    modelo_fuerte = resultado.get("modelo", "") in [
                        "BREAKOUT",
                        "MSS",
                        "VOL_EXPANSION"
                    ]
                    score_suficiente = resultado.get("score", 0) >= 1.0

                    if not (modelo_fuerte and score_suficiente):
                        log_activo(simbolo, "❌ Vela indecisión")
                        print()
                        continue
                    else:
                        log_activo(simbolo, "⚠️ Vela indecisión ignorada (breakout confirmado)")

                if validacion_final(simbolo, resultado, df_h1, df_m15):

                    if simbolo in trades_activos:
                        continue

                    # ── IA: evaluar si el trade debe ejecutarse ──────────
                    hash_fp, features_fp = sniper_ai.generar_fingerprint(
                        simbolo, resultado, df_m15, estado_mercado, micro
                    )
                    contexto_ia = sniper_ai.analizar_contexto_mercado(df_m15)
                    ia_ok, score_mod, ia_razon = sniper_ai.evaluar_trade(
                        hash_fp, features_fp, resultado, contexto_ia
                    )

                    if not ia_ok:
                        log_activo(simbolo, f"🚫 IA VETÓ: {ia_razon}")
                        print()
                        continue

                    # Aplicar modificador de score
                    score_original = resultado.get("score", 1.0)
                    resultado["score"] = round(score_original * score_mod, 4)
                    resultado["hash_fp"] = hash_fp
                    resultado["features_fp"] = features_fp

                    if score_mod != 1.0:
                        log_activo(simbolo,
                            f"🧠 Score {score_original:.2f} → {resultado['score']:.2f} "
                            f"(×{score_mod}) | {ia_razon.split('|')[0]}")
                    # ────────────────────────────────────────────────────

                    log_activo(simbolo, f"🚀 {resultado['accion']} CONFIRMADO")
                    print()

                    t = threading.Thread(
                        target=ejecutar_trade,
                        args=(simbolo, resultado)
                    )
                    t.start()

                else:
                    log_activo(simbolo, "⏳ ESPERAR")
                    print()

            print("----------------------------------------")
            print("⏳ Esperando 20 segundos...")
            print("_______Descanso para nuevo análisis________")
            print("========================================")

            time.sleep(20)

        except Exception as e:
            print(f"Error loop: {e}")
            time.sleep(5)

# ======================================
# COMANDOS TELEGRAM
# ======================================

@bot.message_handler(commands=['status'])
def status(message):

    actualizar_balance()
    stats = obtener_estadisticas()

    bot.reply_to(
        message,
        f"🤖 SNIPER PRO 3.0\n\n"
        f"💰 Balance: ${balance_total:.2f}\n"
        f"📊 Trades Activos: {len(trades_activos)}\n\n"
        f"📈 Operaciones cerradas: {stats['total']}\n"
        f"✅ TP: {stats['ganadas']}\n"
        f"❌ SL: {stats['perdidas']}\n"
        f"🎯 Winrate: {stats['winrate']:.2f}%\n"
        f"💵 PNL Total: ${stats['pnl_total']:.2f}\n\n"
        f"⚠️ Riesgo por Trade: {config.PORCENTAJE_POR_TRADE*100:.1f}%\n"
        f"📈 Max Trades: {config.MAX_TRADES_ACTIVOS}"
    )

@bot.message_handler(commands=['trades'])
def ver_trades(message):

    try:
        posiciones = client.futures_position_information()

        mensaje = "🚀 <b>TRADES EN CURSO</b>\n\n"

        pnl_total = 0.0
        hay_trades = False

        for pos in posiciones:

            cantidad = float(pos['positionAmt'])

            if cantidad == 0:
                continue

            hay_trades = True

            simbolo = pos['symbol']
            entry = float(pos['entryPrice'])
            precio_actual = float(client.futures_symbol_ticker(symbol=simbolo)['price'])
            pnl = float(pos['unRealizedProfit'])
            margen = float(pos.get('positionInitialMargin', 0))

            pnl_total += pnl
            info_trade = trades_info.get(simbolo, {})
            sl_guardado = info_trade.get("sl", None)
            tp_guardado = info_trade.get("tp", None)

            # Dirección textual
            tipo = "LONG" if cantidad > 0 else "SHORT"

            # Emoji dirección
            emoji_direccion = "📈" if cantidad > 0 else "📉"

            # Emoji por resultado (PNL)
            emoji_resultado = "🟢" if pnl >= 0 else "🔴"

            mensaje += (
                f"{emoji_resultado} {simbolo}\n"
                f"{emoji_direccion} {tipo}\n"
                f"🔹 Entrada: ${entry:.4f}\n"
            )

            if sl_guardado:
                mensaje += f"🛑 SL: ${sl_guardado:.4f}\n"

            if tp_guardado:
                mensaje += f"🎯 TP: ${tp_guardado:.4f}\n"

            mensaje += (
                f"💰 Margen: ${margen:.2f}\n"
                f"📊 Actual: ${precio_actual:.4f}\n"
                f"💵 PNL: ${pnl:.2f} USDT\n\n"
            )

        if not hay_trades:
            mensaje += "No hay posiciones abiertas.\n\n"

        estado = "🟢 GANANCIA" if pnl_total >= 0 else "🔴 PÉRDIDA"

        mensaje += (
            f"💰 *PNL TOTAL GLOBAL:* ${pnl_total:.2f} USDT\n"
            f"Estado: {estado}\n\n"
            "_Sniper Pro 3.0_"
        )

        bot.reply_to(message, mensaje, parse_mode="HTML")

    except Exception as e:
        bot.reply_to(message, f"Error obteniendo trades: {e}")

@bot.message_handler(commands=['stats'])
def ver_stats_detalle(message):
    try:
        archivo = os.path.join(os.getcwd(), "bitacora_trading.xlsx")
        df = pd.read_excel(archivo)
        if df.empty:
            bot.reply_to(message, "Sin datos aún.")
            return

        resumen = "📊 <b>RENDIMIENTO POR SÍMBOLO</b>\n\n"
        por_simbolo = df.groupby("Simbolo")["PNL"].agg(["count", "sum", lambda x: (x > 0).mean() * 100])
        por_simbolo.columns = ["trades", "pnl", "wr"]
        por_simbolo = por_simbolo.sort_values("pnl")

        for sym, row in por_simbolo.iterrows():
            emoji = "🟢" if row["pnl"] > 0 else "🔴"
            resumen += f"{emoji} {sym}: {row['wr']:.0f}% WR | {row['pnl']:+.2f} USDT ({int(row['trades'])} trades)\n"

        bot.reply_to(message, resumen, parse_mode="HTML")
    except Exception as e:
        bot.reply_to(message, f"Error: {e}")

@bot.message_handler(commands=['pause'])
def pause(message):
    global BOT_PAUSADO
    BOT_PAUSADO = True
    bot.reply_to(message, "⏸ Bot en pausa.")

@bot.message_handler(commands=['resume'])
def resume(message):
    global BOT_PAUSADO
    BOT_PAUSADO = False
    bot.reply_to(message, "▶️ Bot reanudado.")

@bot.message_handler(commands=['risk'])
def cambiar_riesgo(message):
    try:
        nuevo = float(message.text.split()[1])
        config.PORCENTAJE_POR_TRADE = nuevo
        bot.reply_to(message, f"Nuevo riesgo: {nuevo*100:.1f}%")
    except:
        bot.reply_to(message, "Usa: /risk 0.03")

@bot.message_handler(commands=['maxtrades'])
def cambiar_max(message):
    try:
        nuevo = int(message.text.split()[1])
        config.MAX_TRADES_ACTIVOS = nuevo
        bot.reply_to(message, f"Nuevo máximo trades: {nuevo}")
    except:
        bot.reply_to(message, "Usa: /maxtrades 2")

@bot.message_handler(commands=['closeall'])
def close_all(message):

    global trades_activos, trades_info

    try:
        posiciones = client.futures_position_information()

        for pos in posiciones:
            cantidad = float(pos['positionAmt'])

            if cantidad != 0:

                simbolo = pos['symbol']
                side = SIDE_SELL if cantidad > 0 else SIDE_BUY

                # Cerrar posición
                client.futures_create_order(
                    symbol=simbolo,
                    side=side,
                    type="MARKET",
                    quantity=abs(cantidad)
                )

                # Cancelar órdenes pendientes
                client.futures_cancel_all_open_orders(symbol=simbolo)

        trades_activos.clear()
        trades_info.clear()

        bot.reply_to(message, "🛑 Todas las posiciones cerradas y órdenes canceladas.")

    except Exception as e:
        bot.reply_to(message, f"Error cerrando posiciones: {e}")

@bot.message_handler(commands=['sync'])
def sync_trades(message):

    global trades_activos, trades_info

    try:
        posiciones = client.futures_position_information()

        trades_activos.clear()
        trades_info.clear()

        for pos in posiciones:
            cantidad = float(pos['positionAmt'])

            if cantidad != 0:
                trades_activos.append(pos['symbol'])

        bot.reply_to(
            message,
            f"🔄 Sincronización completada.\nTrades activos: {trades_activos}"
        )

    except Exception as e:
        bot.reply_to(message, f"Error en sincronización: {e}")

@bot.message_handler(commands=['panic'])
def panic(message):

    global trades_activos, trades_info, BOT_PAUSADO

    try:
        posiciones = client.futures_position_information()

        for pos in posiciones:
            cantidad = float(pos['positionAmt'])

            if cantidad != 0:

                simbolo = pos['symbol']
                side = SIDE_SELL if cantidad > 0 else SIDE_BUY

                client.futures_create_order(
                    symbol=simbolo,
                    side=side,
                    type="MARKET",
                    quantity=abs(cantidad)
                )

                client.futures_cancel_all_open_orders(symbol=simbolo)

        trades_activos.clear()
        trades_info.clear()
        BOT_PAUSADO = True

        bot.reply_to(
            message,
            "🚨 PANIC ACTIVADO\n\n"
            "Todas las posiciones cerradas.\n"
            "Órdenes canceladas.\n"
            "Bot en pausa."
        )

    except Exception as e:
        bot.reply_to(message, f"Error en PANIC: {e}")

@bot.message_handler(commands=['reset'])
def reset_bot(message):

    global trades_activos, trades_info

    trades_activos.clear()
    trades_info.clear()

    bot.reply_to(
        message,
        "♻️ Reset interno realizado.\nMemoria limpia."
    )

# ======================================
# INICIO
# ======================================

if __name__ == "__main__":

    sincronizar_trades_activos()
    revisar_cierres()
    actualizar_balance()
    revisar_break_even()
    

    archivo = os.path.join(os.getcwd(), "bitacora_trading.xlsx")

    if not os.path.exists(archivo):
        df = pd.DataFrame(columns=["Fecha","Simbolo","Direccion","Entrada","Salida","PNL"])
        df.to_excel(archivo, index=False)
        print("Bitácora creada automáticamente.")

    enviar_telegram_privado(
        "🤖 *SNIPER PRO 3.0 INICIADO*\n\n"
        "📌 *Comandos Disponibles:*\n"
        "/status → Estado general\n"
        "/trades → Ver trades activos\n"
        "/pause → Pausar bot\n"
        "/resume → Reanudar bot\n"
        "/risk 0.03 → Cambiar riesgo\n"
        "/maxtrades 2 → Máximo trades activos\n"
        "/closeall → Cerrar todas las posiciones\n"
        "/panic → Cerrar todo y pausar\n"
        "/sync → Sincronizar con Binance\n"
        "/reset → Limpiar memoria interna\n\n"
        "_Sniper Pro 3.0_"
    )

    threading.Thread(
        target=bot.infinity_polling,
        daemon=True
    ).start()

    threading.Thread(target=mensajes_automaticos).start()

    hilo = threading.Thread(target=loop_principal)
    hilo.start()