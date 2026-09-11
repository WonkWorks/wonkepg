# Design decisions and lessons learned

## Curate first

Raw playlists can be enormous and contain regional duplicates, radio, VOD, and pseudo-channels. WonkEPG starts from the smaller lineup the administrator intends to expose.

Foundation/tuner identity and schedule identity are different. Per-channel baseline selection handles mislabeled/regional streams without downstream tuner changes. Stable `wonk.<number>` IDs decouple consumers from provider IDs.

Baseline time is authoritative and enrichment is additive. Explicit IDs are preserved; parallel numbering systems may coexist; WonkEPG never invents mathematical conversions. Presentation noise such as explicit NEW/LIVE decorations can be normalized, but genuine titles cannot. Absence of `<previously-shown>` is not evidence of NEW.

A stale ID is retained, an ambiguous candidate rejected, and a resolver conflict does not overwrite. Missing enrichment is better than confidently wrong metadata.

External APIs are onboarding/cache-refresh tools, not build dependencies. JSON retains provenance/conflicts while XMLTV stays the interoperability export.
