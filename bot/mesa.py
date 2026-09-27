"""La mesa: elegis el activo, apretas CALCULAR y sale el trade. Sin dependencias.

    python mesa.py                      # http://localhost:8778
    python mesa.py --puerto 9000
    python mesa.py --simbolos CHZUSDT,SOLUSDT,ADAUSDT

Una pantalla, tres controles: el cajon del activo, la temporalidad, y el boton.
Abajo sale la entrada como orden limite, el stop, el objetivo, y el contexto de
H4 y H1 para saber si va a favor o contra la tendencia mayor.

No manda ordenes. Calcula y te dice donde poner los precios; las cargas vos.
Cada trade que sale queda en la bitacora de la misma pagina: se ve ahi y se
baja un CSV. El archivo vive al lado de mesa.py, en bitacora.json.
Esa separacion es a proposito: mientras no haya una medicion que diga que esto
le gana al azar, que un boton pueda mandar una orden sola no es una comodidad,
es una forma de perder plata rapido.

Usa el mismo analizar() que la consola, asi que la pantalla y `python zonas.py`
no pueden decir cosas distintas. Y el costo sale del mercado del que realmente
vinieron las velas: futuros 0.10% ida y vuelta, spot 0.20%. Si pedis futuros y
Binance bloquea tu region, cae a spot y te lo dice arriba, porque con el doble
de comision el piso del stop se duplica y el trade puede dejar de cerrar.
"""
import csv
import io
import json
import os
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import zonas

# Los diez de arriba son los de libro mas profundo. Los dieciocho de abajo
# salen de la lista del celular: futuros USDT-M con historia real y un precio
# en el que el tick no se come la zona. Quedaron afuera los de precio
# microscopico y los de libro flaco (BRISE, WIN, XEC, MBL, SLP, SHIB, PEPE,
# RSR, C98, TLM): ahi el spread se come el piso del stop antes de que el
# analisis diga nada.
POR_DEFECTO = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
               "ADAUSDT", "DOGEUSDT", "AVAXUSDT", "LINKUSDT", "CHZUSDT",
               "LTCUSDT", "TRXUSDT", "DOTUSDT", "NEARUSDT", "ATOMUSDT",
               "UNIUSDT", "AAVEUSDT", "FILUSDT", "OPUSDT", "ARBUSDT",
               "ETCUSDT", "XLMUSDT", "ICPUSDT", "FETUSDT", "RUNEUSDT",
               "ARUSDT", "GRTUSDT", "DYDXUSDT"]
TFS = ["5m", "15m", "30m", "1h", "4h"]
# El numero que se ve abajo de la pagina. Si no dice este, el archivo del
# celular es viejo. Se sube junto con zonas.VERSION.
VERSION = 20

# Cuantas velas se deja puesta la orden limite. En 15m son 6 horas. Pasado
# eso, si el precio no toco la entrada, el trade vencio: no es una perdida,
# es una orden que no se lleno.
VIGENCIA_VELAS = 24
BITACORA = os.environ.get(
    "MESA_BITACORA",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "bitacora.json"))
_candado_bit = threading.Lock()

CACHE_SEG = 45
_cache = {}
_candado = threading.Lock()


def _cuando(ms):
    if not ms:
        return ""
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%d/%m %H:%M")


