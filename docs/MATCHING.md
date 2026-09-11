# Programme matching and normalization

WonkEPG strips only explicit prefix/suffix signals such as `Live:`, `[LIVE]`, `New:`, `[NEW]`, and trailing `[NEW]`, peeling combined forms iteratively. It retains those signals so explicit NEW may become `<new/>` and explicit LIVE may become the widely tolerated `<live/>` extension. Genuine titles such as `Saturday Night Live`, `Live PD`, and `New Girl` remain untouched.

## Implemented matching rule

A standard enrichment match requires the same mapped channel, same normalized title, start within two minutes, and duration within two minutes.

A padding-tolerant match requires mutual one-to-one uniqueness, start within two minutes, duration difference no greater than three minutes, at least 90% interval overlap, and no material subtitle, shared episode-ID, or substantive synopsis conflict.

Ambiguous relaxed candidates and metadata conflicts are rejected and counted. Metadata is copied only after a match; baseline start/stop is never shifted or stretched. This accommodates small provider padding differences while preferring false negatives to false positives.
