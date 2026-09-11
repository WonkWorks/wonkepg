from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from html import escape
import re
import xml.etree.ElementTree as ET
import json
import os
import shutil
import signal
import tempfile
import threading
from uuid import uuid4
from copy import deepcopy
from pathlib import Path
from urllib.parse import urlsplit
from app.cloudflare_settings import (
    CloudflareValidationError,
    asset_hosting_status,
    save_token as save_cloudflare_token,
    validate_token as validate_cloudflare_token,
)
from app.foundation import (
    FoundationValidationError,
    load_configured_foundation,
    load_foundation,
    validate_foundation,
)

from app.channel_merge import (
    EPGSHARE_XMLTV,
    EPGTALK_LOCAL_XMLTV,
    EPGTALK_XMLTV,
)
from app.epgtalk_catalog import combine_epgtalk_catalog, read_channel_definitions
from app.episode_resolver import (
    binding_key as episode_binding_key,
    refresh_confirmed_catalogs,
    resolver_status,
    save_binding as save_episode_binding,
    search_shows as search_episode_shows,
)
from app.full_build import (
    FULL_OUTPUT_XMLTV,
    build_full_xmltv,
    enrichment_ids_for_row,
    inspect_stale_mappings,
)
from app.logo_bootstrap import bootstrap_store, download_and_classify
from app.notifications import (
    error_handling_status,
    process_build_result,
    process_event,
    process_refresh_result,
    process_stale_result,
    save_error_handling,
    send_test_email,
)
from app.scheduler import (
    load_settings,
    record_build_status,
    save_schedule,
    scheduler_service,
    scheduler_status,
)
from app.source_manager import refresh_sources, source_status
from app.source_settings import (
    DEFAULT_BASELINE_SOURCE_ID,
    SourceValidationError,
    ensure_source_settings,
    load_source_settings,
    preserve_redacted_source_urls,
    public_source_settings,
    save_source_settings,
    validate_source_settings,
    validate_xmltv_url,
)
from app.baseline_sources import (
    inspect_baseline_mappings,
    load_schedule_catalogs,
    public_schedule_sources,
    read_schedule_catalog,
    schedule_source_registry,
)
from app.security import (
    CSRF_HEADER,
    SESSION_COOKIE,
    admin_auth_configured,
    cookie_secure,
    create_session,
    destroy_session,
    get_session,
    password_matches,
    session_minutes,
)
from app.ui import render_mapping_page
from app.version import __version__

app = FastAPI()

_PUBLIC_GET_ENDPOINTS = {"/status", "/xmltv.xml", "/login"}
_PUBLIC_PREFIXES = ("/static/", "/logos/")
_STATE_CHANGING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _public_request(request: Request) -> bool:
    return (
        request.method == "GET"
        and (
            request.url.path in _PUBLIC_GET_ENDPOINTS
            or request.url.path.startswith(_PUBLIC_PREFIXES)
        )
    ) or (request.method == "POST" and request.url.path == "/login")


def _login_page(message: str = "", status_code: int = 200) -> HTMLResponse:
    configured = admin_auth_configured()
    notice = (
        message
        or (
            "Admin authentication is not configured. Set "
            "WONKEPG_ADMIN_PASSWORD and restart WonkEPG."
            if not configured else "Sign in to administer WonkEPG."
        )
    )
    disabled = "" if configured else " disabled"
    html = f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>WonkEPG Admin Sign In</title>
