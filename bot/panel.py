"""Panel web para mirar el bot desde el celular. Python puro, sin dependencias.

    python panel.py                 # http://localhost:8777
    python panel.py --puerto 9000

Abrilo en el navegador del celular. Muestra, para cada simbolo:

  * la tendencia de la temporalidad mayor,
  * el chequeo de confluencias (cuantas condiciones se cumplen ahora),
  * la senal viva si hay,
  * y el boton "medir", que corre el backtest de la configuracion actual y lo
    compara contra ENTRAR AL AZAR en el mismo activo.

Esa comparacion es lo importante del panel. Un tablero lindo que muestre cinco
indicadores en verde no dice nada; lo que decide es si le ganas al azar mas de lo
que te cuesta operar. Ver el numero al lado de cada configuracion evita meses de
entusiasmo con una idea que no gana.

No manda ordenes. Es para mirar y medir.
"""
import json
import os
import statistics
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import bot
import indicadores as ind
import referencia
import scalper

ESPEJO = "https://data-api.binance.vision"
CACHE_SEG = 60
_cache = {}
_candado = threading.Lock()

TAKER = 0.05
MAKER = 0.02


# ─────────────────────────────────────────────────────────────────────────────
#  Datos
# ─────────────────────────────────────────────────────────────────────────────
def klines(simbolo, tf, limite=1000):
    """Velas de Binance con cache corto, para no castigar la API."""
    clave = (simbolo, tf, limite)
    with _candado:
        guardado = _cache.get(clave)
        if guardado and time.time() - guardado[0] < CACHE_SEG:
            return guardado[1]

    url = f"{ESPEJO}/api/v3/klines?symbol={simbolo}&interval={tf}&limit={limite}"
    pedido = urllib.request.Request(url, headers={"User-Agent": "panel-bot/1.0"})
    try:
        with urllib.request.urlopen(pedido, timeout=20) as r:
            crudas = json.loads(r.read())
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
        raise RuntimeError(f"No pude bajar {simbolo} {tf}: {e}")

    velas = ind.desde_klines(crudas)[:-1]   # la ultima esta abierta
    with _candado:
        _cache[clave] = (time.time(), velas)
    return velas


def minutos_de(tf):
    return bot.MINUTOS_TF.get(tf, 5)


def historia(simbolo, tf, dias=30):
    """Historia larga, bajada por tramos. Para medir, no para el estado en vivo.

    Binance entrega 1000 velas por pedido: en M5 son 3.5 dias, que no alcanza
    para medir nada. Con esto se juntan varios tramos.
    """
    clave = ("historia", simbolo, tf, dias)
    with _candado:
        guardado = _cache.get(clave)
        if guardado and time.time() - guardado[0] < 900:
            return guardado[1]

    ms = minutos_de(tf) * 60_000
    fin = int(time.time() * 1000)
    cursor = fin - dias * 86_400_000
    filas = []
    while cursor < fin:
        url = (f"{ESPEJO}/api/v3/klines?symbol={simbolo}&interval={tf}"
               f"&startTime={cursor}&limit=1000")
        pedido = urllib.request.Request(url, headers={"User-Agent": "panel-bot/1.0"})
        try:
            with urllib.request.urlopen(pedido, timeout=25) as r:
                lote = json.loads(r.read())
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
            if not filas:
                raise RuntimeError(f"No pude bajar {simbolo} {tf}: {e}")
            break
        if not lote:
            break
        filas.extend(lote)
        siguiente = lote[-1][0] + ms
        if siguiente <= cursor:
            break
        cursor = siguiente
        time.sleep(0.05)

    velas = ind.desde_klines(filas)[:-1]
    with _candado:
        _cache[clave] = (time.time(), velas)
    return velas


