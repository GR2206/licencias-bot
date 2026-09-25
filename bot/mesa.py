"""La mesa: elegis el activo, apretas CALCULAR y sale el trade. Sin dependencias.

    python mesa.py                      # http://localhost:8778
    python mesa.py --puerto 9000
    python mesa.py --simbolos CHZUSDT,SOLUSDT,ADAUSDT

Una pantalla, tres controles: el cajon del activo, la temporalidad, y el boton.
Abajo sale la entrada como orden limite, el stop, el objetivo, y el contexto de
H4 y H1 para saber si va a favor o contra la tendencia mayor.

No manda ordenes. Calcula y te dice donde poner los precios; las cargas vos.
Esa separacion es a proposito: mientras no haya una medicion que diga que esto
le gana al azar, que un boton pueda mandar una orden sola no es una comodidad,
es una forma de perder plata rapido.

Usa el mismo analizar() que la consola, asi que la pantalla y `python zonas.py`
no pueden decir cosas distintas. Y el costo sale del mercado del que realmente
vinieron las velas: futuros 0.10% ida y vuelta, spot 0.20%. Si pedis futuros y
Binance bloquea tu region, cae a spot y te lo dice arriba, porque con el doble
de comision el piso del stop se duplica y el trade puede dejar de cerrar.
"""
import json
import os
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import zonas

# Diez por defecto, ordenados de mas a menos liquido. La liquidez no es un
# detalle: en un par flaco el spread y el slippage se comen el margen que la
# aritmetica del costo deja, y ese margen ya es chico.
POR_DEFECTO = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
               "ADAUSDT", "DOGEUSDT", "AVAXUSDT", "LINKUSDT", "CHZUSDT"]
TFS = ["5m", "15m", "30m", "1h", "4h"]

CACHE_SEG = 45
_cache = {}
_candado = threading.Lock()


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
        }

    return {
        "simbolo": a["simbolo"], "tf": a["tf"], "dias": a["dias"], "rr": a["rr"],
        "mercado": a["mercado"], "mercado_nombre": a["mercado_nombre"],
        "mercado_pedido": a["mercado_pedido"], "respaldo": a["respaldo"],
        "costo_pct": a["costo_pct"],
        "piso_stop_pct": round(a["piso_stop_pct"], 2),
        "stop_max_atr": a["stop_max_atr"], "tick": a["tick"],
        "precio": pr(a["precio"]), "ultimo_cierre": pr(a["ultimo_cierre"]),
        "atr": pr(a["atr"]), "atr_pct": round(a["atr_pct"], 2),
        "decimales": d,
        "desde": datetime.fromtimestamp(
            a["desde_ms"] / 1000, timezone.utc).strftime("%d/%m %H:%M"),
        "hasta": datetime.fromtimestamp(
            a["hasta_ms"] / 1000, timezone.utc).strftime("%d/%m %H:%M"),
        "calculado": datetime.now(timezone.utc).strftime("%H:%M:%S"),
        "contexto": ctx, "zonas": zs, "recomendacion": rec,
        "diagnostico": a["diagnostico"],
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
  </div>
  <div id="salida"></div>
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
  const b = $("#calcular");
  b.disabled = true; b.textContent = "CALCULANDO…";
  $("#salida").innerHTML =
    `<div class="estado">bajando velas de Binance y buscando zonas…</div>`;
  const p = new URLSearchParams({
    simbolo: $("#simbolo").value, tf: $("#tf").value,
    dias: $("#dias").value, rr: $("#rr").value,
    mercado: $("#mercado").value,
  });
  try {
    const d = await (await fetch("api/calcular?" + p)).json();
    $("#salida").innerHTML = d.error
      ? `<div class="panel nota nRojo">${esc(d.error)}</div>` : pintar(d);
  } catch (e) {
    $("#salida").innerHTML =
      `<div class="panel nota nRojo">no pude calcular: ${esc(e.message)}</div>`;
  }
  b.disabled = false; b.textContent = "CALCULAR";
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
      </table>
      <div class="nota nGris">${esc(r.motivos.join("; "))}</div>`;

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
  }

  h += `<h3>Temporalidad mayor</h3><div class="ctx">` + d.contexto.map(c => {
    if (c.error) return `<div class="ctxCaja"><span class="ctxTf">${esc(c.tf)}</span>
      <div>${esc(c.error)}</div></div>`;
    const pos = Math.max(0, Math.min(100, c.pos_rango));
    return `<div class="ctxCaja">
      <div><span class="ctxTf">${esc(c.tf)}</span>
        <span class="${c.sesgo}"> ${esc(c.sesgo)}</span>
        <span class="zDet"> · RSI ${c.rsi}</span></div>
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
        ${z.riesgo_pct}% · costo ${z.costo_r} R</div>
      <div class="zDet" style="margin-top:4px">${
        z.operable ? "✓ " + esc(z.motivo_operable)
                   : "· " + esc(z.motivo_operable)}</div>
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
    (${d.atr_pct}% del precio) · comision ${d.costo_pct}% ida y vuelta${
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
  </div>`;
  return h;
}

function copiar() {
  const t = $("#pine").textContent;
  navigator.clipboard?.writeText(t).then(
    () => alert("Pine copiado. Pegalo en el Pine Editor de TradingView."),
    () => alert("No pude copiar solo. Marcalo a mano."));
}

$("#calcular").addEventListener("click", calcular);
inicio();
</script></body></html>
"""


class Manejador(BaseHTTPRequestHandler):
    simbolos = POR_DEFECTO
    tf_def = "15m"
    costo_max_r = 0.15

    def log_message(self, formato, *args):
        pass

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
        partes = urlparse(self.path)
        ruta = partes.path.rstrip("/") or "/"
        q = parse_qs(partes.query)

        def uno(nombre, defecto=""):
            return q.get(nombre, [defecto])[0]

        try:
            if ruta == "/":
                self._responder(200, "text/html; charset=utf-8", PAGINA.encode())
            elif ruta == "/api/config":
                self._json({"simbolos": self.simbolos, "tfs": TFS,
                            "tf_def": self.tf_def})
            elif ruta == "/api/calcular":
                simbolo = uno("simbolo").upper()
                if not simbolo.isalnum():
                    self._json({"error": "simbolo invalido"}, 400)
                    return
                tf = uno("tf", self.tf_def)
                if tf not in zonas.MINUTOS:
                    self._json({"error": f"temporalidad {tf} desconocida"}, 400)
                    return
                mercado = uno("mercado", "futuros")
                if mercado not in zonas.MERCADOS:
                    mercado = "futuros"
                try:
                    dias = max(1.0, min(30.0, float(uno("dias", "3"))))
                    rr = max(1.0, min(10.0, float(uno("rr", "3"))))
                except ValueError:
                    dias, rr = 3.0, 3.0
                try:
                    self._json(calcular(simbolo, tf, dias, rr, mercado,
                                        self.costo_max_r))
                except RuntimeError as e:
                    self._json({"error": str(e)}, 502)
            else:
                self._json({"error": "no existe"}, 404)
        except Exception as e:
            self._json({"error": f"error inesperado: {e!r}"}, 500)


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
    print("=" * 66)

    servidor = ThreadingHTTPServer(("0.0.0.0", puerto), Manejador)
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        print("\nchau")
        servidor.shutdown()


if __name__ == "__main__":
    main()
