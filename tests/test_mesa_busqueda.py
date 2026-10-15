import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bot"))

import mesa  # noqa: E402


class BusquedaTests(unittest.TestCase):
    def test_la_pagina_tiene_el_apartado(self):
        pagina = mesa.PAGINA
        self.assertEqual(mesa.VERSION, 28)
        self.assertIn('id="busqueda"', pagina)
        self.assertIn('id="qBusqueda"', pagina)
        self.assertIn('id="correrBusqueda"', pagina)
        self.assertIn("function correrBusqueda()", pagina)
        self.assertIn("también en los cerrados", pagina)
        self.assertIn("versión 28", pagina)


if __name__ == "__main__":
    unittest.main()