<style>body{{font:16px system-ui;background:#16191d;color:#f4f6f8;display:grid;
place-items:center;min-height:100vh;margin:0}}main{{width:min(420px,calc(100% - 40px));
padding:28px;border:1px solid #505761;border-radius:10px;background:#22272e}}
label,input,button{{display:block;width:100%;box-sizing:border-box}}input,button{{
margin-top:8px;padding:11px}}small{{color:#bac2cc}}</style></head><body><main>
<h1>WonkEPG</h1><p>{escape(notice)}</p><form method="post" action="/login">
<label>Admin password<input type="password" name="password" autocomplete="current-password"
required{{disabled}}></label><button type="submit"{{disabled}}>Sign in</button></form>
<small>The XMLTV feed and health endpoint remain available without signing in.</small>
</main></body></html>'''
    return HTMLResponse(html, status_code=status_code)


@app.middleware("http")
async def admin_security_boundary(request: Request, call_next):
    if _public_request(request):
        response = await call_next(request)
    elif not admin_auth_configured():
        if request.url.path == "/":
            response = RedirectResponse("/login", status_code=303)
        else:
            response = JSONResponse(
                {"detail": "admin authentication is not configured"},
                status_code=503,
            )
    else:
        session = get_session(request.cookies.get(SESSION_COOKIE))
        if session is None:
            if request.url.path == "/":
                response = RedirectResponse("/login", status_code=303)
            else:
                response = JSONResponse(
                    {"detail": "admin authentication required"},
                    status_code=401,
                )
        elif (
            request.method in _STATE_CHANGING_METHODS
            and request.headers.get(CSRF_HEADER) != session.csrf_token
        ):
            response = JSONResponse(
                {"detail": "invalid CSRF token"}, status_code=403
            )
        else:
            request.state.admin_session = session
            response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault(
        "Content-Security-Policy", "frame-ancestors 'none'"
    )
    return response


@app.get("/login", response_class=HTMLResponse)
def admin_login_page():
    return _login_page()


@app.post("/login")
async def admin_login(request: Request):
    if not admin_auth_configured():
        return _login_page(status_code=503)
    form = await request.form()
    if not password_matches(form.get("password")):
        return _login_page("Invalid admin password.", status_code=401)
    session = create_session()
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(
        SESSION_COOKIE,
        session.token,
        max_age=session_minutes() * 60,
        httponly=True,
        secure=cookie_secure(),
        samesite="strict",
        path="/",
    )
    return response


@app.post("/logout")
def admin_logout(request: Request):
    destroy_session(request.cookies.get(SESSION_COOKIE))
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


CONFIG_DIR = Path("/app/config")
CHANNELS_JSON = CONFIG_DIR / "channels.json"
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
SERVER_DOMAIN = os.getenv("SERVER_DOMAIN", "localhost").strip().strip(".")
SECURE_ASSET_ROOT = Path(os.getenv("SECURE_ASSET_ROOT", "/secure-assets"))
LOGOS_DIR = SECURE_ASSET_ROOT / "logos"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
PRETTY_NAME_MAX_LENGTH = 50
MAX_LOGO_UPLOAD_BYTES = 5 * 1024 * 1024
CHANNEL_SCHEMA_VERSION = 2
INSTANCE_ID = uuid4().hex
_RESTART_LOCK = threading.Lock()

LOGOS_DIR.mkdir(parents=True, exist_ok=True)




def asset_base_url(server_domain: str | None = None) -> str:
    """Return the credential-free canonical HTTPS asset namespace."""
    domain = (server_domain or SERVER_DOMAIN).strip().strip(".")
    if not domain or "/" in domain or ":" in domain:
        raise ValueError("SERVER_DOMAIN must be a DNS hostname")
    return f"https://assets.{domain}/wonkepg"


def canonical_logo_url(filename: str) -> str:
    return f"{asset_base_url()}/logos/{filename}"



@app.on_event("startup")
def start_scheduler():
    ensure_source_settings()
    scheduler_service.start()

@app.on_event("shutdown")
def stop_scheduler():
    scheduler_service.stop()


def parse_foundation_m3u(location: str) -> dict:
    """Parse one configured, source-agnostic curated channel foundation."""
    return load_foundation(location)


def parse_enrichment_xmltv(path: str | Path) -> list[dict]:
    """Read one enrichment channel catalog without loading programmes."""
    return read_channel_definitions(path, "epgshare")


def _bootstrap_identity(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def discover_default_baseline_matches():
    """Match the configured foundation against the default schedule catalog."""
    foundation_channels = load_configured_foundation()["channels"]
    settings = load_source_settings()
    source_id = settings["baseline"]["default_source"]
    catalog = load_schedule_catalogs(settings).get(source_id)
    schedule_channels = catalog["channels"] if catalog and catalog["available"] else []
    results = []
    unique_matches = ambiguous_matches = unmatched = 0

    for foundation_channel in foundation_channels:
        foundation_id = _bootstrap_identity(
            foundation_channel.get("channel_id")
        )
        foundation_name = _bootstrap_identity(foundation_channel.get("name"))
        id_matches = [
            channel for channel in schedule_channels
            if foundation_id
            and _bootstrap_identity(channel.get("channel_id")) == foundation_id
        ]
        name_matches = [
            channel for channel in schedule_channels
            if foundation_name
            and foundation_name in {
                _bootstrap_identity(name)
                for name in channel.get("display_names", [])
            }
        ]
        candidates = id_matches or name_matches
        match_type = "channel_id" if id_matches else "display_name"
        if len(candidates) == 1:
            candidate = candidates[0]
            status = "unique_match"
            unique_matches += 1
            baseline_channel_id = candidate["channel_id"]
        elif len(candidates) > 1:
            status = "ambiguous"
            ambiguous_matches += 1
            baseline_channel_id = None
        else:
            status = "unmatched"
            unmatched += 1
            baseline_channel_id = None
        results.append({
            "foundation_number": str(foundation_channel.get("number") or ""),
            "foundation_name": foundation_channel.get("name") or "",
            "status": status,
            "match_type": match_type if candidates else None,
            "baseline_source": source_id,
            "baseline_channel_id": baseline_channel_id,
            "candidates": [
                {
                    "channel_id": candidate["channel_id"],
                    "display_name": candidate["display_name"],
                }
                for candidate in candidates
            ],
            "foundation_channel": foundation_channel,
        })

    return {
        "total_foundation_channels": len(foundation_channels),
        "baseline_source": source_id,
        "source_available": bool(catalog and catalog["available"]),
        "unique_matches": unique_matches,
        "ambiguous_matches": ambiguous_matches,
        "unmatched": unmatched,
        "results": results,
    }


def build_channels_matrix():
    """Build a new matrix from foundation + default schedule exact matches."""
    discovery = discover_default_baseline_matches()
    matrix = []
    default_source = discovery["baseline_source"]
    for result in discovery["results"]:
        foundation_channel = result["foundation_channel"]
        baseline_channel_id = result.get("baseline_channel_id")
        matrix.append({
            "channel_id": foundation_channel.get("channel_id"),
            "number": foundation_channel.get("number"),
            "name": foundation_channel.get("name"),
            "pretty_name": foundation_channel.get("name"),
            "group": foundation_channel.get("group"),
            "logo": foundation_channel.get("logo"),
            "baseline": {
                "source": default_source,
                "channel_id": baseline_channel_id,
                "status": "valid" if baseline_channel_id else "missing",
            },
            "enrichment_1": None,
            "enrichment_2": None,
        })
    return {
        "schema_version": CHANNEL_SCHEMA_VERSION,
        "count": len(matrix),
        "channels": matrix,
    }


def save_channels_matrix(matrix: dict) -> bool:
    """Save the channels matrix to file. Returns True if saved, False if file already exists."""
    if CHANNELS_JSON.exists():
        return False
    
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    
    with open(CHANNELS_JSON, 'w', encoding='utf-8') as f:
        json.dump(matrix, f, indent=2)
    
    return True


def load_channels_matrix() -> dict:
    """Load the matrix and apply idempotent, non-rematching migrations."""
    if not CHANNELS_JSON.exists():
        return None
    
    with open(CHANNELS_JSON, 'r', encoding='utf-8') as f:
        matrix = json.load(f)

    migrated = matrix.get("schema_version") != CHANNEL_SCHEMA_VERSION
    default_source = DEFAULT_BASELINE_SOURCE_ID
    for row in matrix.get("channels", []):
        if "pretty_name" not in row:
            row["pretty_name"] = row.get("name")
            migrated = True
        baseline = row.get("baseline")
        if isinstance(baseline, dict) and (
            baseline.get("source") == "hive"
            or (baseline.get("channel_id") and not baseline.get("source"))
        ):
            baseline["source"] = default_source
            migrated = True
    if migrated:
        backup = CHANNELS_JSON.with_name("channels.json.pre-0.6.0.bak")
        if not backup.exists():
            shutil.copy2(CHANNELS_JSON, backup)
        matrix["schema_version"] = CHANNEL_SCHEMA_VERSION
        atomic_write_channels_matrix(matrix)
    return matrix


def atomic_write_channels_matrix(matrix: dict) -> None:
    """Serialize safely before atomically replacing the saved matrix."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    existing_metadata = CHANNELS_JSON.stat()
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".channels.", suffix=".tmp", dir=CONFIG_DIR
    )
    os.fchmod(descriptor, existing_metadata.st_mode & 0o777)
    os.fchown(descriptor, existing_metadata.st_uid, existing_metadata.st_gid)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(matrix, output, indent=2)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_path, CHANNELS_JSON)
        directory = os.open(CONFIG_DIR, os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise

def _normalized_identity(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def _baseline_for_discovery(result: dict | None) -> dict:
    default_source = load_source_settings()["baseline"]["default_source"]
    if (
        result
        and result.get("status") == "unique_match"
        and result.get("baseline_channel_id")
    ):
        return {
            "source": default_source,
            "channel_id": result["baseline_channel_id"],
            "status": "valid",
        }
    return {
        "source": default_source,
        "channel_id": None,
        "status": "missing",
    }


def compare_foundation_drift(
    matrix: dict,
    foundation_data: dict | None = None,
    discovery: dict | None = None,
) -> dict:
    """Compare configured foundation metadata with persisted rows read-only."""
    if foundation_data is None:
        foundation_data = load_configured_foundation()
    foundation = foundation_data.get("channels", [])
    persisted = {
        str(row.get("number")): row for row in matrix.get("channels", [])
    }
    live = {
        str(row.get("number")): row
        for row in foundation
        if row.get("number") not in (None, "")
    }

    if discovery is None:
        discovery = discover_default_baseline_matches()
    discovery_by_number = {
        str(result.get("foundation_number")): result
        for result in discovery.get("results", [])
    }

    inactive = []
    for number, foundation_row in live.items():
        if number in persisted:
            continue
        name = foundation_row.get("name") or ""
        inactive.append(
            {
                "channel_id": foundation_row.get("channel_id"),
                "number": number,
                "name": name,
                "pretty_name": name,
                "group": foundation_row.get("group"),
                "logo": foundation_row.get("logo"),
                "baseline": _baseline_for_discovery(
                    discovery_by_number.get(number)
                ),
                "enrichment_1": None,
                "enrichment_2": None,
            }
        )

    warnings = {}
    for number, persisted_row in persisted.items():
        foundation_row = live.get(number)
        if foundation_row is None:
            warnings[number] = "missing_from_foundation"
            continue
        persisted_channel_id = _normalized_identity(
            persisted_row.get("channel_id")
        )
        foundation_channel_id = _normalized_identity(
            foundation_row.get("channel_id")
        )
        name_changed = _normalized_identity(
            persisted_row.get("name")
        ) != _normalized_identity(foundation_row.get("name"))
        id_changed = bool(
            persisted_channel_id
            and foundation_channel_id
            and persisted_channel_id != foundation_channel_id
        )
        if name_changed or id_changed:
            warnings[number] = "identity_changed"

    inactive.sort(
        key=lambda row: (
            int(row["number"]) if row["number"].isdigit() else 10**12,
            row["number"],
        )
    )
    return {"inactive": inactive, "warnings": warnings}


CANONICAL_LOGO_FILENAME = re.compile(r"^[0-9]{5}-logo[.]png$")


def legacy_managed_logo_filename(url: object) -> str | None:
    """Identify only legacy WonkEPG /logos canonical PNG URLs."""
    if not isinstance(url, str):
        return None
    parsed = urlsplit(url)
    path = parsed.path
    if parsed.scheme or parsed.netloc:
        try:
            port = parsed.port
        except ValueError:
            return None
        if parsed.scheme not in {"http", "https"} or port != 34500:
            return None
    elif not path.startswith("/"):
        return None
    prefix = "/logos/"
    if not path.startswith(prefix) or "/" in path[len(prefix):]:
        return None
    filename = path[len(prefix):]
    return filename if CANONICAL_LOGO_FILENAME.fullmatch(filename) else None


def migrate_legacy_logo_urls(matrix: dict) -> dict:
    """Rewrite legacy managed logo URLs once, leaving all other fields intact."""
    scanned = 0
    migrated = []
    for row in matrix.get("channels", []):
        scanned += 1
        filename = legacy_managed_logo_filename(row.get("logo"))
        if not filename:
            continue
        new_url = canonical_logo_url(filename)
        if row.get("logo") == new_url:
            continue
        row["logo"] = new_url
        migrated.append({
            "number": str(row.get("number") or ""),
            "filename": filename,
            "logo": new_url,
        })
    return {
        "rows_scanned": scanned,
        "rows_migrated": len(migrated),
        "rows_skipped": scanned - len(migrated),
        "migrated_channels": migrated,
    }


def atomic_write_logo(path: Path, contents: bytes) -> None:
    """Write an uploaded logo without exposing a partially written file."""
    LOGOS_DIR.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=LOGOS_DIR
    )
    os.fchmod(descriptor, 0o644)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(contents)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_path, path)
        directory = os.open(LOGOS_DIR, os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


def atomic_write_new_logo(path: Path, contents: bytes) -> None:
    """Atomically publish a new logo without ever replacing an existing one."""
    LOGOS_DIR.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=LOGOS_DIR
    )
    os.fchmod(descriptor, 0o644)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(contents)
            output.flush()
            os.fsync(output.fileno())
        os.link(temporary_path, path)
        temporary_path.unlink()
        directory = os.open(LOGOS_DIR, os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


@app.post("/logos/migrate-legacy-urls")
def migrate_persisted_legacy_logo_urls():
    """Explicitly migrate only old WonkEPG-managed /logos PNG URLs."""
    matrix = load_channels_matrix()
    if matrix is None:
        raise HTTPException(status_code=503, detail="channels.json missing")
    report = migrate_legacy_logo_urls(matrix)
    if report["rows_migrated"]:
        atomic_write_channels_matrix(matrix)
    return {"status": "migrated", **report}


@app.post("/logos/bootstrap/scan")
def scan_logos_for_bootstrap():
    """Download and classify persisted logo URLs without changing files."""
    matrix = load_channels_matrix()
    if matrix is None:
        raise HTTPException(status_code=503, detail="channels.json missing")
    return bootstrap_store.scan(matrix, f"{asset_base_url()}/logos")


@app.post("/logos/bootstrap/apply")
def apply_logo_bootstrap(payload: dict):
    """Revalidate one dry run and migrate only its valid PNG candidates."""
    scan_id = payload.get("scan_id")
    if not isinstance(scan_id, str) or not scan_id:
        raise HTTPException(status_code=400, detail="valid scan_id required")
    candidates = bootstrap_store.candidates(scan_id)
    if candidates is None:
        raise HTTPException(status_code=409, detail="scan expired; scan again")
    if not bootstrap_store.apply_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="logo bootstrap already running")

    written_paths = []
    migrated = []
    skipped = []
    try:
        matrix = load_channels_matrix()
        if matrix is None:
            raise HTTPException(status_code=503, detail="channels.json missing")
        rows = {str(row.get("number")): row for row in matrix["channels"]}

        for candidate in candidates:
            number = candidate["number"]
            row = rows.get(number)
            current_logo = row.get("logo") if row else None
            if row is None or current_logo != candidate["logo"]:
                skipped.append(
                    {
                        "number": number,
                        "name": candidate["name"],
                        "reason": "logo changed since scan",
                    }
                )
                continue

            filename = f"{int(number):05d}-logo.png"
            logo_path = LOGOS_DIR / filename
            if logo_path.exists():
                skipped.append(
                    {
                        "number": number,
                        "name": candidate["name"],
                        "reason": "canonical logo file already exists",
                    }
                )
                continue

            downloaded = download_and_classify(current_logo)
            if downloaded.category != "valid_png":
                skipped.append(
                    {
                        "number": number,
                        "name": candidate["name"],
                        "reason": downloaded.error or downloaded.category,
                    }
                )
                continue

            try:
                atomic_write_new_logo(logo_path, downloaded.contents)
            except FileExistsError:
                skipped.append(
                    {
                        "number": number,
                        "name": candidate["name"],
                        "reason": "canonical logo file already exists",
                    }
                )
                continue
            except Exception as error:
                skipped.append(
                    {
                        "number": number,
                        "name": candidate["name"],
                        "reason": f"logo write failed ({type(error).__name__})",
                    }
                )
                continue

            written_paths.append(logo_path)
            logo_url = canonical_logo_url(filename)
            row["logo"] = logo_url
            migrated.append(
                {
                    "number": number,
                    "name": candidate["name"],
                    "filename": filename,
                    "logo": logo_url,
                    "bytes": len(downloaded.contents),
                }
            )

        if migrated:
            try:
                atomic_write_channels_matrix(matrix)
            except Exception as error:
                for path in written_paths:
                    path.unlink(missing_ok=True)
                raise HTTPException(
                    status_code=500, detail="logo bootstrap config update failed"
                ) from error

        return {
            "status": "applied",
            "scan_id": scan_id,
            "candidates": len(candidates),
            "migrated": len(migrated),
            "skipped": len(skipped),
            "migrated_channels": migrated,
            "skipped_channels": skipped,
        }
    finally:
        bootstrap_store.apply_lock.release()


@app.get("/", response_class=HTMLResponse)
def mapping_ui(request: Request):
    matrix = load_channels_matrix()
    if matrix is None:
        # First-run UI: settings and inactive foundation channels remain usable
        # before the administrator explicitly creates the runtime matrix.
        matrix = {"schema_version": CHANNEL_SCHEMA_VERSION, "count": 0, "channels": []}
    drift = compare_foundation_drift(matrix)
    epgshare = parse_enrichment_xmltv(EPGSHARE_XMLTV)
    epgtalk_catalog = combine_epgtalk_catalog(
        EPGTALK_XMLTV, EPGTALK_LOCAL_XMLTV
    )
    epgtalk = epgtalk_catalog["channels"]
    epgtalk_available = {
        item["channel_id"] for item in epgtalk
    } | {
        item["channel_id"] for item in epgtalk_catalog["conflicts"]
    }
    schedule_catalogs = load_schedule_catalogs()
    stale = inspect_stale_mappings(
        matrix,
        {item["channel_id"] for item in epgshare},
        epgtalk_available,
        schedule_catalogs,
    )
    operations = operational_status(
        matrix=matrix,
        stale=stale,
        foundation_warning_count=len(drift["warnings"]),
    )
    return render_mapping_page(
        matrix, epgshare, epgtalk, drift, operations,
        source_settings=load_source_settings(), version=__version__,
        schedule_sources=public_schedule_sources(schedule_catalogs),
        schedule_catalogs=schedule_catalogs,
        csrf_token=request.state.admin_session.csrf_token,
    )


def operational_status(
    matrix: dict | None = None,
    stale: dict | None = None,
    foundation_warning_count: int | None = None,
) -> dict:
    """Return a compact operational snapshot without network access."""
    if matrix is None:
        matrix = load_channels_matrix()
    settings = load_settings()
    last_build = settings.get("last_xmltv_build")
    if not isinstance(last_build, dict):
        last_build = None
    stale_available = True
    if stale is None and matrix is not None:
        try:
            stale = inspect_stale_mappings(matrix)
        except Exception:
            stale_available = False
            stale = {
                "stale_baseline_count": (
                    last_build.get("stale_baseline_count", 0)
                    if last_build else 0
                ),
                "stale_epgshare_count": (
                    last_build.get("stale_epgshare_count", 0)
                    if last_build else 0
                ),
                "stale_epgtalk_count": (
                    last_build.get("stale_epgtalk_count", 0)
                    if last_build else 0
                ),
                "stale_baseline_mappings": [],
                "stale_epgshare_mappings": [],
                "stale_epgtalk_mappings": [],
            }
    stale = stale or {
        "stale_baseline_count": 0,
        "stale_baseline_mappings": [],
        "stale_epgshare_count": 0,
        "stale_epgtalk_count": 0,
        "stale_epgshare_mappings": [],
        "stale_epgtalk_mappings": [],
    }
    sources = source_status()
    refresh_times = [
        item.get("last_successful_refresh")
        for item in sources.get("sources", [])
        if item.get("last_successful_refresh")
    ]
    schedule = scheduler_status()
    return {
        **stale,
        "stale_status_available": stale_available,
        "foundation_drift_warnings": foundation_warning_count,
        "channels_with_no_schedule": (
            last_build.get("channels_with_no_schedule")
            if last_build else None
        ),
        "last_source_refresh": max(refresh_times) if refresh_times else None,
        "last_xmltv_build": last_build,
        "scheduler_next_run": schedule.get("next_run_at"),
    }


@app.get("/operations/status")
def get_operational_status():
    """Return the compact warning/status panel data."""
    return operational_status()


def _terminate_for_restart() -> None:
    try:
        os.kill(os.getpid(), signal.SIGTERM)
    except OSError:
        _RESTART_LOCK.release()


@app.get("/status")
def get_status():
    return {
        "service": "WonkEPG",
        "status": "running",
        "version": __version__,
        "instance_id": INSTANCE_ID,
    }


@app.post("/maintenance/restart", status_code=202)
def restart_wonkepg():
    """Gracefully stop only this PID after the response can be delivered."""
    if not _RESTART_LOCK.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="WonkEPG restart in progress")
    timer = threading.Timer(1.0, _terminate_for_restart)
    timer.daemon = True
    timer.start()
    return {
        "status": "restarting",
        "instance_id": INSTANCE_ID,
    }


@app.get("/channels")
def get_channels():
    return load_configured_foundation()


@app.get("/bootstrap/default-schedule")
def bootstrap_default_schedule():
    """Preview new-install foundation/default-schedule matching read-only."""
    return discover_default_baseline_matches()


@app.post("/bootstrap/channels")
def bootstrap_channels():
    """Bootstrap the persistent channel matrix if it doesn't exist."""
    if CHANNELS_JSON.exists():
        return {
            "status": "already_initialized",
            "message": "channels.json already exists, no action taken"
        }
    
    # Build and save the matrix
    matrix = build_channels_matrix()
    saved = save_channels_matrix(matrix)
    
    if saved:
        return {
            "status": "created",
            "message": f"channels.json created with {matrix['count']} channels",
            "count": matrix['count'],
            "valid_baselines": sum(1 for ch in matrix['channels'] if ch['baseline']['status'] == 'valid'),
            "missing_baselines": sum(1 for ch in matrix['channels'] if ch['baseline']['status'] == 'missing')
        }
    else:
        return {
            "status": "error",
            "message": "failed to save channels.json"
        }


@app.get("/config/channels")
def get_config_channels():
    """Return the persisted channel matrix."""
    matrix = load_channels_matrix()
    if matrix is None:
        return {
            "status": "not_initialized",
            "message": "channels.json does not exist, run POST /bootstrap/channels first"
        }
    return matrix


@app.post("/channels/{number}/logo")
async def upload_channel_logo(
    number: str, inactive: bool = False, file: UploadFile = File(...)
):
    """Store an exact PNG upload and select it for one Wonk channel."""
    matrix = load_channels_matrix()
    if matrix is None:
        raise HTTPException(status_code=503, detail="channels.json missing")

    if not number.isascii() or not number.isdecimal():
        raise HTTPException(status_code=404, detail="channel not found")
    numeric_number = int(number)
    if numeric_number < 0 or numeric_number > 99999:
        raise HTTPException(status_code=404, detail="channel not found")
    normalized_number = str(numeric_number)
    row = next(
        (
            channel
            for channel in matrix["channels"]
            if str(channel.get("number")) == normalized_number
        ),
        None,
    )
    if inactive:
        row = next(
            (
                channel
                for channel in compare_foundation_drift(matrix)["inactive"]
                if channel["number"] == normalized_number
            ),
            None,
        )
    if row is None or (inactive and normalized_number in {
        str(channel.get("number")) for channel in matrix["channels"]
    }):
        raise HTTPException(status_code=404, detail="channel not found")

    original_filename = file.filename or ""
    if Path(original_filename).suffix.casefold() != ".png":
        raise HTTPException(
            status_code=400, detail="only .png files are accepted"
        )
    try:
        contents = await file.read(MAX_LOGO_UPLOAD_BYTES + 1)
    finally:
        await file.close()
    if len(contents) > MAX_LOGO_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413, detail="PNG upload must not exceed 5 MiB"
        )
    if not contents.startswith(PNG_SIGNATURE):
        raise HTTPException(
            status_code=400, detail="file does not have a valid PNG signature"
        )

    filename = f"{numeric_number:05d}-logo.png"
    logo_path = LOGOS_DIR / filename
    logo_url = canonical_logo_url(filename)
    previous_contents = logo_path.read_bytes() if logo_path.exists() else None
    atomic_write_logo(logo_path, contents)
    row["logo"] = logo_url
    if not inactive:
        try:
            atomic_write_channels_matrix(matrix)
        except Exception as error:
            try:
                if previous_contents is None:
                    logo_path.unlink(missing_ok=True)
                else:
                    atomic_write_logo(logo_path, previous_contents)
            except Exception:
                pass
            raise HTTPException(
                status_code=500, detail="logo config update failed"
            ) from error

    return {
        "status": "uploaded",
        "number": normalized_number,
        "filename": filename,
        "logo": logo_url,
        "bytes": len(contents),
    }


def _validated_pretty_name(value: object, number: str) -> str:
    if not isinstance(value, str):
        raise HTTPException(
            status_code=400, detail=f"invalid Pretty Name for {number}"
        )
    if len(value) > PRETTY_NAME_MAX_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"Pretty Name for {number} exceeds 50 characters",
        )
    return value


def _validated_enrichments(
    item: dict,
    number: str,
    share_ids: set,
    talk_ids: set,
    preserved_ids: tuple[str | None, str | None] = (None, None),
) -> tuple[dict | None, dict | None]:
    share = item.get("enrichment_1") or None
    talk = item.get("enrichment_2") or None
    if share is not None and (
        not isinstance(share, str)
        or (share not in share_ids and share != preserved_ids[0])
    ):
        raise HTTPException(
            status_code=400, detail=f"invalid Enrichment 1 channel ID for {number}"
        )
    if talk is not None and (
        not isinstance(talk, str)
        or (talk not in talk_ids and talk != preserved_ids[1])
    ):
        raise HTTPException(
            status_code=400, detail=f"invalid Enrichment 2 channel ID for {number}"
        )
    return (
        {"source": "epgshare", "channel_id": share} if share else None,
        {"source": "epgtalk", "channel_id": talk} if talk else None,
    )


def _validated_baseline(
    selection: object,
    number: str,
    catalogs: dict,
    preserved: object = None,
) -> dict | None:
    if selection is None:
        return None
    if not isinstance(selection, dict):
        raise HTTPException(
            status_code=400, detail=f"invalid baseline selection for {number}"
        )
    source_id = selection.get("source")
    channel_id = selection.get("channel_id")
    if not isinstance(source_id, str) or not isinstance(channel_id, str):
        raise HTTPException(
            status_code=400, detail=f"invalid baseline selection for {number}"
        )
    normalized = {"source": source_id, "channel_id": channel_id}
    preserved_pair = None
    if isinstance(preserved, dict):
        preserved_pair = {
            "source": preserved.get("source"),
            "channel_id": preserved.get("channel_id"),
        }
    source = catalogs.get(source_id)
    if normalized != preserved_pair and (
        source is None
        or not source["available"]
        or channel_id not in source["channel_ids"]
    ):
        raise HTTPException(
            status_code=400, detail=f"invalid baseline selection for {number}"
        )
    return normalized


@app.post("/config/channels/mappings")
def save_channel_changes(payload: dict):
    """Atomically save active edits and checked discovered activations."""
    current = load_channels_matrix()
    if current is None:
        raise HTTPException(status_code=503, detail="channels.json missing")
    mappings = payload.get("mappings")
    activations = payload.get("activations", [])
    if not isinstance(mappings, list):
        raise HTTPException(status_code=400, detail="mappings must be a list")
    if not isinstance(activations, list):
        raise HTTPException(status_code=400, detail="activations must be a list")

    share_ids = {
        item["channel_id"]
        for item in parse_enrichment_xmltv(EPGSHARE_XMLTV)
    }
    talk_ids = {
        item["channel_id"]
        for item in combine_epgtalk_catalog(
            EPGTALK_XMLTV, EPGTALK_LOCAL_XMLTV
        )["channels"]
    }
    schedule_catalogs = load_schedule_catalogs()
    matrix = deepcopy(current)
    rows = {str(row.get("number")): row for row in matrix["channels"]}
    planned_active = []
    seen = set()
    for mapping in mappings:
        if not isinstance(mapping, dict):
            raise HTTPException(status_code=400, detail="invalid mapping row")
        number = str(mapping.get("number", ""))
        if number not in rows or number in seen:
            raise HTTPException(
                status_code=400, detail=f"invalid or duplicate channel {number}"
            )
        seen.add(number)
        pretty_name = (
            _validated_pretty_name(mapping["pretty_name"], number)
            if "pretty_name" in mapping
            else rows[number].get("pretty_name", rows[number].get("name", ""))
        )
        active_mapping = dict(mapping)
        if "baseline" not in active_mapping:
            active_mapping["baseline"] = rows[number].get("baseline")
        for field in ("enrichment_1", "enrichment_2"):
            if field not in active_mapping:
                current_selection = rows[number].get(field)
                active_mapping[field] = (
                    current_selection.get("channel_id")
                    if isinstance(current_selection, dict)
                    else current_selection
                )
        enrichment_1, enrichment_2 = _validated_enrichments(
            active_mapping,
            number,
            share_ids,
            talk_ids,
            enrichment_ids_for_row(rows[number]),
        )
        baseline = _validated_baseline(
            active_mapping.get("baseline"), number, schedule_catalogs,
            rows[number].get("baseline"),
        )
        planned_active.append(
            (rows[number], pretty_name, baseline, enrichment_1, enrichment_2)
        )

    inactive_by_number = {}
    if activations:
        inactive_by_number = {
            row["number"]: row
            for row in compare_foundation_drift(current)["inactive"]
        }
    planned_activations = []
    activation_seen = set()
    for activation in activations:
        if not isinstance(activation, dict):
            raise HTTPException(status_code=400, detail="invalid activation row")
        number = str(activation.get("number", ""))
        if number not in inactive_by_number or number in activation_seen:
            raise HTTPException(
                status_code=409,
                detail=f"channel {number} is no longer an inactive discovery",
            )
        activation_seen.add(number)
        row = deepcopy(inactive_by_number[number])
        row["pretty_name"] = _validated_pretty_name(
            activation.get("pretty_name", row["name"]), number
        )
        logo = activation.get("logo", row.get("logo"))
        if logo is not None and not isinstance(logo, str):
            raise HTTPException(status_code=400, detail=f"invalid logo for {number}")
        row["logo"] = logo
        row["baseline"] = _validated_baseline(
            activation.get("baseline", row.get("baseline")),
            number,
            schedule_catalogs,
            row.get("baseline"),
        )
        row["enrichment_1"], row["enrichment_2"] = _validated_enrichments(
            activation, number, share_ids, talk_ids
        )
        planned_activations.append(row)

    changed = 0
    for row, pretty_name, baseline, enrichment_1, enrichment_2 in planned_active:
        if (
            row.get("pretty_name") != pretty_name
            or row.get("baseline") != baseline
            or row.get("enrichment_1") != enrichment_1
            or row.get("enrichment_2") != enrichment_2
        ):
            changed += 1
        row["pretty_name"] = pretty_name
        row["baseline"] = baseline
        row["enrichment_1"] = enrichment_1
        row["enrichment_2"] = enrichment_2
    matrix["channels"].extend(planned_activations)
    matrix["count"] = len(matrix["channels"])

    try:
        atomic_write_channels_matrix(matrix)
    except Exception as error:
        raise HTTPException(status_code=500, detail="mapping save failed") from error
    return {
        "status": "saved",
        "rows_received": len(planned_active),
        "rows_changed": changed,
        "channels_activated": len(planned_activations),
        "count": matrix["count"],
    }



@app.get("/sources/epgshare/channels")
def get_epgshare_channels():
    """Return Enrichment 1 channel definitions."""
    channels = parse_enrichment_xmltv(EPGSHARE_XMLTV)
    
    # Sort by display_name alphabetically
    sorted_channels = sorted(channels, key=lambda ch: ch['display_name'] or '')
    
    return {
        "source": "epgshare",
        "count": len(sorted_channels),
        "channels": sorted_channels
    }


@app.get("/sources/epgtalk/channels")
def get_epgtalk_channels():
    """Return Enrichment 2 channel definitions from its configured feeds."""
    catalog = combine_epgtalk_catalog(EPGTALK_XMLTV, EPGTALK_LOCAL_XMLTV)
    return {
        "source": "epgtalk",
        "count": catalog["combined_count"],
        "national_count": catalog["national_count"],
        "local_count": catalog["local_count"],
        "conflict_count": len(catalog["conflicts"]),
        "conflicts": catalog["conflicts"],
        "channels": catalog["channels"],
    }


@app.get("/sources/schedule")
def get_schedule_sources():
    """Return credential-free baseline source availability metadata."""
    return {"sources": public_schedule_sources()}


@app.get("/sources/schedule/{source_id}/channels")
def get_schedule_source_channels(source_id: str):
    """Lazily return one sanitized baseline source catalog."""
    registry = schedule_source_registry()
    source = registry.get(source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="schedule source not found")
    try:
        channels = read_schedule_catalog(source["path"])
    except (OSError, ET.ParseError):
        raise HTTPException(
            status_code=503, detail="schedule source cache unavailable"
        )
    return {
        "source_id": source_id,
        "provider_name": source["provider_name"],
        "count": len(channels),
        "channels": channels,
    }


@app.get("/sources/status")
def get_source_status():
    """Return cached XMLTV source status without downloading."""
    return source_status()


@app.post("/sources/refresh")
def refresh_all_sources():
    """Refresh all XMLTV inputs while preserving last-known-good files."""
    try:
        result = refresh_sources()
    except Exception as error:
        try:
            process_event(
                "source_refresh",
                True,
                {
                    "status": (
                        "source refresh failed "
                        f"({type(error).__name__})"
                    ),
                    "affected": [],
                },
            )
        except Exception:
            pass
        raise
    try:
        process_refresh_result(result)
        matrix = load_channels_matrix()
        if matrix is not None:
            process_stale_result(inspect_stale_mappings(matrix))
    except Exception:
        pass
    return result


@app.get("/settings/sources")
def get_source_settings():
    """Return editable source settings only to the local admin UI."""
    try:
        return public_source_settings()
    except ValueError as error:
        raise HTTPException(status_code=500, detail=str(error)) from error


@app.post("/settings/sources")
def update_source_settings(payload: dict):
    """Atomically persist the three logical XMLTV source roles."""
    try:
        current = load_source_settings()
        proposed = validate_source_settings(
            preserve_redacted_source_urls(payload, current)
        )
        matrix = load_channels_matrix()
        referenced = {
            row.get("baseline", {}).get("source")
            for row in (matrix or {}).get("channels", [])
            if isinstance(row.get("baseline"), dict)
            and row.get("baseline", {}).get("channel_id")
        }
        removed_referenced = referenced - set(proposed["schedule_sources"])
        if removed_referenced:
            raise ValueError(
                "cannot remove referenced schedule source: "
                + ", ".join(sorted(removed_referenced))
            )
        proposed_ids = set(proposed["schedule_sources"])
        if current["baseline"]["default_source"] not in proposed_ids:
            raise ValueError("current default schedule source cannot be removed")
        return public_source_settings(save_source_settings(proposed))
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.get("/settings/episode-resolvers")
def get_episode_resolvers():
    """Return cache-only resolver bindings, candidates, and last-build status."""
    return resolver_status()


@app.post("/settings/episode-resolvers/search")
def search_episode_resolvers(payload: dict):
    """Run an explicit TVmaze onboarding search; never used by guide builds."""
    try:
        return {
            "results": search_episode_shows(
                payload.get("query"), payload.get("provider", "tvmaze")
            )
        }
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(
            status_code=502,
            detail=f"episode resolver search failed ({type(error).__name__})",
        ) from error


@app.post("/settings/episode-resolvers/bind")
def bind_episode_resolver(payload: dict):
    """Persist one explicit user-confirmed channel/title to provider show ID."""
    channel_id = payload.get("channel_id")
    title = payload.get("title")
    if not isinstance(channel_id, str) or not isinstance(title, str):
        raise HTTPException(status_code=400, detail="channel and title are required")
    status = resolver_status()
    current_keys = {
        item["key"] for item in status["unresolved_shows"]
    } | {
        item["key"] for item in status["bindings"]
    }
    if episode_binding_key(channel_id, title) not in current_keys:
        raise HTTPException(
            status_code=400,
            detail="programme is not a current episode-only resolver candidate",
        )
    try:
        binding = save_episode_binding(
            channel_id=channel_id,
            title=title,
            provider=payload.get("provider", "tvmaze"),
            show_id=payload.get("show_id"),
            canonical_name=payload.get("canonical_name", ""),
            mode=payload.get("mode", "strict_episode_match"),
            timezone_name=payload.get("timezone", "America/New_York"),
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return {"saved": True, "binding": binding, "status": resolver_status()}


@app.post("/settings/episode-resolvers/refresh")
def refresh_episode_resolvers():
    """Explicitly refresh confirmed catalogs; builds never call this endpoint."""
    return refresh_confirmed_catalogs()


@app.post("/settings/foundation/validate")
def validate_proposed_foundation(payload: dict):
    """Read-only curated-M3U validation; never saves or changes mappings."""
    location = payload.get("configured_m3u")
    if not isinstance(location, str):
        raise HTTPException(
            status_code=400, detail="Configured M3U is required"
        )
    try:
        return validate_foundation(location)
    except FoundationValidationError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.post("/settings/sources/validate")
def validate_proposed_source(payload: dict):
    """Advisory live validation that never persists the proposed URL."""
    url = payload.get("url")
    if not isinstance(url, str):
        raise HTTPException(status_code=400, detail="source URL is required")
    try:
        return validate_xmltv_url(url)
    except SourceValidationError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.get("/settings/asset-hosting")
def get_asset_hosting_settings():
    """Return non-sensitive HTTPS asset mode and token readiness."""
    return asset_hosting_status(SERVER_DOMAIN, SECURE_ASSET_ROOT)


@app.post("/settings/asset-hosting/token")
def replace_asset_hosting_token(payload: dict):
    """Replace the local token without ever returning its value."""
    try:
        return save_cloudflare_token(payload.get("token"))
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.post("/settings/asset-hosting/validate")
def validate_asset_hosting_token():
    """Perform only safe Cloudflare API reads with sanitized failures."""
    try:
        return validate_cloudflare_token(SERVER_DOMAIN)
    except CloudflareValidationError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.get("/settings/schedule")
def get_schedule():
    """Return persisted scheduler configuration and runtime state."""
    return scheduler_status()


@app.post("/settings/schedule")
def update_schedule(payload: dict):
    """Validate and persist an anchored schedule."""
    try:
        save_schedule(payload)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    scheduler_service.reschedule()
    return scheduler_status()


@app.get("/settings/error-handling")
def get_error_handling():
    """Return preferences and non-sensitive SMTP readiness."""
    return error_handling_status()


@app.post("/settings/error-handling")
def update_error_handling(payload: dict):
    """Validate and persist notification preferences."""
    try:
        return save_error_handling(payload)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.post("/settings/error-handling/test")
def test_error_handling_email():
    """Send a test without modifying preferences or event state."""
    try:
        return send_test_email()
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(
            status_code=502,
            detail=f"test email delivery failed ({type(error).__name__})",
        ) from error


@app.post("/build")
def build_xmltv():
    """Build combined XMLTV output for the persisted channel matrix."""
    matrix = load_channels_matrix()
    if matrix is None:
        return {
            "status": "not_initialized",
            "message": "channels.json does not exist",
        }
    try:
        result = build_full_xmltv(matrix)
    except Exception as error:
        safe_error = f"XMLTV build failed ({type(error).__name__})"
        try:
            record_build_status(None, "manual", safe_error)
            process_event(
                "build",
                True,
                {"status": safe_error, "affected": []},
            )
        except Exception:
            pass
        raise
    try:
        record_build_status(result, "manual")
    except Exception:
        pass
    try:
        process_build_result(result)
    except Exception:
        pass
    return result


@app.get("/xmltv.xml")
def serve_built_xmltv():
    """Serve the last explicitly generated full XMLTV output."""
    if not FULL_OUTPUT_XMLTV.is_file():
        raise HTTPException(
            status_code=404, detail="XMLTV output has not been built"
        )
    return FileResponse(FULL_OUTPUT_XMLTV, media_type="application/xml")


# Register the catch-all static mount after explicit /logos API routes.
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.mount("/logos", StaticFiles(directory=LOGOS_DIR), name="logos")
