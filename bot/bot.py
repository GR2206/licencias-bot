"""Bot de confluencia: Tendencia + Order Block, ejecutando en Binance Futures.

No depende de webhooks ni de TradingView: baja las velas de Binance y calcula
la misma logica que confluence_engine.pine. Asi funciona en un celular con
Termux sin necesidad de IP publica.

Ciclo de trabajo:

  1. Baja las ultimas velas cerradas de cada simbolo y temporalidad.
  2. Calcula si la vela recien cerrada genero una entrada.
  3. Si hay entrada y no hay posicion abierta, calcula el tamano segun el
     riesgo configurado y manda la orden a mercado + SL + TP.
  4. Anota la vela en estado.json para no repetir la misma entrada.

Arranca en MODO=simulacion: registra todo pero no manda ninguna orden.
Cambialo a real solo despues de verlo funcionar.
"""

import dataclasses
import json
import os
import sys
import time
from datetime import datetime, timezone

import binance_api as api
import estrategia
import indicadores as ind

RUTA = os.path.dirname(os.path.abspath(__file__))
ARCHIVO_ESTADO = os.path.join(RUTA, "estado.json")
ARCHIVO_LOG = os.path.join(RUTA, "bot.log")


# ─────────────────────────────────────────────────────────────────────────────
#  Configuracion
# ─────────────────────────────────────────────────────────────────────────────
def cargar_env(ruta=None):
    """Lee un archivo tipo `CLAVE=valor` y lo mete en las variables de entorno.

    Evita la dependencia de python-dotenv, que en Termux es una molestia mas.
    """
    ruta = ruta or os.path.join(RUTA, "config.env")
    if not os.path.exists(ruta):
        return
    with open(ruta, "r", encoding="utf-8") as f:
        for linea in f:
            linea = linea.strip()
            if not linea or linea.startswith("#") or "=" not in linea:
                continue
            clave, valor = linea.split("=", 1)
            os.environ.setdefault(clave.strip(), valor.strip().strip('"').strip("'"))


class Ajustes:
    def __init__(self):
        self.api_key = os.getenv("BINANCE_API_KEY", "")
        self.api_secret = os.getenv("BINANCE_API_SECRET", "")
        self.entorno = os.getenv("ENTORNO", "testnet").lower()
        self.modo = os.getenv("MODO", "simulacion").lower()
        self.simbolos = [
            s.strip().upper() for s in os.getenv("SIMBOLOS", "BTCUSDT").split(",") if s.strip()
        ]
        self.temporalidades = [
            t.strip() for t in os.getenv("TEMPORALIDADES", "1h,30m").split(",") if t.strip()
        ]
        self.riesgo_pct = float(os.getenv("RIESGO_PCT", "2"))
        self.apalancamiento = int(os.getenv("APALANCAMIENTO", "5"))
        self.max_posiciones = int(os.getenv("MAX_POSICIONES", "2"))
        self.intervalo = int(os.getenv("INTERVALO_SEGUNDOS", "30"))
        self.velas = int(os.getenv("VELAS", "500"))
        self.saldo_simulado = float(os.getenv("SALDO_SIMULADO", "1000"))
        # El RR y el resto de la estrategia se leen en config_estrategia().

    @property
    def base(self):
        return api.REAL if self.entorno == "real" else api.TESTNET

    @property
    def en_vivo(self):
        return self.modo == "real"


def avisos_ventana(ajustes, cfg):
    """Revisa que la ventana de velas alcance para lo que pide la estrategia.

    El bot solo ve las ultimas VELAS velas. Si un Order Block util es mas viejo
    que eso, en vivo no existe, y el bot opera distinto a lo que se probo. Ese
    desfase es silencioso, asi que conviene avisarlo al arrancar.
    """
    avisos = []
    # Margen para que ATR, SuperTrend y RSI lleguen estabilizados a la zona util.
    calentamiento = max(200, cfg.atr_periodo * 10, cfg.ema_periodo if cfg.usar_ema else 0)

    if cfg.edad_max_bloque <= 0:
        if ajustes.velas < 1500:
            avisos.append(
                f"EDAD_MAX_BLOQUE=0 (los bloques no caducan) con VELAS={ajustes.velas}. "
                "Un bloque mas viejo que la ventana no se ve y el bot opera distinto al "
                "backtest. Poné VELAS=1500 o EDAD_MAX_BLOQUE=250"
            )
    elif ajustes.velas < cfg.edad_max_bloque + calentamiento:
        avisos.append(
            f"VELAS={ajustes.velas} es corto para EDAD_MAX_BLOQUE={cfg.edad_max_bloque}: "
            f"hacen falta al menos {cfg.edad_max_bloque + calentamiento}"
        )

    if ajustes.velas > 1500:
        avisos.append(f"Binance entrega 1500 velas como maximo, VELAS={ajustes.velas} se recorta")
    return avisos