def _cargar_bitacora():
    if not os.path.exists(BITACORA):
        return []
    try:
        with open(BITACORA, encoding="utf-8") as f:
            datos = json.load(f)
        return datos if isinstance(datos, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def _guardar_bitacora(filas):
    carpeta = os.path.dirname(BITACORA)
    if carpeta:
        os.makedirs(carpeta, exist_ok=True)
    temporal = BITACORA + ".tmp"
    with open(temporal, "w", encoding="utf-8") as f:
        json.dump(filas, f, ensure_ascii=False, indent=2)
    os.replace(temporal, BITACORA)


def _toca_entrada(largo, vela, entrada):
    return vela.minimo <= entrada if largo else vela.maximo >= entrada


def _toque_salida(largo, vela, stop, objetivo):
    """SL gana si la misma vela toca el stop y el objetivo: adentro de la vela
    no se sabe el orden, y contar el objetivo ahi fabrica un acierto."""
    if largo:
        sl = vela.minimo <= stop
        tp = vela.maximo >= objetivo
    else:
        sl = vela.maximo >= stop
        tp = vela.minimo <= objetivo
    if sl:
        return "sl"
    if tp:
        return "tp"
    return None


def _cierre_invalida(largo, vela, invalida):
    if invalida is None:
        return False
    return vela.cierre < invalida if largo else vela.cierre > invalida


def _fin(estado, vela, minutos, erres, detalle):
    return {
        "estado": estado,
        "r": erres,
        "detalle": detalle,
        "resuelto_ms": vela.tiempo + minutos * 60_000,
        "resuelto": _cuando(vela.tiempo + minutos * 60_000),
    }


def juzgar(fila, velas):
    """Como salio el trade, mirando solo velas ABIERTAS despues de anotarlo.

    La vela que estaba en curso cuando se apreto CALCULAR no cuenta: el precio
    de esa vela ya habia pasado antes de que la orden existiera.
    """
    minutos = zonas.MINUTOS.get(fila["tf"], 15)
    anotado = fila["anotado_ms"]
    if velas and velas[0].tiempo > anotado + minutos * 60_000 * 2:
        return {
            "estado": fila.get("estado") or "pendiente",
            "r": fila.get("r"),
            "detalle": "faltan velas del momento en que se anoto",
            "resuelto_ms": None, "resuelto": "",
        }

    largo = fila["lado"] == "LONG"
    entrada = float(fila["entrada"])
    stop = float(fila["stop"])
    objetivo = float(fila["objetivo"])
    invalida = fila.get("invalida")
    invalida = None if invalida is None else float(invalida)
    riesgo = abs(entrada - stop) or 1e-12
    posteriores = [v for v in velas if v.tiempo >= anotado]
    lleno = False
    vistas = 0

    for v in posteriores:
        if not lleno:
            vistas += 1
            entro = _toca_entrada(largo, v, entrada)
            toco_objetivo = v.maximo >= objetivo if largo else v.minimo <= objetivo
            if toco_objetivo and not entro:
                return _fin("agotado", v, minutos, None,
                            "el objetivo se negocio sin llenar la orden: "
                            "cancela el limite")
            if _cierre_invalida(largo, v, invalida) and not entro:
                return _fin("invalidado", v, minutos, None,
                            "un cierre paso la invalidacion y la orden no se lleno")
            if entro:
                lleno = True
                salida = _toque_salida(largo, v, stop, objetivo)
                if salida == "sl":
                    return _fin("sl", v, minutos, -1.0,
                                "el precio lleno la orden y toco el stop")
                if salida == "tp":
                    return _fin("tp", v, minutos, fila.get("rr_real"),
                                "el precio llego al objetivo")
                if _cierre_invalida(largo, v, invalida):
                    movido = ((v.cierre - entrada) if largo
                              else (entrada - v.cierre))
                    return _fin("invalidado", v, minutos, round(movido / riesgo, 3),
                                "la orden se lleno y el cierre de esa vela "
                                "invalido la zona")
                continue
            if vistas >= VIGENCIA_VELAS:
                return _fin("vencido", v, minutos, None,
                            f"pasaron {VIGENCIA_VELAS} velas de {fila['tf']} "
                            f"y el limite no se lleno")
        else:
            salida = _toque_salida(largo, v, stop, objetivo)
            if salida == "sl":
                return _fin("sl", v, minutos, -1.0, "se fue al stop")
            if salida == "tp":
                return _fin("tp", v, minutos, fila.get("rr_real"),
                            "llego al objetivo")
            if _cierre_invalida(largo, v, invalida):
                movido = (v.cierre - entrada) if largo else (entrada - v.cierre)
                return _fin("invalidado", v, minutos, round(movido / riesgo, 3),
                            "el cierre paso la invalidacion con la posicion abierta")

    if lleno:
        return {"estado": "en_curso", "r": None, "resuelto_ms": None,
                "resuelto": "",
                "detalle": "la orden se lleno y el trade sigue abierto"}
    return {"estado": "pendiente", "r": None, "resuelto_ms": None,
            "resuelto": "",
            "detalle": "esperando que el precio toque la entrada"}


def _vista(filas):
    orden = sorted(filas, key=lambda f: f.get("anotado_ms") or 0, reverse=True)
    cerrados = [f for f in orden if f.get("r") is not None]
    tp = sum(1 for f in orden if f.get("estado") == "tp")
    sl = sum(1 for f in orden if f.get("estado") == "sl")
    return {
        "filas": orden,
        "resumen": {
            "n": len(orden),
            "tp": tp,
            "sl": sl,
            "en_curso": sum(1 for f in orden if f.get("estado") == "en_curso"),
            "pendiente": sum(1 for f in orden if f.get("estado") == "pendiente"),
            "invalidado": sum(1 for f in orden if f.get("estado") == "invalidado"),
            "agotado": sum(1 for f in orden if f.get("estado") == "agotado"),
            "vencido": sum(1 for f in orden if f.get("estado") == "vencido"),
            "r_medio": (round(sum(f["r"] for f in cerrados) / len(cerrados), 3)
                        if cerrados else None),
        },
    }


def anotar(analisis):
    """Guarda el trade recomendado. El mismo setup no se anota dos veces."""
    r = analisis.get("recomendacion")
    if not r or r.get("nacio_ms") is None:
        return
    ident = (f"{analisis['simbolo']}|{analisis['tf']}|{r['nacio_ms']}|"
             f"{r['lado']}|{r['entrada']}")
    ahora = int(time.time() * 1000)
    fila = {
        "id": ident,
        "anotado_ms": ahora,
        "anotado": _cuando(ahora),
        "simbolo": analisis["simbolo"],
        "tf": analisis["tf"],
        "mercado": analisis.get("mercado") or "futuros",
        "lado": r["lado"],
        "accion": r["accion"],
        "zona_tipo": r["zona_tipo"],
        "entrada": r["entrada"],
        "stop": r["stop"],
        "objetivo": r["objetivo"],
        "invalida": r["invalida"],
        "riesgo_pct": r["riesgo_pct"],
        "rr_real": r["rr_real"],
        "costo_r": r["costo_r"],
        "precio_al_anotar": analisis.get("precio"),
        "decimales": analisis.get("decimales", 2),
        "nacio": r.get("nacio"),
        "nacio_ms": r["nacio_ms"],
        "estado": "pendiente",
        "r": None,
        "detalle": "recien anotado, esperando el precio",
        "resuelto_ms": None,
        "resuelto": "",
    }
    with _candado_bit:
        filas = _cargar_bitacora()
        if any(x.get("id") == ident for x in filas):
            return
        filas.append(fila)
        _guardar_bitacora(filas)


def actualizar_bitacora():
    """Recorre las ordenes abiertas contra las velas de Binance y anota como salieron."""
    with _candado_bit:
        filas = _cargar_bitacora()
    cambios = {}
    for f in filas:
        if f.get("estado") not in ("pendiente", "en_curso"):
            continue
        try:
            minutos = zonas.MINUTOS.get(f["tf"], 15)
            ahora = int(time.time() * 1000)
            transcurridas = int((ahora - f["anotado_ms"]) / (minutos * 60_000)) + 5
            n = max(VIGENCIA_VELAS + 5, min(2000, transcurridas))
            velas, _ = zonas.bajar(f["simbolo"], f["tf"], n,
                                   f.get("mercado") or "futuros")
            cambios[f["id"]] = juzgar(f, velas)
        except (RuntimeError, OSError, ValueError) as e:
            cambios[f["id"]] = {"detalle": f"no pude mirar el precio: {e}"}
    with _candado_bit:
        filas = _cargar_bitacora()
        for f in filas:
            nuevo = cambios.get(f.get("id"))
            if nuevo and f.get("estado") in ("pendiente", "en_curso"):
                f.update(nuevo)
        _guardar_bitacora(filas)
        return _vista(filas)


def bitacora_csv(filas):
    buffer = io.StringIO()
    campos = ["anotado", "simbolo", "tf", "lado", "zona_tipo", "entrada",
              "stop", "objetivo", "invalida", "estado", "r", "detalle",
              "precio_al_anotar", "resuelto", "nacio"]
    escritor = csv.DictWriter(buffer, fieldnames=campos, extrasaction="ignore")
    escritor.writeheader()
    for f in filas:
        escritor.writerow(f)
    return buffer.getvalue()


def mirar(simbolo, tf, dias, rr, mercado, costo_max_r):
    """Si ese par tiene trade en la temporalidad pedida. No anota la bitacora."""
    a = zonas.analizar(simbolo, tf, dias, rr, costo_max_r, mercado)
    r = a.get("recomendacion")
    if not r:
        return {"simbolo": a["simbolo"], "lado": None}
    return {
        "simbolo": a["simbolo"],
        "accion": r["accion"],
        "lado": "LONG" if r["zona"]["direccion"] == 1 else "SHORT",
    }


def calcular(simbolo, tf, dias, rr, mercado, costo_max_r):
    """analizar() con cache corto, para que apretar dos veces no baje todo de nuevo."""
    clave = (simbolo, tf, dias, rr, mercado, costo_max_r)
    with _candado:
        guardado = _cache.get(clave)
        if guardado and time.time() - guardado[0] < CACHE_SEG:
            return guardado[1]

    a = zonas.analizar(simbolo, tf, dias, rr, costo_max_r, mercado)
    salida = _para_web(a)
    with _candado:
        _cache[clave] = (time.time(), salida)
    return salida


def _osciladores_web(osc):
    """RSI(6) y MACD de la temporalidad del trade, para mostrarlos en la mesa."""
    if not osc:
        return None
    salida = {}
    for clave in ("rsi", "dif", "dea", "hist"):
        valor = osc.get(clave)
        salida[clave] = None if valor is None else float(valor)
    return salida


def _para_web(a):
    """El diccionario sin las velas crudas, que no sirven en el navegador."""
    d = a["decimales"]

    def pr(x):
        return None if x is None else round(x, d + 2)

    zs = []
    for z in a["zonas"]:
        p = z["plan"]
        zs.append({
            "tipo": z["tipo"],
            "lado": "LONG" if z["direccion"] == 1 else "SHORT",
            "puntos": round(z["puntos"], 1),
            "techo": pr(z["top"]), "piso": pr(z["bot"]),
            "nacio": datetime.fromtimestamp(
                z["tiempo"] / 1000, timezone.utc).strftime("%d/%m %H:%M"),
            "vivo": z["vivo"],
            "operable": z["operable"],
            "motivo_operable": z["motivo_operable"],
            "entrada": pr(p["entrada"]), "stop": pr(p["stop"]),
            "objetivo": pr(p["objetivo"]),
            "riesgo_pct": round(p["riesgo_pct"], 2),
            "costo_r": round(p["costo_r"], 3),
            "ensanchado": p["ensanchado"],
            "stop_zona_pct": round(p["stop_zona_pct"], 2),
            "motivos": z["motivos"],
            "confluencia": z.get("confluencia") or [],
            "falta_confluencia": z.get("falta_confluencia") or [],
            "vwap_toca": bool(p.get("vwap_toca")),
            "vwap_afino": bool(p.get("vwap_afino")),
        })

    ctx = []
    for c in a["contexto"]:
        if "error" in c:
            ctx.append({"tf": c["tf"], "error": c["error"]})
            continue
        ctx.append({
            "tf": c["tf"], "sesgo": c["sesgo"],
            "rsi": None if c["rsi"] is None else round(c["rsi"]),
            "lecturas": c["lecturas"],
            "alto_24h": pr(c["alto_24h"]), "bajo_24h": pr(c["bajo_24h"]),
            "pos_rango": round(c["pos_rango"]),
        })

    r = a["recomendacion"]
    rec = None
    if r:
        z, p = r["zona"], r["zona"]["plan"]
        rec = {
            "accion": r["accion"], "tipo_orden": r["tipo_orden"],
            "lado": "LONG" if z["direccion"] == 1 else "SHORT",
            "zona_tipo": z["tipo"],
            "entrada": pr(p["entrada"]), "stop": pr(p["stop"]),
            "objetivo": pr(p["objetivo"]),
            "riesgo_pct": round(p["riesgo_pct"], 2),
            "rr_real": round(p.get("rr_real", a["rr"]), 2),
            "costo_r": round(p["costo_r"], 3),
            "ensanchado": p["ensanchado"],
            "stop_zona_pct": round(p["stop_zona_pct"], 2),
            "invalida": pr(r["invalida"]),
            "distancia_pct": round(r["distancia_pct"], 2),
            "acierto_empate": round(r["acierto_empate"], 1),
            "acierto_azar": round(r["acierto_azar"], 1),
            "contracorriente": r["contracorriente"],
            "contra_tf": r["contra_tf"], "favor_tf": r["favor_tf"],
            "motivos": z["motivos"],
            "nacio": datetime.fromtimestamp(
                z["tiempo"] / 1000, timezone.utc).strftime("%d/%m %H:%M"),
            "nacio_ms": z["tiempo"],
            "confluencia": z.get("confluencia") or [],
            "vwap_afino": bool(p.get("vwap_afino")),
            "gatillo": r.get("gatillo") or "",
        }

    return {
        "simbolo": a["simbolo"], "tf": a["tf"], "dias": a["dias"], "rr": a["rr"],
        "mercado": a["mercado"], "mercado_nombre": a["mercado_nombre"],
        "mercado_pedido": a["mercado_pedido"], "respaldo": a["respaldo"],
        "costo_pct": a["costo_pct"],
        "piso_stop_pct": round(a["piso_stop_pct"], 2),
        "stop_max_atr": a["stop_max_atr"], "tick": a["tick"],
        "precio": pr(a["precio"]), "ultimo_cierre": pr(a["ultimo_cierre"]),
        "vwap": pr(a.get("vwap")),
        "osciladores": _osciladores_web(a.get("osciladores")),
        "atr": pr(a["atr"]), "atr_pct": round(a["atr_pct"], 2),
        "decimales": d,
        "desde": datetime.fromtimestamp(
            a["desde_ms"] / 1000, timezone.utc).strftime("%d/%m %H:%M"),
        "hasta": datetime.fromtimestamp(
            a["hasta_ms"] / 1000, timezone.utc).strftime("%d/%m %H:%M"),
        "calculado": datetime.now(timezone.utc).strftime("%H:%M:%S"),
        "contexto": ctx, "zonas": zs, "recomendacion": rec,
        "diagnostico": a["diagnostico"],
        "version_mesa": VERSION,
        "version_zonas": getattr(zonas, "VERSION", None),
        "pine": zonas.escribir_pine(a["simbolo"], a["tf"], a["zonas"],
                                    a["_velas"], a["rr"], a["dias"]),
    }


PAGINA = """<!DOCTYPE html>
<html lang="es"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Mesa</title>
<style>
  :root { --fondo:#0d1117; --caja:#161b22; --borde:#30363d; --texto:#e6edf3;
          --suave:#8b949e; --verde:#3fb950; --rojo:#f85149; --ambar:#d29922;
          --azul:#58a6ff; }
  * { box-sizing:border-box; -webkit-tap-highlight-color:transparent; }
  body { margin:0; background:var(--fondo); color:var(--texto); font-size:15px;
         font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; }
  header { padding:14px 16px; border-bottom:1px solid var(--borde); }
  h1 { margin:0; font-size:17px; letter-spacing:.3px; }
  .sub { color:var(--suave); font-size:12px; margin-top:3px; }
  main { padding:12px; max-width:720px; margin:0 auto; }
  .panel { background:var(--caja); border:1px solid var(--borde);
           border-radius:12px; padding:14px; margin-bottom:12px; }
  label { display:block; font-size:12px; color:var(--suave); margin-bottom:5px;
          text-transform:uppercase; letter-spacing:.5px; }
  select, input { width:100%; background:#0d1117; color:var(--texto);
          border:1px solid var(--borde); border-radius:8px; padding:11px 10px;
          font-size:16px; font-family:inherit; }
  .rejilla { display:grid; grid-template-columns:1fr 1fr; gap:10px; }
  .rejilla3 { display:grid; grid-template-columns:1fr 1fr 1fr; gap:10px; }
  .campo { margin-bottom:12px; }
  #calcular { width:100%; margin-top:2px; background:var(--azul); color:#04121f;
          border:0; border-radius:10px; padding:15px; font-size:16px;
          font-weight:700; letter-spacing:.6px; cursor:pointer; }
  #calcular:disabled { background:#21262d; color:var(--suave); }
  #calcular:active { filter:brightness(.9); }
  #buscar { width:100%; margin-top:8px; background:transparent; color:var(--azul);
          border:1px solid var(--azul); border-radius:10px; padding:13px;
          font-size:15px; font-weight:700; letter-spacing:.6px; cursor:pointer; }
  #buscar:disabled { color:var(--suave); border-color:var(--borde); }
  .giro { width:28px; height:28px; margin:4px auto 10px; border-radius:50%;
          border:3px solid var(--borde); border-top-color:var(--azul);
          animation:girar .8s linear infinite; }
  @keyframes girar { to { transform:rotate(360deg); } }
  .filaTrade { width:100%; display:flex; justify-content:space-between;
          align-items:center; background:transparent; color:var(--texto);
          border:0; border-bottom:1px solid var(--borde); padding:13px 2px;
          font-size:16px; font-family:inherit; cursor:pointer; }
  .filaTrade:active { background:#21262d; }
  .ladoL { color:var(--verde); font-weight:700; }
  .ladoS { color:var(--rojo); font-weight:700; }
  .pasando { font-size:18px; font-weight:700; letter-spacing:.4px;
             color:var(--texto); }
  .estado { text-align:center; color:var(--suave); font-size:13px; padding:18px; }
  .trade { border-radius:12px; padding:0; overflow:hidden; margin-bottom:12px;
           border:1px solid var(--borde); }
  .trade h2 { margin:0; padding:13px 14px; font-size:16px; letter-spacing:.5px; }
  .tLONG h2 { background:#0d2818; color:var(--verde);
              border-bottom:1px solid var(--verde); }
  .tSHORT h2 { background:#2d1214; color:var(--rojo);
               border-bottom:1px solid var(--rojo); }
  .niveles { display:grid; grid-template-columns:1fr 1fr 1fr; gap:1px;
             background:var(--borde); }
  .nivel { background:var(--caja); padding:12px 8px; text-align:center; }
  .nivel .et { font-size:10px; color:var(--suave); text-transform:uppercase;
               letter-spacing:.7px; }
  .nivel .vl { font-size:17px; font-weight:650; margin-top:4px;
               font-variant-numeric:tabular-nums; }
  .nivel.sl .vl { color:var(--rojo); } .nivel.tp .vl { color:var(--verde); }
  .cuerpo { background:var(--caja); padding:12px 14px; font-size:13px;
            line-height:1.65; }
  .cuerpo table { width:100%; border-collapse:collapse;
                  font-variant-numeric:tabular-nums; }
  .cuerpo td { padding:4px 0; border-top:1px solid #21262d; }
  .cuerpo td:first-child { color:var(--suave); }
  .cuerpo td:last-child { text-align:right; }
  .nota { margin-top:10px; padding:10px; border-radius:8px; font-size:12.5px;
          line-height:1.55; }
  .nAmbar { background:#2d2000; border:1px solid var(--ambar); color:#f0d68a; }
  .nRojo { background:#2d1214; border:1px solid var(--rojo); color:#ffb4ab; }
  .nGris { background:#12171e; border:1px solid var(--borde);
           color:var(--suave); }
  .ctx { display:grid; grid-template-columns:1fr 1fr; gap:10px; }
  .ctxCaja { background:#12171e; border:1px solid var(--borde);
             border-radius:8px; padding:10px; font-size:12px; }
  .ctxTf { font-weight:700; font-size:14px; }
  .alcista { color:var(--verde); } .bajista { color:var(--rojo); }
  .mixto { color:var(--ambar); }
  .barra { height:4px; background:#21262d; border-radius:2px; margin-top:7px;
           position:relative; }
  .barra i { position:absolute; top:-2px; width:3px; height:8px;
             background:var(--texto); border-radius:1px; }
  h3 { font-size:12px; color:var(--suave); text-transform:uppercase;
       letter-spacing:.6px; margin:16px 0 8px; }
  .zona { background:var(--caja); border:1px solid var(--borde);
          border-left-width:3px; border-radius:8px; padding:10px 12px;
          margin-bottom:8px; font-size:12.5px; }
  .zLONG { border-left-color:var(--verde); }
  .zSHORT { border-left-color:var(--rojo); }
  .zona.muerta { opacity:.5; }
  .zTitulo { display:flex; justify-content:space-between; gap:8px;
             font-weight:600; margin-bottom:4px; }
  .pts { color:var(--suave); font-weight:400; }
  .zDet { color:var(--suave); font-variant-numeric:tabular-nums; }
  details { margin-top:12px; }
  summary { cursor:pointer; color:var(--azul); font-size:13px; padding:6px 0; }
  pre { background:#0d1117; border:1px solid var(--borde); border-radius:8px;
        padding:10px; overflow-x:auto; font-size:10.5px; line-height:1.45;
        max-height:320px; }
  .pie { color:var(--suave); font-size:11.5px; line-height:1.6; margin-top:16px;
         padding-top:12px; border-top:1px solid var(--borde); }
  .filaBotones { display:grid; grid-template-columns:1fr 1fr; gap:10px; margin-top:10px; }
  .filaBotones button, .filaBotones a {
          display:block; text-align:center; text-decoration:none;
          background:#21262d; color:var(--texto); border:1px solid var(--borde);
          border-radius:8px; padding:12px 8px; font-size:14px; font-family:inherit; }
  .bita { background:#12171e; border:1px solid var(--borde); border-left-width:3px;
          border-radius:8px; padding:10px 12px; margin-bottom:8px; font-size:12.5px; }
  .bTop { display:flex; justify-content:space-between; gap:8px; align-items:center;
          font-weight:600; margin-bottom:4px; }
  .pill { font-size:11px; font-weight:700; letter-spacing:.4px; padding:2px 7px;
          border-radius:99px; border:1px solid var(--borde); color:var(--suave); }
  .pill.tp { color:var(--verde); border-color:var(--verde); }
  .pill.sl { color:var(--rojo); border-color:var(--rojo); }
  .pill.en_curso { color:var(--azul); border-color:var(--azul); }
  .pill.invalidado, .pill.agotado { color:var(--ambar); border-color:var(--ambar); }
  .bLONG { border-left-color:var(--verde); }
  .bSHORT { border-left-color:var(--rojo); }
</style></head><body>
<header>
  <h1>Mesa</h1>
  <div class="sub">elegis el activo, calculas, y salen los precios para la orden
    limite. No manda ordenes.</div>
</header>
<main>
  <div class="panel">
    <div class="campo">
      <label for="simbolo">Activo</label>
      <select id="simbolo"></select>
    </div>
    <div class="rejilla3">
      <div class="campo">
        <label for="tf">Temporalidad</label>
        <select id="tf"></select>
      </div>
      <div class="campo">
        <label for="dias">Ventana (dias)</label>
        <input id="dias" type="number" value="3" min="1" max="30" step="1">
      </div>
      <div class="campo">
        <label for="rr">Objetivo (1:R)</label>
        <input id="rr" type="number" value="3" min="1" max="10" step="0.5">
      </div>
    </div>
    <div class="campo">
      <label for="mercado">Mercado</label>
      <select id="mercado">
        <option value="futuros">futuros USDT-M (0.10% ida y vuelta)</option>
        <option value="spot">spot (0.20% ida y vuelta)</option>
      </select>
    </div>
    <button id="calcular">CALCULAR</button>
    <button id="buscar" type="button">CALCULAR TODO</button>
    <div id="lista"></div>
  </div>
  <div id="salida"></div>
  <div class="panel" id="bitacora">
    <h3 style="margin-top:0">Bitácora</h3>
    <div class="zDet" style="margin-bottom:8px">Cada CALCULAR con trade queda
      anotado aca. Actualizar mira las velas de despues y marca si llego al
      objetivo, al stop, se invalido en el cierre, o la orden vencio sin
      llenarse. El CSV se baja al celular.</div>
    <div id="bitacoraCuerpo" class="estado">cargando…</div>
    <div class="filaBotones">
      <button id="actualizarBit" type="button">actualizar resultados</button>
      <a id="bajarCsv" href="api/bitacora.csv">descargar CSV</a>
    </div>
  </div>
  <div class="pie" style="text-align:center">versión __VERSION__</div>
</main>
<script>
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s).replace(/[&<>"]/g,
  c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));

let CFG = {simbolos: [], tfs: []};

function fijo(x, d) { return (x === null || x === undefined) ? "—"
                            : Number(x).toFixed(d); }

async function inicio() {
  const d = await (await fetch("api/config")).json();
  CFG = d;
  $("#simbolo").innerHTML = d.simbolos.map(
    s => `<option value="${s}">${s}</option>`).join("");
  $("#tf").innerHTML = d.tfs.map(
    t => `<option value="${t}"${t === d.tf_def ? " selected" : ""}>${t}</option>`
  ).join("");

  // El enlace se puede guardar con el activo ya elegido: ?simbolo=CHZUSDT
  const q = new URLSearchParams(location.search);
  if (q.get("simbolo")) $("#simbolo").value = q.get("simbolo").toUpperCase();
  if (q.get("tf")) $("#tf").value = q.get("tf");
  if (q.get("calcular") !== null) calcular();
}

async function calcular() {
  const b = $("#calcular"), t = $("#buscar");
  b.disabled = true; t.disabled = true; b.textContent = "CALCULANDO…";
  $("#salida").innerHTML =
    `<div class="estado"><div class="giro"></div>
      <div class="pasando">${esc($("#simbolo").value)}</div>
      <div class="zDet">bajando velas y buscando la zona…</div>
    </div>`;
  const p = new URLSearchParams({
    simbolo: $("#simbolo").value, tf: $("#tf").value,
    dias: $("#dias").value, rr: $("#rr").value,
    mercado: $("#mercado").value,
  });
  try {
    const d = await (await fetch("api/calcular?" + p)).json();
    $("#salida").innerHTML = d.error
      ? `<div class="panel nota nRojo">${esc(d.error)}</div>` : pintar(d);
    if (!d.error) cargarBitacora(false);
  } catch (e) {
    $("#salida").innerHTML =
      `<div class="panel nota nRojo">no pude calcular: ${esc(e.message)}</div>`;
  }
  b.disabled = false; t.disabled = false; b.textContent = "CALCULAR";
}

function elegir(simbolo) {
  const sel = $("#simbolo");
  if ([...sel.options].some(o => o.value === simbolo)) sel.value = simbolo;
  sel.scrollIntoView({block: "center"});
}

function filasHtml(filas) {
  return filas.map(t =>
    `<button type="button" class="filaTrade" data-sim="${esc(t.simbolo)}">
      <span>${esc(t.simbolo)}</span>
      <span class="${t.lado === "LONG" ? "ladoL" : "ladoS"}">${esc(t.lado)}</span>
    </button>`).join("");
}

function engancharFilas() {
  $("#lista").querySelectorAll(".filaTrade").forEach(el => {
    el.addEventListener("click", () => elegir(el.dataset.sim));
  });
}

async function buscar() {
  const b = $("#buscar"), c = $("#calcular");
  const tf = $("#tf").value;
  const simbolos = CFG.simbolos || [];
  const encontrados = [];
  b.disabled = true; c.disabled = true;
  b.textContent = "REVISANDO…";
  try {
    for (let i = 0; i < simbolos.length; i++) {
      const s = simbolos[i];
      $("#lista").innerHTML =
        `<div class="estado"><div class="giro"></div>
          <div class="pasando">${esc(s)}</div>
          <div class="zDet">${i + 1} / ${simbolos.length} · ${esc(tf)}</div>
        </div>` + filasHtml(encontrados);
      engancharFilas();
      const p = new URLSearchParams({
        simbolo: s, tf,
        dias: $("#dias").value, rr: $("#rr").value,
        mercado: $("#mercado").value,
      });
      const d = await (await fetch("api/buscar?" + p)).json();
      if (d.error) throw new Error(d.error);
      if (d.lado) encontrados.push(d);
    }
    if (encontrados.length) {
      $("#lista").innerHTML = filasHtml(encontrados);
      engancharFilas();
    } else {
      $("#lista").innerHTML =
        `<div class="nota nGris" style="margin-top:12px">Revisé ${simbolos.length} pares en ${esc(tf)}. No encontré ningún trade.</div>`;
    }
  } catch (e) {
    $("#lista").innerHTML =
      `<div class="panel nota nRojo">no pude revisar: ${esc(e.message)}</div>`
      + filasHtml(encontrados);
    engancharFilas();
  }
  b.disabled = false; c.disabled = false; b.textContent = "CALCULAR TODO";
}

function lineasValor(motivos) {
  return (motivos || []).filter(m =>
    m.indexOf("RSI(6)") === 0 || m.indexOf("MACD") === 0);
}

function fmtOsc(x) {
  if (x === null || x === undefined || Number.isNaN(Number(x))) return "—";
  const n = Number(x);
  const a = Math.abs(n);
  if (a >= 100) return n.toFixed(2);
  if (a >= 1) return n.toFixed(3);
  return n.toFixed(4);
}

function notaOsciladores(d) {
  const o = d.osciladores;
  if (!o || (o.rsi == null && o.hist == null)) return "";
  const partes = [];
  if (o.rsi != null) partes.push(`RSI(6) ${Number(o.rsi).toFixed(1)}`);
  if (o.hist != null) {
    let t = `MACD ${fmtOsc(o.hist)}`;
    if (o.dif != null && o.dea != null)
      t += ` (DIF ${fmtOsc(o.dif)}, DEA ${fmtOsc(o.dea)})`;
    partes.push(t);
  }
  return `<div class="panel nota nGris"><b>Valoración.</b> ${esc(partes.join(" · "))}
    en la última vela cerrada de ${esc(d.tf)}. En un largo suman con RSI(6) desde
    50 y MACD positivo; en un corto, al revés. Cada uno vale 2 puntos. Si los
    dos van en contra del lado, el trade no sale.</div>`;
}

function pintar(d) {
  const r = d.recomendacion, dec = d.decimales;
  let h = "";

  if (d.respaldo) {
    h += `<div class="panel nota nAmbar" style="margin-bottom:12px">
      <b>Pediste ${esc(d.mercado_pedido)} y Binance no respondio.</b> Esto salio
      de ${esc(d.mercado_nombre)}, y de ahi sale tambien la comision:
      ${d.costo_pct}% ida y vuelta, o sea un piso de stop de
      ${d.piso_stop_pct}%. Si vas a operar futuros, calcula desde una conexion
      que llegue a la API de futuros: los precios del perpetuo no son
      exactamente estos.</div>`;
  }

  if (r) {
    h += `<div class="trade t${r.lado}">
      <h2>${r.accion} · ${esc(d.simbolo)} · ${esc(d.tf)} · ${esc(r.zona_tipo)}</h2>
      <div class="niveles">
        <div class="nivel"><div class="et">entrada ${esc(r.tipo_orden)}</div>
          <div class="vl">${fijo(r.entrada, dec)}</div></div>
        <div class="nivel sl"><div class="et">stop loss</div>
          <div class="vl">${fijo(r.stop, dec)}</div></div>
        <div class="nivel tp"><div class="et">take profit</div>
          <div class="vl">${fijo(r.objetivo, dec)}</div></div>
      </div>
      <div class="cuerpo"><table>
        <tr><td>precio ahora</td><td>${fijo(d.precio, dec)}
            · la entrada esta a ${r.distancia_pct}%</td></tr>
        <tr><td>riesgo</td><td>${r.riesgo_pct}% del precio · 1:${r.rr_real}</td></tr>
        <tr><td>la comision se lleva</td><td>${r.costo_r} R por operacion</td></tr>
        <tr><td>acierto para empatar</td><td><b>${r.acierto_empate}%</b>
            · al azar da ${r.acierto_azar}%</td></tr>
        <tr><td>se invalida</td><td>cierre de ${esc(d.tf)} pasando
            ${fijo(r.invalida, dec)}</td></tr>
        <tr><td>la zona nacio</td><td>${esc(r.nacio)} UTC</td></tr>
        ${d.vwap ? `<tr><td>VWAP del día</td><td>${fijo(d.vwap, dec)}</td></tr>` : ""}
      </table>
      <div class="nota nGris">${esc(r.motivos.join("; "))}</div>`;

    if (r.vwap_afino) {
      h += `<div class="nota nGris"><b>La entrada se afinó al VWAP.</b>
        El valor del día cae en la mitad cercana de la zona, así que el límite
        queda ahí y el stop sigue en el borde de la idea.</div>`;
    }

    const valor = lineasValor(r.motivos);
    if (valor.length) {
      h += `<div class="nota nGris"><b>Valoración.</b> ${esc(valor.join(" · "))}.
        RSI(6) y el MACD de ${esc(d.tf)} suman o restan 2 puntos cada uno.
        Si los dos van en contra, este trade no sale.</div>`;
    }

    if (r.gatillo) {
      h += `<div class="nota nGris"><b>${esc(r.gatillo)}.</b>
        La entrada es el límite en esa media.</div>`;
    } else if (r.confluencia && r.confluencia.length) {
      h += `<div class="nota nGris"><b>Los tres puntos coinciden.</b>
        ${esc(r.confluencia.join(" · "))}. El ${esc(r.lado)} se activa cuando
        el precio toca la entrada.</div>`;
    }

    if (r.contracorriente) {
      h += `<div class="nota nAmbar"><b>Va contra ${esc(r.contra_tf.join(" y "))}.</b>
        Es un scalp hasta el objetivo y afuera. No lo dejes correr esperando mas,
        porque la temporalidad mayor empuja para el otro lado.</div>`;
    } else if (r.favor_tf.length) {
      h += `<div class="nota nGris">A favor de
        ${esc(r.favor_tf.join(" y "))}.</div>`;
    }
    if (r.ensanchado) {
      h += `<div class="nota nAmbar"><b>El stop se ensancho.</b> El borde de la
        zona daba ${r.stop_zona_pct}% y con eso la comision se llevaba
        ${(d.costo_pct / r.stop_zona_pct).toFixed(2)} R por operacion. Se corrio
        al piso de ${d.piso_stop_pct}%. Ojo: el nivel que define la idea y el que
        paga las cuentas ya no son el mismo.</div>`;
    }
    h += `</div></div>`;
  } else {
    h += `<div class="panel nota nAmbar"><b>Ninguna zona operable ahora.</b>
      ${esc(d.diagnostico || "")}</div>`;
    h += notaOsciladores(d);
  }

  h += `<h3>Temporalidad mayor</h3><div class="ctx">` + d.contexto.map(c => {
    if (c.error) return `<div class="ctxCaja"><span class="ctxTf">${esc(c.tf)}</span>
      <div>${esc(c.error)}</div></div>`;
    const pos = Math.max(0, Math.min(100, c.pos_rango));
    return `<div class="ctxCaja">
      <div><span class="ctxTf">${esc(c.tf)}</span>
        <span class="${c.sesgo}"> ${esc(c.sesgo)}</span>
        <span class="zDet"> · RSI(6) ${c.rsi}</span></div>
      <div class="zDet" style="margin-top:4px">${esc(c.lecturas.join(" · "))}</div>
      <div class="zDet" style="margin-top:6px">rango 24h ${fijo(c.bajo_24h, dec)}
        — ${fijo(c.alto_24h, dec)} · precio en el ${c.pos_rango}%</div>
      <div class="barra"><i style="left:${pos}%"></i></div>
    </div>`;
  }).join("") + `</div>`;

  h += `<h3>Todas las zonas (${d.zonas.length})</h3>` + d.zonas.map(z => `
    <div class="zona z${z.lado}${z.operable ? "" : " muerta"}">
      <div class="zTitulo"><span>${z.lado} ${esc(z.tipo)}
        <span class="pts">${z.puntos} pts</span></span>
        <span class="zDet">${esc(z.nacio)}</span></div>
      <div class="zDet">zona ${fijo(z.piso, dec)} — ${fijo(z.techo, dec)}</div>
      <div class="zDet">entrada ${fijo(z.entrada, dec)} · SL
        ${fijo(z.stop, dec)} · TP ${fijo(z.objetivo, dec)} · riesgo
        ${z.riesgo_pct}% · costo ${z.costo_r} R${
          z.vwap_afino ? " · entrada afinada al VWAP"
                       : (z.vwap_toca ? " · toca el VWAP" : "")}</div>
      <div class="zDet" style="margin-top:4px">${
        z.operable ? "✓ " + esc(z.motivo_operable)
                   : "· " + esc(z.motivo_operable)}</div>
      <div class="zDet" style="margin-top:4px">${
        (z.confluencia && z.confluencia.length)
          ? "✓ " + esc(z.confluencia.join(" · "))
          : ((z.falta_confluencia && z.falta_confluencia.length)
             ? "· " + esc(z.falta_confluencia.join("; ")) : "")}</div>
      ${lineasValor(z.motivos).length
        ? `<div class="zDet" style="margin-top:4px">${esc(lineasValor(z.motivos).join(" · "))}</div>`
        : ""}
    </div>`).join("");

  h += `<details><summary>Ver el Pine para dibujarlo en TradingView</summary>
    <pre id="pine">${esc(d.pine)}</pre>
    <button onclick="copiar()" style="width:100%;background:#21262d;
      color:var(--texto);border:1px solid var(--borde);border-radius:8px;
      padding:11px;font-size:14px;margin-top:8px">copiar al portapapeles</button>
    </details>`;

  h += `<div class="pie">
    ${esc(d.simbolo)} ${esc(d.tf)} · ventana ${esc(d.desde)} → ${esc(d.hasta)} UTC
    · calculado ${esc(d.calculado)} UTC<br>
    datos de ${esc(d.mercado_nombre)} · ATR ${fijo(d.atr, dec)}
    (${d.atr_pct}% del precio)${
      d.vwap ? " · VWAP del día " + fijo(d.vwap, dec) : ""} · comision ${d.costo_pct}% ida y vuelta${
      d.tick ? " · precios redondeados al tick de " + d.tick : ""}<br><br>
    El RR que figura puede quedar abajo del que pediste: al llevar los precios al
    tick del par, el stop se redondea alejandose de la entrada y el objetivo
    acercandose, para que el numero informado sea el peor de los dos y no el
    mejor.<br><br>
    El puntaje ordena la lista para no mirar veinte cajas iguales: los pesos son
    un criterio, no una medicion, y no son la probabilidad de nada. El numero que
    manda es <b>acierto para empatar</b>: entrando al azar con el mismo stop se
    acierta 1/(1+RR), asi que lo que hay que superar son esos puntos, no el 50%.
    Y hasta no tener 30 operaciones anotadas, ninguna racha significa nada.
    <br><br><b>versión ${esc(d.version_mesa)} · análisis ${
      d.version_zonas ? esc(d.version_zonas) : "sin número (zonas.py viejo)"}</b>
  </div>`;
  return h;
}

function copiar() {
  const t = $("#pine").textContent;
  navigator.clipboard?.writeText(t).then(
    () => alert("Pine copiado. Pegalo en el Pine Editor de TradingView."),
    () => alert("No pude copiar solo. Marcalo a mano."));
}

const PILLS = {pendiente:"pendiente", en_curso:"abierta", tp:"TP",
               sl:"SL", invalidado:"invalidada", agotado:"agotada",
               vencido:"vencio"};

function pintarBitacora(d) {
  const s = d.resumen || {};
  const rMedio = s.r_medio === null || s.r_medio === undefined
    ? "" : ` · ${s.r_medio} R por trade cerrado`;
  let h = `<div class="zDet" style="margin-bottom:8px">${s.n || 0} anotados · `
    + `${s.tp || 0} al TP · ${s.sl || 0} al SL · ${s.en_curso || 0} abiertas · `
    + `${s.pendiente || 0} pendientes · ${s.invalidado || 0} invalidadas · `
    + `${s.agotado || 0} agotadas · ${s.vencido || 0} vencidas${rMedio}</div>`;
  if (!d.filas || !d.filas.length) {
    h += `<div class="estado">todavia no hay trades. Apreta CALCULAR y el que
      salga queda aca.</div>`;
    $("#bitacoraCuerpo").innerHTML = h;
    return;
  }
  h += d.filas.map(f => {
    const dec = f.decimales || 2;
    const cuando = f.resuelto ? ` · resuelto ${esc(f.resuelto)} UTC` : "";
    return `<div class="bita b${esc(f.lado)}">
      <div class="bTop"><span>${esc(f.simbolo)} ${esc(f.tf)} ${esc(f.lado)}
        ${esc(f.zona_tipo)}</span>
        <span class="pill ${esc(f.estado)}">${esc(PILLS[f.estado] || f.estado)}</span></div>
      <div class="zDet">entrada ${fijo(f.entrada, dec)} · SL ${fijo(f.stop, dec)}
        · TP ${fijo(f.objetivo, dec)} · invalida ${fijo(f.invalida, dec)}</div>
      <div class="zDet">anotado ${esc(f.anotado)} UTC${cuando}</div>
      <div style="margin-top:4px">${esc(f.detalle || "")}</div>
    </div>`;
  }).join("");
  $("#bitacoraCuerpo").innerHTML = h;
}

async function cargarBitacora(actualizar) {
  const caja = $("#bitacoraCuerpo");
  if (actualizar) caja.innerHTML =
    `<div class="estado">mirando como salieron las ordenes abiertas…</div>`;
  try {
    const d = await (await fetch("api/bitacora" + (actualizar ? "?actualizar=1" : ""))).json();
    if (d.error) {
      caja.innerHTML = `<div class="nota nRojo">${esc(d.error)}</div>`;
      return;
    }
    pintarBitacora(d);
  } catch (e) {
    caja.innerHTML = `<div class="nota nRojo">no pude leer la bitacora: ${esc(e.message)}</div>`;
  }
}

$("#calcular").addEventListener("click", calcular);
$("#buscar").addEventListener("click", buscar);
$("#actualizarBit").addEventListener("click", () => cargarBitacora(true));
inicio();
cargarBitacora(false);
</script></body></html>
"""
PAGINA = PAGINA.replace("__VERSION__", str(VERSION))


class Manejador(BaseHTTPRequestHandler):
    simbolos = POR_DEFECTO
    tf_def = "15m"
    costo_max_r = 0.15

    def log_message(self, formato, *args):
        pass

    def _responder(self, codigo, tipo, cuerpo, extra=None):
        self.send_response(codigo)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(cuerpo)))
        for clave, valor in (extra or {}).items():
            self.send_header(clave, valor)
        self.end_headers()
        self.wfile.write(cuerpo)

    def _json(self, datos, codigo=200):
        self._responder(codigo, "application/json; charset=utf-8",
                        json.dumps(datos, ensure_ascii=False).encode())

    def do_GET(self):
        partes = urlparse(self.path)
        ruta = partes.path.rstrip("/") or "/"
        if ruta == "/":
            self._responder(200, "text/html; charset=utf-8", PAGINA.encode())
            return
        codigo, tipo, cuerpo, extra = atender(self.path)
        self._responder(codigo, tipo, cuerpo.encode("utf-8"), extra)