# ─────────────────────────────────────────────────────────────────────────────
#  Medicion
# ─────────────────────────────────────────────────────────────────────────────
def simular(velas, cfg, mod, minutos_vela):
    """Operaciones cerradas, con comisiones y salida por tiempo si corresponde."""
    limite = 0
    minutos_max = getattr(cfg, "minutos_max", 0)
    if minutos_max:
        limite = max(1, minutos_max // minutos_vela)

    costo = getattr(cfg, "costo_ida_vuelta_pct", TAKER * 2)
    ops = []
    libre = -1
    for s in mod.senales(velas, cfg):
        if s.indice <= libre:
            continue
        r = None
        etiqueta = ""
        for j in range(s.indice + 1, len(velas)):
            v = velas[j]
            pasadas = j - s.indice
            if s.es_compra:
                golpe_stop, golpe_obj = v.minimo <= s.stop, v.maximo >= s.objetivo
            else:
                golpe_stop, golpe_obj = v.maximo >= s.stop, v.minimo <= s.objetivo
            if golpe_stop:
                r, etiqueta = -1.0, "stop"
            elif golpe_obj:
                r, etiqueta = cfg.rr, "objetivo"
            elif limite and pasadas >= limite:
                movido = ((v.cierre - s.entrada) if s.es_compra
                          else (s.entrada - v.cierre))
                r, etiqueta = movido / abs(s.entrada - s.stop), "tiempo"
            if r is not None:
                libre = j
                ops.append({
                    "tiempo": s.tiempo, "accion": s.accion,
                    "r": r - costo / s.riesgo_pct,
                    "riesgo_pct": s.riesgo_pct, "salida": etiqueta,
                    "minutos": pasadas * minutos_vela,
                })
                break
    return ops


def medir(simbolo, tf, mod, cfg, dias=30):
    velas = historia(simbolo, tf, dias)
    minutos_vela = minutos_de(tf)
    ops = simular(velas, cfg, mod, minutos_vela)
    dias = (velas[-1].tiempo - velas[0].tiempo) / 86_400_000 if velas else 0

    salida = {"simbolo": simbolo, "tf": tf, "velas": len(velas),
              "dias": round(dias, 1), "ops": len(ops)}
    if not ops:
        salida["mensaje"] = ("Sin operaciones en esta muestra. Los filtros son "
                            "exigentes o la ventana es corta.")
        return salida

    erres = [o["r"] for o in ops]
    curva = pico = caida = 0.0
    for r in erres:
        curva += r
        pico = max(pico, curva)
        caida = min(caida, curva - pico)

    porque = {}
    for o in ops:
        porque[o["salida"]] = porque.get(o["salida"], 0) + 1

    salida.update({
        "por_dia": round(len(ops) / dias, 2) if dias else 0,
        "acierto": round(sum(1 for r in erres if r > 0) / len(erres) * 100, 1),
        "r_op": round(statistics.fmean(erres), 3),
        "total_r": round(sum(erres), 1),
        "caida": round(caida, 1),
        "stop": round(statistics.median([o["riesgo_pct"] for o in ops]), 3),
        "salidas": porque,
    })

    veredicto = referencia.comparar(
        [(o["r"], o["riesgo_pct"]) for o in ops], velas, cfg.rr,
        costo_pct=getattr(cfg, "costo_ida_vuelta_pct", TAKER * 2),
        minutos_max=getattr(cfg, "minutos_max", 0), minutos_vela=minutos_vela)
    if veredicto:
        salida["azar"] = {
            "acierto": round(veredicto["acierto_azar"], 1),
            "teorico": round(veredicto["teorico"], 1),
            "r_op": round(veredicto["r_op_azar"], 3),
            "ventaja_pp": round(veredicto["ventaja_pp"], 1),
            "margen_error": round(veredicto["margen_error"], 1),
            "suficiente": veredicto["muestra_suficiente"],
            "costo_en_r": round(veredicto["costo_en_r"], 2),
            "veredicto": veredicto["veredicto"],
        }
    return salida


def estado(simbolos, tf, mod, cfg):
    filas = []
    for simbolo in simbolos:
        fila = {"simbolo": simbolo}
        try:
            velas = klines(simbolo, tf, 1000)
        except RuntimeError as e:
            fila["error"] = str(e)
            filas.append(fila)
            continue

        v = velas[-1]
        fila["precio"] = v.cierre
        fila["vela"] = datetime.fromtimestamp(
            v.tiempo / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M")

        if mod is scalper:
            lista, cuantas = scalper.confluencias(velas, cfg)
            fila["confluencias"] = [
                {"nombre": n, "ok": bool(ok), "detalle": d} for n, ok, d in lista]
            fila["cuantas"] = cuantas
            fila["de"] = len(lista)

        senal = mod.senal_actual(velas, cfg)
        if senal:
            fila["senal"] = {
                "accion": senal.accion,
                "entrada": senal.entrada, "stop": senal.stop,
                "objetivo": senal.objetivo,
                "riesgo_pct": round(senal.riesgo_pct, 3),
            }
        filas.append(fila)
    return filas


# ─────────────────────────────────────────────────────────────────────────────
#  Web
# ─────────────────────────────────────────────────────────────────────────────
PAGINA = """<!DOCTYPE html>
<html lang="es"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Panel del bot</title>
<style>
  :root { --fondo:#0d1117; --caja:#161b22; --borde:#30363d; --texto:#e6edf3;
          --suave:#8b949e; --verde:#3fb950; --rojo:#f85149; --ambar:#d29922; }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--fondo); color:var(--texto); font-size:15px;
         font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; }
  header { padding:16px; border-bottom:1px solid var(--borde); position:sticky;
           top:0; background:var(--fondo); z-index:5; }
  h1 { margin:0 0 4px; font-size:17px; }
  .sub { color:var(--suave); font-size:12px; }
  main { padding:12px; max-width:760px; margin:0 auto; }
  .aviso { background:#2d2000; border:1px solid var(--ambar); color:#f0d68a;
           padding:12px; border-radius:8px; font-size:13px; line-height:1.5;
           margin-bottom:14px; }
  .tarjeta { background:var(--caja); border:1px solid var(--borde);
             border-radius:10px; padding:14px; margin-bottom:12px; }
  .fila { display:flex; justify-content:space-between; align-items:baseline;
          gap:8px; }
  .simbolo { font-weight:600; font-size:16px; }
  .precio { font-variant-numeric:tabular-nums; color:var(--suave); font-size:13px; }
  ul.chk { list-style:none; padding:0; margin:10px 0 0; }
  ul.chk li { display:flex; gap:8px; padding:4px 0; font-size:13px;
              border-top:1px solid #21262d; }
  .marca { width:16px; flex:none; text-align:center; }
  .ok { color:var(--verde); } .no { color:var(--suave); }
  .detalle { color:var(--suave); margin-left:auto; font-size:12px;
             text-align:right; }
  .cuenta { font-size:12px; color:var(--suave); margin-top:8px; }
  .senal { margin-top:10px; padding:10px; border-radius:8px; font-size:13px;
           font-variant-numeric:tabular-nums; }
  .compra { background:#0d2818; border:1px solid var(--verde); }
  .venta { background:#2d1214; border:1px solid var(--rojo); }
  button { background:#21262d; color:var(--texto); border:1px solid var(--borde);
           border-radius:7px; padding:8px 14px; font-size:13px; cursor:pointer;
           margin-top:10px; }
  button:active { background:#30363d; }
  .medicion { margin-top:10px; font-size:13px; line-height:1.6;
              font-variant-numeric:tabular-nums; }
  .medicion table { width:100%; border-collapse:collapse; }
  .medicion td { padding:3px 0; border-top:1px solid #21262d; }
  .medicion td:last-child { text-align:right; }
  .veredicto { margin-top:8px; padding:8px; border-radius:6px; font-size:13px; }
  .malo { background:#2d1214; border:1px solid var(--rojo); }
  .medio { background:#2d2000; border:1px solid var(--ambar); }
  .bueno { background:#0d2818; border:1px solid var(--verde); }
  .pos { color:var(--verde); } .neg { color:var(--rojo); }
</style></head><body>
<header>
  <h1>Panel del bot</h1>
  <div class="sub" id="cabecera">cargando…</div>
</header>
<main>
  <div class="aviso">
    <b>Esto no manda ordenes.</b> Muestra las senales y las mide. El numero que
    importa de cada medicion es <b>vs azar</b>: cuanto le gana la estrategia a
    entrar al azar en el mismo activo con el mismo stop. En un mercado sin memoria
    el acierto del azar es 1/(1+RR) y la esperanza bruta es cero, asi que si tu
    estrategia no le gana al azar mas de lo que cuesta la comision, pierde plata
    por mas verde que se vea el tablero.
  </div>
  <div id="cuerpo"></div>
</main>
<script>
const $ = (s) => document.querySelector(s);
const num = (x, d=2) => (x===undefined||x===null) ? "—" : Number(x).toFixed(d);
const signo = (x) => x > 0 ? "pos" : x < 0 ? "neg" : "";

async function cargar() {
  const r = await fetch("api/estado");
  const d = await r.json();
  $("#cabecera").textContent =
     `${d.estrategia} · ${d.tf} · ${d.simbolos.length} simbolos · RR 1:${d.rr}`
     + (d.minutos_max ? ` · salida por tiempo a los ${d.minutos_max} min` : "");
  $("#cuerpo").innerHTML = d.filas.map(tarjeta).join("");
}

function tarjeta(f) {
  if (f.error) return `<div class="tarjeta"><div class="simbolo">${f.simbolo}</div>
      <div class="detalle">${f.error}</div></div>`;
  let html = `<div class="tarjeta" id="c-${f.simbolo}">
    <div class="fila"><span class="simbolo">${f.simbolo}</span>
      <span class="precio">${f.precio} · ${f.vela} UTC</span></div>`;
  if (f.confluencias) {
    html += `<ul class="chk">` + f.confluencias.map(c =>
      `<li><span class="marca ${c.ok?'ok':'no'}">${c.ok?'✓':'·'}</span>
       <span>${c.nombre}</span><span class="detalle">${c.detalle}</span></li>`
    ).join("") + `</ul>
    <div class="cuenta">${f.cuantas} de ${f.de} condiciones cumplidas</div>`;
  }
  if (f.senal) {
    const s = f.senal, c = s.accion === "BUY" ? "compra" : "venta";
    html += `<div class="senal ${c}"><b>${s.accion}</b> entrada ${s.entrada}
      · SL ${s.stop} · TP ${s.objetivo} · riesgo ${s.riesgo_pct}%</div>`;
  }
  html += `<button onclick="medir('${f.simbolo}')">medir esta configuracion</button>
    <div class="medicion" id="m-${f.simbolo}"></div></div>`;
  return html;
}

async function medir(simbolo) {
  const caja = $("#m-" + simbolo);
  caja.innerHTML = "midiendo 30 dias… (la primera vez baja la historia, tarda)";
  const r = await fetch("api/medir?simbolo=" + simbolo + "&dias=30");
  const d = await r.json();
  if (d.mensaje) { caja.innerHTML = `<div class="detalle">${d.mensaje}</div>`; return; }
  const a = d.azar || {};
  const clase = a.ventaja_pp === undefined || a.suficiente === false ? "medio"
              : a.ventaja_pp <= 0 ? "malo"
              : a.ventaja_pp > a.margen_error ? "bueno" : "medio";
  const salidas = Object.entries(d.salidas || {})
        .map(([k, v]) => `${k} ${v}`).join(" · ");
  caja.innerHTML = `<table>
    <tr><td>muestra</td><td>${d.ops} operaciones en ${d.dias} dias
        (${d.por_dia}/dia)</td></tr>
    <tr><td>stop tipico</td><td>${num(d.stop,3)}% del precio</td></tr>
    <tr><td>la comision se lleva</td><td>${num(a.costo_en_r,2)} R por
        operacion</td></tr>
    <tr><td>acierto</td><td>${num(d.acierto,1)}%</td></tr>
    <tr><td>acierto entrando al azar</td><td>${num(a.acierto,1)}%
        (teoria ${num(a.teorico,1)}%)</td></tr>
    <tr><td><b>vs azar</b></td><td class="${a.suficiente===false?'':signo(a.ventaja_pp)}">
        <b>${a.ventaja_pp>0?'+':''}${num(a.ventaja_pp,1)} puntos</b>
        <span class="detalle">± ${num(a.margen_error,1)} de error</span></td></tr>
    <tr><td>R por operacion</td><td class="${signo(d.r_op)}">
        ${d.r_op>0?'+':''}${num(d.r_op,3)} R</td></tr>
    <tr><td>total / peor caida</td><td>${num(d.total_r,1)} R /
        ${num(d.caida,1)} R</td></tr>
    <tr><td>como salio</td><td>${salidas}</td></tr>
  </table><div class="veredicto ${clase}">${a.veredicto || "sin referencia"}</div>`;
}

cargar();
setInterval(cargar, 60000);
</script></body></html>
"""


class Manejador(BaseHTTPRequestHandler):
    simbolos = []
    tf = "5m"
    mod = scalper
    cfg = None

    def log_message(self, formato, *args):
        pass   # el log del bot ya alcanza

    def _responder(self, codigo, tipo, cuerpo):
        self.send_response(codigo)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(cuerpo)))
        self.end_headers()
        self.wfile.write(cuerpo)

    def _json(self, datos, codigo=200):
        self._responder(codigo, "application/json; charset=utf-8",
                        json.dumps(datos, ensure_ascii=False).encode())

    def do_GET(self):
        ruta = self.path.split("?")[0].rstrip("/") or "/"
        parametros = {}
        if "?" in self.path:
            for trozo in self.path.split("?", 1)[1].split("&"):
                if "=" in trozo:
                    k, v = trozo.split("=", 1)
                    parametros[k] = v

        try:
            if ruta == "/":
                self._responder(200, "text/html; charset=utf-8", PAGINA.encode())
            elif ruta == "/api/estado":
                self._json({
                    "estrategia": self.mod.NOMBRE,
                    "tf": self.tf,
                    "rr": self.cfg.rr,
                    "minutos_max": getattr(self.cfg, "minutos_max", 0),
                    "simbolos": self.simbolos,
                    "filas": estado(self.simbolos, self.tf, self.mod, self.cfg),
                })
            elif ruta == "/api/medir":
                simbolo = parametros.get("simbolo", "").upper()
                if simbolo not in self.simbolos:
                    self._json({"mensaje": "simbolo desconocido"}, 400)
                    return
                try:
                    dias = max(3, min(120, int(parametros.get("dias", 30))))
                except ValueError:
                    dias = 30
                self._json(medir(simbolo, self.tf, self.mod, self.cfg, dias))
            else:
                self._json({"mensaje": "no existe"}, 404)
        except Exception as e:
            self._json({"mensaje": f"error: {e!r}"}, 500)


