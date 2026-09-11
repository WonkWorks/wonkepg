# WonkEPG — Keep your EPG. Make it better.

**Multiple sources are easy. Reconciling what they say is the hard part.**

WonkEPG is an EPG intelligence layer that reconciles multiple XMLTV guide sources into one normalized, enriched, DVR-ready feed.

IPTV guide data is rarely complete. One source may have the correct schedule, another better season and episode information, and another richer descriptions, artwork, ratings, or longer coverage. WonkEPG identifies matching programmes across those sources and combines what each knows instead of forcing you to choose one.

WonkEPG does not proxy streams, emulate tuners, or replace your media server. Use Threadfin, Dispatcharr, xTeVe, or whatever television backend already works for you. WonkEPG focuses on making the guide better.

```text
Provider XMLTV ─────┐
                    │
Guide source #2 ────┼────► WonkEPG ─────► one DVR-ready XMLTV feed
                    │                         │
Guide source #3 ────┘                         ├──► Plex
                                              ├──► Jellyfin
IPTV streams ─► Threadfin / Dispatcharr ──────┼──► Emby
                 / xTeVe                      └──► other XMLTV consumers
```

These products are examples of compatible neighboring tools, not dependencies. WonkEPG also fits beside TVHeadend or any workflow that can consume standard XMLTV.

## Reconciliation, not concatenation

**WonkEPG is not an XML concatenator.** Traditional XMLTV merging commonly combines schedules or chooses/replaces a programme record when sources overlap. WonkEPG matches records that describe the same airing, then enriches the canonical programme field by field while retaining the selected schedule authority.

```text
Traditional merge                         WonkEPG

Source A programme                        Source A programme
Source B programme                        Source B programme
        │                                 Source C programme
        ▼                                         │
choose/replace one record                         ▼
                                          identify the same airing
                                                  │
                                                  ▼
                                             compare fields
                                                  │
                                                  ▼
                                        build one canonical record
```

For example, a Bravo mapping might use one source such as Hive for schedule authority, a source such as EPGTalk for deeper episode metadata and coverage, and a source such as EPGShare for artwork or presentation metadata. When records match conservatively, WonkEPG keeps the authoritative airing times and adds compatible subtitles, descriptions, episode identifiers, credits, categories, artwork, dates, ratings, and explicit new/repeat/live signals that the other sources know.

## Why DVR users care

For a DVR, XMLTV is operational data, not just grid decoration. Depending on the consumer, metadata quality can influence series recording, season and episode identity, repeat detection, new-airing behavior, duplicate suppression, library classification, recording names, scheduling horizon, descriptions, artwork, and ratings.

WonkEPG produces XMLTV intended to improve DVR metadata quality and compatibility. It cannot control proprietary consumer behavior or guarantee that every DVR interprets every XMLTV field identically.

## What defines WonkEPG

- Multiple user-supplied XMLTV sources, with per-channel schedule authority.
- Explicit channel mapping and conservative cross-source programme matching.
- Field-level enrichment instead of whole-record replacement.
- Conflict and ambiguity rejection: missing metadata is safer than a wrong match.
- Season/episode identity and explicit new/repeat/live normalization.
- Optional horizon extension from the configured long-range enrichment role.
- Stable `wonk.<channel-number>` IDs and one unified feed at `/xmltv.xml`.

Operationally, WonkEPG also provides scheduled refreshes, source validation and health information, last-known-good caches, atomic output publication, build diagnostics, an authenticated admin UI, Docker Compose deployment, and optional HTTPS asset support.

## Why not just…

### …use the IPTV provider's EPG?

That may be enough. When it is not, another source may fill gaps in episode identity, descriptions, artwork, ratings, reliability, or future coverage without displacing the provider's useful schedule.

### …pick one guide provider?

Every source can have different strengths. WonkEPG's premise is that no single source has to be complete: one can own timing while others contribute compatible metadata.

### …use XMLTV `tv_merge`?

`tv_merge` is useful for combining schedules and selecting programme records. WonkEPG solves a narrower, different problem: programme-level matching followed by enrichment within the matched canonical record.

### …let Threadfin or xTeVe handle it?

Those tools are useful stream/tuner bridges and provide EPG plumbing. WonkEPG deliberately specializes in guide reconciliation and can supply its XMLTV output alongside them.

### …use Dispatcharr?

