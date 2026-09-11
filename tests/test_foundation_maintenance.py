import json
import tempfile
import unittest
from pathlib import Path
from urllib.error import URLError
from unittest.mock import patch

from app import foundation, main, source_settings
from app.ui import render_mapping_page


THREADFIN_M3U = b"""#EXTM3U
#EXTINF:-1 channelID="source.101" tvg-chno="101" tvg-name="NEWS" group-title="News" tvg-logo="https://logos.test/101.png",NEWS
http://stream.test/101
"""
GENERIC_M3U = b"""#EXTM3U
#EXTINF:-1 tvg-id="generic.7" tvg-chno="7" tvg-name="GENERIC" group-title="General" tvg-logo="https://logos.test/7.png",Generic Channel
http://stream.test/7
"""


class Response:
    status = 200

    def __init__(self, contents):
        self.contents = contents

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit):
        return self.contents


def empty_settings(foundation_location="/threadfin/threadfin.m3u", first="EPGShare", second="EPGTalk"):
    return {
        "foundation": {"configured_m3u": foundation_location},
        "baseline": {"url": "", "provider_name": "Hive"},
        "enrichment_1": {"url": "https://one.test/guide", "provider_name": first},
        "enrichment_2": {
            "primary_url": "https://two.test/guide",
            "secondary_url": "https://two.test/local",
            "provider_name": second,
        },
    }


def channel(number="7"):
    return {
        "channel_id": f"source.{number}",
        "number": number,
        "name": "CHANNEL",
        "pretty_name": "Pretty Channel",
        "group": "Group",
        "logo": "https://logo.test/channel.png",
        "baseline": {"source": "hive", "channel_id": "base.id", "status": "valid"},
        "enrichment_1": {"source": "epgshare", "channel_id": "art.id"},
        "enrichment_2": {"source": "epgtalk", "channel_id": "long.id"},
    }


