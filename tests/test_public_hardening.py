import asyncio
from datetime import datetime, timedelta, timezone
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlencode

from app import main, security, source_settings
from app.location_safety import (
    UnsafeLocationError,
    validate_foundation_location,
    validate_http_location,
)


async def asgi_request(method, path, *, body=b"", headers=None):
    sent = []
    supplied = False

    async def receive():
        nonlocal supplied
        if supplied:
            await asyncio.Event().wait()
        supplied = True
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message):
        sent.append(message)

    raw_headers = [
        (key.lower().encode("latin-1"), value.encode("latin-1"))
        for key, value in (headers or {}).items()
    ]
    scope = {
        "type": "http", "asgi": {"version": "3.0"},
        "http_version": "1.1", "method": method, "scheme": "http",
        "path": path, "raw_path": path.encode(), "query_string": b"",
        "root_path": "", "headers": raw_headers,
        "client": ("127.0.0.1", 12345), "server": ("test", 80),
    }
    await main.app(scope, receive, send)
    start = next(item for item in sent if item["type"] == "http.response.start")
    response_headers = {
        key.decode("latin-1").lower(): value.decode("latin-1")
        for key, value in start["headers"]
    }
    response_body = b"".join(
        item.get("body", b"") for item in sent
        if item["type"] == "http.response.body"
    )
    return start["status"], response_headers, response_body


def request(method, path, **kwargs):
    return asyncio.run(asgi_request(method, path, **kwargs))


