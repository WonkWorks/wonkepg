# Known limitations and future work

- Source quality and horizon set the ceiling; WonkEPG cannot manufacture trustworthy metadata.
- Schedule sources are extensible, but two persisted enrichment roles retain internal compatibility IDs predating provider-neutral labels.
- The `hive.xml` default-cache seed remains as an isolated one-time 0.7.x migration.
- Local files are supported for the foundation only; schedule XMLTV requires HTTP(S).
- URL validation blocks obvious private/local destinations but is not a complete network sandbox; isolated deployments need egress controls.
- Optional managed HTTPS channel logos do not proxy every programme image.
- `<live/>` is a compatibility extension, not strict XMLTV DTD.
- TVmaze is the only current resolver adapter and may use different numbering from guide sources.
- Sessions are in-memory and single-node; restart logs everyone out.
- Every consumer, playlist generator, and reverse proxy is not independently tested.
