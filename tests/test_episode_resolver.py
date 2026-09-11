import json
import re
import shutil
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

from app import episode_resolver
from app.ui import render_mapping_page
from app.tvmaze_provider import ProviderError, TVmazeProvider


START = "20260910220000 -0400"
STOP = "20260910230000 -0400"


def programme(
    title="The Last Word With Lawrence O'Donnell",
    episode="E169",
    start=START,
    stop=STOP,
    previous=False,
    extra="",
):
    marker = "<previously-shown/>" if previous else ""
    return ET.fromstring(
        f'<programme channel="wonk.102" start="{start}" stop="{stop}">'
        f"<title>{title}</title>{extra}"
        f'<episode-num system="onscreen">{episode}</episode-num>'
        f"{marker}</programme>"
    )


def config(
    path, title="The Last Word With Lawrence O'Donnell", show_id=9312,
    mode=episode_resolver.STRICT_EPISODE_MATCH, confirmed=True,
):
    path.write_text(json.dumps({
        "schema_version": 1,
        "bindings": {
            episode_resolver.binding_key("wonk.102", title): {
                "provider": "tvmaze",
                "show_id": show_id,
                "confirmed": confirmed,
                "canonical_name": "Provider display name",
                "aliases": [],
                "mode": mode,
                "timezone": "America/New_York",
            }
        },
    }))


def cache(root, show_id=9312, episodes=None, canonical="Changed provider name"):
    root.mkdir(parents=True, exist_ok=True)
    (root / f"show-{show_id}.json").write_text(json.dumps({
        "schema_version": 1,
        "provider": "tvmaze",
        "show_id": show_id,
        "canonical_name": canonical,
        "fetched_at": "2026-09-10T12:00:00+00:00",
        "episodes": episodes if episodes is not None else [{
            "id": 3718224,
            "name": "Episode 169",
            "season": 2026,
            "number": 169,
            "type": "regular",
            "airdate": "2026-09-10",
            "airtime": "22:00",
            "runtime": 60,
        }],
    }))


def resolve(groups, directory):
    directory = Path(directory)
    return episode_resolver.apply_cached_episode_resolution(
        groups,
        config_path=directory / "episode_resolvers.json",
        cache_root=directory / "tvmaze",
        state_path=directory / "status.json",
    )


