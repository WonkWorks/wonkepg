# External episode resolution

External Episode Resolution is an optional quality-control pass, not a guide source. It operates only on eligible episodic records that lack a complete structured identity and only after an administrator confirms the show binding.

Provider-agnostic policy lives in `episode_resolver.py`; TVmaze-specific search/download/parsing lives in `tvmaze_provider.py`. TVmaze is currently the only supported adapter.

## Modes

- `strict_episode_match`: the existing source episode number must exactly match one regular external episode on the original local airdate.
- `date_anchored_identity`: a parallel numbering system may be added only when one complete regular external season/episode pair uniquely agrees on original local date, airtime, runtime, and any meaningful subtitle.

Source IDs are never replaced. WonkEPG does not combine an external season with a source episode number, infer NEW/repeat state, or resolve an overnight replay using its replay date. An eligible replay can inherit only an original airing resolved earlier in the same build.

Search, binding, and cache refresh are explicit authenticated actions. Normal builds read ignored `data/tvmaze/` caches and make zero external calls. Missing/stale cache, outages, ambiguity, movies, sports, specials, existing structured identity, and material conflicts leave the programme unchanged.

TVmaze data/artwork are not bundled. Its official API documentation currently states that API use is licensed under CC BY-SA 4.0 with attribution/ShareAlike requirements, and documents caching plus a rate limit of at least 20 calls per 10 seconds per IP. Review [TVmaze's API documentation](https://www.tvmaze.com/api). WonkEPG's Apache-2.0 license does not relicense cached TVmaze data.