class FoundationMaintenanceTests(unittest.TestCase):
    def source_context(self, directory):
        directory = Path(directory)
        return patch.multiple(
            source_settings,
            CONFIG_DIR=directory,
            SOURCES_PATH=directory / "sources.json",
        )

    def test_threadfin_and_generic_m3u_structures_validate(self):
        threadfin = foundation.inspect_m3u(THREADFIN_M3U)
        generic = foundation.inspect_m3u(GENERIC_M3U)
        self.assertEqual(threadfin["channel_count"], 1)
        self.assertEqual(threadfin["unique_channel_number_count"], 1)
        self.assertEqual(threadfin["metadata_presence"]["channel_id"], 1)
        self.assertEqual(generic["usable_channel_count"], 1)
        self.assertEqual(generic["channels"][0]["channel_id"], "generic.7")
        self.assertIn(
            "1 channel entries missing channelID", generic["advisories"]
        )

    def test_local_path_and_http_url_work(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "channels.m3u"
            path.write_bytes(GENERIC_M3U)
            local = foundation.validate_foundation(str(path))
        self.assertTrue(local["valid"])
        with patch.object(
            foundation, "urlopen", return_value=Response(GENERIC_M3U)
        ) as opened:
            remote = foundation.validate_foundation(
                "https://user:pass@example.test/channels.m3u?token=secret"
            )
        self.assertTrue(remote["valid"])
        self.assertEqual(opened.call_count, 1)

    def test_malformed_duplicate_and_missing_metadata_diagnostics(self):
        with self.assertRaisesRegex(
            foundation.FoundationValidationError, "missing #EXTM3U"
        ):
            foundation.inspect_m3u(b"not an m3u")
        duplicate = foundation.inspect_m3u(
            b"""#EXTM3U
#EXTINF:-1 tvg-chno="2" tvg-name="One",One
http://one
#EXTINF:-1 tvg-chno="2" tvg-name="Two",Two
http://two
#EXTINF:-1 tvg-name="Missing",Missing
http://missing
"""
        )
        self.assertEqual(duplicate["duplicate_channel_number_count"], 1)
        self.assertEqual(duplicate["duplicate_channel_numbers"], ["2"])
        self.assertEqual(duplicate["missing_channel_number_count"], 1)
        self.assertTrue(any("group-title" in item for item in duplicate["advisories"]))

    def test_authenticated_errors_are_sanitized(self):
        credential_url = "https://user:password@example.test/a?token=secret"
        with patch.object(
            foundation, "urlopen", side_effect=URLError(credential_url)
        ):
            with self.assertRaisesRegex(
                foundation.FoundationValidationError,
                "^Could not fetch configured M3U$",
            ) as raised:
                foundation.validate_foundation(credential_url)
        message = str(raised.exception)
        self.assertNotIn("password", message)
        self.assertNotIn("secret", message)
        self.assertNotIn(credential_url, message)

    def test_foundation_migrates_in_sources_and_never_changes_channels(self):
        old = empty_settings()
        old.pop("foundation")
        with tempfile.TemporaryDirectory() as directory, self.source_context(directory):
            directory = Path(directory)
            source_path = directory / "sources.json"
            channels_path = directory / "channels.json"
            source_path.write_text(json.dumps(old))
            channels_path.write_bytes(b'{"channels":[{"number":"1"}]}')
            before = channels_path.read_bytes()
            loaded = source_settings.load_source_settings()
            self.assertEqual(
                loaded["foundation"]["configured_m3u"],
                "/foundation/channels.m3u",
            )
            loaded["foundation"]["configured_m3u"] = "/foundation/custom.m3u"
            source_settings.save_source_settings(loaded)
            self.assertEqual(channels_path.read_bytes(), before)
            persisted = json.loads(source_path.read_text())
            self.assertEqual(
                persisted["foundation"]["configured_m3u"],
                "/foundation/custom.m3u",
            )

    def test_discovery_and_drift_use_configured_foundation(self):
        configured = {
            "count": 1,
            "channels": [{
                "channel_id": "generic.9",
                "number": "9",
                "name": "NINE",
                "group": "Test",
                "logo": None,
            }],
        }
        catalogs = {
            "baseline-default": {
                "available": True,
                "channels": [{
                    "channel_id": "baseline.9",
                    "display_name": "NINE",
                    "display_names": ["NINE"],
                }],
            },
        }
        settings = {"baseline": {"default_source": "baseline-default"}}
        with patch.object(
            main, "load_configured_foundation", return_value=configured
        ) as loaded, patch.object(
            main, "load_source_settings", return_value=settings
        ), patch.object(
            main, "load_schedule_catalogs", return_value=catalogs
        ):
            discovery = main.discover_default_baseline_matches()
        loaded.assert_called_once_with()
        self.assertEqual(discovery["results"][0]["foundation_number"], "9")
        self.assertEqual(discovery["unique_matches"], 1)
        self.assertEqual(
            discovery["results"][0]["baseline_channel_id"], "baseline.9"
        )

        with patch.object(
            main, "load_configured_foundation", return_value=configured
        ) as loaded, patch.object(
            main, "discover_default_baseline_matches", return_value=discovery
        ), patch.object(main, "load_source_settings", return_value=settings):
            drift = main.compare_foundation_drift({"channels": []})
        loaded.assert_called_once_with()
        self.assertEqual([item["number"] for item in drift["inactive"]], ["9"])
        self.assertEqual(
            drift["inactive"][0]["baseline"]["channel_id"], "baseline.9"
        )

    def test_dynamic_headers_fallbacks_and_existing_mappings(self):
        row = channel()
        html = render_mapping_page(
            {"count": 1, "channels": [row]},
            [{"channel_id": "art.id", "display_name": "Artwork"}],
            [{"channel_id": "long.id", "display_name": "Long Range"}],
            {"inactive": [], "warnings": {}},
            source_settings=empty_settings(
                first="My Artwork Guide", second="Long Range Guide"
            ),
        )
        self.assertIn("Foundation Group /<br>Baseline Schedule", html)
        self.assertEqual(html.count('data-heading="enrichment_1">My Artwork Guide'), 2)
        self.assertEqual(html.count('data-heading="enrichment_2">Long Range Guide'), 2)
        self.assertIn('data-selected-id="art.id"', html)
        self.assertIn('data-selected-id="long.id"', html)
        self.assertNotIn("<th>Threadfin", html)

        fallback = render_mapping_page(
            {"count": 0, "channels": []},
            [],
            [],
            {"inactive": [], "warnings": {}},
            source_settings=empty_settings(first="", second=""),
        )
        self.assertEqual(fallback.count('data-heading="enrichment_1">Enrichment 1'), 2)
        self.assertEqual(fallback.count('data-heading="enrichment_2">Enrichment 2'), 2)
        self.assertIn("updateMappingHeadings()", fallback)
        self.assertIn("providerNames.epgshare=data.enrichment_1.provider_name ||", fallback)

    def test_restart_route_and_ui_are_narrowly_scoped(self):
        route = next(
            route for route in main.app.routes
            if route.path == "/maintenance/restart"
        )
        self.assertEqual(route.methods, {"POST"})
        paths = {route.path for route in main.app.routes}
        self.assertFalse(any("command" in path or "shell" in path for path in paths))
        html = render_mapping_page(
            {"count": 0, "channels": []},
            [],
            [],
            {"inactive": [], "warnings": {}},
        )
        self.assertIn(
            'window.confirm("Restart WonkEPG? Guide serving will be unavailable briefly.")',
            html,
        )
        self.assertIn('adminFetch("/maintenance/restart",{method:"POST"})', html)
        self.assertIn("status.instance_id!==previousInstance", html)
        self.assertIn("WonkEPG did not return within 90 seconds", html)

    def test_no_docker_socket_or_docker_admin_mount(self):
        compose = Path("compose.yaml").read_text()
        self.assertNotIn("/var/run/docker.sock", compose)
        self.assertNotIn("docker.sock", compose)


if __name__ == "__main__":
    unittest.main()