def config_estrategia():
    """Arma la Config de la estrategia desde las variables de entorno.

    Cada campo de estrategia.Config se puede sobreescribir con una variable en
    MAYUSCULAS con el mismo nombre. Por ejemplo RIESGO_MAX_PCT=5 o PIVOTE=3.
    Asi se puede afinar un activo sin tocar el codigo.
    """
    cfg = estrategia.Config()
    for campo in dataclasses.fields(cfg):
        crudo = os.getenv(campo.name.upper())
        if crudo is None or crudo.strip() == "":
            continue
        actual = getattr(cfg, campo.name)
        texto = crudo.strip()
        try:
            if isinstance(actual, bool):  # bool antes que int: bool es subclase
                valor = texto.lower() in ("1", "true", "si", "sí", "yes", "y", "on")
            elif isinstance(actual, int):
                valor = int(float(texto))
            elif isinstance(actual, float):
                valor = float(texto)
            else:
                valor = texto
        except ValueError:
            registrar(f"Valor invalido para {campo.name.upper()}: {texto!r}, lo ignoro")
            continue
        setattr(cfg, campo.name, valor)
    return cfg


def registrar(*partes):
    marca = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    linea = f"[{marca}] " + " ".join(str(p) for p in partes)
    print(linea, flush=True)
    try:
        with open(ARCHIVO_LOG, "a", encoding="utf-8") as f:
            f.write(linea + "\n")
    except OSError:
        pass


