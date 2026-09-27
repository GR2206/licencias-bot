import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from polymarket_agent.api import Book, Market, parse_market
from polymarket_agent.audit import auditar, revisar
from polymarket_agent.cycle import ScriptedData, run_scan
from polymarket_agent.engine import (
    Portfolio,
    Research,
    Source,
    binary_pnl,
    evaluate,
    fee_per_share,
    kelly_fraction,
    parse_research,
    round_probability,
    worked_example,
)
from polymarket_agent.ledger import Ledger
from polymarket_agent.llm import parse_model_json
from polymarket_agent.risk import LIMITS


NOW = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)


def market(**overrides) -> Market:
    data = dict(
        slug="ejemplo",
        question="¿Pasa X antes de octubre?",
        description="Regla concreta de resolución.",
        category="crypto_fees",
        end=NOW + timedelta(days=10),
        fee_rate=0.07,
        fee_exponent=1.0,
        event_id="evt-1",
        outcomes=("Sí", "No"),
        token_ids=("yes-token", "no-token"),
        gamma_prices=(0.42, 0.58),
        min_order_size=5.0,
        tick_size=0.01,
        accepting=True,
        closed=False,
    )
    data.update(overrides)
    return Market(**data)


def book(bid=0.41, ask=0.43, depth=100000.0, minimum=5.0) -> Book:
    return Book(bid=bid, ask=ask, ask_depth=depth, min_order_size=minimum, tick_size=0.01)


def research(**overrides) -> Research:
    payload = {
        "resolution_line": "El primer outcome paga solo si X ocurre antes del cierre publicado.",
        "resolution_clear": True,
        "probability": 0.55,
        "confidence": "media",
        "reasons_for": ["Hay un dato oficial del 26/09.", "La regla de resolución es verificable."],
        "reasons_against": ["La otra punta puede tener un dato más fresco.", "Puede ser liquidez, no información."],
        "counterparty_hypothesis": "Quien vende descuenta un escenario que todavía no está en las dos fuentes.",
        "sources": [
            {"title": "Boletín", "url": "https://datos.example/boletin", "date": "2026-09-26", "kind": "primaria"},
            {"title": "Cobertura", "url": "https://medio.example/nota", "date": "2026-09-26", "kind": "medio"},
        ],
        "fast_market": False,
        "reviewed_large_divergence": False,
    }
    payload.update(overrides)
    return parse_research(payload, human=True)


def portfolio(**overrides) -> Portfolio:
    data = dict(
        cash=1000.0,
        equity=1000.0,
        peak_equity=1000.0,
        open_event_ids=frozenset(),
        open_slugs=frozenset(),
        last_edge={},
    )
    data.update(overrides)
    return Portfolio(**data)


class MathTests(unittest.TestCase):
    def test_guia_53_contra_42_en_crypto(self):
        example = worked_example()
        self.assertAlmostEqual(example["edge_bruto_pp"], 11.0, places=6)
        self.assertAlmostEqual(example["comision_pp"], 1.7052, places=3)
        self.assertAlmostEqual(example["edge_neto_pp"], 9.2948, places=3)
        self.assertAlmostEqual(example["kelly_completo"], 0.11 / 0.58, places=6)
        self.assertAlmostEqual(example["fraccion"], 0.25 * (0.11 / 0.58), places=6)
        self.assertAlmostEqual(example["stake_sobre_1000"], 47.41, places=2)
        self.assertEqual(example["probabilidad_que_opera"], 0.55)
        self.assertLess(example["fraccion"], LIMITS.max_position_fraction)

    def test_redondeo_a_cinco(self):
        self.assertEqual(round_probability(0.537), 0.55)
        self.assertEqual(round_probability(0.53), 0.55)
        self.assertEqual(round_probability(0.52), 0.50)
        self.assertEqual(round_probability(0.525), 0.55)

    def test_techo_de_kelly(self):
        self.assertEqual(kelly_fraction(0.90, 0.40), 0.06)

    def test_comision_cero_en_tasa_cero(self):
        self.assertEqual(fee_per_share(0.42, 0.0, 1.0), 0.0)

    def test_pnl_descuenta_comision(self):
        win = binary_pnl(100, 0.42, 0.07, 1.0, won=True)
        loss = binary_pnl(100, 0.42, 0.07, 1.0, won=False)
        self.assertGreater(win, 0)
        self.assertLess(loss, -100)

    def test_limites_congelados(self):
        with self.assertRaises(Exception):
            LIMITS.min_edge_pp = 1


