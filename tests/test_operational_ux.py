import json
import tempfile
import threading
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from app import notifications, scheduler
from app.ui import render_mapping_page


SCHEDULE = {
    "enabled": True,
    "start_time": "02:30",
    "interval": 1,
    "unit": "days",
    "run_immediately_on_startup": False,
}


class OperationalUxTests(unittest.TestCase):
    def settings_context(self, directory):
        directory = Path(directory)
        return patch.multiple(
            scheduler,
            CONFIG_DIR=directory,
            SETTINGS_PATH=directory / "settings.json",
        )

    def test_legacy_schedule_migrates_anchor_and_preserves_last_run(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            last_run = {"finished_at": "2026-01-01T00:00:00+00:00"}
            path.write_text(json.dumps({
                "schedule": {
                    "enabled": False,
                    "interval": 6,
                    "unit": "hours",
                    "run_immediately_on_startup": False,
                },
                "last_scheduled_run": last_run,
                "future_setting": {"kept": True},
            }))
            with self.settings_context(directory):
                loaded = scheduler.load_settings()
                scheduler.save_schedule(SCHEDULE)
            saved = json.loads(path.read_text())
            self.assertEqual(loaded["schedule"]["start_time"], "02:30")
            self.assertEqual(saved["last_scheduled_run"], last_run)
            self.assertEqual(saved["future_setting"], {"kept": True})

    def test_next_run_is_anchored_not_save_relative(self):
        now = datetime.fromisoformat("2026-09-08T01:07:00-04:00")
        self.assertEqual(
            scheduler.next_run_time(SCHEDULE, now).isoformat(),
            "2026-09-08T02:30:00-04:00",
        )
        later = datetime.fromisoformat("2026-09-08T03:00:00-04:00")
        self.assertEqual(
            scheduler.next_run_time(SCHEDULE, later).isoformat(),
            "2026-09-09T02:30:00-04:00",
        )
        hourly = {**SCHEDULE, "interval": 6, "unit": "hours"}
        self.assertEqual(
            scheduler.next_run_time(
                hourly, datetime.fromisoformat("2026-09-08T09:00:00-04:00")
            ).isoformat(),
            "2026-09-08T14:30:00-04:00",
        )

    def test_next_run_uses_wonkserv_timezone_across_dst(self):
        zone = ZoneInfo("America/New_York")
        before_spring = datetime(2026, 3, 8, 1, 0, tzinfo=zone)
        spring_run = scheduler.next_run_time(SCHEDULE, before_spring)
        self.assertEqual(spring_run.isoformat(), "2026-03-08T03:30:00-04:00")
        before_fall = datetime(2026, 11, 1, 0, 45, tzinfo=zone)
        fall_run = scheduler.next_run_time(
            {**SCHEDULE, "start_time": "01:30"}, before_fall
        )
        self.assertEqual(fall_run.isoformat(), "2026-11-01T01:30:00-04:00")

    def test_schedule_validation_rejects_non_half_hour_and_minutes(self):
        with self.assertRaises(ValueError):
            scheduler.validate_schedule({**SCHEDULE, "start_time": "02:15"})
        with self.assertRaises(ValueError):
            scheduler.validate_schedule({**SCHEDULE, "unit": "minutes"})

    def test_runner_prevents_overlap(self):
        entered = threading.Event()
        release = threading.Event()

        def workflow():
            entered.set()
            release.wait(2)
            return {
                "success": True,
                "stage": "complete",
                "refresh": None,
                "build": None,
                "error": None,
            }

        runner = scheduler.ScheduleRunner(workflow, lambda result: None)
        thread = threading.Thread(target=runner.run)
        thread.start()
        self.assertTrue(entered.wait(1))
        overlap = runner.run()
        release.set()
        thread.join(2)
        self.assertFalse(overlap["started"])
        self.assertTrue(overlap["overlap_prevented"])

    def test_scheduled_build_failure_restores_previous_output(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            output = directory / "wonkepg.xml"
            output.write_bytes(b"last-known-good")
            channels = directory / "channels.json"
            channels.write_text('{"count": 0, "channels": []}')
            refreshed = {
                "sources": [
                    {"source": "baseline-default", "success": True}
                ]
            }
            with patch.object(
                scheduler, "FULL_OUTPUT_XMLTV", output
            ), patch.object(
                scheduler, "CHANNELS_PATH", channels
            ), patch.object(
                scheduler, "refresh_sources", return_value=refreshed
            ), patch.object(
                scheduler, "build_full_xmltv", side_effect=RuntimeError("secret")
            ):
                result = scheduler._scheduled_workflow()
            self.assertFalse(result["success"])
            self.assertEqual(result["stage"], "build")
            self.assertEqual(output.read_bytes(), b"last-known-good")
            self.assertNotIn("secret", result["error"])

    def test_notification_failure_dedup_and_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            sent = []
            with self.settings_context(directory):
                scheduler.save_schedule(SCHEDULE)
                notifications.save_error_handling({
                    "enabled": True,
                    "recipient": "ops@example.com",
                    "notify_source_refresh": True,
                    "notify_build": True,
                    "notify_stale_mappings": True,
                    "notify_recovery": True,
                })
                failure = {
                    "status": "one or more source refreshes failed",
                    "private_detail": "should-never-enter-email",
                    "affected": ["hive"],
                }
                first = notifications.process_event(
                    "source_refresh", True, failure,
                    sender=lambda *message: sent.append(message),
                )
                duplicate = notifications.process_event(
                    "source_refresh", True, failure,
                    sender=lambda *message: sent.append(message),
                )
                recovery = notifications.process_event(
                    "source_refresh", False,
                    {"status": "all sources refreshed successfully", "affected": []},
                    sender=lambda *message: sent.append(message),
                )
            self.assertTrue(first["sent"])
            self.assertFalse(duplicate["sent"])
            self.assertTrue(recovery["sent"])
            self.assertEqual(len(sent), 2)
            bodies = "\n".join(item[2] for item in sent)
            self.assertNotIn("should-never-enter-email", bodies)
            self.assertIn("Affected: hive", bodies)

    def test_disabled_notifications_do_not_send(self):
        with tempfile.TemporaryDirectory() as directory:
            sent = []
            with self.settings_context(directory):
                scheduler.save_schedule(SCHEDULE)
                notifications.save_error_handling({
                    "enabled": False,
                    "recipient": "ops@example.com",
                    "notify_source_refresh": True,
                    "notify_build": True,
                    "notify_stale_mappings": True,
                    "notify_recovery": True,
                })
                result = notifications.process_event(
                    "build", True, {"status": "failed", "affected": []},
                    sender=lambda *message: sent.append(message),
                )
            self.assertFalse(result["sent"])
            self.assertEqual(sent, [])

    def test_error_settings_persist_and_invalid_email_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.settings_context(directory):
                saved = notifications.save_error_handling({
                    "enabled": True,
                    "recipient": "ops@example.com",
                    "notify_source_refresh": True,
                    "notify_build": False,
                    "notify_stale_mappings": True,
                    "notify_recovery": False,
                })
                loaded = notifications.error_handling_status()
                with self.assertRaises(ValueError):
                    notifications.save_error_handling({
                        **saved, "recipient": "not-an-email"
                    })
            self.assertEqual(loaded["recipient"], "ops@example.com")
            self.assertFalse(loaded["notify_build"])

    def test_test_email_reports_missing_smtp_without_state_change(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.settings_context(directory), patch.dict(
                notifications.os.environ, {}, clear=True
            ):
                scheduler.save_schedule(SCHEDULE)
                notifications.save_error_handling({
                    "enabled": False,
                    "recipient": "ops@example.com",
                    "notify_source_refresh": True,
                    "notify_build": True,
                    "notify_stale_mappings": True,
                    "notify_recovery": True,
                })
                before = scheduler.load_settings()
                with self.assertRaises(ValueError):
                    notifications.send_test_email()
                after = scheduler.load_settings()
            self.assertEqual(before, after)

    def test_ui_consolidates_schedule_and_error_handling_in_settings(self):
        html = render_mapping_page(
            {"count": 0, "channels": []}, [], [],
            {"inactive": [], "warnings": {}},
        )
        self.assertEqual(html.count('id="schedule-start-time"'), 1)
        self.assertEqual(html.count('<option value="02:30">02:30</option>'), 1)
        self.assertEqual(html.count(':00</option>'), 24)
        self.assertEqual(html.count(':30</option>'), 24)
        self.assertIn('value="23:30"', html)
        self.assertIn('id="settings-dialog"', html)
        self.assertIn('id="settings"', html)
        self.assertNotIn('id="error-dialog"', html)
        self.assertNotIn('id="error-handling"', html)
        self.assertIn("Send Test Email", html)
        self.assertEqual(html.count('id="build"'), 1)


if __name__ == "__main__":
    unittest.main()
