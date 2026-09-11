# Plex + Threadfin reference deployment

This is a tested reference integration, not a core requirement.

```text
provider streams/M3U → Threadfin → Plex tuner/video
curated Threadfin M3U → WonkEPG foundation
user-supplied XMLTV → WonkEPG → Plex guide
```

Threadfin can curate the lineup and emulate a tuner. WonkEPG consumes the curated playlist but supplies its XMLTV directly to Plex. WonkEPG does not require Threadfin's XMLTV or XEPG mapping. Another curated playlist source and another XMLTV consumer can replace either product.

Use `http://<wonkepg-host>:34500/xmltv.xml` in Plex. Plex-specific deployments may append `?cachedlogos=false`; WonkEPG ignores harmless query parameters. Stable `wonk.*` IDs generally let metadata changes flow through a guide refresh, though behavior varies by version.

Keep tuner identity separate from schedule identity: the stream retains its foundation ID while its baseline points to the schedule that matches the video. Use DVR recording padding for overruns instead of allowing enrichment to rewrite baseline times.

Keep TV and movie directory trees separate and verify the numeric UID/GID used by your own containers and host. No particular ID or host path is assumed.
