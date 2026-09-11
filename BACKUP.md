# WonkEPG backup and restore

## Persistent state

Back up these ignored runtime paths together:

- `.env`
- `config/channels.json`
- `config/settings.json`
- `config/sources.json`
- `config/episode_resolvers.json`
- `config/secrets.env`, if used
- `secure-assets/`

`data/` contains recreatable downloads and resolver caches, but retaining it preserves last-known-good inputs during an outage. `output/xmltv.xml` is also recreatable, though retaining it provides operational insurance.

## Restore

1. Restore or clone the application code at the intended version.
2. Restore runtime config and secrets with restrictive permissions.
3. Restore managed assets; optionally restore `data/` and `output/`.
4. Run `docker compose up -d --build`.
5. Verify `/status`, authenticate to the UI, parse `/xmltv.xml`, and confirm scheduler state.

Startup does not require an immediate successful source refresh when valid caches and output were restored. Never put backup archives, credentials, provider URLs, or third-party artwork into source control.
