import asyncio
from io import BytesIO
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException, UploadFile

from app import main
from app.channel_merge import create_output_channel
from app.ui import render_mapping_page


def row(number="212", name="BRAVO"):
    return {
        "channel_id": f"source.{number}",
        "number": number,
        "name": name,
        "pretty_name": name,
        "group": "Entertainment",
        "logo": "https://example/logo.png",
        "baseline": {
            "source": "baseline-default",
            "channel_id": "bravo.us",
        },
        "enrichment_1": None,
        "enrichment_2": None,
    }


def foundation(*rows):
    return {"count": len(rows), "channels": list(rows)}


class ChannelManagementTests(unittest.TestCase):
    def test_migration_initializes_once_without_changing_count(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "channels.json"
            existing = row("1", "SOURCE ONE")
            del existing["pretty_name"]
            customized = row("2", "SOURCE TWO")
            customized["pretty_name"] = "My Two"
            path.write_text(json.dumps({"count": 2, "channels": [existing, customized]}))
            with patch.object(main, "CONFIG_DIR", Path(directory)), patch.object(
                main, "CHANNELS_JSON", path
            ):
                migrated = main.load_channels_matrix()
                loaded_again = main.load_channels_matrix()
            self.assertEqual(migrated["count"], 2)
            self.assertEqual(migrated["channels"][0]["pretty_name"], "SOURCE ONE")
            self.assertEqual(migrated["channels"][1]["pretty_name"], "My Two")
            self.assertEqual(migrated, loaded_again)

    def test_xmltv_has_exact_ordered_names_and_stable_metadata(self):
        original = row()
        changed = deepcopy(original)
        changed["pretty_name"] = "Real Housewives of Orange County"
        channel = create_output_channel(changed)
        names = [item.text for item in channel.findall("display-name")]
        self.assertEqual(
            names,
            ["Real Housewives of Orange County", "BRAVO"],
        )
        self.assertEqual(channel.get("id"), "wonk.212")
        self.assertEqual(channel.find("icon").get("src"), original["logo"])
        self.assertEqual(changed["baseline"], original["baseline"])
        self.assertEqual(changed["enrichment_1"], original["enrichment_1"])

    def test_xmltv_emits_duplicate_names_when_equal(self):
        channel = create_output_channel(row())
        self.assertEqual(
            [item.text for item in channel.findall("display-name")],
            ["BRAVO", "BRAVO"],
        )

    def test_server_rejects_long_pretty_name(self):
        self.assertEqual(main._validated_pretty_name("x" * 50, "1"), "x" * 50)
        with self.assertRaises(HTTPException) as raised:
            main._validated_pretty_name("x" * 51, "1")
        self.assertEqual(raised.exception.status_code, 400)

    def test_drift_new_missing_and_identity_changed_are_read_only(self):
        matrix = {"count": 3, "channels": [row("1", "ONE"), row("2", "TWO"), row("3", "THREE")]}
        before = deepcopy(matrix)
        live = foundation(
            {"channel_id": "source.1", "number": "1", "name": "ONE", "group": "G", "logo": "one"},
            {"channel_id": "replacement.2", "number": "2", "name": "TWO NEW", "group": "G", "logo": "two"},
            {"channel_id": "source.4", "number": "4", "name": "FOUR", "group": "G", "logo": "four"},
        )
        discovery = {
            "baseline_source": "baseline-default",
            "results": [{
                "foundation_number": "4", "status": "unique_match",
                "baseline_channel_id": "four.us",
            }]
        }
        drift = main.compare_foundation_drift(matrix, live, discovery)
        self.assertEqual([item["number"] for item in drift["inactive"]], ["4"])
        self.assertEqual(drift["inactive"][0]["pretty_name"], "FOUR")
        self.assertEqual(drift["inactive"][0]["baseline"]["channel_id"], "four.us")
        self.assertEqual(drift["warnings"]["2"], "identity_changed")
        self.assertEqual(drift["warnings"]["3"], "missing_from_foundation")
        self.assertEqual(matrix, before)

    def test_atomic_activation_appends_one_and_preserves_unrelated_row(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "channels.json"
            active = row("1", "ONE")
            matrix = {
                "schema_version": 2, "count": 1, "channels": [active]
            }
            path.write_text(json.dumps(matrix))
            discovered = row("4", "FOUR")
            discovered["baseline"]["channel_id"] = "four.us"
            payload = {
                "mappings": [{
                    "number": "1", "pretty_name": "ONE",
                    "enrichment_1": None, "enrichment_2": None,
                }],
                "activations": [{
                    "number": "4", "pretty_name": "Pretty Four",
                    "logo": "https://example/four.png",
                    "enrichment_1": None, "enrichment_2": None,
                }],
            }
            with patch.object(main, "CONFIG_DIR", Path(directory)), patch.object(
                main, "CHANNELS_JSON", path
            ), patch.object(main, "parse_enrichment_xmltv", return_value=[]), patch.object(
                main, "combine_epgtalk_catalog", return_value={"channels": []}
            ), patch.object(
                main, "compare_foundation_drift",
                return_value={"inactive": [discovered], "warnings": {}},
            ):
                result = main.save_channel_changes(payload)
            saved = json.loads(path.read_text())
            self.assertEqual(result["channels_activated"], 1)
            self.assertEqual(saved["count"], 2)
            self.assertEqual(saved["channels"][0], active)
            self.assertEqual(saved["channels"][1]["pretty_name"], "Pretty Four")
            self.assertEqual(saved["channels"][1]["name"], "FOUR")
            live_after = foundation(
                {"channel_id": "source.1", "number": "1", "name": "ONE", "group": "Entertainment", "logo": "one"},
                {"channel_id": "source.4", "number": "4", "name": "FOUR", "group": "Entertainment", "logo": "four"},
            )
            drift_after = main.compare_foundation_drift(
                saved,
                live_after,
                {"results": []},
            )
            self.assertEqual(drift_after["inactive"], [])
            self.assertNotIn("4", drift_after["warnings"])


    def test_invalid_enrichment_rejects_without_write(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "channels.json"
            matrix = {
                "schema_version": 2,
                "count": 1,
                "channels": [row("1", "ONE")],
            }
            path.write_text(json.dumps(matrix))
            before = path.read_bytes()
            payload = {"mappings": [{
                "number": "1", "pretty_name": "ONE",
                "enrichment_1": "not-valid", "enrichment_2": None,
            }]}
            with patch.object(main, "CONFIG_DIR", Path(directory)), patch.object(
                main, "CHANNELS_JSON", path
            ), patch.object(main, "parse_enrichment_xmltv", return_value=[]), patch.object(
                main, "combine_epgtalk_catalog", return_value={"channels": []}
            ):
                with self.assertRaises(HTTPException):
                    main.save_channel_changes(payload)
            self.assertEqual(path.read_bytes(), before)

    def test_inactive_png_upload_selects_logo_without_persisting_row(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            config = directory / "channels.json"
            matrix = {
                "schema_version": 2,
                "count": 1,
                "channels": [row("1", "ONE")],
            }
            config.write_text(json.dumps(matrix))
            before = config.read_bytes()
            discovered = row("4", "FOUR")
            upload = UploadFile(
                filename="four.png",
                file=BytesIO(main.PNG_SIGNATURE + b"test-png-body"),
            )
            with patch.object(main, "CONFIG_DIR", directory), patch.object(
                main, "CHANNELS_JSON", config
            ), patch.object(main, "LOGOS_DIR", directory), patch.object(
                main, "canonical_logo_url",
                side_effect=lambda filename: f"http://logos/{filename}",
            ), patch.object(
                main, "compare_foundation_drift",
                return_value={"inactive": [discovered], "warnings": {}},
            ):
                result = asyncio.run(
                    main.upload_channel_logo("4", inactive=True, file=upload)
                )
            self.assertEqual(config.read_bytes(), before)
            self.assertEqual(result["logo"], "http://logos/00004-logo.png")
            self.assertTrue((directory / "00004-logo.png").is_file())


    def test_ui_contains_locked_controls_and_inactive_table(self):
        drift = {"inactive": [row("4", "FOUR")], "warnings": {"1": "missing_from_foundation"}}
        html = render_mapping_page(
            {"count": 1, "channels": [row("1", "ONE")]}, [], [], drift
        )
        self.assertIn('maxlength="50"', html)
        self.assertIn('class="pretty-name"', html)
        self.assertIn(" readonly>", html)
        self.assertIn("Inactive / Discovered Channels", html)
        self.assertIn("missing_from_foundation", html)
        self.assertIn('data-inactive="true"', html)
        self.assertIn('input.readOnly=!unlocking', html)


if __name__ == "__main__":
    unittest.main()
