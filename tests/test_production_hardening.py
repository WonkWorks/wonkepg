import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from app import full_build, main, notifications, scheduler, source_manager
from app.ui import render_mapping_page


def channel_row(
    number,
    baseline_id=None,
    share_id=None,
    talk_id=None,
):
    return {
        "channel_id": f"source.{number}",
        "number": str(number),
        "name": f"CHANNEL {number}",
        "pretty_name": f"Pretty {number}",
        "group": "Test",
        "logo": f"http://192.0.2.10:34500/logos/{int(number):05d}-logo.png",
        "baseline": {
            "source": "baseline-default",
            "channel_id": baseline_id,
        },
        "enrichment_1": (
            {"source": "epgshare", "channel_id": share_id}
            if share_id else None
        ),
        "enrichment_2": (
            {"source": "epgtalk", "channel_id": talk_id}
            if talk_id else None
        ),
    }


def write_xmltv(path, channel_ids):
    channels = "".join(
        f'<channel id="{channel_id}"><display-name>{channel_id}</display-name></channel>'
        for channel_id in channel_ids
    )
    programmes = "".join(
        (
            f'<programme channel="{channel_id}" '
            'start="20260908000000 +0000" stop="20260908010000 +0000">'
            "<title>Shared Show</title></programme>"
        )
        for channel_id in channel_ids
    )
    path.write_text(f"<?xml version='1.0'?><tv>{channels}{programmes}</tv>")


