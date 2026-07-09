# ─────────────────────────────────────────────────────────────────────────────
#  SNIPER PRO 3.0 — IA ACTIVA DE APRENDIZAJE
#  Archivo: sniper_ai.py
#
#  La IA:
#  1. Analiza el contexto de mercado (200+ velas) → régimen actual
#  2. Genera un fingerprint de cada trade (modelo + condiciones)
#  3. Bloquea patrones con 4+ pérdidas y WR < 35%
#  4. Amplifica patrones con 4+ ganancias y WR > 65%
#  5. Puede vetar un trade antes de ejecutar (solo tras 10 SL consecutivos)
#  6. Aprende de cada SL y retroalimenta score/predicción
#  7. Persiste toda la memoria entre sesiones
# ─────────────────────────────────────────────────────────────────────────────

import json
import os
import hashlib
import numpy as np
import pandas as pd
from datetime import datetime
from velas import identificar_patrones

AI_FILE = "sniper_ai_state.json"

# ─────────────────────────────────────────────────────────────────────────────
#  PARÁMETROS DE APRENDIZAJE
# ─────────────────────────────────────────────────────────────────────────────
MIN_TRADES_PARA_APRENDER  = 4      # Mínimo de trades para activar bloqueo/boost
UMBRAL_BLOQUEO_WR         = 0.35   # WR < 35% con 4+ trades → BLOQUEADO
UMBRAL_BOOST_WR           = 0.65   # WR > 65% con 4+ trades → BOOST
BOOST_MULTIPLICADOR       = 1.30   # Multiplica el score en patrones ganadores
PENALIZACION_MULTIPLICADOR= 0.65   # Reduce score en patrones débiles (sin bloquear)
RACHA_NEGATIVA_ALERTA     = 3      # SL seguidos → alerta en consola (no bloquea)
RACHA_NEGATIVA_BLOQUEO    = 10     # 10 SL consecutivos → veto total
RACHA_PENALIZACION_POR_SL = 0.04   # -4% score por cada SL en racha activa
MIN_TRADES_SL_RETRO       = 3      # Mínimo de SL para retroalimentar predicción


# ─────────────────────────────────────────────────────────────────────────────
#  CLASE PRINCIPAL
# ─────────────────────────────────────────────────────────────────────────────

