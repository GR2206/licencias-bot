const origFetch = window.fetch.bind(window);
let pyodide;
const motorEl = () => document.getElementById("motor");

const listo = (async () => {
  try {
    const etiqueta = document.querySelector("script[src*='pyodide.js']");
    const srcPy = (etiqueta && etiqueta.getAttribute("src")) || "";
    const indexURL = srcPy.startsWith("http")
      ? "https://cdn.jsdelivr.net/pyodide/v0.27.7/full/"
      : new URL("pyodide/", location.href).href;
    pyodide = await loadPyodide({indexURL});
    pyodide.FS.mkdirTree("/app");
    const archivos = globalThis.ARCHIVOS_MESA || {};
    for (const nombre of ["indicadores.py", "estrategia.py", "zonas.py", "mesa.py"]) {
      const texto = archivos[nombre];
      if (!texto) throw new Error("falta " + nombre);
      pyodide.FS.writeFile("/app/" + nombre, texto);
    }
    const guardada = localStorage.getItem("mesa-bitacora");
    if (guardada != null) pyodide.FS.writeFile("/bitacora.json", guardada);

    globalThis.pedirNavegador = (url) => {
      if (globalThis.Mesa && typeof Mesa.pedir === "function") {
        return Mesa.pedir(url);
      }
      const xhr = new XMLHttpRequest();
      xhr.open("GET", url, false);
      xhr.send(null);
      if (xhr.status < 200 || xhr.status >= 300) {
        throw new Error("HTTP " + xhr.status);
      }
      return xhr.responseText;
    };
    if (globalThis.Mesa && Mesa.pedir) {
      const sub = document.querySelector(".sub");
      if (sub) sub.textContent =
        "App de la mesa. Elegis el activo, calculas, y salen los precios. No manda ordenes.";
    }

    await pyodide.runPythonAsync(`
import os, sys, json, urllib.error
os.environ["MESA_BITACORA"] = "/bitacora.json"
sys.path.insert(0, "/app")
from js import pedirNavegador
import mesa
import zonas

def _pedir_nav(url, timeout=30):
    try:
        return json.loads(pedirNavegador(url))
    except Exception as e:
        raise urllib.error.URLError(str(e))

zonas._pedir = _pedir_nav

def atender_web(path):
    codigo, tipo, cuerpo, extra = mesa.atender(path)
    return json.dumps({
        "codigo": codigo,
        "tipo": tipo,
        "cuerpo": cuerpo,
        "disposicion": (extra or {}).get("Content-Disposition", ""),
    })
`);
    const aviso = motorEl();
    if (aviso) aviso.remove();
  } catch (e) {
    const aviso = motorEl();
    const texto = (e && e.message) ? e.message : String(e);
    if (aviso) {
      aviso.className = "panel nota nRojo";
      aviso.textContent = "No pude cargar la mesa en Chrome: " + texto;
    }
    throw e;
  }
})();

function guardarBitacora() {
  try {
    const raw = pyodide.FS.readFile("/bitacora.json", {encoding: "utf8"});
    localStorage.setItem("mesa-bitacora", raw);
  } catch (e) {}
}

function restaurarBitacora() {
  const raw = localStorage.getItem("mesa-bitacora");
  if (raw == null) return;
  pyodide.FS.writeFile("/bitacora.json", raw);
}

let cola = Promise.resolve();

window.fetch = function (input, init) {
  const url = typeof input === "string" ? input : (input && input.url) || "";
  if (!String(url).includes("api/")) return origFetch(input, init);
  const job = cola.then(async () => {
    await listo;
    restaurarBitacora();
    const atender = pyodide.globals.get("atender_web");
    let bruto = atender(String(url));
    const texto = typeof bruto === "string" ? bruto : String(bruto);
    if (bruto && bruto.destroy) bruto.destroy();
    if (atender.destroy) atender.destroy();
    const d = JSON.parse(texto);
    guardarBitacora();
    const headers = {"Content-Type": d.tipo};
    if (d.disposicion) headers["Content-Disposition"] = d.disposicion;
    return new Response(d.cuerpo, {status: d.codigo, headers});
  });
  cola = job.then(() => {}, () => {});
  return job;
};

document.addEventListener("click", (ev) => {
  const a = ev.target.closest && ev.target.closest("a");
  if (!a) return;
  const href = a.getAttribute("href") || "";
  if (!href.includes("api/")) return;
  ev.preventDefault();
  window.fetch(href).then(async (res) => {
    const blob = await res.blob();
    const u = URL.createObjectURL(blob);
    const tmp = document.createElement("a");
    tmp.href = u;
    tmp.download = "bitacora.csv";
    tmp.click();
    URL.revokeObjectURL(u);
  });
}, true);
