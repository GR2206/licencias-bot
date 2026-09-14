"""Prueba de integracion del bot, sin tocar tu cuenta.

Reemplaza el cliente de Binance por uno falso que anota las ordenes en vez de
mandarlas, y verifica lo que de verdad importa:

  1. Que una senal real dispare entrada + stop + parcial + objetivo.
  2. Que el stop se manda DESPUES de la entrada (nunca al revés).
  3. Que el tamano de la posicion respete el riesgo configurado.
  4. Que al cobrarse la parcial el stop se mueva a la entrada.

Uso:
    python prueba_integracion.py
"""

import sys

import binance_api as api
import bot
import estrategia
import indicadores as ind

SALDO = 1000.0
RIESGO_PCT = 1.0


def a_klines(velas):
    return [
        [v.tiempo, str(v.apertura), str(v.maximo), str(v.minimo), str(v.cierre), str(v.volumen)]
        for v in velas
    ]


class ClienteFalso(api.Cliente):
    def __init__(self, velas):
        super().__init__(base=api.TESTNET)
        self._velas = velas
        self.ordenes = []
        self.posicion_actual = 0.0

    def klines(self, simbolo, intervalo, limite=500):
        return a_klines(self._velas[-limite:])

    def filtros(self, simbolo):
        return {"tick": 0.1, "paso": 0.001, "min_qty": 0.001, "min_notional": 5.0}

    def posicion(self, simbolo):
        return self.posicion_actual

    def saldo_usdt(self):
        return SALDO

    def apalancamiento(self, simbolo, x):
        self.ordenes.append(("apalancamiento", simbolo, x))
        return {}

    def orden_mercado(self, simbolo, lado, cantidad):
        self.ordenes.append(("mercado", simbolo, lado, cantidad))
        return {"orderId": 1}

    def orden_stop(self, simbolo, lado, precio):
        self.ordenes.append(("stop", simbolo, lado, precio))
        return {"orderId": 2}

    def orden_objetivo(self, simbolo, lado, precio):
        self.ordenes.append(("tp_final", simbolo, lado, precio))
        return {"orderId": 3}

    def orden_objetivo_parcial(self, simbolo, lado, precio, cantidad):
        self.ordenes.append(("tp_parcial", simbolo, lado, precio, cantidad))
        return {"orderId": 4}

    def cancelar_ordenes(self, simbolo):
        self.ordenes.append(("cancelar", simbolo))
        return {}


def main():
    cfg = estrategia.Config()

    print("Bajando velas del testnet para buscar una senal historica...")
    publico = api.Cliente(base=api.TESTNET)
    velas = ind.desde_klines(publico.klines("BTCUSDT", "1h", 1500))[:-1]
    lista = estrategia.senales(velas, cfg)
    print(f"  {len(velas)} velas, {len(lista)} senales encontradas")
    if not lista:
        print("No aparecieron senales en este tramo. Probá de nuevo mas tarde.")
        return 1

    senal = lista[len(lista) // 2]
    print(f"  uso la del indice {senal.indice}: {senal.accion} @ {senal.entrada}")

    # El bot descarta la ultima vela por estar abierta: hay que darle una mas.
    tramo = velas[: senal.indice + 2]
    cliente = ClienteFalso(tramo)

    bot.ARCHIVO_ESTADO = "/tmp/estado_prueba_integracion.json"
    bot.ARCHIVO_LOG = "/tmp/bot_prueba_integracion.log"

    ajustes = bot.Ajustes()
    ajustes.modo = "real"  # falso "real": el cliente no manda nada afuera
    ajustes.api_key = "clave_de_prueba"
    ajustes.api_secret = "secreto_de_prueba"
    ajustes.riesgo_pct = RIESGO_PCT
    estado = {}

    print("\n--- 1) Entrada ---")
    bot.revisar(cliente, ajustes, "BTCUSDT", "1h", cfg, estado)
    for o in cliente.ordenes:
        print("   ", o)

    tipos = [o[0] for o in cliente.ordenes]
    assert "mercado" in tipos, "no mando la orden de entrada"
    assert "stop" in tipos, "no mando el stop"
    assert "tp_parcial" in tipos, "no mando la parcial de 1R"
    assert "tp_final" in tipos, "no mando el objetivo final"
    assert tipos.index("mercado") < tipos.index("stop"), "el stop debe ir despues de la entrada"

    cantidad = [o for o in cliente.ordenes if o[0] == "mercado"][0][3]
    perdida = cantidad * abs(senal.entrada - senal.stop)
    esperado = SALDO * RIESGO_PCT / 100
    print(f"\n   tamano {cantidad} -> si salta el stop pierde {perdida:.2f} USDT (objetivo {esperado:.2f})")
    assert perdida <= esperado * 1.05, "la posicion arriesga mas de lo configurado"
    assert perdida >= esperado * 0.80, "la posicion quedo demasiado chica"

    lado_stop = [o for o in cliente.ordenes if o[0] == "stop"][0][2]
    assert lado_stop == ("SELL" if senal.es_compra else "BUY"), "el stop cierra para el lado equivocado"

    print("\n--- 2) Se cobra la parcial: el stop tiene que ir a la entrada ---")
    cliente.ordenes.clear()
    cliente.posicion_actual = (cantidad / 2) * (1 if senal.es_compra else -1)
    bot.gestionar_posiciones(cliente, ajustes, estado)
    for o in cliente.ordenes:
        print("   ", o)

    nuevos = [o for o in cliente.ordenes if o[0] == "stop"]
    assert nuevos, "no repuso el stop despues de la parcial"
    assert abs(nuevos[0][3] - senal.entrada) < 1e-9, "el stop nuevo no quedo en la entrada"
    assert not estado["posiciones"]["BTCUSDT"]["parcial_pendiente"]

    print("\n--- 3) Posicion cerrada: tiene que limpiar ordenes y estado ---")
    cliente.ordenes.clear()
    cliente.posicion_actual = 0.0
    bot.gestionar_posiciones(cliente, ajustes, estado)
    for o in cliente.ordenes:
        print("   ", o)
    assert "BTCUSDT" not in estado.get("posiciones", {}), "quedo basura en el estado"

    print("\nTODO OK: el camino completo funciona y respeta el riesgo.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
