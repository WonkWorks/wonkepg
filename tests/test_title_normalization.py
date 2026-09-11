import unittest
import xml.etree.ElementTree as ET

from app.channel_merge import (
    merge_single_channel,
    normalize_title_decorations,
)


START = "20260910000000 +0000"
STOP = "20260910010000 +0000"


def programme(title, channel="source", start=START, stop=STOP, body=""):
    return ET.fromstring(
        f'<programme channel="{channel}" start="{start}" stop="{stop}">'
        f"<title>{title}</title>{body}</programme>"
    )


def channel_row():
    return {
        "channel_id": "source.channel",
        "number": "102",
        "name": "MS NOW",
        "pretty_name": "MS NOW",
        "group": "News",
        "logo": "https://example.test/logo.png",
    }


def merge(baseline=None, share=None, talk=None):
    return merge_single_channel(
        channel_row=channel_row(),
        baseline_xmltv=None,
        baseline_channel_id=None,
        baseline_programmes=baseline or [],
        epgshare_channel_id="share" if share is not None else None,
        epgtalk_channel_id="talk" if talk is not None else None,
        epgshare_programmes=share,
        epgtalk_programmes=talk,
    )


class TitleDecorationNormalizationTests(unittest.TestCase):
    def assert_normalized(self, raw, canonical, new=False, live=False):
        result = normalize_title_decorations(raw)
        self.assertEqual(result.canonical_title, canonical)
        self.assertEqual(result.signals.new, new)
        self.assertEqual(result.signals.live, live)

    def test_recognized_prefix_and_suffix_forms(self):
        cases = (
            ("Live: Morning Joe", "Morning Joe", False, True),
            ("[LIVE] Morning Joe", "Morning Joe", False, True),
            ("[LIVE]: Morning Joe", "Morning Joe", False, True),
            ("NEW: Morning Joe", "Morning Joe", True, False),
            ("[NEW] Morning Joe", "Morning Joe", True, False),
            ("[NEW]: Morning Joe", "Morning Joe", True, False),
            ("Morning Joe [NEW]", "Morning Joe", True, False),
        )
        for raw, canonical, new, live in cases:
            with self.subTest(raw=raw):
                self.assert_normalized(raw, canonical, new, live)

    def test_combined_and_repeated_decorations_terminate(self):
        for raw in (
            "[NEW] Live: Morning Joe",
            "Live: [NEW] Morning Joe",
            "[NEW]: Live: Morning Joe",
            "[NEW] [NEW]: [LIVE]: Live: Morning Joe [NEW]",
        ):
            with self.subTest(raw=raw):
                self.assert_normalized(raw, "Morning Joe", True, True)
        self.assert_normalized("[NEW]", "[NEW]")
        self.assert_normalized("Live:", "Live:")

    def test_legitimate_titles_are_unchanged(self):
        for title in (
            "Saturday Night Live",
            "Live PD",
            "Live Rescue",
            "New Girl",
            "New Amsterdam",
        ):
            with self.subTest(title=title):
                self.assert_normalized(title, title)

    def test_matching_normalizes_either_source_role(self):
        baseline_live = merge(
            baseline=[programme("Live: Morning Joe")],
            share=[programme("Morning Joe", "share", body="<date>2026</date>")],
        )
        output = baseline_live.programmes[0]
        self.assertEqual(output.findtext("title"), "Morning Joe")
        self.assertIsNotNone(output.find("live"))
        self.assertEqual(output.findtext("date"), "2026")
        self.assertEqual(baseline_live.diagnostics["exact_matches"]["epgshare"], 1)

        enrichment_live = merge(
            baseline=[programme("Morning Joe")],
            share=[programme("Live: Morning Joe", "share", body="<date>2026</date>")],
        )
        output = enrichment_live.programmes[0]
        self.assertEqual(output.findtext("title"), "Morning Joe")
        self.assertIsNotNone(output.find("live"))
        self.assertEqual(output.findtext("date"), "2026")
        self.assertEqual(enrichment_live.diagnostics["exact_matches"]["epgshare"], 1)

    def test_schedule_extension_uses_the_same_normalization(self):
        result = merge(
            baseline=[],
            talk=[programme("[NEW] Live: Extended Show", "talk")],
        )
        self.assertEqual(result.diagnostics["epgtalk_extension_programmes_added"], 1)
        output = result.programmes[0]
        self.assertEqual(output.findtext("title"), "Extended Show")
        self.assertEqual(len(output.findall("new")), 1)
        self.assertEqual(len(output.findall("live")), 1)

    def test_explicit_markers_emit_once_without_inference(self):
        result = merge(
            baseline=[programme(
                "NEW: Morning Joe", body="<new/><new/><previously-shown/>"
            )],
            share=[programme("[NEW] Live: Morning Joe", "share", body="<new/>")],
            talk=[programme("Live: [NEW] Morning Joe", "talk")],
        )
        output = result.programmes[0]
        self.assertEqual(len(output.findall("new")), 1)
        self.assertEqual(len(output.findall("live")), 1)
        self.assertIsNotNone(output.find("previously-shown"))

        plain = merge(baseline=[programme("Morning Joe")]).programmes[0]
        self.assertEqual(len(plain.findall("new")), 0)
        self.assertEqual(len(plain.findall("live")), 0)

    def test_epgtalk_trailing_new_behavior_remains_compatible(self):
        result = merge(
            baseline=[programme("The Situation Room")],
            talk=[programme("The Situation Room [NEW]", "talk")],
        )
        output = result.programmes[0]
        self.assertEqual(output.findtext("title"), "The Situation Room")
        self.assertEqual(len(output.findall("new")), 1)
        self.assertEqual(result.diagnostics["exact_matches"]["epgtalk"], 1)

    def test_ms_now_live_record_enriches_without_synthetic_episode_ids(self):
        rich = (
            "<desc>Current political reporting.</desc>"
            "<credits><presenter>Joe Scarborough</presenter></credits>"
            "<date>2026</date><category>News</category>"
            '<icon src="https://example.test/morning-joe.jpg"/>'
            '<episode-num system="onscreen">E182</episode-num>'
            "<previously-shown/>"
        )
        result = merge(
            baseline=[programme("Morning Joe")],
            share=[programme("Live: Morning Joe", "share", body=rich)],
        )
        output = result.programmes[0]
        self.assertEqual(output.findtext("title"), "Morning Joe")
        self.assertEqual(
            [(item.get("system"), item.text) for item in output.findall("episode-num")],
            [("onscreen", "E182")],
        )
        self.assertEqual(output.findtext("date"), "2026")
        self.assertIsNotNone(output.find("credits"))
        self.assertIsNotNone(output.find("icon"))
        self.assertIsNotNone(output.find("previously-shown"))
        self.assertIsNotNone(output.find("live"))
        self.assertIsNone(output.find("new"))

    def test_large_duration_mismatch_remains_rejected(self):
        result = merge(
            baseline=[programme("Morning Joe")],
            share=[programme(
                "Live: Morning Joe",
                "share",
                stop="20260910010400 +0000",
                body="<date>2026</date>",
            )],
        )
        self.assertEqual(result.diagnostics["exact_matches"]["epgshare"], 0)
        self.assertEqual(result.diagnostics["near_matches"]["epgshare"], 0)
        self.assertIsNone(result.programmes[0].find("date"))


if __name__ == "__main__":
    unittest.main()
