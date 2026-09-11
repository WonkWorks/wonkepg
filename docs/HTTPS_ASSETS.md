# Optional HTTPS asset hosting

WonkEPG core does not require Caddy, Cloudflare, a public hostname, or HTTPS artwork. Local XMLTV delivery, mapping, refresh, scheduling, and builds work without them.

Some remote/native consumers will not fetch plain-HTTP or LAN-only artwork. Managed logos use `https://assets.<SERVER_DOMAIN>/wonkepg/logos/<file>.png`. You are responsible for routing that hostname to the read-only asset directory and securing the infrastructure.

`compose.https-example.yaml` is an override example that keeps WonkEPG on loopback and enables Secure session cookies. It does not install/configure a proxy, certificate, DNS record, tunnel, Caddy, or Cloudflare.

Caddy plus Cloudflare is one tested convenience pattern. An existing reverse proxy, CDN, object store, or static host can serve the same paths. Never expose config, `.env`, caches, or source credentials as static content.

If using optional Cloudflare token settings, scope the token narrowly, keep `config/secrets.env` at `0600`, and remember validation is read-only. Saving it in WonkEPG does not update an independently managed proxy/tunnel.