class PublicHardeningTests(unittest.TestCase):
    def setUp(self):
        security.clear_sessions()

    def tearDown(self):
        security.clear_sessions()

    def test_admin_boundary_login_session_csrf_and_public_endpoints(self):
        with patch.dict(os.environ, {
            "WONKEPG_ADMIN_PASSWORD": "correct horse battery staple",
            "WONKEPG_COOKIE_SECURE": "false",
        }, clear=False):
            status, headers, _ = request("GET", "/status")
            self.assertEqual(status, 200)
            self.assertEqual(headers["x-frame-options"], "DENY")
            self.assertEqual(request("GET", "/config/channels")[0], 401)

            payload = urlencode({"password": "wrong"}).encode()
            login_headers = {"content-type": "application/x-www-form-urlencoded"}
            status, _, body = request(
                "POST", "/login", body=payload, headers=login_headers
            )
            self.assertEqual(status, 401)
            self.assertNotIn(b"correct horse", body)

            payload = urlencode({
                "password": "correct horse battery staple"
            }).encode()
            status, headers, _ = request(
                "POST", "/login", body=payload, headers=login_headers
            )
            self.assertEqual(status, 303)
            cookie_header = headers["set-cookie"]
            self.assertIn("HttpOnly", cookie_header)
            self.assertIn("SameSite=strict", cookie_header)
            token = cookie_header.split("=", 1)[1].split(";", 1)[0]
            session = security.get_session(token)
            self.assertIsNotNone(session)
            cookie = {"cookie": f"{security.SESSION_COOKIE}={token}"}
            self.assertEqual(request("POST", "/logout", headers=cookie)[0], 403)
            csrf_headers = {
                **cookie, security.CSRF_HEADER: session.csrf_token,
            }
            self.assertEqual(
                request("POST", "/logout", headers=csrf_headers)[0], 303
            )
            self.assertIsNone(security.get_session(token))

    def test_public_xmltv_and_health_disclose_no_admin_secret(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ, {"WONKEPG_ADMIN_PASSWORD": "not-in-responses"},
            clear=False,
        ), patch.object(main, "FULL_OUTPUT_XMLTV", Path(directory) / "xmltv.xml"):
            output = Path(directory) / "xmltv.xml"
            output.write_text("<tv></tv>")
            status, _, body = request("GET", "/xmltv.xml")
            self.assertEqual(status, 200)
            self.assertEqual(body, b"<tv></tv>")
            status, _, body = request("GET", "/status")
            self.assertEqual(status, 200)
            self.assertNotIn(b"not-in-responses", body)
            self.assertNotIn(b"password", body.lower())

    def test_sensitive_operation_classes_are_unauthenticated_by_default(self):
        cases = [
            ("GET", "/settings/sources"),
            ("POST", "/settings/sources"),
            ("POST", "/settings/sources/validate"),
            ("POST", "/settings/foundation/validate"),
            ("POST", "/sources/refresh"),
            ("POST", "/build"),
            ("POST", "/config/channels/mappings"),
            ("POST", "/channels/1/logo"),
            ("POST", "/settings/episode-resolvers/search"),
            ("POST", "/settings/episode-resolvers/bind"),
            ("POST", "/settings/episode-resolvers/refresh"),
            ("POST", "/settings/error-handling/test"),
            ("POST", "/settings/asset-hosting/token"),
            ("POST", "/maintenance/restart"),
        ]
        with patch.dict(
            os.environ, {"WONKEPG_ADMIN_PASSWORD": "secret"}, clear=False
        ):
            for method, path in cases:
                with self.subTest(method=method, path=path):
                    status, _, body = request(method, path)
                    self.assertEqual(status, 401)
                    self.assertNotIn(b"url", body.lower())

    def test_unconfigured_admin_fails_closed(self):
        with patch.dict(os.environ, {"WONKEPG_ADMIN_PASSWORD": ""}, clear=False):
            self.assertEqual(request("GET", "/config/channels")[0], 503)
            status, headers, _ = request("GET", "/")
            self.assertEqual(status, 303)
            self.assertEqual(headers["location"], "/login")

    def test_sessions_expire_and_are_bounded(self):
        with patch.dict(os.environ, {
            "WONKEPG_ADMIN_PASSWORD": "secret",
            "WONKEPG_SESSION_MINUTES": "1",
        }, clear=False):
            now = datetime.now(timezone.utc)
            session = security.create_session(now)
            self.assertEqual(security.session_minutes(), 5)
            self.assertIsNotNone(security.get_session(session.token, now))
            self.assertIsNone(
                security.get_session(session.token, now + timedelta(minutes=6))
            )

    def test_private_source_urls_are_denied_unless_explicitly_allowed(self):
        clean = {
            "WONKEPG_ALLOW_PRIVATE_SOURCE_URLS": "false",
            "WONKEPG_ALLOWED_SOURCE_HOSTS": "",
        }
        with patch.dict(os.environ, clean, clear=False):
            for url in (
                "http://127.0.0.1/feed.xml",
                "http://10.0.0.2/feed.xml",
                "http://[::1]/feed.xml",
                "file:///etc/passwd",
            ):
                with self.subTest(url=url), self.assertRaises(UnsafeLocationError):
                    validate_http_location(url)
        with patch.dict(os.environ, {
            **clean, "WONKEPG_ALLOWED_SOURCE_HOSTS": "10.0.0.2",
        }, clear=False):
            self.assertEqual(
                validate_http_location("http://10.0.0.2/feed.xml"),
                "http://10.0.0.2/feed.xml",
            )

    def test_foundation_local_paths_stay_within_mounted_roots(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ, {"WONKEPG_ALLOWED_FOUNDATION_ROOTS": directory},
            clear=False,
        ):
            inside = str(Path(directory) / "channels.m3u")
            self.assertEqual(validate_foundation_location(inside), inside)
            with self.assertRaises(UnsafeLocationError):
                validate_foundation_location("/etc/passwd")

    def test_credential_urls_are_masked_and_blank_save_preserves_them(self):
        settings = source_settings.default_source_settings()
        settings["schedule_sources"]["baseline-default"]["url"] = (
            "https://user:pass@example.test/guide.xml?token=hidden"
        )
        public = source_settings.public_source_settings(settings)
        self.assertEqual(
            public["schedule_sources"]["baseline-default"]["url"], ""
        )
        self.assertIn(
            "schedule_sources.baseline-default.url", public["redacted_urls"]
        )
        preserved = source_settings.preserve_redacted_source_urls(public, settings)
        self.assertEqual(
            preserved["schedule_sources"]["baseline-default"]["url"],
            settings["schedule_sources"]["baseline-default"]["url"],
        )


if __name__ == "__main__":
    unittest.main()
