# Contributing

WonkEPG targets Python 3.11 and FastAPI. Keep changes small, source-agnostic, and conservative about attaching metadata.

Read [the positioning strategy](docs/POSITIONING.md) before changing product language or scope. WonkEPG is an EPG intelligence layer, not a stream server, tuner emulator, guide provider, or media server.

Useful extension points include source adapters, conservative matchers and normalization rules, resolver/metadata adapters, diagnostics and provenance, and future consumer profiles. Provider-specific work must not embed private URLs, credentials, or redistributable third-party data.

## Development

```bash
cp .env.example .env
# Set a development-only admin password.
docker compose build
docker compose run --rm wonkepg python -B -m unittest discover -s tests
```

For local Python development, install `requirements.txt` in an isolated environment and run the same unittest command. Before submitting changes, also run `git diff --check`, render the mapping page and syntax-check its JavaScript, and parse a generated XMLTV document.

## Expectations

- Never commit real provider URLs, credentials, private domains/IPs, personal email, runtime mappings, downloaded guide data, caches, output, resolver bindings, or third-party artwork.
- Use synthetic `example.com` fixtures and minimal XMLTV/M3U samples.
- Baseline times remain authoritative; ambiguous enrichment is rejected. Prefer a missing field to confidently attaching the wrong field.
- Preserve explicit source identity and numbering instead of inventing conversions.
- Normal builds must not depend on an external metadata API.
- Put provider-specific network behavior in an adapter where practical; keep resolver and merge policy provider-agnostic.
- Add regression tests for behavior changes, including failure and last-known-good paths.

The code is Apache-2.0. Contributions must be compatible with that license and must not introduce third-party data whose terms prevent redistribution.
