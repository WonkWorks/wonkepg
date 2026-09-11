import asyncio
from copy import deepcopy
from io import BytesIO
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import UploadFile

from app import main
from app.logo_bootstrap import DownloadResult
from app.ui import render_mapping_page


def channel(number="212", logo="https://example.com/logo.png"):
    return {
        "channel_id": f"source.{number}",
        "number": number,
        "name": "CHANNEL",
        "pretty_name": "CHANNEL",
        "group": "Group",
        "logo": logo,
        "baseline": {"source": "hive", "channel_id": "hive.id", "status": "valid"},
        "enrichment_1": None,
        "enrichment_2": None,
    }


class AssetMigrationTests(unittest.TestCase):
    def test_static_branding_routes_resolve(self):
        mount = next(route for route in main.app.routes if route.path == "/static")
        for filename in (
            "favicon.png",
            "wonkepg-logo-dark.png",
            "wonkepg-logo-light.png",
        ):
            full_path, stat_result = mount.app.lookup_path(filename)
            self.assertTrue(Path(full_path).is_file())
            self.assertGreater(stat_result.st_size, 0)

    def test_ui_theme_uses_system_default_persists_choice_and_switches_logo(self):
        html = render_mapping_page(
            {"count": 0, "channels": []}, [], [],
            {"inactive": [], "warnings": {}},
        )
        self.assertIn('href="/static/favicon.png"', html)
        self.assertIn('localStorage.getItem("wonkepg-theme")', html)
        self.assertIn('localStorage.setItem("wonkepg-theme",theme)', html)
        self.assertIn('matchMedia("(prefers-color-scheme: dark)")', html)
        self.assertIn('theme==="dark"', html)
        self.assertIn('"/static/wonkepg-logo-dark.png"', html)
        self.assertIn('"/static/wonkepg-logo-light.png"', html)
        self.assertIn('id="theme-toggle"', html)
        self.assertIn('class="app-header"', html)

    def test_asset_url_generation_uses_server_domain(self):
        with patch.object(main, "SERVER_DOMAIN", "example.test"):
            self.assertEqual(
                main.asset_base_url(), "https://assets.example.test/wonkepg"
            )
            self.assertEqual(
                main.canonical_logo_url("00212-logo.png"),
                "https://assets.example.test/wonkepg/logos/00212-logo.png",
            )

    def test_legacy_url_migration_is_narrow_and_idempotent(self):
        rows = [
            channel("1", "http://192.0.2.10:34500/logos/00001-logo.png"),
            channel("2", "https://external.test/logos/00002-logo.png"),
            channel("3", "http://192.0.2.10:34400/images/3.png"),
            channel("4", "http://192.0.2.10:34500/logos/00004-logo.jpg"),
            channel("5", "https://assets.example.test/wonkepg/logos/00005-logo.png"),
        ]
        matrix = {"count": len(rows), "channels": rows}
        untouched = deepcopy(rows[1:])
        with patch.object(main, "SERVER_DOMAIN", "example.test"):
            first = main.migrate_legacy_logo_urls(matrix)
            second = main.migrate_legacy_logo_urls(matrix)
        self.assertEqual(first["rows_scanned"], 5)
        self.assertEqual(first["rows_migrated"], 1)
        self.assertEqual(first["rows_skipped"], 4)
        self.assertEqual(second["rows_migrated"], 0)
        self.assertEqual(
            rows[0]["logo"],
            "https://assets.example.test/wonkepg/logos/00001-logo.png",
        )
        self.assertEqual(rows[1:], untouched)

    def test_upload_writes_canonical_name_and_preserves_exact_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            config = directory / "channels.json"
            config.write_text(json.dumps({"count": 1, "channels": [channel()]}))
            contents = main.PNG_SIGNATURE + b"exact-upload-bytes"
            upload = UploadFile(filename="LoGo.PNG", file=BytesIO(contents))
            with patch.object(main, "CONFIG_DIR", directory), patch.object(
                main, "CHANNELS_JSON", config
            ), patch.object(main, "LOGOS_DIR", directory), patch.object(
                main, "SERVER_DOMAIN", "example.test",
            ):
                result = asyncio.run(main.upload_channel_logo("212", file=upload))
            path = directory / "00212-logo.png"
            self.assertEqual(path.read_bytes(), contents)
            self.assertEqual(result["filename"], "00212-logo.png")
            self.assertEqual(
                result["logo"],
                "https://assets.example.test/wonkepg/logos/00212-logo.png",
            )
            saved = json.loads(config.read_text())
            self.assertEqual(saved["channels"][0]["logo"], result["logo"])

    def test_bootstrap_writes_secure_png_and_assigns_https_url(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            config = directory / "channels.json"
            source_url = "https://example.test/source.png"
            config.write_text(json.dumps({
                "count": 1, "channels": [channel("7", source_url)]
            }))
            contents = main.PNG_SIGNATURE + b"bootstrap-exact-bytes"
            candidate = [{
                "number": "7", "name": "CHANNEL", "logo": source_url,
                "sha256": "unused",
            }]
            with patch.object(main, "CONFIG_DIR", directory), patch.object(
                main, "CHANNELS_JSON", config
            ), patch.object(main, "LOGOS_DIR", directory), patch.object(
                main, "SERVER_DOMAIN", "example.test",
            ), patch.object(
                main.bootstrap_store, "candidates", return_value=candidate
            ), patch.object(
                main, "download_and_classify",
                return_value=DownloadResult("valid_png", contents=contents),
            ):
                result = main.apply_logo_bootstrap({"scan_id": "scan"})
            self.assertEqual(result["migrated"], 1)
            self.assertEqual(
                (directory / "00007-logo.png").read_bytes(), contents
            )
            saved = json.loads(config.read_text())
            self.assertEqual(
                saved["channels"][0]["logo"],
                "https://assets.example.test/wonkepg/logos/00007-logo.png",
            )


if __name__ == "__main__":
    unittest.main()