class DecisionTests(unittest.TestCase):
    def test_simula_cuando_el_edge_neto_pasa_el_8(self):
        decision = evaluate(market(), book(), book(0.57, 0.59), research(), portfolio(), NOW)
        self.assertEqual(decision.decision, "simular")
        self.assertEqual(decision.lado, "YES")
        self.assertGreaterEqual(decision.edge_neto, 8)
        self.assertLessEqual(decision.fraccion, 0.06)
        self.assertGreater(decision.stake, 0)

    def test_compra_el_no_si_ahi_esta_el_edge(self):
        decision = evaluate(
            market(),
            book(),
            book(0.57, 0.59),
            research(probability=0.30),
            portfolio(),
            NOW,
        )
        self.assertEqual(decision.decision, "simular")
        self.assertEqual(decision.lado, "NO")

    def test_umbral_descarta(self):
        decision = evaluate(market(), book(), book(0.57, 0.59), research(probability=0.50), portfolio(), NOW)
        self.assertEqual(decision.motivo, "umbral_8")
        self.assertEqual(decision.stake, 0)

    def test_divergencia_grande_sin_humano(self):
        payload = research(probability=0.90)
        decision = evaluate(market(), book(), book(0.57, 0.59), payload, portfolio(), NOW)
        self.assertEqual(decision.motivo, "divergencia_25")

    def test_el_modelo_no_puede_autoaprobar_la_divergencia(self):
        raw = research(probability=0.90)
        blocked = Research(**{**raw.__dict__, "divergence_attested_by_human": False, "reviewed_large_divergence": True})
        decision = evaluate(market(), book(), book(0.57, 0.59), blocked, portfolio(), NOW)
        self.assertEqual(decision.motivo, "divergencia_25")

    def test_confianza_baja_solo_registra(self):
        decision = evaluate(market(), book(), book(0.57, 0.59), research(confidence="baja"), portfolio(), NOW)
        self.assertEqual(decision.decision, "solo_registro")
        self.assertEqual(decision.stake, 0)

    def test_spread_ancho(self):
        decision = evaluate(market(), book(0.30, 0.40), book(0.60, 0.70), research(), portfolio(), NOW)
        self.assertEqual(decision.motivo, "spread")

    def test_precio_extremo_y_horizonte(self):
        extreme = evaluate(market(gamma_prices=(0.02, 0.98)), book(0.01, 0.03), book(0.97, 0.99), research(), portfolio(), NOW)
        self.assertEqual(extreme.motivo, "precio_extremo")
        far = evaluate(market(end=NOW + timedelta(days=200)), book(), book(0.57, 0.59), research(), portfolio(), NOW)
        self.assertEqual(far.motivo, "horizonte")

    def test_fuentes_de_x_no_cuentan_y_un_mercado_rapido_pide_fecha(self):
        only_social = research(
            sources=[
                {"title": "Post", "url": "https://x.com/alguien/status/1", "date": "2026-09-26", "kind": "agregador"},
                {"title": "Otro", "url": "https://twitter.com/alguien/status/2", "date": "2026-09-26", "kind": "agregador"},
            ]
        )
        decision = evaluate(market(), book(), book(0.57, 0.59), only_social, portfolio(), NOW)
        self.assertEqual(decision.motivo, "fuentes_insuficientes")

        stale = research(
            sources=[
                {"title": "A", "url": "https://datos.example/a", "date": "2026-09-20", "kind": "primaria"},
                {"title": "B", "url": "https://medio.example/b", "date": "2026-09-20", "kind": "medio"},
            ]
        )
        fast = evaluate(market(category="sports_fees", fee_rate=0.05), book(), book(0.57, 0.59), stale, portfolio(), NOW)
        self.assertEqual(fast.motivo, "fuentes_viejas")

    def test_libro_flaco_y_correlacion_y_sesgo_y_drawdown(self):
        thin = evaluate(market(), book(depth=1), book(0.57, 0.59, depth=1), research(), portfolio(), NOW)
        self.assertEqual(thin.motivo, "libro_flaco")

        crowded = portfolio(open_event_ids=frozenset({"evt-1"}))
        correlated = evaluate(market(slug="otro"), book(), book(0.57, 0.59), research(), crowded, NOW)
        self.assertEqual(correlated.motivo, "correlacion")

        biased = portfolio(last_edge={"ejemplo": 1.0})
        growing = evaluate(market(), book(), book(0.57, 0.59), research(), biased, NOW)
        self.assertEqual(growing.motivo, "sesgo_edge_creciente")
        self.assertEqual(growing.stake, 0)

        hurting = portfolio(equity=700, peak_equity=1000)
        halted = evaluate(market(), book(), book(0.57, 0.59), research(), hurting, NOW)
        self.assertEqual(halted.motivo, "drawdown_25")


