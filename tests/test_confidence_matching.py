import unittest
import xml.etree.ElementTree as ET

from app.channel_merge import (
    _match_source,
    _prepared_programmes,
    merge_single_channel,
)


def programme(
    title, channel="source", start="20260915010000 +0000",
    stop="20260915013000 +0000", body="",
):
    return ET.fromstring(
        f'<programme channel="{channel}" start="{start}" stop="{stop}">'
        f"<title>{title}</title>{body}</programme>"
    )


def merge(baseline, enrichment, source="epgtalk"):
    arguments = {
        "channel_row": {
            "channel_id": "x-ID.47", "number": "224", "name": "FX",
            "pretty_name": "FX", "group": "Entertainment",
        },
        "baseline_xmltv": None,
        "baseline_channel_id": None,
        "baseline_programmes": baseline,
        "epgshare_channel_id": None,
        "epgtalk_channel_id": None,
    }
    if source == "epgtalk":
        arguments.update(
            epgtalk_channel_id="talk", epgtalk_programmes=enrichment
        )
    else:
        arguments.update(
            epgshare_channel_id="share", epgshare_programmes=enrichment
        )
    return merge_single_channel(**arguments)


class ConfidenceMatchingTests(unittest.TestCase):
    def test_exact_start_three_minute_padding_matches(self):
        result = merge(
            [programme("Example")],
            [programme(
                "Example", "talk", stop="20260915013300 +0000",
                body="<sub-title>Episode</sub-title>",
            )],
        )
        self.assertEqual(
            result.diagnostics["padding_tolerant_matches"]["epgtalk"], 1
        )
        self.assertEqual(result.programmes[0].findtext("sub-title"), "Episode")

    def test_fx_always_sunny_merges_s18e6_without_changing_timing(self):
        rich = (
            "<sub-title>The War on Alcohol</sub-title>"
            "<desc>Rich episode description.</desc>"
            "<credits><actor>Charlie Day</actor></credits>"
            "<date>20260914</date><category>Sitcom</category>"
            '<episode-num system="dd_progid">EP00762956.0207</episode-num>'
            '<episode-num system="xmltv_ns">17.5.</episode-num>'
            '<rating system="USA Parental Rating"><value>TVMA</value></rating>'
            "<new/>"
        )
        result = merge(
            [programme("It's Always Sunny in Philadelphia")],
            [programme(
                "It's Always Sunny in Philadelphia [NEW]", "talk",
                stop="20260915013300 +0000", body=rich,
            )],
        )
        output = result.programmes[0]
        self.assertEqual(output.get("start"), "20260915010000 +0000")
        self.assertEqual(output.get("stop"), "20260915013000 +0000")
        self.assertEqual(output.findtext("title"),
                         "It's Always Sunny in Philadelphia")
        self.assertEqual(output.findtext("sub-title"), "The War on Alcohol")
        self.assertEqual(output.findtext("date"), "20260914")
        self.assertEqual(
            [(item.get("system"), item.text)
             for item in output.findall("episode-num")],
            [("dd_progid", "EP00762956.0207"), ("xmltv_ns", "17.5.")],
        )
        self.assertIsNotNone(output.find("credits"))
        self.assertEqual(output.findtext("category"), "Sitcom")
        self.assertEqual(output.findtext("rating/value"), "TVMA")
        self.assertEqual(len(output.findall("new")), 1)
        self.assertEqual(result.diagnostics["near_matches"]["epgtalk"], 1)

    def test_existing_two_minute_standard_path_is_unchanged(self):
        result = merge(
            [programme(
                "Odd News", start="20260915010000 +0000",
                stop="20260915020000 +0000",
            )],
            [programme(
                "Odd News", "talk", start="20260915010200 +0000",
                stop="20260915020200 +0000", body="<date>2026</date>",
            )],
        )
        self.assertEqual(result.diagnostics["standard_matches"]["epgtalk"], 1)
        self.assertEqual(
            result.diagnostics["padding_tolerant_matches"]["epgtalk"], 0
        )
        self.assertEqual(result.programmes[0].findtext("date"), "2026")

    def test_odd_time_near_start_matches_with_high_overlap(self):
        result = merge(
            [programme(
                "Late Show", start="20260915033500 +0000",
                stop="20260915043500 +0000",
            )],
            [programme(
                "Late Show", "talk", start="20260915033700 +0000",
                stop="20260915044000 +0000", body="<date>2026</date>",
            )],
        )
        self.assertEqual(
            result.diagnostics["padding_tolerant_matches"]["epgtalk"], 1
        )
        self.assertEqual(result.programmes[0].findtext("date"), "2026")

    def test_large_duration_mismatch_is_rejected_and_diagnosed(self):
        result = merge(
            [programme("Example")],
            [programme(
                "Example", "talk", stop="20260915013400 +0000",
                body="<date>2026</date>",
            )],
        )
        self.assertIsNone(result.programmes[0].find("date"))
        self.assertEqual(
            result.diagnostics["duration_mismatch_rejected"]["epgtalk"], 1
        )

    def test_low_overlap_candidate_is_rejected(self):
        result = merge(
            [programme("Short", stop="20260915011000 +0000")],
            [programme(
                "Short", "talk", start="20260915010200 +0000",
                stop="20260915011500 +0000", body="<date>2026</date>",
            )],
        )
        self.assertIsNone(result.programmes[0].find("date"))
        self.assertEqual(
            result.diagnostics["low_overlap_rejected"]["epgtalk"], 1
        )

    def test_relaxed_path_rejects_conflicting_subtitle_episode_or_synopsis(self):
        cases = (
            (
                "<sub-title>Original Episode</sub-title>",
                "<sub-title>Different Episode</sub-title>",
            ),
            (
                '<episode-num system="xmltv_ns">1.2.</episode-num>',
                '<episode-num system="xmltv_ns">1.3.</episode-num>',
            ),
            (
                "<desc>Mac and Dee spend the entire evening at a Renaissance "
                "Faire while Charlie learns to play the lute for an audience.</desc>",
                "<desc>Dennis and Frank recruit office workers for happy hour "
                "while Mac and Charlie organize factory workers across town.</desc>",
            ),
        )
        for baseline_body, candidate_body in cases:
            with self.subTest(baseline_body=baseline_body):
                result = merge(
                    [programme("Example", body=baseline_body)],
                    [programme(
                        "Example", "talk", stop="20260915013300 +0000",
                        body=candidate_body + "<date>2026</date>",
                    )],
                )
                self.assertIsNone(result.programmes[0].find("date"))
                self.assertEqual(
                    result.diagnostics["metadata_conflict_rejected"]["epgtalk"],
                    1,
                )

    def test_standard_path_is_not_changed_by_metadata_conflict_diagnostics(self):
        result = merge(
            [programme("Example", body="<sub-title>Original</sub-title>")],
            [programme(
                "Example", "talk", body=
                "<sub-title>Different</sub-title><date>2026</date>",
            )],
        )
        self.assertEqual(result.diagnostics["standard_matches"]["epgtalk"], 1)
        self.assertEqual(result.programmes[0].findtext("date"), "2026")

    def test_multiple_relaxed_candidates_are_ambiguous_and_do_not_merge(self):
        result = merge(
            [programme(
                "Example", stop="20260915020000 +0000"
            )],
            [
                programme(
                    "Example", "talk", stop="20260915020300 +0000",
                    body="<sub-title>One</sub-title>",
                ),
                programme(
                    "Example", "talk", start="20260915010100 +0000",
                    stop="20260915020400 +0000",
                    body="<sub-title>Two</sub-title>",
                ),
            ],
        )
        self.assertIsNone(result.programmes[0].find("sub-title"))
        self.assertEqual(result.diagnostics["ambiguous_matches"]["epgtalk"], 2)
        self.assertEqual(
            result.diagnostics["ambiguous_relaxed_matches_rejected"]["epgtalk"],
            2,
        )

    def test_one_enrichment_cannot_attach_to_two_baseline_programmes(self):
        baselines = _prepared_programmes([
            programme(
                "Rolling Coverage", start="20260915010000 +0000",
                stop="20260915020000 +0000",
            ),
            programme(
                "Rolling Coverage", start="20260915010200 +0000",
                stop="20260915020200 +0000",
            ),
        ])
        candidate = _prepared_programmes([programme(
            "Rolling Coverage", "talk", start="20260915010100 +0000",
            stop="20260915020400 +0000",
        )])
        matches, diagnostics = _match_source(baselines, candidate)
        self.assertEqual(matches, {})
        self.assertEqual(diagnostics["ambiguous"], 1)
        self.assertEqual(diagnostics["ambiguous_relaxed"], 1)

    def test_same_title_marathon_blocks_remain_one_to_one(self):
        result = merge(
            [
                programme(
                    "Marathon", start="20260915010000 +0000",
                    stop="20260915020000 +0000",
                ),
                programme(
                    "Marathon", start="20260915020000 +0000",
                    stop="20260915030000 +0000",
                ),
            ],
            [programme(
                "Marathon", "talk", start="20260915010000 +0000",
                stop="20260915020300 +0000",
                body="<sub-title>First</sub-title>",
            )],
        )
        self.assertEqual(result.programmes[0].findtext("sub-title"), "First")
        self.assertIsNone(result.programmes[1].find("sub-title"))
        self.assertEqual(
            result.diagnostics["padding_tolerant_matches"]["epgtalk"], 1
        )

    def test_sports_same_title_ambiguity_remains_rejected(self):
        baselines = _prepared_programmes([
            programme(
                "SportsCenter", start="20260915010000 +0000",
                stop="20260915020000 +0000", body="<category>Sports</category>",
            ),
            programme(
                "SportsCenter", start="20260915010200 +0000",
                stop="20260915020200 +0000", body="<category>Sports</category>",
            ),
        ])
        candidate = _prepared_programmes([programme(
            "SportsCenter", "talk", start="20260915010100 +0000",
            stop="20260915020400 +0000", body="<category>Sports</category>",
        )])
        matches, diagnostics = _match_source(baselines, candidate)
        self.assertEqual(matches, {})
        self.assertEqual(diagnostics["ambiguous_relaxed"], 1)

    def test_relaxed_path_preserves_new_and_live_normalization(self):
        for title, marker in (
            ("Example [NEW]", "new"),
            ("Live: Example", "live"),
        ):
            with self.subTest(marker=marker):
                result = merge(
                    [programme("Example")],
                    [programme(
                        title, "talk", stop="20260915013300 +0000"
                    )],
                )
                self.assertEqual(result.programmes[0].findtext("title"), "Example")
                self.assertEqual(len(result.programmes[0].findall(marker)), 1)
                self.assertEqual(
                    result.diagnostics["padding_tolerant_matches"]["epgtalk"],
                    1,
                )


if __name__ == "__main__":
    unittest.main()
