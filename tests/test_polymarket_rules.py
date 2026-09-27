import unittest

from polymarket.config import LIMITS, RiskLimits, assert_limits_intact
from polymarket.rules import (
    edge_bruto_pp,
    edge_neto_pp,
    fee_per_share,
    fee_pp,
    fee_rate_for,
    infer_category,
    kelly_full,
    position_fraction,
    round_probability,
    valid_sources,
)


class FeeAndEdgeTests(unittest.TestCase):
    def test_guide_fee_politics_around_42_cents(self):
        fee = fee_per_share(0.42, 0.04)
        self.assertAlmostEqual(fee, 0.04 * 0.42 * 0.58, places=8)
        self.assertAlmostEqual(fee_pp(0.42, 0.04), 0.9744, places=3)

    def test_geo_has_zero_fee(self):
        self.assertEqual(fee_rate_for("geopolitics"), 0.0)
        self.assertEqual(fee_rate_for("geopolítica"), 0.0)

    def test_published_rate_wins(self):
        self.assertEqual(fee_rate_for("sports", published_rate=0.05), 0.05)

    def test_guide_edge_11_passes_6_fails(self):
        self.assertAlmostEqual(edge_bruto_pp(0.53, 0.42), 11.0, places=6)
        self.assertAlmostEqual(edge_bruto_pp(0.48, 0.42), 6.0, places=6)
        neto_11 = edge_neto_pp(0.53, 0.42, 0.01, 0.04)
        self.assertGreater(neto_11, 8.0)
        neto_6 = edge_neto_pp(0.48, 0.42, 0.01, 0.04)
        self.assertLess(neto_6, 8.0)

    def test_crypto_fee_eats_edge(self):
        bruto = edge_bruto_pp(0.53, 0.42)
        crypto = edge_neto_pp(0.53, 0.42, 0.0, 0.07)
        self.assertLess(crypto, bruto)
        self.assertAlmostEqual(bruto - crypto, fee_pp(0.42, 0.07), places=6)


class KellyTests(unittest.TestCase):
    def test_guide_kelly_quarter_and_cap(self):
        full = kelly_full(0.53, 0.42)
        self.assertAlmostEqual(full, 0.11 / 0.58, places=8)
        frac = position_fraction(0.53, 0.42)
        self.assertAlmostEqual(frac, min(0.25 * full, 0.06), places=8)
        self.assertLess(frac, 0.06)

    def test_cap_binds_on_huge_edge(self):
        frac = position_fraction(0.90, 0.40)
        self.assertEqual(frac, 0.06)

    def test_negative_edge_is_zero(self):
        self.assertEqual(position_fraction(0.30, 0.42), 0.0)

    def test_limits_cannot_be_relaxed(self):
        with self.assertRaises(PermissionError):
            assert_limits_intact(RiskLimits(edge_min_pp=5.0))
        with self.assertRaises(PermissionError):
            assert_limits_intact(RiskLimits(kelly_fraction=0.5))
        with self.assertRaises(PermissionError):
            assert_limits_intact(RiskLimits(position_cap=0.10))


class ProbabilityTests(unittest.TestCase):
    def test_round_to_five(self):
        self.assertAlmostEqual(round_probability(0.537), 0.55)
        self.assertAlmostEqual(round_probability(0.52), 0.50)
        self.assertAlmostEqual(round_probability(0.53), 0.55)

    def test_category_from_fee_type_and_tags(self):
        self.assertEqual(infer_category(fee_type="politics_fees"), "politics")
        self.assertEqual(infer_category(tags=[{"slug": "nba"}]), "sports")
        self.assertEqual(infer_category(slug="will-btc-hit-100k"), "crypto")

    def test_fast_sources_need_recent_dates(self):
        sources = [
            "https://a.test 2020-01-01",
            "https://b.test 2020-01-02",
        ]
        kept = valid_sources(sources, fast=True, now=None)
        self.assertEqual(kept, [])
        fresh = [
            {"url": "https://a.test", "date": "2099-01-01"},
            {"url": "https://b.test", "date": "2099-01-02"},
        ]
        self.assertEqual(len(valid_sources(fresh, fast=False)), 2)


if __name__ == "__main__":
    unittest.main()