class ProductionHardeningTests(unittest.TestCase):
    def settings_context(self, directory):
        directory = Path(directory)
        return patch.multiple(
            scheduler,
            CONFIG_DIR=directory,
            SETTINGS_PATH=directory / "settings.json",
        )

    def test_stale_sources_degrade_per_channel_without_mutating_matrix(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            hive = directory / "hive.xml"
            share = directory / "share.xml"
            talk = directory / "talk.xml"
            local = directory / "local.xml"
            output = directory / "xmltv.xml"
            write_xmltv(hive, ["hive.1", "hive.2", "hive.3", "hive.5"])
            write_xmltv(share, ["share.valid"])
            write_xmltv(talk, ["talk.valid"])
            write_xmltv(local, [])
            matrix = {
                "count": 5,
                "channels": [
                    channel_row("1", "hive.1", "share.stale", "talk.valid"),
                    channel_row("2", "hive.2", "share.valid", "talk.stale"),
                    channel_row("3", "hive.3", "share.stale", "talk.stale"),
                    channel_row("4", None, "share.stale", "talk.stale"),
                    channel_row("5", "hive.5", None, None),
                ],
            }
            before = deepcopy(matrix)
            with patch.object(full_build, "HIVE_XMLTV", hive), patch.object(
                full_build, "EPGSHARE_XMLTV", share
            ), patch.object(full_build, "EPGTALK_XMLTV", talk), patch.object(
                full_build, "EPGTALK_LOCAL_XMLTV", local
            ), patch.object(full_build, "FULL_OUTPUT_XMLTV", output):
                result = full_build.build_full_xmltv(matrix, {
                    "baseline-default": {
                        "path": hive,
                        "available": True,
                        "channel_ids": {"hive.1", "hive.2", "hive.3", "hive.5"},
                        "channels": [],
                    }
                })

            self.assertEqual(matrix, before)
            self.assertEqual(result["stale_epgshare_count"], 3)
            self.assertEqual(result["stale_epgtalk_count"], 3)
            self.assertEqual(result["channels_degraded_to_baseline_only"], 1)
            self.assertEqual(result["channels_built_with_programmes"], 4)
            self.assertEqual(result["channels_with_no_schedule"], 1)
            self.assertEqual(result["total_collisions"], 0)
            self.assertTrue(result["parse_validation"]["valid"])
            ET.parse(output)

            diagnostics = {
                item["number"]: item
                for item in result["per_channel_diagnostics"]
            }
            self.assertEqual(
                [item["source"] for item in diagnostics["1"]["stale_mappings"]],
                ["epgshare"],
            )
            self.assertEqual(
                [item["source"] for item in diagnostics["2"]["stale_mappings"]],
                ["epgtalk"],
            )
            self.assertEqual(
                {item["source"] for item in diagnostics["3"]["stale_mappings"]},
                {"epgshare", "epgtalk"},
            )
            self.assertEqual(diagnostics["5"]["stale_mappings"], [])

    def test_exact_id_validation_has_no_fuzzy_replacement(self):
        matrix = {
            "channels": [
                channel_row("1", "hive.1", "Share.Channel", "Talk.Channel")
            ]
        }
        report = full_build.inspect_stale_mappings(
            matrix,
            {"share.channel"},
            {"talk.channel"},
        )
        self.assertEqual(report["stale_epgshare_count"], 1)
        self.assertEqual(report["stale_epgtalk_count"], 1)

    def test_cached_source_refresh_failure_does_not_create_false_stale(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            cache = directory / "share.xml"
            write_xmltv(cache, ["share.saved"])
            spec = source_manager.SourceSpec(
                "epgshare", "share.xml", url="https://example.invalid"
            )
            state = {"sources": {"epgshare": {
                "source": "epgshare",
                "success": True,
                "last_successful_refresh": "2026-09-08T00:00:00+00:00",
                "channel_count": 1,
                "programme_count": 1,
            }}}
            with patch.object(source_manager, "DATA_DIR", directory), patch.object(
                source_manager, "_download", side_effect=OSError("offline")
            ):
                refresh = source_manager._refresh_one(spec, state)
                report = full_build.inspect_stale_mappings(
                    {"channels": [channel_row("1", share_id="share.saved")]},
                    full_build._channel_ids(cache),
                    set(),
                )
            self.assertFalse(refresh["success"])
            self.assertTrue(cache.exists())
            self.assertEqual(report["stale_epgshare_count"], 0)

    def test_stale_mapping_remains_persisted_when_other_edits_are_saved(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            config = directory / "channels.json"
            saved_row = channel_row("1", "hive.1", "share.stale", None)
            config.write_text(json.dumps({"count": 1, "channels": [saved_row]}))
            payload = {"mappings": [{
                "number": "1",
                "pretty_name": "Updated Pretty Name",
                "enrichment_1": "share.stale",
                "enrichment_2": None,
            }]}
            with patch.object(main, "CONFIG_DIR", directory), patch.object(
                main, "CHANNELS_JSON", config
            ), patch.object(
                main, "parse_enrichment_xmltv", return_value=[]
            ), patch.object(
                main, "combine_epgtalk_catalog", return_value={"channels": []}
            ):
                result = main.save_channel_changes(payload)
            persisted = json.loads(config.read_text())["channels"][0]
            self.assertEqual(result["rows_changed"], 1)
            self.assertEqual(
                persisted["enrichment_1"]["channel_id"], "share.stale"
            )
            self.assertEqual(
                persisted["pretty_name"], "Updated Pretty Name"
            )

    def test_stale_notifications_deduplicate_and_recover_per_source(self):
        successful = {
            "output_written": True,
            "parse_validation": {"valid": True},
            "stale_epgshare_mappings": [
                {"number": "1", "channel_id": "share.stale"}
            ],
            "stale_epgtalk_mappings": [],
        }
        with tempfile.TemporaryDirectory() as directory:
            sent = []
            with self.settings_context(directory):
                scheduler.save_schedule({
                    "enabled": False,
                    "start_time": "02:30",
                    "interval": 1,
                    "unit": "days",
                    "run_immediately_on_startup": False,
                })
                notifications.save_error_handling({
                    "enabled": True,
                    "recipient": "ops@example.com",
                    "notify_source_refresh": True,
                    "notify_build": True,
                    "notify_stale_mappings": True,
                    "notify_recovery": True,
                })
                first = notifications.process_build_result(
                    successful, sender=lambda *args: sent.append(args)
                )
                duplicate = notifications.process_build_result(
                    successful, sender=lambda *args: sent.append(args)
                )
                recovered = notifications.process_build_result(
                    {
                        **successful,
                        "stale_epgshare_mappings": [],
                    },
                    sender=lambda *args: sent.append(args),
                )
                states = scheduler.load_settings()["notification_state"]

            self.assertTrue(first["stale_epgshare"]["sent"])
            self.assertFalse(duplicate["stale_epgshare"]["sent"])
            self.assertTrue(recovered["stale_epgshare"]["sent"])
            self.assertEqual(len(sent), 2)
            self.assertFalse(states["stale_epgshare"]["active"])
            self.assertFalse(states["stale_epgtalk"]["active"])
            bodies = "\n".join(item[2] for item in sent)
            self.assertNotIn("https://", bodies)
            self.assertIn("channel 1: share.stale", bodies)

    def test_ui_preserves_missing_id_and_warning_clears_on_recovery(self):
        row = channel_row("1", "hive.1", "share.saved", None)
        stale_html = render_mapping_page(
            {"channels": [row]},
            [],
            [],
            {"inactive": [], "warnings": {}},
            {
                "stale_epgshare_count": 1,
                "stale_epgtalk_count": 0,
                "stale_epgshare_mappings": [
                    {"number": "1", "channel_id": "share.saved"}
                ],
                "stale_epgtalk_mappings": [],
            },
        )
        recovered_html = render_mapping_page(
            {"channels": [row]},
            [{
                "channel_id": "share.saved",
                "display_name": "Recovered Share",
                "display_names": ["Recovered Share"],
            }],
            [],
            {"inactive": [], "warnings": {}},
            {
                "stale_epgshare_count": 0,
                "stale_epgtalk_count": 0,
                "stale_epgshare_mappings": [],
                "stale_epgtalk_mappings": [],
            },
        )
        self.assertIn('data-selected-id="share.saved"', stale_html)
        self.assertIn(
            "⚠ Selected Enrichment 1 channel not present in current source",
            stale_html,
        )
        self.assertIn('class="stale-selection">', stale_html)
        self.assertIn('class="stale-selection" hidden>', recovered_html)
        self.assertIn("Stale Enrichment 1", stale_html)
        self.assertIn("Last XMLTV build", stale_html)

    def test_ui_only_shows_warnings_for_nonblank_exact_missing_ids(self):
        bravo = channel_row(
            "212", "bravo.us", "Bravo.HD.us2",
            "I237.58625.schedulesdirect.org",
        )
        stale = channel_row(
            "214", "stale.us", "share.missing", "talk.missing"
        )
        inactive = channel_row("2000", "gacfamily.us", None, None)
        html = render_mapping_page(
            {"channels": [bravo, channel_row("213"), stale]},
            [{"channel_id": "Bravo.HD.us2", "display_name": "Bravo HD"}],
            [{
                "channel_id": "I237.58625.schedulesdirect.org",
                "display_name": "Bravo HD",
            }],
            {"inactive": [inactive], "warnings": {}},
            source_settings={
                "foundation": {"configured_m3u": "/threadfin/threadfin.m3u"},
                "baseline": {"url": "", "provider_name": "Hive"},
                "enrichment_1": {
                    "url": "", "provider_name": "Artwork Guide",
                },
                "enrichment_2": {
                    "primary_url": "", "secondary_url": "",
                    "provider_name": "Long Range Guide",
                },
            },
        )

        self.assertEqual(html.count("class=\"stale-selection\">"), 2)
        self.assertEqual(
            html.count("class=\"stale-selection\" hidden>"), 10
        )
        self.assertIn(
            "⚠ Selected Artwork Guide channel not present in current source",
            html,
        )
        self.assertIn(
            "⚠ Selected Long Range Guide channel not present in current source",
            html,
        )
        self.assertIn("small:not([hidden])", html)
        self.assertIn("root.querySelector(\".stale-selection\").hidden=!isStale", html)

    def test_xmltv_endpoint_serves_last_known_good_file(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "xmltv.xml"
            output.write_text("<tv></tv>")
            with patch.object(main, "FULL_OUTPUT_XMLTV", output):
                response = main.serve_built_xmltv()
            self.assertEqual(Path(response.path), output)
            self.assertEqual(response.media_type, "application/xml")

    def test_compose_has_restart_and_lightweight_healthcheck(self):
        compose = Path("compose.yaml").read_text()
        self.assertIn("restart: unless-stopped", compose)
        self.assertIn("http://127.0.0.1:8000/status", compose)
        self.assertIn("import urllib.request", compose)
        self.assertNotIn("private.example.com", compose)

    def test_backup_documentation_marks_secure_path_and_rollback(self):
        documentation = Path("BACKUP.md").read_text()
        self.assertIn("config/channels.json", documentation)
        self.assertIn("secure-assets/", documentation)
        self.assertIn("output/xmltv.xml", documentation)
        self.assertNotIn("/opt/", documentation)


if __name__ == "__main__":
    unittest.main()
