import unittest

from dashboard.historical_replay import assess_replay_readiness


class HistoricalReplayReadinessTests(unittest.TestCase):
    def test_fails_closed_when_intersection_is_below_gate(self):
        result = assess_replay_readiness(100, 92, 88, 84, 80, 0.85)
        self.assertEqual(result["status"], "DATA_INSUFFICIENT")
        self.assertIn("POINT_IN_TIME_INPUT_COVERAGE_BELOW_GATE", result["blockers"])

    def test_ready_requires_exit_observation(self):
        result = assess_replay_readiness(100, 95, 92, 90, 0, 0.85)
        self.assertEqual(result["status"], "DATA_INSUFFICIENT")
        self.assertIn("NEXT_SESSION_EXIT_PRICE_MISSING", result["blockers"])

    def test_ready_when_both_gates_pass(self):
        result = assess_replay_readiness(100, 95, 92, 90, 88, 0.85)
        self.assertEqual(result["status"], "READY")
        self.assertEqual(result["blockers"], [])


if __name__ == "__main__":
    unittest.main()