def main():
    bot.cargar_env()
    puerto = 8777
    if "--puerto" in sys.argv:
        puerto = int(sys.argv[sys.argv.index("--puerto") + 1])

    nombre = os.getenv("ESTRATEGIA", "scalper").strip().lower()
    mod = {"scalper": scalper}.get(nombre) or bot.motor(nombre)
    cfg = bot.config_estrategia(mod)

    simbolos = [s.strip().upper() for s in
                os.getenv("SIMBOLOS", "BTCUSDT,ETHUSDT,SOLUSDT").split(",")
                if s.strip()]
    tf = (os.getenv("TEMPORALIDADES", "5m").split(",")[0]).strip()

    Manejador.simbolos = simbolos
    Manejador.tf = tf
    Manejador.mod = mod
    Manejador.cfg = cfg

    print("=" * 62)
    print(f"Panel del bot — estrategia {mod.NOMBRE}, {tf}")
    print(f"  Simbolos: {', '.join(simbolos)}")
    print(f"  Abrilo en: http://localhost:{puerto}")
    print("  En Termux el navegador del celular llega a localhost sin nada mas.")
    print("  No manda ordenes: es para mirar y medir.")
    print("=" * 62)

    servidor = ThreadingHTTPServer(("0.0.0.0", puerto), Manejador)
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        print("\nchau")
        servidor.shutdown()


if __name__ == "__main__":
    main()
