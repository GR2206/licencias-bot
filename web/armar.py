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
    '<main>\n  <div id="motor" class="estado">Cargando la mesa en Chrome…</div>',
    1,
)
boot = (RAIZ / "web" / "boot.js").read_text(encoding="utf-8")
html = html.replace("<script>\n", "<script>\n" + boot + "\n", 1)
destino = RAIZ / "web" / "index.html"
destino.write_text(html, encoding="utf-8")
print(destino, "bytes", destino.stat().st_size)
