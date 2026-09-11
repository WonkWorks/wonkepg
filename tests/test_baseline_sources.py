import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from app import (
    baseline_sources,
    full_build,
    main,
    notifications,
    scheduler,
    source_manager,
    source_settings,
)
from app.channel_merge import merge_single_channel
from app.ui import render_mapping_page


def write_xmltv(path, channel_ids):
    channels = "".join(
        f'<channel id="{item}"><display-name>{item}</display-name></channel>'
        for item in channel_ids
    )
    programmes = "".join(
        f'<programme channel="{item}" start="20260909000000 +0000" '
        f'stop="20260909010000 +0000"><title>{item} Show</title></programme>'
        for item in channel_ids
    )
    path.write_text(f"<tv>{channels}{programmes}</tv>")


def row(number, source="baseline-default", channel_id="base.one"):
    return {
        "channel_id": f"foundation.{number}",
        "number": str(number),
        "name": f"CHANNEL {number}",
        "pretty_name": f"Pretty {number}",
        "group": "Group",
        "logo": "https://assets.example/logo.png",
        "baseline": (
            {"source": source, "channel_id": channel_id}
            if channel_id else {"source": source, "channel_id": None}
        ),
        "enrichment_1": {"source": "epgshare", "channel_id": "share.one"},
        "enrichment_2": {"source": "epgtalk", "channel_id": "talk.one"},
    }


def catalogs(path, channel_ids, source_id="baseline-default", available=True):
    return {
        source_id: {
            "source_id": source_id,
            "provider_name": "Provider",
            "default": source_id == "baseline-default",
            "path": path,
            "available": available,
            "channels": [
                {
                    "channel_id": item,
                    "display_name": item,
                    "display_names": [item],
                }
                for item in channel_ids
            ],
            "channel_ids": set(channel_ids),
        }
    }


