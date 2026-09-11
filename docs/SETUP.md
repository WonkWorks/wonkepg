# Setup and prerequisites

## Inputs and output

WonkEPG requires a curated M3U/M3U8 channel foundation and at least one XMLTV schedule source. The foundation establishes channels, numbers, names, groups, and logos. The default schedule supplies programme timing; optional per-channel alternate schedules and two enrichment roles add metadata. Output is `/xmltv.xml` with stable `wonk.<number>` channel IDs.

WonkEPG supplies no tuner/video and no guide data.

## Docker Compose installation

1. Copy `.env.example` to `.env` and set a long unique `WONKEPG_ADMIN_PASSWORD`.
2. Create `config`, `data`, `output`, `secure-assets`, and `foundation` directories. Ensure the container can write all except a read-only foundation mount.
3. Put the curated playlist at `foundation/channels.m3u`, or configure another allowed local path/HTTP(S) URL.
4. Optionally seed settings and resolver state from their `.example.json` files. Do not copy `channels.example.json` unless intentionally testing its synthetic mappings.
5. Run `docker compose up -d --build`, open `http://127.0.0.1:34500/`, and sign in.
6. In Settings, configure provider names and XMLTV URLs. Validate and save, refresh sources, review inactive foundation channels, then explicitly activate mappings or call the bootstrap endpoint.
7. Build XMLTV and parse `http://127.0.0.1:34500/xmltv.xml` before connecting a consumer.

The first-run UI remains available after login when `channels.json` does not yet exist. Bootstrap compares foundation channel IDs first, then exact normalized display names, against the configured default schedule. It accepts only unique matches and never overwrites an existing matrix.

Runtime files are created with restrictive permissions where the application controls creation. Exact host ownership depends on Docker; verify that sensitive files are not readable by unrelated users.

## Network modes

The default bind is loopback. For a trusted LAN, use a specific host LAN address when possible, or deliberately set `WONKEPG_BIND_ADDRESS=0.0.0.0`, retain native authentication, and firewall the port. For HTTPS/reverse proxy use, keep the upstream loopback/private, set `WONKEPG_COOKIE_SECURE=true`, and see [Security](../SECURITY.md).

## Private/local source feeds

Private address destinations are blocked by default. Prefer an exact hostname allowlist with `WONKEPG_ALLOWED_SOURCE_HOSTS=guide.internal.example`. The broader `WONKEPG_ALLOW_PRIVATE_SOURCE_URLS=true` is available for trusted networks. Local schedule XMLTV paths are not accepted; serve them over HTTP(S). Local foundation paths must stay within colon-separated `WONKEPG_ALLOWED_FOUNDATION_ROOTS` and be explicitly mounted.

## Native Python

Install `requirements.txt`, provide writable application config/data/output/asset paths, and run `uvicorn app.main:app --host 127.0.0.1 --port 8000`. Compose is the documented path because the default runtime paths are container-oriented.
