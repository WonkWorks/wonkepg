import gzip
from io import BytesIO
import unittest
from unittest.mock import patch

from app import source_settings


class ChunkedResponse:
    status = 200

    def __init__(self, payload):
        self.stream = BytesIO(payload)
        self.read_sizes = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, size=-1):
        self.read_sizes.append(size)
        return self.stream.read(size)


def synthetic_xmltv(padding_bytes=0, programme_count=400):
    programmes = []
    for index in range(programme_count):
        day = 1 + (index % 8)
        programmes.append(
            f'<programme channel="one" start="202610{day:02d}000000 +0000" '
            f'stop="202610{day:02d}010000 +0000"><title>Show {index}</title>'
            "</programme>"
        )
    padding = "<!--" + ("x" * padding_bytes) + "-->"
    return (
        '<tv source-info-name="Large Test Guide" '
        'source-info-url="https://metadata.example.test/">'
        '<channel id="one"><display-name>One</display-name></channel>'
        '<channel id="two"><display-name>Two</display-name></channel>'
        + padding
        + "".join(programmes)
        + "</tv>"
    ).encode()


class StreamingXmltvValidationTests(unittest.TestCase):
    def test_large_plain_and_gzip_documents_stream_with_accurate_metrics(self):
        xml = synthetic_xmltv(padding_bytes=3 * 1024 * 1024)
        for payload in (xml, gzip.compress(xml)):
            with self.subTest(gzip=payload.startswith(b"\x1f\x8b")):
                response = ChunkedResponse(payload)
                with patch.object(
                    source_settings, "urlopen", return_value=response
                ):
                    result = source_settings.validate_xmltv_url(
                        "https://user:secret@guide.example.test/feed?token=hidden"
                    )
                self.assertTrue(result["valid"])
                self.assertEqual(result["channel_count"], 2)
                self.assertEqual(result["programme_count"], 400)
                self.assertEqual(result["horizon_days"], 7.0)
                self.assertEqual(
                    result["detected_provider_name"], "Large Test Guide"
                )
                self.assertEqual(
                    result["suggested_provider_name"], "Large Test Guide"
                )
                self.assertLessEqual(
                    max(response.read_sizes),
                    source_settings.DOWNLOAD_CHUNK_BYTES,
                )
                self.assertNotIn("<programme", repr(result))
                self.assertNotIn("secret", repr(result))
                self.assertNotIn("hidden", repr(result))

    def test_excessive_decompression_is_rejected(self):
        xml = synthetic_xmltv(padding_bytes=256 * 1024, programme_count=1)
        with patch.object(
            source_settings, "urlopen",
            return_value=ChunkedResponse(gzip.compress(xml)),
        ), patch.object(source_settings, "MAX_XML_BYTES", 64 * 1024):
            with self.assertRaisesRegex(
                source_settings.SourceValidationError,
                "decompressed validation safety limit",
            ):
                source_settings.validate_xmltv_url(
                    "https://example.test/bomb.xml.gz"
                )

    def test_large_malformed_xml_fails_cleanly(self):
        malformed = b"<tv>" + (b" " * (2 * 1024 * 1024))
        with patch.object(
            source_settings, "urlopen",
            return_value=ChunkedResponse(malformed),
        ):
            with self.assertRaisesRegex(
                source_settings.SourceValidationError, "^Invalid XML$"
            ):
                source_settings.validate_xmltv_url(
                    "https://user:secret@example.test/bad?token=hidden"
                )

    def test_total_validation_timeout_is_finite_and_sanitized(self):
        response = ChunkedResponse(synthetic_xmltv(programme_count=1))
        with patch.object(
            source_settings, "urlopen", return_value=response
        ), patch.object(
            source_settings.time, "monotonic", side_effect=[0.0, 181.0]
        ):
            with self.assertRaisesRegex(
                source_settings.SourceValidationError,
                "^Source validation timed out$",
            ) as raised:
                source_settings.validate_xmltv_url(
                    "https://user:secret@example.test/feed?token=hidden"
                )
        self.assertNotIn("secret", str(raised.exception))
        self.assertNotIn("hidden", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
