# Operations and troubleshooting

Use the authenticated UI to validate/save sources, Refresh Sources, review mapping warnings, Save Mappings, and Build XMLTV. Refresh preserves last-known-good caches. Build publishes atomically only after validation; failure leaves previous output available.

The scheduler stores runtime state in ignored `config/settings.json`. Restart invalidates admin sessions but does not erase mappings, caches, bindings, or output.

## Validate the served feed

```python
import urllib.request
import xml.etree.ElementTree as ET
with urllib.request.urlopen("http://127.0.0.1:34500/xmltv.xml", timeout=30) as r:
    root = ET.fromstring(r.read())
assert root.tag == "tv"
```

Harmless query parameters are accepted. Some Plex deployments use `?cachedlogos=false`; that consumer option is not part of the canonical URL.

For sparse metadata, inspect `episode-num`, `date`, `sub-title`, `previously-shown`, `new`, and `live`, then check source horizon, stale mappings, title normalization, timing/overlap, ambiguity, and conflicts. If WonkEPG serves a field but a consumer omits it, investigate consumer ingestion.

Source failures should retain the last successful cache. Resolver failure must not block a normal build. Back up ignored state before manual edits and parse HTTP-served XML after recovery. See [BACKUP.md](../BACKUP.md) and [Security](../SECURITY.md).