def atender(path):
    """La API de la mesa, sin el servidor. La usa Termux y la pagina de Chrome.

    Devuelve codigo, tipo, cuerpo y encabezados extra.
    """
    if not path.startswith("/"):
        path = "/" + path
    partes = urlparse(path)
    ruta = partes.path.rstrip("/") or "/"
    q = parse_qs(partes.query)

    def uno(nombre, defecto=""):
        return q.get(nombre, [defecto])[0]

    def json_respuesta(datos, codigo=200):
        return (codigo, "application/json; charset=utf-8",
                json.dumps(datos, ensure_ascii=False), {})

    try:
        if ruta == "/api/config":
            return json_respuesta({
                "simbolos": Manejador.simbolos,
                "tfs": TFS,
                "tf_def": Manejador.tf_def,
            })
        if ruta in ("/api/calcular", "/api/buscar"):
            simbolo = uno("simbolo").upper()
            if not simbolo.isalnum():
                return json_respuesta({"error": "simbolo invalido"}, 400)
            tf = uno("tf", Manejador.tf_def)
            if tf not in zonas.MINUTOS:
                return json_respuesta(
                    {"error": f"temporalidad {tf} desconocida"}, 400)
            mercado = uno("mercado", "futuros")
            if mercado not in zonas.MERCADOS:
                mercado = "futuros"
            try:
                dias = max(1.0, min(30.0, float(uno("dias", "3"))))
                rr = max(1.0, min(10.0, float(uno("rr", "3"))))
            except ValueError:
                dias, rr = 3.0, 3.0
            try:
                if ruta == "/api/calcular":
                    salida = calcular(simbolo, tf, dias, rr, mercado,
                                      Manejador.costo_max_r)
                    anotar(salida)
                    return json_respuesta(salida)
                return json_respuesta(mirar(
                    simbolo, tf, dias, rr, mercado, Manejador.costo_max_r))
            except RuntimeError as e:
                return json_respuesta({"error": str(e)}, 502)
        if ruta == "/api/bitacora":
            if uno("actualizar") == "1":
                return json_respuesta(actualizar_bitacora())
            with _candado_bit:
                return json_respuesta(_vista(_cargar_bitacora()))
        if ruta == "/api/bitacora.csv":
            vista = actualizar_bitacora()
            return (200, "text/csv; charset=utf-8", bitacora_csv(vista["filas"]),
                    {"Content-Disposition": "attachment; filename=bitacora.csv"})
        return json_respuesta({"error": "no existe"}, 404)
    except Exception as e:
        return json_respuesta({"error": f"error inesperado: {e!r}"}, 500)


