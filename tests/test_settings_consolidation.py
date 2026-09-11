import gzip
from io import BytesIO
import json
import stat
import tempfile
import unittest
from pathlib import Path
from urllib.error import URLError
from unittest.mock import patch

from app import cloudflare_settings, main, source_manager, source_settings
from app.ui import render_mapping_page
from app.version import __version__


XMLTV = b"""<tv source-info-name="Schedules Direct" source-info-url="https://metadata.example/">
<channel id="one"><display-name>One</display-name></channel>
<programme channel="one" start="20260908000000 +0000" stop="20260915000000 +0000"><title>Show</title></programme>
</tv>"""


class Response:
    status = 200

    def __init__(self, contents):
        self.stream = BytesIO(contents)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, limit=-1):
        return self.stream.read(limit)


class SettingsConsolidationTests(unittest.TestCase):
    def source_paths(self, directory):
        root = Path(directory)
        return patch.multiple(
            source_settings,
            CONFIG_DIR=root,
            SOURCES_PATH=root / "sources.json",
        )

    def secret_paths(self, directory):
        root = Path(directory)
        return patch.multiple(
            cloudflare_settings,
            CONFIG_DIR=root,
            SECRETS_PATH=root / "secrets.env",
        )

    def test_version_is_canonical_and_reported(self):
        self.assertEqual(__version__, "0.8.0")
        self.assertEqual(main.get_status()["version"], __version__)

    def test_first_start_migrates_environment_defaults_without_overwrite(self):
        secret_url = "https://user:password@example.test/guide.xml?token=secret"
        with tempfile.TemporaryDirectory() as directory, self.source_paths(
            directory
        ), patch.dict(source_settings.os.environ, {"HIVE_XMLTV_URL": secret_url}):
            migrated = source_settings.ensure_source_settings()
            self.assertEqual(
                migrated["schedule_sources"]["baseline-default"]["url"],
                secret_url,
            )
            self.assertEqual(migrated["enrichment_1"]["provider_name"], "Enrichment 1")
            path = Path(directory) / "sources.json"
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            migrated["schedule_sources"]["baseline-default"][
                "provider_name"
            ] = "My Provider"
            source_settings.save_source_settings(migrated)
            with patch.dict(
                source_settings.os.environ,
                {"HIVE_XMLTV_URL": "https://changed.invalid"},
            ):
                loaded = source_settings.load_source_settings()
            self.assertEqual(
                loaded["schedule_sources"]["baseline-default"]["url"],
                secret_url,
            )
            self.assertEqual(
                loaded["schedule_sources"]["baseline-default"][
                    "provider_name"
                ],
                "My Provider",
            )

    def test_configured_urls_drive_configured_physical_refresh_feeds(self):
        configured = {
            "baseline": {"url": "https://one.test/base", "provider_name": "One"},
            "enrichment_1": {"url": "https://two.test/art", "provider_name": "Two"},
            "enrichment_2": {
                "primary_url": "https://three.test/main",
                "secondary_url": "https://three.test/local",
                "provider_name": "Three",
            },
        }
        with patch.object(
            source_manager, "load_source_settings", return_value=configured
        ):
            specs = source_manager.current_source_specs()
        self.assertEqual(
            [(item.name, item.resolved_url()) for item in specs],
            [
                ("baseline-default", "https://one.test/base"),
                ("epgshare", "https://two.test/art"),
                ("epgtalk", "https://three.test/main"),
                ("epgtalk_local", "https://three.test/local"),
            ],
        )

    def test_plain_and_gzip_xmltv_validate_with_metrics_and_provider(self):
        for payload in (XMLTV, gzip.compress(XMLTV)):
            with self.subTest(gzip=payload.startswith(b"\x1f\x8b")), patch.object(
                source_settings, "urlopen", return_value=Response(payload)
            ):
                result = source_settings.validate_xmltv_url(
                    "https://credentials.example/guide?password=hidden"
                )
            self.assertTrue(result["valid"])
            self.assertEqual(result["channel_count"], 1)
            self.assertEqual(result["programme_count"], 1)
            self.assertEqual(result["horizon_days"], 7.0)
            self.assertEqual(result["suggested_provider_name"], "Schedules Direct")

    def test_validation_failures_are_specific_and_credential_safe(self):
        cases = [
            (b"<tv>", "Invalid XML"),
            (b"<html></html>", "Root element is not <tv>"),
            (b"<tv></tv>", "No XMLTV channels/programmes found"),
        ]
        for payload, expected in cases:
            with self.subTest(expected=expected), patch.object(
                source_settings, "urlopen", return_value=Response(payload)
            ):
                with self.assertRaisesRegex(
                    source_settings.SourceValidationError, expected
                ):
                    source_settings.validate_xmltv_url(
                        "https://user:secret@example.test/feed?token=private"
                    )
        with patch.object(
            source_settings,
            "urlopen",
            side_effect=URLError(
                "https://user:secret@example.test/feed?token=private"
            ),
        ):
            with self.assertRaisesRegex(
                source_settings.SourceValidationError, "^Could not fetch source$"
            ) as raised:
                source_settings.validate_xmltv_url(
                    "https://user:secret@example.test/feed?token=private"
                )
        self.assertNotIn("secret", str(raised.exception))
        self.assertNotIn("private", str(raised.exception))

    def test_source_info_name_optional_and_hostname_fallback(self):
        xml = b'<tv><channel id="x"/></tv>'
        with patch.object(
            source_settings, "urlopen", return_value=Response(xml)
        ):
            result = source_settings.validate_xmltv_url(
                "https://guide.example.test/path.xml"
            )
        self.assertTrue(result["valid"])
        self.assertIsNone(result["detected_provider_name"])
        self.assertEqual(result["suggested_provider_name"], "guide.example.test")

    def test_cloudflare_secret_state_permissions_and_read_only_validation(self):
        token = "top-secret-cloudflare-token"
        with tempfile.TemporaryDirectory() as directory, self.secret_paths(
            directory
        ):
            saved = cloudflare_settings.save_token(token)
            self.assertEqual(saved, {"configured": True})
            self.assertEqual(cloudflare_settings.token_status(), {"configured": True})
            secret_path = Path(directory) / "secrets.env"
            self.assertEqual(stat.S_IMODE(secret_path.stat().st_mode), 0o600)

            calls = []
            def api_get(path, supplied_token):
                calls.append(path)
                self.assertEqual(supplied_token, token)
                if path == "/user/tokens/verify":
                    return {"success": True, "result": {"status": "active"}}
                if path.startswith("/zones?"):
                    return {"success": True, "result": [{
                        "id": "zone", "name": "example.test",
                        "account": {"id": "account"},
                    }]}
                if path.startswith("/zones/zone/dns_records"):
                    return {"success": True, "result": []}
                if path.startswith("/accounts/account/cfd_tunnel"):
                    return {"success": True, "result": []}
                self.fail(path)

            with patch.object(cloudflare_settings, "_api_get", side_effect=api_get):
                result = cloudflare_settings.validate_token("example.test")
            self.assertTrue(result["valid"])
            self.assertNotIn(token, repr(result))
            self.assertEqual(len(calls), 4)
            self.assertTrue(all(
                "POST" not in path and "PUT" not in path and "DELETE" not in path
                for path in calls
            ))

            def denied(path, _token):
                if path == "/user/tokens/verify":
                    return {"result": {"status": "active"}}
                if path.startswith("/zones?"):
                    return {"result": [{
                        "id": "zone", "name": "example.test",
                        "account": {"id": "account"},
                    }]}
                if path.startswith("/zones/zone/dns_records"):
                    return {"result": []}
                raise cloudflare_settings.CloudflareValidationError("raw secret")

            with patch.object(cloudflare_settings, "_api_get", side_effect=denied):
                with self.assertRaisesRegex(
                    cloudflare_settings.CloudflareValidationError,
                    "Cloudflare Tunnel access is missing",
                ) as raised:
                    cloudflare_settings.validate_token("example.test")
            self.assertNotIn(token, str(raised.exception))

    def test_standard_mode_requires_no_token(self):
        with tempfile.TemporaryDirectory() as directory, self.secret_paths(directory):
            status = cloudflare_settings.asset_hosting_status(
                "localhost", Path("/secure-assets")
            )
        self.assertEqual(status["mode"], "Standard")
        self.assertFalse(status["token"]["configured"])

    def test_runtime_secret_paths_are_ignored_and_example_is_clean(self):
        ignore = Path(".gitignore").read_text()
        self.assertIn("config/sources.json", ignore)
        self.assertIn("config/secrets.env", ignore)
        example = Path("config/sources.example.json").read_text()
        self.assertNotIn("password", example.casefold())
        self.assertNotIn("token=", example.casefold())

    def test_ui_is_one_responsive_theme_aware_settings_workflow(self):
        html = render_mapping_page(
            {"count": 0, "channels": []},
            [],
            [],
            {"inactive": [], "warnings": {}},
        )
        header = html.split('<nav class="header-actions"', 1)[1].split(
            "</nav>", 1
        )[0]
        for label in (
            "Save Mappings", "Build XMLTV", "Refresh Sources", "Settings"
        ):
            self.assertIn(label, header)
        self.assertNotIn("Bootstrap Logos", header)
        self.assertNotIn("Error Handling", header)
        self.assertEqual(html.count('id="settings-dialog"'), 1)
        self.assertIn(':root[data-theme="dark"]', html)
        self.assertIn(".settings-grid{grid-template-columns:1fr}", html)
        self.assertIn('id="xmltv-url"', html)
        self.assertNotIn("cachedlogos=false", html)
        self.assertIn("WonkEPG v0.8.0", html)
        self.assertEqual(html.count('class="validate-source"'), 3)
        self.assertIn('id="schedule-source-list"', html)
        self.assertIn('id="add-schedule-source"', html)
        self.assertIn('if(!provider.value.trim())provider.value=', html)
        self.assertIn('input.addEventListener("input",()=>markNotValidated(input))', html)
        self.assertIn('window.confirm("Apply the eligible logo bootstrap changes?")', html)
        self.assertIn('applyBootstrap.disabled=eligible===0', html)
        self.assertIn("No eligible changes.", html)
        self.assertNotIn('id="error-dialog"', html)
        self.assertNotIn('id="save-schedule"', html)


if __name__ == "__main__":
    unittest.main()
