"""Local Cloudflare token storage and read-only validation."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from app.version import __version__


CONFIG_DIR = Path("/app/config")
SECRETS_PATH = CONFIG_DIR / "secrets.env"
API_BASE = "https://api.cloudflare.com/client/v4"


class CloudflareValidationError(Exception):
    pass


def _read_token() -> str | None:
    try:
        for line in SECRETS_PATH.read_text(encoding="utf-8").splitlines():
            if line.startswith("CLOUDFLARE_API_TOKEN="):
                return line.split("=", 1)[1].strip() or None
    except OSError:
        pass
    return None


def token_status() -> dict:
    return {"configured": bool(_read_token())}


def save_token(value: object) -> dict:
    if not isinstance(value, str):
        raise ValueError("token must be text")
    token = value.strip()
    if not token or any(character in token for character in "\r\n\x00"):
        raise ValueError("paste a valid Cloudflare API token")
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".secrets.", suffix=".tmp", dir=CONFIG_DIR
    )
    os.fchmod(descriptor, 0o600)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(f"CLOUDFLARE_API_TOKEN={token}\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, SECRETS_PATH)
        os.chmod(SECRETS_PATH, 0o600)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return {"configured": True}


def asset_hosting_status(server_domain: str, secure_root: Path) -> dict:
    full = server_domain not in {"", "localhost"} and secure_root.name != ""
    return {
        "mode": "Full HTTPS" if full else "Standard",
        "asset_hostname": (
            f"assets.{server_domain}" if server_domain else "Not configured"
        ),
        "token": token_status(),
        "caddy_sync": (
            "Replacing this token does not update an independently deployed "
            "Caddy container."
        ),
    }


def _api_get(path: str, token: str) -> dict:
    request = Request(
        f"{API_BASE}{path}",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": f"WonkEPG/{__version__}",
        },
    )
    try:
        with urlopen(request, timeout=20) as response:
            payload = json.loads(response.read(2 * 1024 * 1024))
    except HTTPError as error:
        if error.code in {401, 403}:
            raise CloudflareValidationError("Cloudflare permission check failed")
        raise CloudflareValidationError("Cloudflare API request failed")
    except (URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError):
        raise CloudflareValidationError("Cloudflare API request failed")
    if not isinstance(payload, dict) or not payload.get("success"):
        raise CloudflareValidationError("Cloudflare permission check failed")
    return payload


def validate_token(server_domain: str) -> dict:
    """Perform only safe reads; never return token or raw API errors."""
    token = _read_token()
    if not token:
        raise CloudflareValidationError("Cloudflare API token is not configured")
    verified = _api_get("/user/tokens/verify", token).get("result") or {}
    if verified.get("status") != "active":
        raise CloudflareValidationError("Cloudflare API token is not active")

    zones = _api_get(
        f"/zones?name={quote(server_domain)}&status=active&per_page=50", token
    ).get("result") or []
    zone = next(
        (item for item in zones if item.get("name") == server_domain), None
    )
    if not zone:
        raise CloudflareValidationError(
            "Token valid, but the configured zone is not readable"
        )
    zone_id = zone.get("id")
    account_id = (zone.get("account") or {}).get("id")
    if not zone_id or not account_id:
        raise CloudflareValidationError(
            "Token valid, but the Cloudflare account could not be identified"
        )

    try:
        _api_get(f"/zones/{zone_id}/dns_records?per_page=1", token)
    except CloudflareValidationError:
        raise CloudflareValidationError(
            "Token valid, but DNS access for the zone is missing"
        )
    try:
        tunnels = _api_get(
            f"/accounts/{account_id}/cfd_tunnel?is_deleted=false&per_page=1",
            token,
        ).get("result") or []
    except CloudflareValidationError:
        raise CloudflareValidationError(
            "Token valid, but Cloudflare Tunnel access is missing"
        )
    return {
        "valid": True,
        "domain": server_domain,
        "zone_read": True,
        "dns_read": True,
        "tunnel_read": True,
        "tunnel_count_sampled": len(tunnels),
        "permission_note": (
            "Read-only validation confirms zone, DNS, account, and tunnel "
            "access. Cloudflare does not expose write-policy introspection "
            "through token verification."
        ),
    }
