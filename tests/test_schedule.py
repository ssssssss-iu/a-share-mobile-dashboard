import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from dashboard.schedule import (
    BACKUP_UPDATE_TIMES,
    PRIMARY_UPDATE_TIMES,
    intraday_slot,
    should_write_close_history,
    snapshot_is_fresh,
)


TZ = ZoneInfo("Asia/Shanghai")


class ScheduleTests(unittest.TestCase):
    def test_primary_schedule_has_expected_18_slots(self):
        self.assertEqual(len(PRIMARY_UPDATE_TIMES), 18)
        self.assertEqual(PRIMARY_UPDATE_TIMES[0], "09:25")
        self.assertEqual(PRIMARY_UPDATE_TIMES[-1], "15:05")

    def test_every_backup_is_five_minutes_after_primary(self):
        def minute(value):
            hour, minute_value = map(int, value.split(":"))
            return hour * 60 + minute_value

        self.assertEqual([minute(value) for value in BACKUP_UPDATE_TIMES], [minute(value) + 5 for value in PRIMARY_UPDATE_TIMES])

    def test_recent_successful_snapshot_skips_backup(self):
        now = datetime(2026, 9, 17, 10, 30, tzinfo=TZ)
        fresh = {"status": "SUCCESS", "generated_at": "2026-09-17T10:25:30+08:00"}
        stale = {"status": "SUCCESS", "generated_at": "2026-09-17T10:10:00+08:00"}
        self.assertTrue(snapshot_is_fresh(fresh, now))
        self.assertFalse(snapshot_is_fresh(stale, now))
        self.assertFalse(snapshot_is_fresh({"status": "FAILED", "generated_at": fresh["generated_at"]}, now))

    def test_only_close_window_writes_history(self):
        self.assertTrue(should_write_close_history(datetime(2026, 9, 17, 15, 5, tzinfo=TZ)))
        self.assertTrue(should_write_close_history(datetime(2026, 9, 17, 15, 10, tzinfo=TZ)))
        self.assertFalse(should_write_close_history(datetime(2026, 9, 17, 14, 50, tzinfo=TZ)))
        self.assertFalse(should_write_close_history(datetime(2026, 9, 17, 15, 20, tzinfo=TZ)))

    def test_cloudflare_timestamp_resolves_primary_slot(self):
        planned = datetime(2026, 9, 17, 9, 25, tzinfo=TZ)
        now = datetime(2026, 9, 17, 9, 28, tzinfo=TZ)
        slot = intraday_slot(now, "cloudflare", str(int(planned.timestamp() * 1000)))
        self.assertEqual(slot["key"], "scheduled-0925")
        self.assertEqual(slot["source"], "primary")

    def test_backup_maps_to_same_primary_slot(self):
        slot = intraday_slot(datetime(2026, 9, 17, 9, 31, tzinfo=TZ), "schedule")
        self.assertEqual(slot["key"], "scheduled-0925")
        self.assertEqual(slot["source"], "backup")

    def test_manual_run_never_impersonates_scheduled_slot(self):
        slot = intraday_slot(datetime(2026, 9, 17, 15, 5, tzinfo=TZ), "manual")
        self.assertEqual(slot["kind"], "manual")
        self.assertTrue(slot["key"].startswith("manual-"))


if __name__ == "__main__":
    unittest.main()