def leer_estado():
    if not os.path.exists(ARCHIVO_ESTADO):
        return {}
    try:
        with open(ARCHIVO_ESTADO, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def guardar_estado(estado):
    try:
        with open(ARCHIVO_ESTADO, "w", encoding="utf-8") as f:
            json.dump(estado, f, indent=2)
    except OSError as e:
        registrar("No pude guardar el estado:", e)


# ─────────────────────────────────────────────────────────────────────────────
#  Tamano de la posicion
# ─────────────────────────────────────────────────────────────────────────────
def calcular_cantidad(cliente, ajustes, simbolo, senal, saldo):
    """Cantidad tal que, si salta el stop, se pierda el riesgo configurado.

    Devuelve (cantidad, motivo_del_rechazo). Si la cantidad es 0 el motivo
    explica por que.
    """
    distancia = abs(senal.entrada - senal.stop)
    if distancia <= 0:
        return 0.0, "la distancia al stop es cero"

    riesgo_usdt = saldo * ajustes.riesgo_pct / 100
    cantidad = cliente.ajustar_cantidad(simbolo, riesgo_usdt / distancia)
    if cantidad <= 0:
        return 0.0, f"el riesgo de {riesgo_usdt:.2f} USDT no alcanza para el lote minimo"

    filtros = cliente.filtros(simbolo)
    nocional = cantidad * senal.entrada
    if cantidad < filtros["min_qty"]:
        return 0.0, f"cantidad {cantidad} por debajo del minimo {filtros['min_qty']}"
    if nocional < filtros["min_notional"]:
        return 0.0, (
            f"nocional {nocional:.2f} USDT por debajo del minimo "
            f"{filtros['min_notional']:.2f}. Subi el riesgo o usa un simbolo mas barato"
        )

    margen = nocional / max(1, ajustes.apalancamiento)
    if margen > saldo:
        return 0.0, (
            f"hacen falta {margen:.2f} USDT de margen y hay {saldo:.2f}. "
            "Bajá el riesgo o subí el apalancamiento"
        )
    return cantidad, ""


# ─────────────────────────────────────────────────────────────────────────────
#  Ejecucion
# ─────────────────────────────────────────────────────────────────────────────
def ejecutar(cliente, ajustes, simbolo, senal, cantidad, cfg, estado):
    lado = "BUY" if senal.es_compra else "SELL"
    lado_cierre = "SELL" if senal.es_compra else "BUY"
    parcial = cliente.ajustar_cantidad(simbolo, cantidad * cfg.fraccion_parcial)
    usar_parcial = cfg.parcial_1r and parcial > 0 and parcial < cantidad

    if not ajustes.en_vivo:
        detalle = f" | parcial {parcial} en {senal.objetivo_1r}" if usar_parcial else ""
        registrar(
            f"  [SIMULACION] {lado} {cantidad} {simbolo} "
            f"entrada {senal.entrada} SL {senal.stop} TP {senal.objetivo}{detalle}"
        )
        return True

    try:
        cliente.apalancamiento(simbolo, ajustes.apalancamiento)
    except api.ErrorBinance as e:
        registrar("  Aviso al fijar apalancamiento:", e)

    try:
        orden = cliente.orden_mercado(simbolo, lado, cantidad)
        registrar(f"  Entrada enviada: {lado} {cantidad} {simbolo} (id {orden.get('orderId')})")
    except api.ErrorBinance as e:
        registrar("  ERROR al abrir la posicion:", e)
        return False

    # El SL es lo primero: si algo falla, mejor quedarse sin TP que sin stop.
    try:
        cliente.orden_stop(simbolo, lado_cierre, senal.stop)
        registrar(f"  SL colocado en {senal.stop}")
    except api.ErrorBinance as e:
        registrar("  ERROR al colocar el SL, cierro la posicion por seguridad:", e)
        try:
            cliente.orden_mercado(simbolo, lado_cierre, cantidad)
            registrar("  Posicion cerrada a mercado")
        except api.ErrorBinance as e2:
            registrar("  NO PUDE CERRAR, revisá la cuenta a mano:", e2)
        return False

    if usar_parcial:
        try:
            cliente.orden_objetivo_parcial(simbolo, lado_cierre, senal.objetivo_1r, parcial)
            registrar(f"  Parcial de {parcial} colocada en 1R ({senal.objetivo_1r})")
        except api.ErrorBinance as e:
            registrar("  Aviso: no pude colocar la parcial:", e)
            usar_parcial = False

    try:
        cliente.orden_objetivo(simbolo, lado_cierre, senal.objetivo)
        registrar(f"  TP final colocado en {senal.objetivo}")
    except api.ErrorBinance as e:
        registrar("  Aviso: quedo sin TP automatico:", e)

    estado.setdefault("posiciones", {})[simbolo] = {
        "accion": senal.accion,
        "entrada": senal.entrada,
        "stop": senal.stop,
        "objetivo": senal.objetivo,
        "cantidad": cantidad,
        "parcial_pendiente": usar_parcial,
    }
    guardar_estado(estado)
    return True


def gestionar_posiciones(cliente, ajustes, estado):
    """Cuando se cobra la parcial de 1R, mueve el stop a la entrada.

    Binance no sabe hacer esto solo: hay que detectar que la posicion se redujo
    y reemplazar el stop. Se revisa en cada ciclo.
    """
    abiertas = estado.get("posiciones", {})
    if not abiertas or not ajustes.en_vivo:
        return

    for simbolo in list(abiertas.keys()):
        datos = abiertas[simbolo]
        try:
            actual = abs(cliente.posicion(simbolo))
        except api.ErrorBinance as e:
            registrar(f"No pude leer la posicion de {simbolo}:", e)
            continue

        if actual <= 0:
            registrar(f"{simbolo}: posicion cerrada, limpio ordenes sueltas")
            try:
                cliente.cancelar_ordenes(simbolo)
            except api.ErrorBinance as e:
                registrar("  Aviso al cancelar:", e)
            abiertas.pop(simbolo, None)
            guardar_estado(estado)
            continue

        if datos.get("parcial_pendiente") and actual < datos["cantidad"] * 0.9:
            registrar(f"{simbolo}: parcial cobrada, muevo el stop a la entrada")
            lado_cierre = "SELL" if datos["accion"] == "BUY" else "BUY"
            try:
                cliente.cancelar_ordenes(simbolo)
                cliente.orden_stop(simbolo, lado_cierre, datos["entrada"])
                cliente.orden_objetivo(simbolo, lado_cierre, datos["objetivo"])
                datos["parcial_pendiente"] = False
                datos["stop"] = datos["entrada"]
                guardar_estado(estado)
                registrar(f"  Operacion sin riesgo: stop en {datos['entrada']}")
            except api.ErrorBinance as e:
                registrar("  ERROR moviendo el stop, revisá a mano:", e)


def revisar(cliente, ajustes, simbolo, temporalidad, cfg, estado):
    clave = f"{simbolo}:{temporalidad}"
    try:
        crudas = cliente.klines(simbolo, temporalidad, ajustes.velas)
    except api.ErrorBinance as e:
        registrar(f"{clave} no pude bajar velas:", e)
        return

    velas = ind.desde_klines(crudas)
    if len(velas) < 2:
        return
    velas = velas[:-1]  # la ultima esta abierta, no sirve

    senal = estrategia.senal_actual(velas, cfg)
    if senal is None:
        return

    if estado.get(clave, {}).get("ultima_vela") == senal.tiempo:
        return  # ya la procesamos

    momento = datetime.fromtimestamp(senal.tiempo / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M")
    registrar(
        f"SENAL {senal.accion} {clave} vela {momento} | entrada {senal.entrada} "
        f"SL {senal.stop} TP {senal.objetivo} | riesgo {senal.riesgo_pct:.2f}%"
    )

    estado.setdefault(clave, {})["ultima_vela"] = senal.tiempo
    guardar_estado(estado)

    # Sin claves solo se puede simular: se asume cuenta plana y saldo de prueba.
    if not ajustes.api_key:
        saldo = ajustes.saldo_simulado
    else:
        try:
            if abs(cliente.posicion(simbolo)) > 0:
                registrar(f"  Ya hay posicion abierta en {simbolo}, no hago nada")
                return
            abiertas = sum(1 for s in ajustes.simbolos if abs(cliente.posicion(s)) > 0)
            if abiertas >= ajustes.max_posiciones:
                registrar(f"  Limite de {ajustes.max_posiciones} posiciones alcanzado")
                return
            saldo = cliente.saldo_usdt()
        except api.ErrorBinance as e:
            registrar("  No pude consultar la cuenta:", e)
            return

    cantidad, motivo = calcular_cantidad(cliente, ajustes, simbolo, senal, saldo)
    if cantidad <= 0:
        registrar("  Operacion descartada:", motivo)
        return

    ejecutar(cliente, ajustes, simbolo, senal, cantidad, cfg, estado)


def main():
    cargar_env()
    ajustes = Ajustes()
    cfg = config_estrategia()

    registrar("=" * 62)
    registrar("Bot de confluencia — Tendencia + Order Block")
    registrar(f"  Entorno       : {ajustes.entorno} ({ajustes.base})")
    registrar(f"  Modo          : {'REAL, manda ordenes' if ajustes.en_vivo else 'simulacion'}")
    registrar(f"  Simbolos      : {', '.join(ajustes.simbolos)}")
    registrar(f"  Temporalidades: {', '.join(ajustes.temporalidades)}")
    registrar(f"  Riesgo        : {ajustes.riesgo_pct}% de la cuenta por operacion")
    registrar(f"  Apalancamiento: x{ajustes.apalancamiento}")
    registrar(
        f"  Estrategia    : RR 1:{cfg.rr} | stop {cfg.sl_modo} | "
        f"riesgo permitido {cfg.riesgo_min_pct}-{cfg.riesgo_max_pct}% del precio"
    )
    registrar(
        f"                  pivote {cfg.pivote} | FVG {'si' if cfg.exigir_fvg else 'no'} | "
        f"alejarse {cfg.alejarse_atr} ATR | confirmacion {cfg.confirmacion} | "
        f"parcial {'si' if cfg.parcial_1r else 'no'}"
    )
    for aviso in avisos_ventana(ajustes, cfg):
        registrar(f"  AVISO: {aviso}")
    registrar("=" * 62)

    if ajustes.en_vivo and ajustes.entorno == "real":
        registrar("ATENCION: dinero real. Tenés 10 segundos para cortar con Ctrl+C.")
        time.sleep(10)

    cliente = api.Cliente(ajustes.api_key, ajustes.api_secret, ajustes.base)
    try:
        desfase = cliente.sincronizar_hora()
        registrar(f"Reloj sincronizado (desfase {desfase} ms)")
    except api.ErrorBinance as e:
        registrar("No pude sincronizar la hora:", e)

    if ajustes.api_key:
        try:
            registrar(f"Saldo disponible: {cliente.saldo_usdt():.2f} USDT")
        except api.ErrorBinance as e:
            registrar("No pude leer el saldo (revisá las claves):", e)

    estado = leer_estado()
    while True:
        try:
            gestionar_posiciones(cliente, ajustes, estado)
        except Exception as e:
            registrar("Error gestionando posiciones:", repr(e))

        for simbolo in ajustes.simbolos:
            for temporalidad in ajustes.temporalidades:
                try:
                    revisar(cliente, ajustes, simbolo, temporalidad, cfg, estado)
                except Exception as e:  # el bot no se muere por un error puntual
                    registrar(f"Error inesperado en {simbolo}:{temporalidad}:", repr(e))
        time.sleep(ajustes.intervalo)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        registrar("Cortado a mano. Ojo: las posiciones abiertas siguen abiertas.")
        sys.exit(0)
