"""Arma web/index.html a partir de la pagina de la mesa.

La pagina que se abre en Chrome es el mismo dibujo. Este archivo solo le
agrega el motor que corre adentro del navegador.
"""
import pathlib
import sys

RAIZ = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "bot"))
import mesa  # noqa: E402

html = mesa.PAGINA
html = html.replace(
    "<title>Mesa</title>",
    "<title>Mesa</title>\n"
    '<script src="https://cdn.jsdelivr.net/pyodide/v0.27.7/full/pyodide.js"></script>',
    1,
)
html = html.replace(
    "elegis el activo, calculas, y salen los precios para la orden\n"
    "    limite. No manda ordenes.",
    "Corre en Chrome, sin Termux. Elegis el activo, calculas, y salen los\n"
    "    precios para la orden limite. No manda ordenes.",
    1,
)
html = html.replace(
    "<main>",
    "<main>\n  <div id=\"motor\" class=\"estado\"><div class=\"giro\"></div>\n"
    "    <div class=\"pasando\">Cargando la mesa…</div></div>",
    1,
)
import json

archivos = {
    nombre: (RAIZ / "bot" / nombre).read_text(encoding="utf-8")
    for nombre in ("indicadores.py", "estrategia.py", "zonas.py", "mesa.py")
}
boot = (RAIZ / "web" / "boot.js").read_text(encoding="utf-8")
# El HTML corta el script en cuanto ve </script>, aunque esté dentro de un
# string. Las fuentes de Python traen esa marca porque la página vive ahí.
carga = json.dumps(archivos, ensure_ascii=False).replace("<", "\\u003c")
boot = "globalThis.ARCHIVOS_MESA = " + carga + ";\n" + boot
html = html.replace("<script>\n", "<script>\n" + boot + "\n", 1)
destino = RAIZ / "web" / "index.html"
destino.write_text(html, encoding="utf-8")
print(destino, "bytes", destino.stat().st_size)