class SniperAI:

    def __init__(self):
        self.patrones       = {}    # {hash: {wins, losses, pnl, bloqueado, features}}
        self.estadisticas_activos = {}  # {SIMBOLO|MODELO|ACCION: {wins, losses, pnl}}
        self.historial      = []    # Últimos 200 trades completos
        self.racha_actual   = []    # Últimos resultados para detectar racha negativa
        self.retroalimentacion_sl = {}  # Aprendizaje por condiciones que llevan a SL
        self.total_wins     = 0
        self.total_losses   = 0
        self._cargar()

    # ─────────────────────────────────────────────────────────────────────────
    #  PERSISTENCIA
    # ─────────────────────────────────────────────────────────────────────────

    def _cargar(self):
        if not os.path.exists(AI_FILE):
            print("🧠 SniperAI: iniciando sin historial previo.")
            return
        try:
            with open(AI_FILE) as f:
                data = json.load(f)
            self.patrones     = data.get("patrones", {})
            self.estadisticas_activos = data.get("estadisticas_activos", {})
            self.historial    = data.get("historial", [])[-200:]
            self.racha_actual = data.get("racha_actual", [])[-15:]
            self.retroalimentacion_sl = data.get("retroalimentacion_sl", {})
            self.total_wins   = data.get("total_wins", 0)
            self.total_losses = data.get("total_losses", 0)

            bloqueados = sum(1 for p in self.patrones.values() if p.get("bloqueado"))
            boosteados = sum(1 for p in self.patrones.values()
                            if not p.get("bloqueado") and
                            p.get("wins", 0) / max(1, p.get("wins",0) + p.get("losses",0)) >= UMBRAL_BOOST_WR
                            and p.get("wins",0) + p.get("losses",0) >= MIN_TRADES_PARA_APRENDER)

            wr = self.total_wins / max(1, self.total_wins + self.total_losses) * 100
            print(f"🧠 SniperAI cargada: {len(self.historial)} trades | "
                  f"WR: {wr:.1f}% | "
                  f"Patrones: {len(self.patrones)} "
                  f"({bloqueados} bloqueados, {boosteados} boosteados)")
        except Exception as e:
            print(f"🧠 SniperAI error cargando: {e} — iniciando limpio")

    def _guardar(self):
        try:
            with open(AI_FILE, "w") as f:
                json.dump({
                    "patrones"     : self.patrones,
                    "estadisticas_activos": self.estadisticas_activos,
                    "historial"    : self.historial[-200:],
                    "racha_actual" : self.racha_actual[-15:],
                    "retroalimentacion_sl": self.retroalimentacion_sl,
                    "total_wins"   : self.total_wins,
                    "total_losses" : self.total_losses,
                    "ultima_act"   : datetime.now().strftime("%Y-%m-%d %H:%M"),
                }, f, indent=2)
        except Exception as e:
            print(f"🧠 SniperAI error guardando: {e}")

    def _clave_estadistica(self, features: dict) -> str:
        return "|".join([
            features.get("simbolo", "UNKNOWN"),
            features.get("modelo", "UNKNOWN"),
            features.get("accion", "UNKNOWN"),
        ])

    def _stats_resumen(self, stats: dict) -> tuple:
        wins = stats.get("wins", 0)
        losses = stats.get("losses", 0)
        total = wins + losses
        wr = wins / max(1, total)
        return wins, losses, total, wr

    def _contar_racha_negativa(self) -> int:
        racha = 0
        for resultado in reversed(self.racha_actual):
            if resultado == "LOSS":
                racha += 1
            else:
                break
        return racha

    def _clave_retroalimentacion_sl(self, features: dict) -> str:
        return "|".join([
            features.get("modelo", "UNKNOWN"),
            features.get("regimen", "DESCONOCIDO"),
            features.get("distancia_sl", "CONTROLADO"),
            features.get("accion", ""),
            features.get("sesion", "NY"),
        ])

    def _modificador_retroalimentacion_sl(self, features: dict) -> tuple:
        clave = self._clave_retroalimentacion_sl(features)
        stats = self.retroalimentacion_sl.get(clave)
        if not stats:
            return 1.0, ""

        wins = stats.get("wins", 0)
        losses = stats.get("sl_hits", 0)
        total = wins + losses
        if total < MIN_TRADES_SL_RETRO:
            return 1.0, ""

        sl_rate = losses / total
        if sl_rate >= 0.70:
            return 0.80, f"SL_RETRO_ALTO({clave},sl={sl_rate*100:.0f}%)"
        if sl_rate >= 0.55:
            return 0.90, f"SL_RETRO_MEDIO({clave},sl={sl_rate*100:.0f}%)"
        if sl_rate <= 0.35 and total >= MIN_TRADES_SL_RETRO:
            return 1.08, f"SL_RETRO_FUERTE({clave},sl={sl_rate*100:.0f}%)"
        return 1.0, ""

    def resumen_estadisticas_activos(self, limite: int = 8) -> str:
        if not self.estadisticas_activos:
            return "🧠 IA: sin estadísticas por activo todavía."

        filas = sorted(
            self.estadisticas_activos.items(),
            key=lambda item: (item[1].get("wins", 0) + item[1].get("losses", 0), item[1].get("pnl", 0)),
            reverse=True
        )[:limite]

        lineas = ["🧠 Estadísticas IA por activo/modelo:"]
        for clave, stats in filas:
            wins, losses, total, wr = self._stats_resumen(stats)
            pnl = stats.get("pnl", 0.0)
            lineas.append(
                f"{clave.replace('|', ' + ')} = {wr*100:.0f}% WR "
                f"({wins}W/{losses}L, pnl {pnl:+.2f}, n={total})"
            )

        return "\n".join(lineas)

    # ─────────────────────────────────────────────────────────────────────────
    #  FINGERPRINT — identidad única de las condiciones de un trade
    # ─────────────────────────────────────────────────────────────────────────

    def generar_fingerprint(self, simbolo: str, analisis: dict,
                             df_m15, estado_btc: str,
                             micro: str, contexto: dict = None) -> tuple:
        """
        Genera un fingerprint reproducible de las condiciones de mercado.
        Retorna (hash_str, features_dict).
        """
        try:
            rsi_val = float(df_m15["rsi"].iloc[-1]) if "rsi" in df_m15.columns else 50.0
            if rsi_val <= 30:
                rsi_zona = "SOBREVENTA"
            elif rsi_val < 40:
                rsi_zona = "BAJO"
            elif rsi_val >= 70:
                rsi_zona = "SOBRECOMPRA"
            elif rsi_val > 60:
                rsi_zona = "ALTO"
            else:
                rsi_zona = "MEDIO"

            macd_hist = float(df_m15["macd_hist"].iloc[-1]) if "macd_hist" in df_m15.columns else 0
            macd_dir  = "POS" if macd_hist > 0 else "NEG"

            contexto = contexto or self.analizar_contexto_mercado(df_m15)
            regimen = contexto.get("regimen", "DESCONOCIDO")

            patrones = analisis.get("patrones_vela")
            if patrones is None:
                patrones = identificar_patrones(df_m15)
            patrones = [str(p).upper() for p in patrones]
            patrones_direccionales = [
                p for p in patrones
                if p not in ["DOJI_INDECISION", "INSIDE_BAR"]
            ]
            patron_vela = patrones_direccionales[0] if patrones_direccionales else "NINGUNO"

            try:
                vol_actual = float(df_m15["volume"].iloc[-1])
                vol_media = float(df_m15["volume"].rolling(30).mean().iloc[-1])
                vol_ratio = vol_actual / max(vol_media, 1e-12)
            except Exception:
                vol_ratio = 1.0

            if vol_ratio < 0.70:
                volumen_zona = "BAJO"
            elif vol_ratio < 1.20:
                volumen_zona = "NORMAL"
            elif vol_ratio < 2.00:
                volumen_zona = "ALTO"
            else:
                volumen_zona = "EXPLOSIVO"

            try:
                adx_val = float(analisis.get("adx_actual"))
                if np.isnan(adx_val):
                    raise ValueError
            except Exception:
                try:
                    adx_val = float(df_m15["ADX"].iloc[-1]) if "ADX" in df_m15.columns else 0.0
                except Exception:
                    adx_val = 0.0

            if adx_val < 18:
                adx_zona = "DEBIL"
            elif adx_val < 25:
                adx_zona = "NORMAL"
            elif adx_val < 35:
                adx_zona = "FUERTE"
            else:
                adx_zona = "MUY_FUERTE"

            try:
                distancia_sl_pct = float(analisis.get("distancia_sl_pct"))
            except Exception:
                precio = float(analisis.get("precio", 0))
                sl = float(analisis.get("sl_precio", 0))
                distancia_sl_pct = abs(precio - sl) / precio * 100 if precio > 0 and sl > 0 else 0.0

            if distancia_sl_pct <= 1.2:
                distancia_sl_zona = "CORTO"
            elif distancia_sl_pct <= 2.5:
                distancia_sl_zona = "CONTROLADO"
            else:
                distancia_sl_zona = "AMPLIO"

            hora = datetime.now().hour
            if 0 <= hora < 8:
                sesion = "ASIA"
            elif 8 <= hora < 16:
                sesion = "EUROPA"
            else:
                sesion = "NY"

            features = {
                "simbolo"    : simbolo,
                "modelo"     : analisis.get("modelo", "UNKNOWN"),
                "accion"     : analisis.get("accion", ""),
                "regimen"    : regimen,
                "patron_vela": patron_vela,
                "patrones_vela": patrones,
                "btc"        : estado_btc,
                "micro"      : micro,
                "rsi_zona"   : rsi_zona,
                "volumen"    : volumen_zona,
                "vol_ratio"  : round(vol_ratio, 2),
                "adx_zona"   : adx_zona,
                "adx"        : round(adx_val, 2),
                "distancia_sl": distancia_sl_zona,
                "distancia_sl_pct": round(distancia_sl_pct, 3),
                "macd_dir"   : macd_dir,
                "sesion"     : sesion,
                "contra"     : str(analisis.get("modo_contra", False)),
            }

            # Hash reproducible
            raw = json.dumps(features, sort_keys=True)
            hash_str = hashlib.md5(raw.encode()).hexdigest()[:12]
            return hash_str, features

        except Exception as e:
            print(f"🧠 fingerprint error: {e}")
            return "default", {}

    # ─────────────────────────────────────────────────────────────────────────
    #  ANALIZADOR DE MERCADO — régimen actual en 200 velas
    # ─────────────────────────────────────────────────────────────────────────

    def analizar_contexto_mercado(self, df: pd.DataFrame) -> dict:
        """
        Analiza 200+ velas y determina el régimen actual del activo.
        Retorna dict con: regimen, tendencia_pct, volatilidad, momentum, confianza
        """
        try:
            close  = df["close"].astype(float)
            high   = df["high"].astype(float)
            low    = df["low"].astype(float)
            volume = df["volume"].astype(float)

            # EMAs de largo plazo
            ema20  = close.ewm(span=20,  adjust=False).mean()
            ema50  = close.ewm(span=50,  adjust=False).mean()
            ema100 = close.ewm(span=100, adjust=False).mean()
            ema200 = close.ewm(span=200, adjust=False).mean()

            precio_actual = close.iloc[-1]

            # Tendencia estructural
            sobre_ema200 = precio_actual > ema200.iloc[-1]
            sobre_ema100 = precio_actual > ema100.iloc[-1]
            sobre_ema50  = precio_actual > ema50.iloc[-1]
            sobre_ema20  = precio_actual > ema20.iloc[-1]
            emas_alcistas = sum([sobre_ema200, sobre_ema100, sobre_ema50, sobre_ema20])

            # Pendiente de la EMA50 (últimas 10 velas)
            slope_ema50 = (ema50.iloc[-1] - ema50.iloc[-10]) / ema50.iloc[-10]

            # Volatilidad (ATR simple)
            tr   = pd.concat([high - low,
                               (high - close.shift()).abs(),
                               (low  - close.shift()).abs()], axis=1).max(axis=1)
            atr  = tr.rolling(14).mean().iloc[-1]
            atr_pct = atr / precio_actual

            # Momentum: variación últimas 20 velas
            tendencia_20 = (precio_actual - close.iloc[-20]) / close.iloc[-20]

            # Volumen: creciente o decreciente
            vol_reciente = volume.iloc[-5:].mean()
            vol_anterior = volume.iloc[-20:-5].mean()
            vol_creciente = vol_reciente > vol_anterior * 1.1

            # ── Determinar régimen ──────────────────────────────────────
            if emas_alcistas >= 3 and slope_ema50 > 0.002:
                regimen = "TENDENCIA_ALCISTA_FUERTE"
            elif emas_alcistas <= 1 and slope_ema50 < -0.002:
                regimen = "TENDENCIA_BAJISTA_FUERTE"
            elif emas_alcistas >= 3 and slope_ema50 > 0:
                regimen = "TENDENCIA_ALCISTA"
            elif emas_alcistas <= 1 and slope_ema50 < 0:
                regimen = "TENDENCIA_BAJISTA"
            elif atr_pct > 0.02 and vol_creciente:
                regimen = "VOLATIL_EXPANSION"
            elif atr_pct < 0.008:
                regimen = "RANGO_COMPRIMIDO"
            else:
                regimen = "RANGO"

            # Confianza basada en alineación de EMAs
            confianza = emas_alcistas / 4.0   # 0.0 a 1.0 (alcista=1, bajista=0)

            return {
                "regimen"       : regimen,
                "tendencia_pct" : round(tendencia_20 * 100, 2),
                "atr_pct"       : round(atr_pct * 100, 3),
                "vol_creciente" : vol_creciente,
                "confianza"     : round(confianza, 2),
                "sobre_emas"    : emas_alcistas,
                "slope_ema50"   : round(slope_ema50 * 100, 4),
            }

        except Exception as e:
            print(f"🧠 analizar_contexto error: {e}")
            return {"regimen": "DESCONOCIDO", "confianza": 0.5}

    # ─────────────────────────────────────────────────────────────────────────
    #  EVALUACIÓN ACTIVA — decide si el trade puede ejecutarse
    # ─────────────────────────────────────────────────────────────────────────

    def evaluar_trade(self, hash_fp: str, features: dict,
                       analisis: dict, contexto: dict) -> tuple:
        """
        Decide si el trade debe ejecutarse y con qué modificador de score.

        Retorna: (permitir: bool, score_modifier: float, razon: str)
        """
        accion = analisis.get("accion", "")
        regimen = contexto.get("regimen", "DESCONOCIDO")
        confianza = contexto.get("confianza", 0.5)
        vol_creciente = contexto.get("vol_creciente", True)

        # ── 1. Racha negativa global ────────────────────────────────────
        racha_sl = self._contar_racha_negativa()
        if racha_sl >= RACHA_NEGATIVA_BLOQUEO:
            return False, 0.0, f"RACHA_{racha_sl}_SL_CONSECUTIVOS"

        # ── 2. Patrón bloqueado ─────────────────────────────────────────
        if hash_fp in self.patrones:
            p = self.patrones[hash_fp]
            if p.get("bloqueado", False):
                w, l = p.get("wins", 0), p.get("losses", 0)
                wr   = w / max(1, w + l) * 100
                return False, 0.0, f"PATRON_BLOQUEADO(WR={wr:.0f}%,{w}W/{l}L)"

        clave_stats = self._clave_estadistica(features)
        stats_activo = self.estadisticas_activos.get(clave_stats)
        if stats_activo:
            w, l, total, wr_activo = self._stats_resumen(stats_activo)
            if total >= MIN_TRADES_PARA_APRENDER and wr_activo < UMBRAL_BLOQUEO_WR:
                return (
                    False,
                    0.0,
                    f"ACTIVO_MODELO_BLOQUEADO({clave_stats},WR={wr_activo*100:.0f}%,{w}W/{l}L)"
                )

        # ── 3. Filtro de régimen vs dirección ───────────────────────────
        if regimen == "TENDENCIA_ALCISTA_FUERTE" and accion == "SHORT":
            if not analisis.get("modo_contra"):
                return False, 0.0, "IA_VETA_SHORT_EN_ALCISTA_FUERTE"

        if regimen == "TENDENCIA_BAJISTA_FUERTE" and accion == "LONG":
            if not analisis.get("modo_contra"):
                return False, 0.0, "IA_VETA_LONG_EN_BAJISTA_FUERTE"

        if regimen == "RANGO_COMPRIMIDO" and not vol_creciente:
            # En rango sin volumen solo aceptar modelos específicos
            modelo = analisis.get("modelo", "")
            if modelo not in ["MSS", "ORDER_BLOCK", "BREAKOUT"]:
                return False, 0.0, f"IA_VETA_RANGO_COMPRIMIDO({modelo})"

        # ── 4. Calcular modificador de score ────────────────────────────
        score_mod = 1.0
        extras_razon = []

        # Retroalimentación por racha: penaliza progresivamente, no bloquea hasta 10
        if racha_sl >= RACHA_NEGATIVA_ALERTA:
            penal_racha = max(0.55, 1.0 - (racha_sl * RACHA_PENALIZACION_POR_SL))
            score_mod *= penal_racha
            extras_razon.append(f"racha_sl={racha_sl}×{penal_racha:.2f}")

        retro_mod, retro_razon = self._modificador_retroalimentacion_sl(features)
        if retro_mod != 1.0:
            score_mod *= retro_mod
            extras_razon.append(retro_razon)

        if stats_activo:
            w, l, total, wr_activo = self._stats_resumen(stats_activo)
            if total >= MIN_TRADES_PARA_APRENDER:
                if wr_activo >= UMBRAL_BOOST_WR:
                    score_mod *= BOOST_MULTIPLICADOR
                elif wr_activo < UMBRAL_BLOQUEO_WR + 0.1:
                    score_mod *= PENALIZACION_MULTIPLICADOR

        if hash_fp in self.patrones:
            p  = self.patrones[hash_fp]
            w  = p.get("wins", 0)
            l  = p.get("losses", 0)
            total = w + l

            if total >= MIN_TRADES_PARA_APRENDER:
                wr_patron = w / total

                if wr_patron >= UMBRAL_BOOST_WR:
                    score_mod = BOOST_MULTIPLICADOR
                elif wr_patron < UMBRAL_BLOQUEO_WR + 0.1:
                    # No bloqueado aún pero tendencia negativa → penalizar
                    score_mod = PENALIZACION_MULTIPLICADOR

        # ── 5. Ajuste por contexto de mercado ───────────────────────────
        if regimen in ["TENDENCIA_ALCISTA_FUERTE", "TENDENCIA_BAJISTA_FUERTE"]:
            if (regimen == "TENDENCIA_ALCISTA_FUERTE" and accion == "LONG") or \
               (regimen == "TENDENCIA_BAJISTA_FUERTE" and accion == "SHORT"):
                score_mod *= 1.15   # Boost extra por ir con tendencia fuerte

        if regimen == "VOLATIL_EXPANSION" and vol_creciente:
            score_mod *= 1.10   # Mercado activo → más oportunidades

        razon = (f"OK|régimen={regimen}|"
                 f"mod_score={score_mod:.2f}|"
                 f"patrón={'nuevo' if hash_fp not in self.patrones else 'conocido'}")
        if extras_razon:
            razon += "|" + "|".join(extras_razon)

        return True, round(score_mod, 3), razon

    # ─────────────────────────────────────────────────────────────────────────
    #  REGISTRAR RESULTADO — aprende del cierre
    # ─────────────────────────────────────────────────────────────────────────

    def registrar_resultado(self, hash_fp: str, features: dict,
                             win: bool, pnl: float):
        """
        Registra el resultado de un trade y actualiza la memoria de patrones.
        """
        # ── Actualizar patrón ────────────────────────────────────────────
        if hash_fp not in self.patrones:
            self.patrones[hash_fp] = {
                "wins": 0, "losses": 0, "pnl": 0.0,
                "bloqueado": False, "features": features
            }

        p = self.patrones[hash_fp]
        if win:
            p["wins"]  += 1
            self.total_wins += 1
        else:
            p["losses"] += 1
            self.total_losses += 1

        p["pnl"] = round(p.get("pnl", 0) + pnl, 4)

        total_patron = p["wins"] + p["losses"]
        wr_patron    = p["wins"] / max(1, total_patron)

        # ── Estadística por activo + modelo + dirección ──────────────────
        clave_stats = self._clave_estadistica(features)
        if clave_stats not in self.estadisticas_activos:
            self.estadisticas_activos[clave_stats] = {
                "wins": 0,
                "losses": 0,
                "pnl": 0.0,
                "features_base": {
                    "simbolo": features.get("simbolo"),
                    "modelo": features.get("modelo"),
                    "accion": features.get("accion"),
                },
                "patrones_vela": {},
                "regimenes": {},
            }

        stats_activo = self.estadisticas_activos[clave_stats]
        if win:
            stats_activo["wins"] += 1
        else:
            stats_activo["losses"] += 1

        stats_activo["pnl"] = round(stats_activo.get("pnl", 0) + pnl, 4)

        patron_vela = features.get("patron_vela", "NINGUNO")
        stats_activo["patrones_vela"][patron_vela] = stats_activo["patrones_vela"].get(patron_vela, 0) + 1

        regimen = features.get("regimen", "DESCONOCIDO")
        stats_activo["regimenes"][regimen] = stats_activo["regimenes"].get(regimen, 0) + 1

        stats_activo["ultimo_update"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        _, _, total_activo, wr_activo = self._stats_resumen(stats_activo)

        # ── Retroalimentación SL: aprender de cada stop loss ─────────────
        clave_sl = self._clave_retroalimentacion_sl(features)
        if clave_sl not in self.retroalimentacion_sl:
            self.retroalimentacion_sl[clave_sl] = {
                "wins": 0,
                "sl_hits": 0,
                "pnl": 0.0,
                "ultimo_sl": None,
            }
        retro = self.retroalimentacion_sl[clave_sl]
        if win:
            retro["wins"] += 1
        else:
            retro["sl_hits"] += 1
            retro["ultimo_sl"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            total_sl = retro["wins"] + retro["sl_hits"]
            sl_rate = retro["sl_hits"] / max(1, total_sl)
            print(
                f"  🧠 SL aprendido: {clave_sl.replace('|', ' + ')} | "
                f"SL rate={sl_rate*100:.0f}% ({retro['sl_hits']}SL/{retro['wins']}W) | "
                f"dist={features.get('distancia_sl_pct', '?')}%"
            )
        retro["pnl"] = round(retro.get("pnl", 0) + pnl, 4)

        # ── Activar bloqueo si cumple umbral ─────────────────────────────
        if (total_patron >= MIN_TRADES_PARA_APRENDER and
                wr_patron < UMBRAL_BLOQUEO_WR and
                not p["bloqueado"]):
            p["bloqueado"] = True
            print(f"🚫 SniperAI: PATRÓN BLOQUEADO "
                  f"(WR={wr_patron*100:.0f}%, {p['wins']}W/{p['losses']}L)\n"
                  f"   Features: {features}")

        # ── Racha global ──────────────────────────────────────────────────
        self.racha_actual.append("WIN" if win else "LOSS")
        self.racha_actual = self.racha_actual[-15:]

        # ── Historial completo ────────────────────────────────────────────
        self.historial.append({
            "ts"      : datetime.now().strftime("%Y-%m-%d %H:%M"),
            "hash"    : hash_fp,
            "features": features,
            "win"     : win,
            "pnl"     : round(pnl, 4),
            "wr_patron": round(wr_patron, 3),
            "resultado": "TP" if win else "SL",
            "clave_sl": clave_sl,
        })
        self.historial = self.historial[-200:]

        self._guardar()

        # Log siempre
        wr_global = self.total_wins / max(1, self.total_wins + self.total_losses) * 100
        icon = "✅" if win else "❌"
        print(f"  🧠 IA {icon} | patrón WR={wr_patron*100:.0f}%({p['wins']}W/{p['losses']}L) | "
              f"activo WR={wr_activo*100:.0f}%({stats_activo['wins']}W/{stats_activo['losses']}L) | "
              f"global WR={wr_global:.1f}%({self.total_wins}W/{self.total_losses}L) | "
              f"racha: {''.join('✅' if r=='WIN' else '❌' for r in self.racha_actual[-5:])}")

        # Análisis profundo cada 10 trades
        total_global = self.total_wins + self.total_losses
        if total_global > 0 and total_global % 10 == 0:
            self._analisis_profundo()

    # ─────────────────────────────────────────────────────────────────────────
    #  ANÁLISIS PROFUNDO — cada 10 trades
    # ─────────────────────────────────────────────────────────────────────────

    def _analisis_profundo(self):
        recientes = self.historial[-20:]
        if len(recientes) < 5:
            return

        wins   = [t for t in recientes if t["win"]]
        losses = [t for t in recientes if not t["win"]]
        wr     = len(wins) / len(recientes) * 100

        # Patrones con más trades
        top_patrones = sorted(
            self.patrones.items(),
            key=lambda x: x[1]["wins"] + x[1]["losses"],
            reverse=True
        )[:5]

        print("\n🧠 ─── ANÁLISIS PROFUNDO IA ───")
        print(f"  WR últimos 20 trades: {wr:.1f}%")
        print(f"  WR global: {self.total_wins/max(1,self.total_wins+self.total_losses)*100:.1f}%")

        bloqueados = [(h,p) for h,p in self.patrones.items() if p.get("bloqueado")]
        if bloqueados:
            print(f"  Patrones bloqueados: {len(bloqueados)}")
            for h, p in bloqueados[:3]:
                f = p.get("features", {})
                print(f"    🚫 {f.get('modelo','?')} {f.get('accion','?')} "
                      f"btc={f.get('btc','?')} "
                      f"({p['wins']}W/{p['losses']}L)")

        boosteados = [(h,p) for h,p in self.patrones.items()
                      if not p.get("bloqueado") and
                      p["wins"]+p["losses"] >= MIN_TRADES_PARA_APRENDER and
                      p["wins"]/max(1,p["wins"]+p["losses"]) >= UMBRAL_BOOST_WR]
        if boosteados:
            print(f"  Patrones boosteados: {len(boosteados)}")
            for h, p in boosteados[:3]:
                f = p.get("features", {})
                print(f"    🚀 {f.get('modelo','?')} {f.get('accion','?')} "
                      f"btc={f.get('btc','?')} "
                      f"({p['wins']}W/{p['losses']}L)")

        if self.estadisticas_activos:
            print("  Estadísticas por activo/modelo:")
            top_activos = sorted(
                self.estadisticas_activos.items(),
                key=lambda item: (
                    item[1].get("wins", 0) + item[1].get("losses", 0),
                    item[1].get("pnl", 0)
                ),
                reverse=True
            )[:5]

            for clave, stats in top_activos:
                w, l, total, wr_activo = self._stats_resumen(stats)
                print(
                    f"    📌 {clave.replace('|', ' + ')} = {wr_activo*100:.0f}% WR "
                    f"({w}W/{l}L, pnl {stats.get('pnl', 0):+.2f}, n={total})"
                )

        # Alerta racha (solo informativa; el bloqueo es a 10)
        racha_sl = self._contar_racha_negativa()
        if racha_sl >= RACHA_NEGATIVA_ALERTA:
            print(f"  ⚠️  {racha_sl} SL consecutivos — retroalimentando score "
                  f"(bloqueo a {RACHA_NEGATIVA_BLOQUEO})")

        if self.retroalimentacion_sl:
            peores = sorted(
                self.retroalimentacion_sl.items(),
                key=lambda item: item[1].get("sl_hits", 0) / max(
                    1, item[1].get("wins", 0) + item[1].get("sl_hits", 0)
                ),
                reverse=True
            )[:3]
            print("  Retroalimentación SL (condiciones más riesgosas):")
            for clave, stats in peores:
                total = stats.get("wins", 0) + stats.get("sl_hits", 0)
                if total < MIN_TRADES_SL_RETRO:
                    continue
                sl_rate = stats.get("sl_hits", 0) / total * 100
                print(
                    f"    🛑 {clave.replace('|', ' + ')} = {sl_rate:.0f}% SL "
                    f"({stats.get('sl_hits', 0)}SL/{stats.get('wins', 0)}W)"
                )

        print("─" * 45)

    # ─────────────────────────────────────────────────────────────────────────
    #  ESTADO RÁPIDO para logs
    # ─────────────────────────────────────────────────────────────────────────

    def estado(self) -> str:
        total = self.total_wins + self.total_losses
        if total == 0:
            return "🧠 IA: sin datos aún"
        wr = self.total_wins / total * 100
        bloq = sum(1 for p in self.patrones.values() if p.get("bloqueado"))
        racha_sl = self._contar_racha_negativa()
        racha = "".join("✅" if r=="WIN" else "❌" for r in self.racha_actual[-5:])
        return (f"🧠 IA: WR={wr:.1f}%({self.total_wins}W/{self.total_losses}L) | "
                f"patrones={len(self.patrones)}({bloq}🚫) | "
                f"activos={len(self.estadisticas_activos)} | "
                f"racha={racha} | sl_seg={racha_sl}/{RACHA_NEGATIVA_BLOQUEO}")


# Instancia global
sniper_ai = SniperAI()