def main():
    puerto = 8778
    if "--puerto" in sys.argv:
        puerto = int(sys.argv[sys.argv.index("--puerto") + 1])

    crudos = os.getenv("MESA_SIMBOLOS", "")
    if "--simbolos" in sys.argv:
        crudos = sys.argv[sys.argv.index("--simbolos") + 1]
    simbolos = [s.strip().upper() for s in crudos.split(",") if s.strip()]
    Manejador.simbolos = simbolos or POR_DEFECTO

    if "--tf" in sys.argv:
        Manejador.tf_def = sys.argv[sys.argv.index("--tf") + 1]

    print("=" * 66)
    print("Mesa — elegis el activo, apretas CALCULAR, salen los precios")
    print(f"  Activos: {', '.join(Manejador.simbolos)}")
    print(f"  Abrila en: http://localhost:{puerto}")
    print("  En Termux el navegador del celular llega a localhost sin nada mas.")
    print("  No manda ordenes: calcula y te dice donde poner los precios.")
    print("  La bitacora se ve en la misma pagina y se baja en CSV.")
    print(f"  Version {VERSION}. Si la pagina no dice ese numero, es el archivo viejo.")
    print("=" * 66)

    servidor = ThreadingHTTPServer(("0.0.0.0", puerto), Manejador)
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        print("\nchau")
        servidor.shutdown()


if __name__ == "__main__":
    main()