class EpisodeResolverTests(unittest.TestCase):
    def test_episode_only_agreement_adds_xmltv_ns_and_preserves_source_semantics(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            config(directory / "episode_resolvers.json")
            cache(directory / "tvmaze")
            item = programme(extra="<live/>")
            before_onscreen = item.findtext("episode-num[@system='onscreen']")
            result = resolve([[item]], directory)
            self.assertEqual(result["resolved_records"], 1)
            self.assertEqual(result["external_api_calls"], 0)
            self.assertEqual(before_onscreen, "E169")
            self.assertEqual(item.findtext("episode-num[@system='onscreen']"), "E169")
            self.assertEqual(item.findtext("episode-num[@system='xmltv_ns']"), "2025.168.")
            self.assertEqual(len(item.findall("episode-num[@system='xmltv_ns']")), 1)
            self.assertEqual(len(item.findall("live")), 1)
            self.assertIsNone(item.find("new"))
            self.assertIsNone(item.find("previously-shown"))

    def test_ineligible_season_structured_and_stronger_identifiers_are_unchanged(self):
        cases = [
            programme(episode="S2 E137"),
            programme(extra='<episode-num system="xmltv_ns">1.136.</episode-num>'),
            programme(extra='<episode-num system="dd_progid">EP1.0001</episode-num>'),
            programme(episode="169"),
        ]
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            config(directory / "episode_resolvers.json")
            cache(directory / "tvmaze")
            before = [ET.tostring(item) for item in cases]
            result = resolve([cases], directory)
            self.assertEqual(result.get("resolved_records", 0), 0)
            self.assertEqual(before, [ET.tostring(item) for item in cases])

    def test_no_binding_and_missing_or_malformed_cache_degrade_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            item = programme()
            before = ET.tostring(item)
            result = resolve([[item]], directory)
            self.assertEqual(result["no_binding_records"], 1)
            self.assertEqual(ET.tostring(item), before)

            config(directory / "episode_resolvers.json")
            result = resolve([[item]], directory)
            self.assertEqual(result["no_cache_records"], 1)
            self.assertEqual(ET.tostring(item), before)

            cache_root = directory / "tvmaze"
            cache_root.mkdir()
            (cache_root / "show-9312.json").write_text("not json")
            result = resolve([[item]], directory)
            self.assertEqual(result["resolver_unavailable_records"], 1)
            self.assertEqual(ET.tostring(item), before)

    def test_conflict_multiple_special_and_null_number_do_not_modify(self):
        scenarios = [
            ([{"id": 1, "name": "Episode 170", "season": 2026, "number": 170,
               "type": "regular", "airdate": "2026-09-10", "airtime": "22:00", "runtime": 60}],
             "conflict_records"),
            ([{"id": 1, "name": "Episode 169", "season": 2026, "number": 169,
               "type": "regular", "airdate": "2026-09-10", "airtime": "22:00", "runtime": 60},
              {"id": 2, "name": "Episode 170", "season": 2026, "number": 170,
               "type": "regular", "airdate": "2026-09-10", "airtime": "22:00", "runtime": 60}],
             "ambiguous_match_records"),
            ([{"id": 1, "name": "Special", "season": 2026, "number": None,
               "type": "significant_special", "airdate": "2026-09-10", "airtime": "22:00", "runtime": 60}],
             "no_episode_match_records"),
        ]
        for episodes, expected in scenarios:
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as directory:
                directory = Path(directory)
                config(directory / "episode_resolvers.json")
                cache(directory / "tvmaze", episodes=episodes)
                item = programme(); before = ET.tostring(item)
                result = resolve([[item]], directory)
                self.assertEqual(result[expected], 1)
                self.assertEqual(ET.tostring(item), before)

    def test_overnight_repeat_reuses_original_but_orphan_repeat_does_not(self):
        repeat = programme(
            start="20260911010000 -0400",
            stop="20260911020000 -0400",
            previous=True,
        )
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            config(directory / "episode_resolvers.json")
            cache(directory / "tvmaze")
            first = programme(extra="<live/>")
            result = resolve([[first, repeat]], directory)
            self.assertEqual(result["resolved_records"], 2)
            self.assertEqual(result["resolved_repeat_records"], 1)
            self.assertEqual(repeat.findtext("episode-num[@system='xmltv_ns']"), "2025.168.")
            self.assertIsNotNone(repeat.find("previously-shown"))
            self.assertIsNone(repeat.find("new"))

        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            config(directory / "episode_resolvers.json")
            cache(directory / "tvmaze")
            orphan = programme(
                start="20260911010000 -0400", stop="20260911020000 -0400",
                previous=True,
            )
            before = ET.tostring(orphan)
            result = resolve([[orphan]], directory)
            self.assertEqual(result["repeat_without_original_records"], 1)
            self.assertEqual(ET.tostring(orphan), before)

    def test_date_anchored_identity_uses_complete_external_pair_and_preserves_source(self):
        episodes = [{
            "id": 3718432, "name": "Episode 180", "season": 2026,
            "number": 180, "type": "regular", "airdate": "2026-09-10",
            "airtime": "22:00", "runtime": 60,
        }]
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            config(
                directory / "episode_resolvers.json",
                mode=episode_resolver.DATE_ANCHORED_IDENTITY,
            )
            cache(directory / "tvmaze", episodes=episodes)
            item = programme(episode="E184", extra="<live/>")
            result = resolve([[item]], directory)
            self.assertEqual(result["date_anchored_identity_records"], 1)
            self.assertEqual(item.findtext("episode-num[@system='onscreen']"), "E184")
            self.assertEqual(item.findtext("episode-num[@system='xmltv_ns']"), "2025.179.")
            self.assertIsNotNone(item.find("live"))
            self.assertIsNone(item.find("new"))
            self.assertIsNone(item.find("previously-shown"))
            detail = result["details"][0]
            self.assertEqual(detail["source_identity"]["value"], "E184")
            self.assertEqual(detail["resolved_identity"]["season"], 2026)
            self.assertEqual(detail["resolved_identity"]["episode"], 180)
            self.assertEqual(detail["resolved_identity"]["mode"], "date_anchored_identity")
            self.assertIn("runtime_match", detail["resolved_identity"]["evidence"])

    def test_date_mode_requires_confirmed_binding(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            config(
                directory / "episode_resolvers.json",
                mode=episode_resolver.DATE_ANCHORED_IDENTITY, confirmed=False,
            )
            cache(directory / "tvmaze")
            item = programme(); before = ET.tostring(item)
            result = resolve([[item]], directory)
            self.assertEqual(result["no_binding_records"], 1)
            self.assertEqual(ET.tostring(item), before)

    def test_date_mode_rejects_multiple_episodes_on_same_date(self):
        base = {
            "name": "Episode 180", "season": 2026, "type": "regular",
            "airdate": "2026-09-10", "airtime": "22:00", "runtime": 60,
        }
        episodes = [
            {**base, "id": 1, "number": 180},
            {**base, "id": 2, "number": 181},
        ]
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            config(directory / "episode_resolvers.json",
                   mode=episode_resolver.DATE_ANCHORED_IDENTITY)
            cache(directory / "tvmaze", episodes=episodes)
            item = programme(episode="E184"); before = ET.tostring(item)
            result = resolve([[item]], directory)
            self.assertEqual(result["ambiguous_match_records"], 1)
            self.assertEqual(ET.tostring(item), before)

    def test_date_mode_rejects_airtime_runtime_subtitle_and_blocked_content(self):
        scenarios = [
            ({"airtime": "21:55"}, "", "airtime_mismatch_records"),
            ({"runtime": 30}, "", "runtime_mismatch_records"),
            ({"name": "A Different Episode"}, "<sub-title>Known Episode</sub-title>",
             "subtitle_conflict_records"),
            ({}, "<category>Sports</category>", "content_type_rejected_records"),
            ({}, "<category>Movie</category>", "content_type_rejected_records"),
            ({"type": "significant_special"}, "", "no_episode_match_records"),
        ]
        base = {
            "id": 1, "name": "Episode 180", "season": 2026, "number": 180,
            "type": "regular", "airdate": "2026-09-10",
            "airtime": "22:00", "runtime": 60,
        }
        for changes, extra, expected in scenarios:
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as directory:
                directory = Path(directory)
                config(directory / "episode_resolvers.json",
                       mode=episode_resolver.DATE_ANCHORED_IDENTITY)
                episode = {**base, **changes}
                cache(directory / "tvmaze", episodes=[episode])
                item = programme(episode="E184", extra=extra); before = ET.tostring(item)
                result = resolve([[item]], directory)
                self.assertEqual(result[expected], 1)
                self.assertEqual(ET.tostring(item), before)

    def test_date_mode_repeat_inherits_resolved_original_not_replay_date(self):
        episodes = [
            {"id": 10, "name": "Episode 180", "season": 2026, "number": 180,
             "type": "regular", "airdate": "2026-09-10", "airtime": "22:00",
             "runtime": 60},
            {"id": 11, "name": "Episode 181", "season": 2026, "number": 181,
             "type": "regular", "airdate": "2026-09-11", "airtime": "01:00",
             "runtime": 60},
        ]
        repeat = programme(episode="E184", start="20260911010000 -0400",
                           stop="20260911020000 -0400", previous=True)
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            config(directory / "episode_resolvers.json",
                   mode=episode_resolver.DATE_ANCHORED_IDENTITY)
            cache(directory / "tvmaze", episodes=episodes)
            original = programme(episode="E184")
            result = resolve([[original, repeat]], directory)
            self.assertEqual(result["resolved_repeat_records"], 1)
            self.assertEqual(repeat.findtext("episode-num[@system='xmltv_ns']"),
                             "2025.179.")
            repeat_detail = result["details"][-1]
            self.assertIn("resolved_original_airing", repeat_detail["evidence"])

    def test_binding_persists_and_provider_display_name_does_not_drive_lookup(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            path = directory / "episode_resolvers.json"
            saved = episode_resolver.save_binding(
                "wonk.102", "The Last Word With Lawrence O'Donnell", 9312,
                "Old Provider Name", path=path,
            )
            self.assertTrue(saved["confirmed"])
            cache(directory / "tvmaze", canonical="Renamed Provider Show")
            item = programme()
            result = resolve([[item]], directory)
            self.assertEqual(result["resolved_records"], 1)
            self.assertEqual(episode_resolver.load_config(path)["bindings"][
                episode_resolver.binding_key("wonk.102", "The Last Word With Lawrence O'Donnell")
            ]["show_id"], 9312)

    def test_unrelated_programme_is_byte_equivalent(self):
        unrelated = ET.fromstring(
            '<programme channel="wonk.201" start="20260910220000 -0400" '
            'stop="20260910230000 -0400"><title>Movie</title><new/></programme>'
        )
        before = ET.tostring(unrelated)
        with tempfile.TemporaryDirectory() as directory:
            resolve([[unrelated]], directory)
        self.assertEqual(ET.tostring(unrelated), before)

    def test_lightweight_settings_ui_exposes_onboarding_and_status(self):
        html = render_mapping_page(
            {"count": 0, "channels": []}, [], [], version="0.7.2"
        )
        self.assertIn('id="resolver-title"', html)
        self.assertIn('id="resolver-search"', html)
        self.assertIn('id="resolver-confirm"', html)
        self.assertIn('id="resolver-detail"', html)
        self.assertIn('/settings/episode-resolvers/refresh', html)
        self.assertIn('normal-build API calls: 0', html)

    def test_primary_operations_share_busy_success_failure_controller(self):
        html = render_mapping_page(
            {"count": 0, "channels": []}, [], [], version="0.7.2"
        )
        self.assertIn('id="operation-feedback"', html)
        self.assertIn('class="operation-spinner"', html)
        self.assertEqual(html.count('operationFeedback.start('), 3)
        for message in ("Saving mappings…", "Building XMLTV…",
                        "Refreshing sources…"):
            self.assertIn(message, html)
        self.assertIn("if(active)return false", html)
        self.assertIn("setButtons(true)", html)
        self.assertEqual(html.count("setButtons(false)"), 2)
        self.assertIn('overlay.className="operation-overlay success"', html)
        self.assertIn('dismissTimer=window.setTimeout(close,3500)', html)
        self.assertIn('overlay.className="operation-overlay failure"', html)
        failure_body = html.split('failure(text){', 1)[1].split('},', 1)[0]
        self.assertNotIn("setTimeout", failure_body)
        for safe_message in ("Mapping save failed", "XMLTV build failed",
                             "Source refresh failed"):
            self.assertIn(safe_message, html)

    def test_rendered_javascript_passes_node_syntax_check(self):
        if shutil.which("node") is None:
            self.skipTest("node is not installed in this test environment")
        html = render_mapping_page(
            {"count": 0, "channels": []}, [], [], version="0.7.2"
        )
        scripts = re.findall(r"<script>(.*?)</script>", html, re.DOTALL)
        self.assertGreaterEqual(len(scripts), 2)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rendered.js"
            path.write_text("\n".join(scripts))
            checked = subprocess.run(
                ["node", "--check", str(path)], capture_output=True, text=True
            )
        self.assertEqual(checked.returncode, 0, checked.stderr)

    def test_normal_build_path_never_calls_network(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            config(directory / "episode_resolvers.json")
            cache(directory / "tvmaze")
            with patch("app.tvmaze_provider.urlopen") as network:
                result = resolve([[programme()]], directory)
            network.assert_not_called()
            self.assertEqual(result["resolved_records"], 1)


class TVmazeProviderFailureTests(unittest.TestCase):
    def test_timeout_and_429_are_sanitized(self):
        with tempfile.TemporaryDirectory() as directory:
            provider = TVmazeProvider(directory)
            with patch("app.tvmaze_provider.urlopen", side_effect=URLError("dns")), patch(
                "app.tvmaze_provider.time.sleep"
            ):
                with self.assertRaises(ProviderError):
                    provider.search_shows("Test")
            error = HTTPError("https://api.tvmaze.com", 429, "limited", {}, None)
            with patch("app.tvmaze_provider.urlopen", side_effect=error), patch(
                "app.tvmaze_provider.time.sleep"
            ):
                with self.assertRaises(ProviderError):
                    provider.search_shows("Test")

    def test_malformed_refresh_preserves_last_known_good_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache(root)
            path = root / "show-9312.json"
            before = path.read_bytes()
            provider = TVmazeProvider(root)
            with patch.object(provider, "_request_json", return_value={"bad": True}):
                with self.assertRaises(ProviderError):
                    provider.refresh_catalog(9312, "Last Word")
            self.assertEqual(path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