class LedgerTests(unittest.TestCase):
    def test_la_resolucion_solo_completa_resultado_y_pnl(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Ledger(Path(tmp), 1000)
            decision = evaluate(market(), book(), book(0.57, 0.59), research(), portfolio(), NOW)
            written = ledger.append(market(), decision, NOW)
            self.assertEqual(written["ejecutado"], "no")
            settled = ledger.settle("ejemplo", "NO")
            self.assertEqual(settled["precio_yes"], written["precio_yes"])
            self.assertEqual(settled["prob_estimada"], written["prob_estimada"])
            self.assertEqual(settled["razonamiento"], written["razonamiento"])
            self.assertEqual(settled["bankroll_post"], written["bankroll_post"])
            self.assertEqual(settled["resultado"], "NO")
            self.assertLess(float(settled["pnl"]), 0)
            again = ledger.settle("ejemplo", "YES")
            self.assertIsNone(again)

    def test_replay_de_posicion_abierta_y_de_perdida(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            ledger = Ledger(home, 1000)
            decision = evaluate(market(), book(), book(0.57, 0.59), research(), portfolio(), NOW)
            ledger.append(market(), decision, NOW)
            open_snap = ledger.snapshot()
            self.assertAlmostEqual(open_snap.equity, 1000, places=2)
            self.assertAlmostEqual(open_snap.cash, 1000 - decision.stake, places=2)
            self.assertFalse(open_snap.halted)

            ledger.settle("ejemplo", "NO")
            closed = ledger.snapshot()
            pnl = float(ledger.rows()[0]["pnl"])
            self.assertAlmostEqual(closed.cash, 1000 + pnl, places=2)
            self.assertEqual(closed.reserved, 0)
            self.assertLess(closed.equity, 1000)

            locked = Ledger(home, 50)
            self.assertEqual(locked.load_state().initial_bankroll, 1000)

    def test_una_perdida_grande_activa_el_freno(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Ledger(Path(tmp), 1000)
            forced = evaluate(market(), book(), book(0.57, 0.59), research(), portfolio(), NOW)
            forced.stake = 400
            forced.fraccion = 0.4
            forced.precio_entrada = 0.43
            ledger.append(market(), forced, NOW)
            ledger.settle("ejemplo", "NO")
            self.assertTrue(ledger.snapshot().halted)


class CycleTests(unittest.TestCase):
    def test_sin_evidencia_no_simula(self):
        now = NOW
        far = market(slug="lejos", end=now + timedelta(days=200), token_ids=("a", "b"))
        extreme = market(slug="extremo", gamma_prices=(0.01, 0.99), token_ids=("c", "d"))
        wide = market(slug="ancho", token_ids=("e", "f"))
        tight = market(slug="fino", token_ids=("g", "h"))
        missing = market(slug="vacio", token_ids=("i", "j"))
        data = ScriptedData(
            [far, extreme, wide, tight, missing],
            {"e": 0.05, "g": 0.01},
            {},
        )
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Ledger(Path(tmp), 1000)
            report = run_scan(ledger, data, now=now)
            self.assertEqual(report.reviewed, 5)
            self.assertEqual(report.counts["horizonte"], 1)
            self.assertEqual(report.counts["precio_extremo"], 1)
            self.assertEqual(report.counts["spread"], 1)
            self.assertEqual(report.counts["sin_spread"], 1)
            self.assertEqual(report.counts["pendiente_investigacion"], 1)
            self.assertEqual(report.opportunities, [])
            self.assertTrue(all(row["ejecutado"] == "no" for row in ledger.rows()))

    def test_con_evidencia_simula_y_el_llm_roto_no_inventa(self):
        tight = market(slug="fino", token_ids=("g", "h"))
        other = market(slug="otro", token_ids=("i", "j"), event_id="evt-2")
        books = {"g": book(), "h": book(0.57, 0.59), "i": book(), "j": book(0.57, 0.59)}
        data = ScriptedData([tight, other], {"g": 0.01, "i": 0.01}, books)
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Ledger(Path(tmp), 1000)
            def broken(_market):
                raise RuntimeError("modelo caido")

            report = run_scan(
                ledger,
                data,
                research_by_slug={"fino": research()},
                estimator=broken,
                max_estimates=1,
                now=NOW,
            )
            self.assertEqual(report.counts["edge_neto"], 1)
            self.assertEqual(report.counts["llm_error"], 1)
            self.assertEqual(len(report.opportunities), 1)


class AuditTests(unittest.TestCase):
    def test_muestra_chica_no_autoriza_cambios(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Ledger(Path(tmp), 1000)
            verdict = revisar(ledger)
            self.assertEqual(verdict["cambio"], "no hay evidencia suficiente para cambiar nada")
            self.assertFalse(verdict["aplicado"])
            report = auditar(ledger)
            self.assertEqual(report["sesgo"]["operaciones_resueltas"], 0)

    def test_parsea_gamma_y_json_del_modelo(self):
        raw = {
            "slug": "xi",
            "question": "¿Sale?",
            "description": "Regla.",
            "feeType": "politics_fees",
            "feeSchedule": {"rate": 0.04, "exponent": 1},
            "endDate": "2026-10-04T02:00:00Z",
            "outcomes": "[\"Yes\", \"No\"]",
            "clobTokenIds": "[\"1\", \"2\"]",
            "outcomePrices": "[\"0.42\", \"0.58\"]",
            "events": [{"id": "99"}],
            "orderMinSize": 5,
            "orderPriceMinTickSize": 0.01,
            "acceptingOrders": True,
            "closed": False,
        }
        parsed = parse_market(raw)
        self.assertEqual(parsed.fee_rate, 0.04)
        self.assertEqual(parsed.event_id, "99")
        self.assertEqual(parsed.gamma_prices, (0.42, 0.58))

        content = json.dumps(
            {
                "resolution_line": "YES paga únicamente si el hecho publicado ocurre dentro del plazo.",
                "resolution_clear": True,
                "probability": 0.9,
                "confidence": "alta",
                "reasons_for": ["Dato A reciente.", "Dato B independiente."],
                "reasons_against": ["La regla podría no cubrir el caso.", "El precio ya se movió."],
                "counterparty_hypothesis": "El mercado espera una desmentida oficial.",
                "sources": [
                    {"title": "A", "url": "https://a.example/a", "date": "2026-09-26", "kind": "primaria"},
                    {"title": "B", "url": "https://b.example/b", "date": "2026-09-26", "kind": "medio"},
                ],
                "reviewed_large_divergence": True,
                "stake": 999,
            }
        )
        estimated = parse_model_json(content)
        self.assertFalse(estimated.divergence_attested_by_human)
        self.assertEqual(estimated.probability, 0.9)


class SourceTests(unittest.TestCase):
    def test_fuentes_del_mismo_dominio_no_son_dos(self):
        same = research(
            sources=[
                {"title": "A", "url": "https://datos.example/a", "date": "2026-09-26", "kind": "primaria"},
                {"title": "B", "url": "https://datos.example/b", "date": "2026-09-26", "kind": "medio"},
            ]
        )
        decision = evaluate(market(), book(), book(0.57, 0.59), same, portfolio(), NOW)
        self.assertEqual(decision.motivo, "fuentes_insuficientes")


if __name__ == "__main__":
    unittest.main()
