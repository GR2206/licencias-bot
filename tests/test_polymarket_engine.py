import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from polymarket.audit import can_propose_change, render_audit
from polymarket.config import LIMITS
from polymarket.engine import CycleStats, evaluate_estimate, paper_pnl, scan, winner_from_market
from polymarket.filters import reject_book, reject_price, reject_resolution, reject_spread
from polymarket.ledger import append_row, complete_resolution, current_bankroll, load_rows
from polymarket.risk import correlated_open, drawdown_halt, growing_edge_trap, reject_strategy_change


NOW = datetime(2026, 9, 27, tzinfo=timezone.utc)


def market(**overrides):
    base = {
        "slug": "demo-market",
        "question": "Will X happen before 2026-10-15?",
        "conditionId": "0xabc",
        "endDate": "2026-10-15T00:00:00Z",
        "clobTokenIds": '["1","2"]',
        "outcomes": '["Yes","No"]',
        "outcomePrices": '["0.42","0.58"]',
        "bestBid": 0.41,
        "bestAsk": 0.42,
        "spread": 0.01,
        "feeType": "politics_fees",
        "feeSchedule": {"rate": 0.04},
        "orderMinSize": 5,
        "liquidityNum": 50000,
        "events": [{"id": "evt-1", "slug": "demo-event", "title": "Demo", "tags": []}],
        "closed": False,
    }
    base.update(overrides)
    return base


def estimate(**overrides):
    base = {
        "slug": "demo-market",
        "probabilidad": 0.53,
        "confianza": "alta",
        "lado": "YES",
        "condicion_resolucion": "YES paga si X ocurre antes del 15/10/2026 00:00 UTC.",
        "a_favor": ["dato oficial del 20/09", "serie histórica alineada"],
        "en_contra": ["el mercado ya lo preció", "puede haber info privada"],
        "hipotesis_error_mercado": "el dato oficial de ayer todavía no está en el precio",
        "fuentes": [
            {"url": "https://oficial.test/doc", "date": "2026-09-20"},
            {"url": "https://diario.test/nota", "date": "2026-09-21"},
        ],
        "razonamiento": "dato oficial del 20/09 que el precio todavía no refleja",
    }
    base.update(overrides)
    return base


class FilterTests(unittest.TestCase):
    def test_hard_filters(self):
        self.assertIsNotNone(reject_spread(0.04))
        self.assertIsNone(reject_spread(0.03))
        self.assertIsNotNone(reject_price(0.04))
        self.assertIsNotNone(reject_price(0.96))
        self.assertIsNone(reject_price(0.42))
        self.assertIsNotNone(reject_resolution(120))
        self.assertIsNotNone(reject_resolution(-1))
        self.assertIsNone(reject_resolution(30))
        self.assertIsNotNone(reject_book(10, 5))
        self.assertIsNone(reject_book(100, 5))


class EstimateTests(unittest.TestCase):
    def test_guide_case_is_simulated(self):
        row = evaluate_estimate(
            estimate(),
            market(),
            bankroll=1000,
            rows=[],
            price_map={"1": {"BUY": 0.42}, "2": {"BUY": 0.58}},
            spread=0.01,
            now=NOW,
        )
        self.assertEqual(row["decision"], "simulado")
        self.assertGreaterEqual(float(row["edge_neto"]), 8.0)
        self.assertLessEqual(float(row["fraccion"]), 0.06)
        self.assertAlmostEqual(float(row["bankroll_post"]), 1000 - float(row["stake_simulado"]), places=2)

    def test_six_points_are_discarded(self):
        row = evaluate_estimate(
            estimate(probabilidad=0.48),
            market(),
            bankroll=1000,
            rows=[],
            price_map={"1": {"BUY": 0.42}},
            spread=0.01,
            now=NOW,
        )
        self.assertEqual(row["decision"], "descartado")
        self.assertIn("edge neto", row["motivo"])

    def test_needs_counterarguments_and_hypothesis(self):
        row = evaluate_estimate(
            estimate(en_contra=["solo una"]),
            market(),
            bankroll=1000,
            rows=[],
            price_map={"1": {"BUY": 0.42}},
            spread=0.01,
            now=NOW,
        )
        self.assertIn("en contra", row["motivo"])
        row = evaluate_estimate(
            estimate(hipotesis_error_mercado=""),
            market(),
            bankroll=1000,
            rows=[],
            price_map={"1": {"BUY": 0.42}},
            spread=0.01,
            now=NOW,
        )
        self.assertIn("hipótesis", row["motivo"])

    def test_low_confidence_is_logged_not_traded(self):
        row = evaluate_estimate(
            estimate(confianza="baja"),
            market(),
            bankroll=1000,
            rows=[],
            price_map={"1": {"BUY": 0.42}},
            spread=0.01,
            now=NOW,
        )
        self.assertEqual(row["decision"], "registrado")

    def test_25pp_alarm(self):
        row = evaluate_estimate(
            estimate(probabilidad=0.80),
            market(),
            bankroll=1000,
            rows=[],
            price_map={"1": {"BUY": 0.42}},
            spread=0.01,
            now=NOW,
        )
        self.assertIn("25", row["motivo"])
        self.assertEqual(row["decision"], "descartado")

    def test_correlation_blocks_second_bet(self):
        first = evaluate_estimate(
            estimate(),
            market(),
            bankroll=1000,
            rows=[],
            price_map={"1": {"BUY": 0.42}},
            spread=0.01,
            now=NOW,
        )
        blocked, _ = correlated_open(
            {"event_id": "evt-1", "slug": "otro"},
            [first],
        )
        self.assertTrue(blocked)

    def test_drawdown_halt(self):
        rows = [
            {
                "decision": "simulado",
                "bankroll_pre": "1000",
                "bankroll_post": "700",
                "resultado": "NO",
            }
        ]
        halted, reason = drawdown_halt(rows, 1000)
        self.assertTrue(halted)
        self.assertIn("drawdown", reason)

    def test_growing_edge_trap(self):
        history = [
            {"slug": "demo-market", "decision": "simulado", "edge_neto": "9.0", "resultado": ""},
            {"slug": "demo-market", "decision": "simulado", "edge_neto": "11.0", "resultado": ""},
        ]
        trapped, _ = growing_edge_trap({"slug": "demo-market", "edge_neto": "14.0"}, history)
        self.assertTrue(trapped)

    def test_paper_pnl_win_and_loss(self):
        win = paper_pnl("YES", 47.41, 0.42, True, 0.04)
        loss = paper_pnl("YES", 47.41, 0.42, False, 0.04)
        self.assertGreater(win, 0)
        self.assertLess(loss, 0)

    def test_winner_from_closed_market(self):
        self.assertEqual(
            winner_from_market(market(closed=True, outcomePrices='["1","0"]')),
            "YES",
        )
        self.assertEqual(
            winner_from_market(market(closed=True, outcomePrices='["0","1"]')),
            "NO",
        )
        self.assertIsNone(winner_from_market(market(closed=False, outcomePrices='["1","0"]')))