Dispatcharr is a broader IPTV management and backend platform. It can manage the television backend while WonkEPG constructs the guide; the two layers can be used together.

Use the IPTV backend you prefer. WonkEPG focuses on the guide.

## Scope

WonkEPG starts with a curated M3U/M3U8 foundation that defines lineup identity—channels, numbers, names, groups, and logos—but it does not carry or manage video. It supplies no streams, tuner emulation, playback, transcoding, DVR execution, or guide data. You must supply sources you are entitled to use.

The architectural boundary is intentional:

```text
stream tools  → excellent at streams
media servers → excellent at playback and DVR
WonkEPG       → excellent at describing what is on television
```

## Requirements

- Docker Engine with Docker Compose v2, or Python 3.11 with the packages in `requirements.txt`.
- A curated M3U/M3U8 foundation.
- At least one XMLTV schedule source.
- An admin password supplied at runtime.

## Quick start

```bash
cp .env.example .env
mkdir -p config data output secure-assets foundation
cp config/settings.example.json config/settings.json
cp config/episode_resolvers.example.json config/episode_resolvers.json
```

Set a long unique `WONKEPG_ADMIN_PASSWORD` in `.env`, place the curated playlist at `foundation/channels.m3u`, then start WonkEPG:

```bash
docker compose up -d --build
```

Open `http://127.0.0.1:34500/`, sign in, configure and validate the default schedule and optional enrichment sources, refresh sources, then activate mappings or use the explicit bootstrap API. WonkEPG never silently rebuilds an existing mapping matrix. See [Setup](docs/SETUP.md) for complete first-run and network instructions.

The consumer endpoint is:

```text
http://127.0.0.1:34500/xmltv.xml
```

`/xmltv.xml`, `/status`, and intentional static assets are public. The UI, source details, mappings, validation, builds, refreshes, resolver operations, uploads, secrets, and maintenance actions require an authenticated session; mutations also require the session CSRF token.

## Matching and explainability

The foundation defines lineup identity. Each active channel selects a baseline schedule source and exact XMLTV channel ID. The baseline supplies canonical schedule times; configured enrichment roles may contribute compatible programme fields.

A standard match requires the same normalized title plus start and duration within two minutes. A padding-tolerant match is accepted only when mutually unique, starting within two minutes, differing in duration by no more than three minutes, overlapping at least 90%, and showing no material subtitle, shared episode-ID, or synopsis conflict. Enrichment never shifts baseline times. See [Matching](docs/MATCHING.md) for details.

Runtime diagnostics report matches, rejected ambiguity and conflicts, source and cache state, horizon, and per-channel build results. Complete field-by-field provenance—answering exactly why every output value won—is a future direction rather than a current UI feature.

## Optional external episode resolution

External Episode Resolution is disabled unless an administrator confirms a show binding. TVmaze is currently the supported adapter. Search and refresh use the network; normal XMLTV builds use only ignored local caches and make no resolver API calls. TVmaze data is not bundled.

TVmaze states that its API output is licensed under CC BY-SA 4.0 and requires attribution and ShareAlike compliance. Link to TVmaze when using its data and review the [official API documentation](https://www.tvmaze.com/api). WonkEPG's Apache-2.0 code license does not relicense TVmaze or any configured third-party data.

## Documentation

- [Setup and prerequisites](docs/SETUP.md)
- [Architecture and source model](docs/ARCHITECTURE.md)
- [Matching and title normalization](docs/MATCHING.md)
- [External episode resolution](docs/EPISODE_RESOLUTION.md)
- [Operations and troubleshooting](docs/OPERATIONS.md)
- [Plex + Threadfin reference deployment](docs/PLEX_THREADFIN.md)
- [Optional HTTPS asset hosting](docs/HTTPS_ASSETS.md)
- [Design decisions](docs/DESIGN_DECISIONS.md)
- [Known limitations](docs/KNOWN_LIMITATIONS.md)
- [Positioning and documentation strategy](docs/POSITIONING.md)
- [Security policy](SECURITY.md)
- [Contributing](CONTRIBUTING.md)

## License and data responsibility

WonkEPG code and original branding are licensed under Apache-2.0; see [LICENSE](LICENSE). Configured XMLTV, playlists, artwork, resolver data, and other third-party material remain governed by their respective owners and terms. Technical validation is not a licensing determination or endorsement.
