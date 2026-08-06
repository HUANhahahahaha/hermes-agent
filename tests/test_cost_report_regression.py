from __future__ import annotations

import importlib.util
import sqlite3
import sys
import tempfile
import types
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import agent.usage_pricing as pricing
from agent.usage_pricing import CanonicalUsage, estimate_usage_cost

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "estimated_cost_report", ROOT / "提醒事项" / "estimated_cost_report.py"
)
report = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
sys.modules[SPEC.name] = report
SPEC.loader.exec_module(report)

SAMPLE = CanonicalUsage(
    input_tokens=347_293,
    output_tokens=4_331,
    cache_read_tokens=519_415,
)


def _report_db(model="claude-sonnet-5", provider="unknown"):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE sessions (
          id TEXT, source TEXT, model TEXT, started_at REAL,
          input_tokens INTEGER, output_tokens INTEGER,
          cache_read_tokens INTEGER, cache_write_tokens INTEGER,
          billing_provider TEXT, billing_base_url TEXT,
          actual_cost_usd REAL, cost_status TEXT
        )
        """
    )
    started = datetime(2026, 8, 6, 8, 0, tzinfo=timezone.utc).timestamp()
    conn.execute(
        "INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "s1", "wechat", model, started,
            SAMPLE.input_tokens, SAMPLE.output_tokens,
            SAMPLE.cache_read_tokens, SAMPLE.cache_write_tokens,
            provider, None, None, "unknown",
        ),
    )
    return conn


class CostReportRegressionTests(unittest.TestCase):
    def test_unknown_provider_is_inferred(self):
        result = estimate_usage_cost("claude-sonnet-5", SAMPLE, provider="unknown")
        self.assertEqual(result.status, "estimated")
        self.assertIsNotNone(result.amount_usd)
        self.assertGreater(result.amount_usd, 0)

    def test_sonnet_promo_expires_automatically(self):
        with patch.object(
            pricing, "_UTC_NOW",
            return_value=datetime(2026, 8, 6, tzinfo=timezone.utc),
        ):
            promo = estimate_usage_cost("claude-sonnet-5", SAMPLE, provider="unknown")
        self.assertEqual(promo.amount_usd, Decimal("0.8417790"))
        self.assertEqual(
            promo.pricing_version,
            "anthropic-sonnet-5-intro-through-2026-08-31",
        )

        with patch.object(
            pricing, "_UTC_NOW",
            return_value=datetime(2026, 9, 1, tzinfo=timezone.utc),
        ):
            standard = estimate_usage_cost("claude-sonnet-5", SAMPLE, provider="unknown")
        self.assertEqual(standard.amount_usd, Decimal("1.2626685"))
        self.assertEqual(standard.pricing_version, "anthropic-pricing-2026-07")

    def test_live_sample_is_nonzero_and_labeled_estimated(self):
        with patch.object(
            pricing, "_UTC_NOW",
            return_value=datetime(2026, 8, 6, tzinfo=timezone.utc),
        ):
            text, payload, code = report.build_report(
                _report_db(),
                now=datetime(2026, 8, 6, 12, 0, tzinfo=timezone.utc),
                rate=Decimal("7.20"),
            )
        self.assertEqual(code, 0)
        self.assertIn("今日 ≈¥6.06", text)
        self.assertIn("不是Console实际账单", text)
        self.assertNotIn("今日 ¥0.00", text)
        self.assertEqual(payload["today"]["unknown_sessions"], 0)

    def test_unknown_price_fails_closed(self):
        text, payload, code = report.build_report(
            _report_db("future-mystery-model", "unknown"),
            now=datetime(2026, 8, 6, 12, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(code, 2)
        self.assertIn("金额不完整", text)
        self.assertIn("绝不按 ¥0 上报", text)
        self.assertTrue(payload["today"]["incomplete"])

    def test_provider_placeholder_self_heals(self):
        stub = types.ModuleType("agent.memory_manager")
        stub.sanitize_context = lambda value: value
        with patch.dict(sys.modules, {"agent.memory_manager": stub}):
            sys.modules.pop("hermes_state", None)
            from hermes_state import SessionDB

        with tempfile.TemporaryDirectory() as folder:
            db = SessionDB(Path(folder) / "state.db")
            try:
                db.create_session("s1", "wechat", model="claude-sonnet-5")
                db._conn.execute(
                    "UPDATE sessions SET billing_provider='unknown', "
                    "billing_mode='unknown' WHERE id='s1'"
                )
                db.update_token_counts(
                    "s1",
                    input_tokens=1,
                    model="claude-sonnet-5",
                    billing_provider="anthropic",
                    billing_mode="official_docs_snapshot",
                )
                row = db._conn.execute(
                    "SELECT billing_provider, billing_mode FROM sessions WHERE id='s1'"
                ).fetchone()
                self.assertEqual(row["billing_provider"], "anthropic")
                self.assertEqual(row["billing_mode"], "official_docs_snapshot")
            finally:
                db.close()

if __name__ == "__main__":
    unittest.main()