class BaselineSourceTests(unittest.TestCase):
    def test_channel_migration_preserves_user_fields_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "channels.json"
            original = row("1", "hive", "legacy.one")
            blank = row("2", "hive", None)
            matrix = {"count": 2, "channels": [original, blank]}
            path.write_text(json.dumps(matrix))
            with patch.object(main, "CONFIG_DIR", Path(directory)), patch.object(
                main, "CHANNELS_JSON", path
            ):
                migrated = main.load_channels_matrix()
                first_bytes = path.read_bytes()
                loaded_again = main.load_channels_matrix()
            self.assertEqual(migrated, loaded_again)
            self.assertEqual(path.read_bytes(), first_bytes)
            self.assertEqual(migrated["schema_version"], 2)
            self.assertEqual(
                migrated["channels"][0]["baseline"],
                {"source": "baseline-default", "channel_id": "legacy.one"},
            )
            self.assertIsNone(migrated["channels"][1]["baseline"]["channel_id"])
            for field in (
                "channel_id", "number", "name", "pretty_name", "group", "logo",
                "enrichment_1", "enrichment_2",
            ):
                self.assertEqual(migrated["channels"][0][field], original[field])
            self.assertTrue((Path(directory) / "channels.json.pre-0.6.0.bak").is_file())

    def test_registry_migration_ids_and_safe_cache_names(self):
        legacy = {
            "foundation": {"configured_m3u": "/foundation/guide.m3u"},
            "baseline": {"url": "https://one.test", "provider_name": "One"},
            "enrichment_1": {"url": "", "provider_name": "Art"},
            "enrichment_2": {
                "primary_url": "", "secondary_url": "", "provider_name": "Long",
            },
        }
        migrated = source_settings.validate_source_settings(legacy)
        self.assertEqual(migrated["baseline"]["default_source"], "baseline-default")
        self.assertEqual(
            set(migrated["schedule_sources"]),
            {"baseline-default"},
        )
        renamed = deepcopy(migrated)
        renamed["schedule_sources"]["baseline-default"]["provider_name"] = "Custom"
        self.assertEqual(
            source_settings.schedule_cache_filename("baseline-default"),
            "schedule-baseline-default.xml",
        )
        self.assertEqual(
            source_settings.validate_source_settings(renamed)["schedule_sources"].keys(),
            migrated["schedule_sources"].keys(),
        )
        for unsafe in ("../secret", "/absolute", "Upper_Case", "two..dots"):
            with self.subTest(unsafe=unsafe), self.assertRaises(ValueError):
                source_settings.schedule_cache_filename(unsafe)

    def test_schedule_refresh_failure_keeps_lkg_mapping_valid(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            cache = directory / "schedule-ca-source.xml"
            write_xmltv(cache, ["ca.channel"])
            spec = source_manager.SourceSpec(
                "ca-source", cache.name, url="https://offline.invalid"
            )
            state = {"sources": {"ca-source": {
                "source": "ca-source", "success": True,
                "last_successful_refresh": "2026-09-09T00:00:00+00:00",
                "channel_count": 1, "programme_count": 1,
            }}}
            settings = {
                "baseline": {"default_source": "ca-source"},
                "schedule_sources": {
                    "ca-source": {"provider_name": "CA", "url": "hidden"}
                },
            }
            with patch.object(source_manager, "DATA_DIR", directory), patch.object(
                source_manager, "_download", side_effect=OSError("offline")
            ):
                refresh = source_manager._refresh_one(spec, state)
            with patch.object(baseline_sources, "DATA_DIR", directory):
                loaded = baseline_sources.load_schedule_catalogs(settings)
            report = baseline_sources.inspect_baseline_mappings(
                {"channels": [row("1", "ca-source", "ca.channel")]}, loaded
            )
            self.assertFalse(refresh["success"])
            self.assertTrue(loaded["ca-source"]["available"])
            self.assertEqual(report["stale_baseline_count"], 0)

    def test_exact_baseline_resolution_and_recovery_states(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "valid.xml"
            write_xmltv(path, ["present"])
            state = catalogs(path, ["present"])
            unavailable_path = Path(directory) / "missing.xml"
            state.update(catalogs(
                unavailable_path, [], "offline-source", available=False
            ))
            matrix = {"channels": [
                row("1", channel_id="present"),
                row("2", channel_id="missing"),
                row("3", source="removed-source", channel_id="saved"),
                row("4", source="offline-source", channel_id="saved"),
                row("5", channel_id=None),
            ]}
            report = baseline_sources.inspect_baseline_mappings(matrix, state)
            self.assertEqual(report["stale_baseline_count"], 3)
            self.assertEqual(
                {item["reason"] for item in report["stale_baseline_mappings"]},
                {"missing_channel", "missing_source", "source_unavailable"},
            )
            state["baseline-default"]["channel_ids"].add("missing")
            recovered = baseline_sources.inspect_baseline_mappings(matrix, state)
            self.assertNotIn(
                "2", {item["number"] for item in recovered["stale_baseline_mappings"]}
            )

    def test_merge_accepts_non_hive_canonical_baseline(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ca2.xml"
            write_xmltv(path, ["Bravo.Canada.HD.ca2"])
            channel = row("212", "epgshare-ca2", "Bravo.Canada.HD.ca2")
            channel["enrichment_1"] = None
            channel["enrichment_2"] = None
            result = merge_single_channel(
                channel, path, "Bravo.Canada.HD.ca2"
            )
            self.assertEqual(len(result.programmes), 1)
            self.assertEqual(result.programmes[0].get("start"), "20260909000000 +0000")
            self.assertEqual(result.programmes[0].get("channel"), "wonk.212")

    def test_stale_explicit_baseline_never_promotes_enrichment(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            base = directory / "base.xml"
            share = directory / "share.xml"
            talk = directory / "talk.xml"
            local = directory / "local.xml"
            output = directory / "output.xml"
            write_xmltv(base, ["different"])
            write_xmltv(share, [])
            write_xmltv(talk, ["talk.one"])
            write_xmltv(local, [])
            channel = row("212", channel_id="missing.baseline")
            channel["enrichment_1"] = None
            matrix = {"channels": [channel]}
            with patch.object(full_build, "EPGSHARE_XMLTV", share), patch.object(
                full_build, "EPGTALK_XMLTV", talk
            ), patch.object(full_build, "EPGTALK_LOCAL_XMLTV", local), patch.object(
                full_build, "FULL_OUTPUT_XMLTV", output
            ):
                result = full_build.build_full_xmltv(
                    matrix, catalogs(base, ["different"])
                )
            self.assertEqual(result["stale_baseline_count"], 1)
            self.assertEqual(result["channels_built_with_programmes"], 0)
            self.assertEqual(result["channels_with_no_schedule"], 1)
            self.assertEqual(result["total_output_programmes"], 0)
            self.assertIn(
                "enrichment was not promoted",
                result["per_channel_diagnostics"][0]["warnings"][0],
            )

    def test_ui_and_lazy_api_keep_source_identity_and_credentials_private(self):
        channel = row("212", "epgshare-ca2", "Bravo.Canada.HD.ca2")
        channel["enrichment_1"] = None
        channel["enrichment_2"] = None
        source_catalogs = catalogs(
            Path("/private/cache.xml"),
            ["Bravo.Canada.HD.ca2"],
            "epgshare-ca2",
        )
        source_catalogs["epgshare-ca2"]["provider_name"] = "EPGShare CA2"
        html = render_mapping_page(
            {"channels": [channel]}, [], [],
            {"inactive": [], "warnings": {}},
            {"stale_baseline_count": 0, "stale_baseline_mappings": []},
            schedule_sources=[{
                "source_id": "epgshare-ca2", "provider_name": "EPGShare CA2",
                "default": False, "available": True, "channel_count": 1,
            }],
            schedule_catalogs=source_catalogs,
        )
        self.assertIn("EPGShare CA2 · Bravo.Canada.HD.ca2", html)
        self.assertIn('data-selected-source="epgshare-ca2"', html)
        self.assertIn("/sources/schedule/${encodeURIComponent(source.source_id)}", html)
        self.assertNotIn("/private/cache.xml", html)

        with patch.object(main, "schedule_source_registry", return_value={
            "epgshare-ca2": {
                "provider_name": "EPGShare CA2", "path": Path("/cache.xml")
            }
        }), patch.object(main, "read_schedule_catalog", return_value=[{
            "channel_id": "Bravo.Canada.HD.ca2",
            "display_name": "Bravo Canada HD",
            "display_names": ["Bravo Canada HD"],
        }]):
            response = main.get_schedule_source_channels("epgshare-ca2")
        serialized = json.dumps(response)
        self.assertNotIn("url", serialized.casefold())
        self.assertNotIn("credential", serialized.casefold())

    def test_stale_baseline_renders_dynamic_warning(self):
        channel = row("212", "epgshare-ca2", "missing.ca2")
        html = render_mapping_page(
            {"channels": [channel]}, [], [],
            {"inactive": [], "warnings": {}},
            {
                "stale_baseline_count": 1,
                "stale_baseline_mappings": [{
                    "number": "212", "source": "epgshare-ca2",
                    "channel_id": "missing.ca2", "reason": "missing_channel",
                }],
            },
            schedule_sources=[{
                "source_id": "epgshare-ca2", "provider_name": "Canada Guide",
                "default": False, "available": True, "channel_count": 1,
            }],
        )
        baseline = html.split('data-source="baseline"', 1)[1].split("</div></td>", 1)[0]
        self.assertIn('class="stale-selection">', baseline)
        self.assertIn("Selected Canada Guide channel not present", baseline)

    def test_baseline_notification_deduplicates_and_recovers(self):
        stale = {
            "stale_baseline_mappings": [{
                "number": "212", "source": "epgshare-ca2",
                "channel_id": "Bravo.Canada.HD.ca2",
                "reason": "missing_channel",
            }],
            "stale_epgshare_mappings": [],
            "stale_epgtalk_mappings": [],
        }
        with tempfile.TemporaryDirectory() as directory:
            sent = []
            with patch.object(scheduler, "CONFIG_DIR", Path(directory)), patch.object(
                scheduler, "SETTINGS_PATH", Path(directory) / "settings.json"
            ):
                notifications.save_error_handling({
                    "enabled": True, "recipient": "ops@example.com",
                    "notify_source_refresh": True, "notify_build": True,
                    "notify_stale_mappings": True, "notify_recovery": True,
                })
                first = notifications.process_stale_result(
                    stale, sender=lambda *args: sent.append(args)
                )
                duplicate = notifications.process_stale_result(
                    stale, sender=lambda *args: sent.append(args)
                )
                recovered = notifications.process_stale_result(
                    {**stale, "stale_baseline_mappings": []},
                    sender=lambda *args: sent.append(args),
                )
            self.assertTrue(first["stale_baseline"]["sent"])
            self.assertFalse(duplicate["stale_baseline"]["sent"])
            self.assertTrue(recovered["stale_baseline"]["sent"])
            self.assertEqual(len(sent), 2)

    def test_new_baseline_pair_validates_atomically(self):
        available = catalogs(Path("/cache.xml"), ["ca.channel"], "ca-source")
        selected = main._validated_baseline(
            {"source": "ca-source", "channel_id": "ca.channel"},
            "212", available,
        )
        self.assertEqual(
            selected, {"source": "ca-source", "channel_id": "ca.channel"}
        )
        with self.assertRaises(HTTPException):
            main._validated_baseline(
                {"source": "ca-source", "channel_id": "missing"},
                "212", available,
            )

    def test_baseline_pair_persists_without_touching_enrichments(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            path = directory / "channels.json"
            channel = row("212")
            original_enrichments = (
                deepcopy(channel["enrichment_1"]),
                deepcopy(channel["enrichment_2"]),
            )
            path.write_text(json.dumps({
                "schema_version": 2, "count": 1, "channels": [channel]
            }))
            available = catalogs(
                directory / "ca.xml", ["Bravo.Canada.HD.ca2"], "epgshare-ca2"
            )
            payload = {"mappings": [{
                "number": "212",
                "baseline": {
                    "source": "epgshare-ca2",
                    "channel_id": "Bravo.Canada.HD.ca2",
                },
            }]}
            with patch.object(main, "CONFIG_DIR", directory), patch.object(
                main, "CHANNELS_JSON", path
            ), patch.object(main, "load_schedule_catalogs", return_value=available), patch.object(
                main, "parse_enrichment_xmltv", return_value=[]
            ), patch.object(
                main, "combine_epgtalk_catalog", return_value={"channels": []}
            ):
                main.save_channel_changes(payload)
                reloaded = main.load_channels_matrix()["channels"][0]
            self.assertEqual(reloaded["baseline"], payload["mappings"][0]["baseline"])
            self.assertEqual(
                (reloaded["enrichment_1"], reloaded["enrichment_2"]),
                original_enrichments,
            )


if __name__ == "__main__":
    unittest.main()
