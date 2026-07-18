"""Unit tests for demo_guard — stdlib only, no Flask or engines required.

Run directly (python tests/test_demo_guard.py) or via pytest.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from demo_guard import DemoGuard, client_ip  # noqa: E402

T0 = 1_800_000_000.0  # fixed base timestamp


class GuardTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._env = {}

    def tearDown(self):
        for key, value in self._env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self._tmp.cleanup()

    def set_env(self, **kwargs):
        for key, value in kwargs.items():
            self._env.setdefault(key, os.environ.get(key))
            os.environ[key] = value

    def make_guard(self, **env) -> DemoGuard:
        self.set_env(**env)
        return DemoGuard(os.path.join(self._tmp.name, "guard.sqlite"))


class TestDisabledByDefault(GuardTestCase):
    def test_everything_allowed_when_off(self):
        guard = self.make_guard(DEMO_MODE="")
        for _ in range(50):
            self.assertTrue(guard.check_and_count("1.2.3.4", "pdf", "docx").allowed)
        self.assertIsNone(guard.public_info())


class TestRateLimit(GuardTestCase):
    def test_per_ip_hourly_cap(self):
        guard = self.make_guard(DEMO_MODE="1", DEMO_RATE_PER_HOUR="3",
                                DEMO_DAILY_BUDGET="1000")
        for i in range(3):
            self.assertTrue(
                guard.check_and_count("1.2.3.4", "md", "pdf", now=T0 + i).allowed
            )
        verdict = guard.check_and_count("1.2.3.4", "md", "pdf", now=T0 + 3)
        self.assertFalse(verdict.allowed)
        self.assertEqual(verdict.status, 429)
        self.assertIn("Self-host", verdict.error)

    def test_other_ips_unaffected(self):
        guard = self.make_guard(DEMO_MODE="1", DEMO_RATE_PER_HOUR="1",
                                DEMO_DAILY_BUDGET="1000")
        self.assertTrue(guard.check_and_count("1.1.1.1", "md", "pdf", now=T0).allowed)
        self.assertFalse(guard.check_and_count("1.1.1.1", "md", "pdf", now=T0 + 1).allowed)
        self.assertTrue(guard.check_and_count("2.2.2.2", "md", "pdf", now=T0 + 2).allowed)

    def test_window_rolls_over(self):
        guard = self.make_guard(DEMO_MODE="1", DEMO_RATE_PER_HOUR="1",
                                DEMO_DAILY_BUDGET="1000")
        self.assertTrue(guard.check_and_count("1.2.3.4", "md", "pdf", now=T0).allowed)
        self.assertFalse(guard.check_and_count("1.2.3.4", "md", "pdf", now=T0 + 10).allowed)
        self.assertTrue(
            guard.check_and_count("1.2.3.4", "md", "pdf", now=T0 + 3601).allowed
        )


class TestDailyBudget(GuardTestCase):
    def test_global_budget_across_ips(self):
        guard = self.make_guard(DEMO_MODE="1", DEMO_RATE_PER_HOUR="100",
                                DEMO_DAILY_BUDGET="2")
        self.assertTrue(guard.check_and_count("1.1.1.1", "md", "pdf", now=T0).allowed)
        self.assertTrue(guard.check_and_count("2.2.2.2", "md", "pdf", now=T0 + 1).allowed)
        verdict = guard.check_and_count("3.3.3.3", "md", "pdf", now=T0 + 2)
        self.assertFalse(verdict.allowed)
        self.assertEqual(verdict.status, 429)
        self.assertIn("budget", verdict.error)

    def test_budget_resets_next_utc_day(self):
        guard = self.make_guard(DEMO_MODE="1", DEMO_RATE_PER_HOUR="100",
                                DEMO_DAILY_BUDGET="1")
        self.assertTrue(guard.check_and_count("1.1.1.1", "md", "pdf", now=T0).allowed)
        self.assertFalse(guard.check_and_count("2.2.2.2", "md", "pdf", now=T0 + 1).allowed)
        next_day = T0 + 86_400
        self.assertTrue(guard.check_and_count("2.2.2.2", "md", "pdf", now=next_day).allowed)


class TestBlockedFormats(GuardTestCase):
    def test_blocked_extension_refused(self):
        guard = self.make_guard(DEMO_MODE="1", DEMO_BLOCK="wav, flac")
        verdict = guard.check_and_count("1.2.3.4", "wav", "mp3", now=T0)
        self.assertFalse(verdict.allowed)
        self.assertEqual(verdict.status, 403)
        verdict = guard.check_and_count("1.2.3.4", "mp3", "flac", now=T0)
        self.assertFalse(verdict.allowed)
        self.assertTrue(guard.check_and_count("1.2.3.4", "md", "pdf", now=T0).allowed)


class TestPersistence(GuardTestCase):
    def test_counters_survive_reinstantiation(self):
        path = os.path.join(self._tmp.name, "guard.sqlite")
        self.set_env(DEMO_MODE="1", DEMO_RATE_PER_HOUR="1", DEMO_DAILY_BUDGET="1000")
        first = DemoGuard(path)
        self.assertTrue(first.check_and_count("1.2.3.4", "md", "pdf", now=T0).allowed)
        second = DemoGuard(path)  # simulates another worker / a restart
        self.assertFalse(second.check_and_count("1.2.3.4", "md", "pdf", now=T0 + 1).allowed)


class TestClientIp(unittest.TestCase):
    def test_forwarded_header_wins(self):
        self.assertEqual(
            client_ip({"X-Forwarded-For": "9.9.9.9, 10.0.0.1"}, "172.17.0.1"),
            "9.9.9.9",
        )

    def test_falls_back_to_peer(self):
        self.assertEqual(client_ip({}, "172.17.0.1"), "172.17.0.1")
        self.assertEqual(client_ip({}, None), "unknown")


if __name__ == "__main__":
    unittest.main(verbosity=2)