class LedgerTests(unittest.TestCase):
    def test_write_once_resolution(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "registro.csv"
            row = append_row(
                {
                    "slug": "demo",
                    "decision": "simulado",
                    "bankroll_pre": "1000",
                    "bankroll_post": "950",
                    "stake_simulado": "50",
                },
                path,
            )
            self.assertEqual(current_bankroll(load_rows(path), 1000), 950)
            complete_resolution(0, resultado="YES", pnl=20, bankroll_post=970, path=path)
            with self.assertRaises(PermissionError):
                complete_resolution(0, resultado="NO", pnl=-50, bankroll_post=900, path=path)
            saved = load_rows(path)[0]
            self.assertEqual(saved["resultado"], "YES")
            self.assertEqual(row["slug"], "demo")


class ScanTests(unittest.TestCase):
    def test_scan_discards_then_keeps_candidate(self):
        class Fake:
            def iter_open_markets(self, limit=200, **_kwargs):
                return [
                    market(slug="far", endDate="2028-01-01T00:00:00Z", clobTokenIds='["10","11"]'),
                    market(
                        slug="cheap",
                        bestAsk=0.02,
                        outcomePrices='["0.02","0.98"]',
                        clobTokenIds='["20","21"]',
                    ),
                    market(
                        slug="wide",
                        spread=0.08,
                        bestBid=0.40,
                        bestAsk=0.48,
                        clobTokenIds='["30","31"]',
                    ),
                    market(slug="ok", clobTokenIds='["40","41"]'),
                ][:limit]

            def prices(self, ids, side="BUY"):
                table = {"10": 0.42, "20": 0.02, "30": 0.48, "40": 0.42}
                return {i: {"BUY": table.get(i, 0.42)} for i in ids}

            def book(self, token_id):
                return {
                    "asks": [{"price": "0.42", "size": "5000"}, {"price": "0.43", "size": "5000"}],
                    "min_order_size": "5",
                }

            def spread(self, token_id):
                return 0.01

        stats, rows, _ = scan(
            limit=10,
            persist=False,
            client=Fake(),
            fetch_books=True,
            bankroll=1000,
        )
        self.assertIsInstance(stats, CycleStats)
        self.assertEqual(stats.revisados, 4)
        self.assertEqual(stats.descartados, 3)
        self.assertEqual(stats.candidatos, 1)
        self.assertEqual(rows[-1]["decision"], "candidato")
        self.assertEqual(rows[-1]["slug"], "ok")


class AuditTests(unittest.TestCase):
    def test_insufficient_sample_and_conservative_gate(self):
        rows = []
        for i in range(8):
            rows.append(
                {
                    "decision": "simulado",
                    "resultado": "YES" if i % 2 == 0 else "NO",
                    "prob_estimada": "0.70",
                    "precio_yes": "0.40",
                    "pnl": "10" if i % 2 == 0 else "-10",
                    "categoria": "politics",
                    "slug": f"m{i}",
                    "razonamiento": "x",
                }
            )
        text = render_audit(rows)
        self.assertIn("muestra insuficiente", text)
        self.assertEqual(can_propose_change(8), "no hay evidencia suficiente para cambiar nada")
        self.assertIn("operar más", reject_strategy_change({"cambio": "operar más"}))
        self.assertIn("8%", reject_strategy_change({"cambio": "bajar el umbral"}))


if __name__ == "__main__":
    unittest.main()
