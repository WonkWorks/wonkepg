# Architecture and source model

## Pipeline

```text
curated M3U/M3U8 foundation ──→ persistent channel matrix
XMLTV schedule sources ───────→ per-channel baseline timing
XMLTV enrichment sources ─────→ conservative metadata merge
local resolver cache (optional) → episode-identity completion
                               ↓
                         output/xmltv.xml
```

The foundation is lineup identity, not guide identity. Each channel stores an atomic `{source, channel_id}` baseline mapping, allowing different schedule authorities per channel. Output IDs are stable `wonk.<channel-number>` values.

Baseline `start` and `stop` always win. Enrichment can add subtitle, description, credits, categories, artwork, ratings, date, episode numbering, and explicit repeat/new/live metadata; it does not become timing authority because it is richer.

Saved exact IDs are retained when a source disappears. WonkEPG reports missing channel/source states, uses last-known-good cache where possible, and never fuzzy-remaps persisted selections.

## Bootstrap and compatibility

New-install bootstrap uses the configured foundation plus configured default schedule. It tries exact normalized channel ID, then exact normalized display name, accepting only a unique candidate. Existing `channels.json` is never regenerated.

The internal enrichment cache/role IDs `epgshare`, `epgtalk`, and `epgtalk_local`, and the one-time `hive.xml` default-cache migration, remain isolated compatibility details for 0.7.x installations. They supply no public defaults or fixed provider URLs. User-facing names and URLs come from runtime settings. Renaming persisted roles in 0.8.0 would create unnecessary mapping risk.

Runtime JSON retains mappings, confidence, provenance, conflicts, cache state, resolver IDs, and diagnostics. XMLTV remains the interoperability export.
