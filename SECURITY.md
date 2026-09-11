# Security policy

## Admin boundary

WonkEPG 0.8.0 uses one deployment-supplied `WONKEPG_ADMIN_PASSWORD`. A successful login creates a random, opaque, in-memory session and a separate CSRF token. Sessions expire after `WONKEPG_SESSION_MINUTES` (bounded to 5–1440 minutes) and all sessions are invalidated by restart. Logout invalidates the current session.

The session cookie is `HttpOnly`, `SameSite=Strict`, and path-scoped to `/`. Set `WONKEPG_COOKIE_SECURE=true` whenever the browser reaches WonkEPG over HTTPS. Passwords are compared as fixed-length SHA-256 digests using constant-time comparison. WonkEPG intentionally has no accounts, roles, recovery, or identity database.

If no admin password is configured, the admin UI redirects to a disabled login page and protected APIs fail closed. Standard Compose binds to `127.0.0.1` by default.

## Public and protected endpoints

Unauthenticated access is intentionally limited to `GET /xmltv.xml`, `GET /status`, `GET /login`, and static assets under `/static/` and `/logos/`. The health response contains service name, status, version, and an ephemeral instance ID only.

Every other endpoint requires a valid session. `POST`, `PUT`, `PATCH`, and `DELETE` requests additionally require the session's `X-WonkEPG-CSRF` header. This includes settings, source URLs and validation, mappings, refresh/build actions, resolver operations, notification tests, logo operations, secret replacement, and restart.

Treat `/xmltv.xml` as public guide data. If schedules themselves are sensitive, restrict the route at a reverse proxy or firewall.

## Deployment

- Loopback: retain `WONKEPG_BIND_ADDRESS=127.0.0.1` and access locally or through SSH forwarding.
- LAN: set an explicit LAN bind address or `0.0.0.0`, keep native authentication enabled, and restrict the port with host/network firewall rules.
- Reverse proxy: terminate HTTPS at a trusted proxy, set the Secure-cookie option, and preserve native auth as defense in depth. Do not expose the upstream container port publicly.

WonkEPG does not infer trusted users from forwarded headers. Configure authentication at the proxy independently if desired.

## URLs, local paths, and secrets

Only HTTP(S) source URLs are accepted. Local, loopback, private, link-local, multicast, reserved, and unspecified destinations are denied by default. Administrators may explicitly allow exact private feed hostnames with `WONKEPG_ALLOWED_SOURCE_HOSTS`, or opt into the broader `WONKEPG_ALLOW_PRIVATE_SOURCE_URLS=true` policy. These are trust decisions: an allowed source can observe requests and return hostile or very large content within WonkEPG's validation/download limits.

Local foundation files are allowed only beneath roots listed in `WONKEPG_ALLOWED_FOUNDATION_ROOTS`; mount those roots read-only where practical. URL checks reduce obvious SSRF and arbitrary-file abuse but are not a complete network sandbox. Use egress controls for hostile multi-tenant environments.

Credential-bearing URLs are masked in admin responses and blank masked fields preserve their existing value on save. Runtime source config, `.env`, resolver bindings, and token files are ignored. Recommended permissions are `0600` for secret/config files and no broader directory access than the service needs.

## Reporting a vulnerability

Do not open a public issue for a suspected vulnerability. Until a private security contact is published, use GitHub's private vulnerability-reporting feature for the repository. Include the affected version, reproduction, impact, and any suggested mitigation. Avoid including real provider credentials or guide data.
