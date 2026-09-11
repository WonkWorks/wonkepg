"""Minimal server-rendered mapping UI."""

import json
from html import escape
from app.version import __version__


def _selected_id(selection):
    if isinstance(selection, dict):
        return selection.get("channel_id", "")
    return selection if isinstance(selection, str) else ""


def _catalog_json(channels):
    """Serialize one compact, safely embeddable catalog for the whole page."""
    catalog = [
        {
            "channel_id": channel["channel_id"],
            "display_name": channel.get("display_name") or channel["channel_id"],
            "display_names": channel.get("display_names") or [
                channel.get("display_name") or channel["channel_id"]
            ],
        }
        for channel in channels
    ]
    catalog.sort(
        key=lambda channel: (
            channel["display_name"].casefold(),
            channel["channel_id"].casefold(),
        )
    )
    return (
        json.dumps(catalog, ensure_ascii=True, separators=(",", ":"))
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )


def _combobox(source, label, number, selected_id, names, stale=False):
    selected_name = names.get(selected_id) or selected_id
    clear_hidden = "" if selected_id else " hidden"
    warning_hidden = "" if stale else " hidden"
    return f"""<div class="combobox" data-source="{source}">
      <div class="combobox-field">
        <input class="mapping" role="combobox" aria-autocomplete="list"
          aria-expanded="false" aria-controls="combobox-options"
          aria-label="{label} channel for {escape(number, quote=True)}"
          value="{escape(selected_name, quote=True)}"
          data-selected-id="{escape(selected_id, quote=True)}"
          placeholder="No mapping" autocomplete="off" spellcheck="false">
        <button type="button" class="clear" title="Clear mapping"
          aria-label="Clear {label} mapping for channel {escape(number, quote=True)}"{clear_hidden}>&times;</button>
      </div>
      <small class="selection-id">{escape(selected_id)}</small>
      <small class="stale-selection"{warning_hidden}>⚠ Selected {escape(label)} channel not present in current source</small>
    </div>"""


def _baseline_combobox(
    number, selection, catalogs, source_names, stale_item=None
):
    selection = selection if isinstance(selection, dict) else {}
    source_id = selection.get("source") or ""
    channel_id = selection.get("channel_id") or ""
    provider = source_names.get(source_id) or source_id
    source = catalogs.get(source_id) or {}
    names = {
        item["channel_id"]: item.get("display_name")
        for item in source.get("channels", [])
    }
    channel_name = names.get(channel_id) or channel_id
    selected_name = (
        f"{provider} · {channel_name}" if source_id and channel_id else ""
    )
    clear_hidden = "" if channel_id else " hidden"
    warning_hidden = "" if stale_item else " hidden"
    reason = (stale_item or {}).get("reason")
    if reason == "missing_source":
        warning = "⚠ Selected baseline source is no longer configured"
    elif reason == "source_unavailable":
        warning = f"⚠ Selected {provider} source cache is unavailable"
    else:
        warning = (
            f"⚠ Selected {provider} channel not present in current source"
        )
    return f"""<div class="combobox baseline-combobox" data-source="baseline">
      <div class="combobox-field">
        <input class="mapping" role="combobox" aria-autocomplete="list"
          aria-expanded="false" aria-controls="combobox-options"
          aria-label="Baseline schedule for {escape(number, quote=True)}"
          value="{escape(selected_name, quote=True)}"
          data-selected-source="{escape(source_id, quote=True)}"
          data-selected-id="{escape(channel_id, quote=True)}"
          placeholder="No baseline schedule" autocomplete="off" spellcheck="false">
        <button type="button" class="clear" title="Clear baseline schedule"
          aria-label="Clear baseline schedule for channel {escape(number, quote=True)}"{clear_hidden}>&times;</button>
      </div>
      <small class="selection-id">{escape(channel_id)}</small>
      <small class="stale-selection"{warning_hidden}>{escape(warning)}</small>
    </div>"""


def _logo_control(number, logo):
    logo = logo or ""
    image_hidden = "" if logo else " hidden"
    return f"""<div class="logo-control" data-logo-url="{escape(logo, quote=True)}">
      <img class="logo-thumbnail" src="{escape(logo, quote=True)}"
        alt="" loading="lazy"{image_hidden}>
      <button type="button" class="upload-logo">Upload PNG</button>
    </div>"""


def _pretty_control(number, value):
    return f"""<div class="pretty-control">
      <input class="pretty-name" maxlength="50" value="{escape(value or '', quote=True)}"
        aria-label="Pretty Name for channel {escape(number, quote=True)}" readonly>
      <button type="button" class="pretty-lock" aria-label="Unlock Pretty Name"
        title="Unlock Pretty Name" aria-pressed="false">&#128274;</button>
    </div>"""


def render_mapping_page(
    matrix, epgshare, epgtalk, drift=None, operations=None,
    source_settings=None, version=__version__, schedule_sources=None,
    schedule_catalogs=None, csrf_token="",
):
    drift = drift or {"inactive": [], "warnings": {}}
    operations = operations or {}
    source_settings = source_settings or {
        "foundation": {"configured_m3u": "/foundation/channels.m3u"},
        "baseline": {"url": "", "provider_name": "Default Schedule"},
        "enrichment_1": {"url": "", "provider_name": "Enrichment 1"},
        "enrichment_2": {
            "primary_url": "", "secondary_url": "",
            "provider_name": "Enrichment 2",
        },
    }
    schedule_sources = schedule_sources or [{
        "source_id": "baseline-default",
        "provider_name": "Baseline",
        "default": True,
        "available": False,
        "channel_count": 0,
    }]
    schedule_catalogs = schedule_catalogs or {}
    schedule_source_names = {
        item["source_id"]: item.get("provider_name") or item["source_id"]
        for item in schedule_sources
    }
    stale_baselines = {
        str(item.get("number")): item
        for item in operations.get("stale_baseline_mappings", [])
    }
    schedule_sources_json = (
        json.dumps(schedule_sources, ensure_ascii=True, separators=(",", ":"))
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )
    provider_names = {
        "epgshare": source_settings["enrichment_1"]["provider_name"] or "Enrichment 1",
        "epgtalk": source_settings["enrichment_2"]["provider_name"] or "Enrichment 2",
        "epgtalk_local": source_settings["enrichment_2"]["provider_name"] or "Enrichment 2",
    }
    provider_names.update(schedule_source_names)
    enrichment_1_label = provider_names["epgshare"] or "Enrichment 1"
    enrichment_2_label = provider_names["epgtalk"] or "Enrichment 2"
    provider_names_json = (
        json.dumps(provider_names, ensure_ascii=True, separators=(",", ":"))
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )
    share_names = {
        item["channel_id"]: item.get("display_name") for item in epgshare
    }
    talk_names = {
        item["channel_id"]: item.get("display_name") for item in epgtalk
    }
    rows = []
    for row in matrix["channels"]:
        number = str(row["number"])
        baseline = row.get("baseline") or {}
        share = _selected_id(row.get("enrichment_1"))
        talk = _selected_id(row.get("enrichment_2"))
        search = escape(
            " ".join(
                (number, row.get("name") or "", row.get("group") or "")
            ).casefold(),
            quote=True,
        )
        warning = drift["warnings"].get(number)
        badge = (
            f'<span class="warning-badge">{escape(warning)}</span>' if warning else ""
        )
        rows.append(
            f"""<tr data-number="{escape(number, quote=True)}"
                data-search="{search}" data-inactive="false">
              <td><input class="active-toggle" type="checkbox" checked disabled
                aria-label="Active channel"></td>
              <td>{escape(number)}</td>
              <td><div class="source-name">{escape(row.get("name") or "")}{badge}</div></td>
              <td>{_pretty_control(number, row.get("pretty_name", row.get("name")))}</td>
              <td>{_logo_control(number, row.get("logo"))}</td>
              <td class="baseline"><div>{escape(row.get("group") or "")}</div>
                {_baseline_combobox(
                    number, baseline, schedule_catalogs,
                    schedule_source_names, stale_baselines.get(number),
                )}</td>
              <td>{_combobox(
                  "epgshare", enrichment_1_label, number, share, share_names,
                  bool(share and share not in share_names),
              )}</td>
              <td>{_combobox(
                  "epgtalk", enrichment_2_label, number, talk, talk_names,
                  bool(talk and talk not in talk_names),
              )}</td>
            </tr>"""
        )

    inactive_rows = []
    for row in drift["inactive"]:
        number = str(row["number"])
        baseline = row.get("baseline") or {}
        share = _selected_id(row.get("enrichment_1"))
        talk = _selected_id(row.get("enrichment_2"))
        search = escape(
            " ".join(
                (number, row.get("name") or "", row.get("group") or "")
            ).casefold(),
            quote=True,
        )
        inactive_rows.append(
            f"""<tr data-number="{escape(number, quote=True)}"
                data-search="{search}" data-inactive="true">
              <td><input class="active-toggle" type="checkbox"
                aria-label="Activate channel {escape(number, quote=True)}"></td>
              <td>{escape(number)}</td>
              <td><div class="source-name">{escape(row.get("name") or "")}</div></td>
              <td>{_pretty_control(number, row.get("pretty_name", row.get("name")))}</td>
              <td>{_logo_control(number, row.get("logo"))}</td>
              <td class="baseline"><div>{escape(row.get("group") or "")}</div>
                {_baseline_combobox(
                    number, baseline, schedule_catalogs,
                    schedule_source_names, stale_baselines.get(number),
                )}</td>
              <td>{_combobox(
                  "epgshare", enrichment_1_label, number, share, share_names,
                  bool(share and share not in share_names),
              )}</td>
              <td>{_combobox(
                  "epgtalk", enrichment_2_label, number, talk, talk_names,
                  bool(talk and talk not in talk_names),
              )}</td>
            </tr>"""
        )

    warning_count = len(drift["warnings"])
    initial_operations = {
        **operations,
        "foundation_drift_warnings": warning_count,
    }
    csrf_json = (
        json.dumps(csrf_token, ensure_ascii=True)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )
    operations_json = (
        json.dumps(initial_operations, ensure_ascii=True, separators=(",", ":"))
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )
    start_time_options = "".join(
        f'<option value="{hour:02d}:{minute:02d}">'
        f'{hour:02d}:{minute:02d}</option>'
        for hour in range(24)
        for minute in (0, 30)
    )

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>WonkEPG mappings</title>
<link rel="icon" type="image/png" href="/static/favicon.png">
<script>
(()=>{{
  const saved=localStorage.getItem("wonkepg-theme");
  const theme=saved==="light" || saved==="dark"
    ? saved
    : (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  document.documentElement.dataset.theme=theme;
}})();
</script>
<style>
:root{{--page:#f6f7f9;--surface:#fff;--surface-soft:#f1f3f5;--header:#687785;--text:#20252b;--muted:#59636e;--border:#c9ced4;--button:#fff;--button-hover:#e9edf1;--danger:#a00;--menu-active:#e2edf8;--shadow:#0003;--warning-bg:#fff0c2;--warning-text:#704d00;--warning:#8a5a00;color-scheme:light}}
:root[data-theme="dark"]{{--page:#101419;--surface:#1b222a;--surface-soft:#242d36;--header:#0b1015;--text:#edf1f5;--muted:#aeb8c2;--border:#46515d;--button:#27313b;--button-hover:#354250;--danger:#ff8d8d;--menu-active:#31475c;--shadow:#0008;--warning-bg:#4a3812;--warning-text:#ffd77c;--warning:#f0b84f;color-scheme:dark}}
*{{box-sizing:border-box}}
body{{font:14px system-ui,sans-serif;margin:0;background:var(--page);color:var(--text)}}
.app-header{{position:sticky;top:0;z-index:4;min-height:78px;padding:10px 20px;display:flex;align-items:center;gap:18px;background:var(--header);border-bottom:1px solid var(--border);box-shadow:0 2px 8px var(--shadow)}}
.brand{{display:flex;align-items:center;flex:0 0 auto}} .brand img{{display:block;width:clamp(180px,18vw,230px);height:auto}}
.header-actions{{display:flex;align-items:center;justify-content:flex-end;flex-wrap:wrap;gap:7px;margin-left:auto}} .header-actions button{{margin:0}}
.theme-toggle{{min-width:42px}} .theme-icon-dark{{display:none}} :root[data-theme="dark"] .theme-icon-light{{display:none}} :root[data-theme="dark"] .theme-icon-dark{{display:inline}}
.operations{{padding:10px 20px 0}} .table-area{{padding:0 20px 24px}}
.controls{{display:flex;align-items:center;flex-wrap:wrap;gap:8px;margin-bottom:8px}} #filter{{min-width:min(320px,100%);flex:0 1 360px}}
.xmltv-url-control{{display:flex;align-items:center;gap:6px;flex:1 1 520px;min-width:min(420px,100%)}} .xmltv-url-control label{{font-size:12px;font-weight:600;white-space:nowrap}} .xmltv-url-control input{{flex:1 1 320px;min-width:180px}} .xmltv-url-control button{{margin:0}} .copy-status{{min-width:42px;color:var(--muted);font-size:12px}}
button,input,select{{font:inherit;padding:6px;color:var(--text);background:var(--button);border:1px solid var(--border);border-radius:4px}}
button{{cursor:pointer}} button:hover{{background:var(--button-hover)}}
table{{border-collapse:collapse;width:100%;background:var(--surface)}} th,td{{border:1px solid var(--border);padding:6px;text-align:left;vertical-align:top}}
th{{position:sticky;top:78px;background:var(--surface-soft)}} td input{{width:100%;min-width:220px}}
small:not([hidden]){{display:block;color:var(--muted);margin-top:3px}} .baseline{{white-space:nowrap}} #status{{color:var(--muted)}}
#result{{white-space:pre-wrap;background:var(--surface-soft);padding:8px}} .error,.source-failed{{color:var(--danger)}}
.combobox-field{{position:relative}} .combobox-field .mapping{{padding-right:30px}}
.combobox-field .clear{{position:absolute;right:1px;top:1px;border:0;background:transparent;color:var(--muted);margin:0;padding:5px 9px;cursor:pointer}}
.combobox-field .clear:hover{{color:var(--text)}} .combo-menu{{position:fixed;z-index:10;max-height:280px;overflow-y:auto;background:var(--surface);border:1px solid var(--border);box-shadow:0 3px 10px var(--shadow)}}
.combo-menu[hidden]{{display:none}} .combo-option{{padding:7px 9px;cursor:pointer}} .combo-option + .combo-option{{border-top:1px solid var(--border)}}
.combo-option.active,.combo-option:hover{{background:var(--menu-active)}} .option-name{{font-weight:600}} .option-id,.combo-message{{color:var(--muted)}} .option-id{{font-size:12px;margin-top:2px}} .combo-message{{padding:8px 9px}}
.logo-control{{min-width:92px;text-align:center}} .logo-thumbnail{{display:block;width:72px;height:44px;object-fit:contain;margin:0 auto 5px;background:var(--surface-soft)}} .logo-thumbnail[hidden]{{display:none}} .upload-logo{{margin:0;padding:4px 6px;white-space:nowrap}}
.source-summary{{display:flex;flex-wrap:wrap;gap:6px 16px;margin:0 0 10px;color:var(--muted);font-size:12px}} .source-summary span{{white-space:nowrap}}
.schedule-controls{{display:flex;align-items:center;flex-wrap:wrap;gap:6px 10px;margin:0 0 12px;padding:8px;background:var(--surface-soft);border:1px solid var(--border)}} .schedule-controls label{{white-space:nowrap}} .schedule-controls input[type="number"]{{width:72px}} .schedule-controls button{{margin:0}} .schedule-run-status{{color:var(--muted);font-size:12px}}
.logo-bootstrap{{margin:0 0 12px;padding:10px;background:var(--surface-soft);border:1px solid var(--border)}} .logo-bootstrap[hidden]{{display:none}} .bootstrap-summary{{display:flex;flex-wrap:wrap;gap:6px 16px;margin:6px 0}} .bootstrap-summary span{{white-space:nowrap}}
.manual-attention{{max-height:260px;overflow:auto;margin:8px 0}} .manual-attention table{{font-size:12px;background:var(--surface)}} .manual-attention th{{position:static}} .manual-attention .logo-url{{overflow-wrap:anywhere;max-width:520px}}
.pretty-control{{display:flex;align-items:center;gap:4px;min-width:245px}} .pretty-control .pretty-name{{min-width:200px}} .pretty-control .pretty-name[readonly]{{background:var(--surface-soft);color:var(--muted)}} .pretty-lock{{margin:0;padding:5px 7px;line-height:1;cursor:pointer}}
.active-toggle{{min-width:0;width:auto}} .warning-badge{{display:inline-block;margin-left:6px;padding:2px 5px;border-radius:3px;background:var(--warning-bg);color:var(--warning-text);font-size:11px}} .stale-selection,.operational-warning{{color:var(--warning);font-weight:600}}
.operational-summary{{display:flex;flex-wrap:wrap;gap:5px 14px;margin:0 0 8px;padding:7px 9px;background:var(--surface-soft);border:1px solid var(--border);font-size:12px}} .operational-summary span{{white-space:nowrap}} .inactive-help{{color:var(--muted)}} #inactive-table th{{top:78px}}
.settings-dialog{{max-width:860px;max-height:calc(100vh - 32px);padding:0;overflow:hidden}} .settings-form{{max-height:calc(100vh - 34px);overflow-y:auto;padding:18px;display:grid;gap:12px}} .settings-heading{{position:sticky;top:-18px;z-index:2;display:flex;align-items:center;justify-content:space-between;background:var(--surface);padding:12px 0 8px;border-bottom:1px solid var(--border)}} .settings-heading h2{{margin:0}} .settings-heading button{{font-size:22px;line-height:1;margin:0}}
.settings-section{{position:relative;border:1px solid var(--border);border-radius:6px;background:var(--surface-soft);padding:12px}} .settings-section h3{{margin:0 0 10px}} .settings-section h4{{margin:0 34px 3px 0}} .settings-section p{{margin:4px 0 8px}} .settings-help{{color:var(--muted);font-size:12px}}
.source-flow{{font-weight:700;color:var(--muted)}} .source-card{{position:relative;display:grid;gap:7px;margin-top:9px;padding:10px;background:var(--surface);border:1px solid var(--border);border-radius:5px}} .source-card label,.settings-grid label{{display:grid;gap:4px}} .source-card input,.settings-grid input[type="email"]{{width:100%}}
.info-popover{{position:absolute;right:10px;top:8px}} .info-popover summary{{cursor:pointer;font-size:18px;list-style:none}} .info-popover p{{position:relative;z-index:3;max-width:560px;padding:8px;background:var(--surface);border:1px solid var(--border);box-shadow:0 3px 10px var(--shadow)}}
.field-row{{display:flex;align-items:center;flex-wrap:wrap;gap:7px}} .field-row button{{margin:0}} .validation-state{{color:var(--muted);font-size:12px}} .validation-state.success{{color:#28753b;font-weight:600}} :root[data-theme="dark"] .validation-state.success{{color:#78d68c}} .validation-state.failure{{color:var(--danger);font-weight:600}}
.settings-grid{{display:grid;grid-template-columns:repeat(2,minmax(240px,1fr));gap:8px 14px}} .settings-actions{{position:sticky;bottom:-18px;background:var(--surface);padding:10px 0 0;border-top:1px solid var(--border)}} .about p{{margin-bottom:0}}
.operation-overlay{{position:fixed;inset:0;z-index:1000;display:grid;place-items:center;background:#0008;padding:20px}} .operation-overlay[hidden]{{display:none}} .operation-card{{min-width:min(420px,calc(100vw - 40px));max-width:620px;display:grid;grid-template-columns:auto 1fr auto;align-items:center;gap:12px;padding:18px 20px;border:1px solid var(--border);border-radius:8px;background:var(--surface);box-shadow:0 12px 38px #0008}} .operation-spinner{{width:22px;height:22px;border:3px solid var(--border);border-top-color:var(--text);border-radius:50%;animation:operation-spin .8s linear infinite}} .operation-overlay.success .operation-spinner,.operation-overlay.failure .operation-spinner{{display:none}} .operation-overlay.success .operation-card{{border-color:#28753b}} .operation-overlay.failure .operation-card{{border-color:var(--danger)}} .operation-dismiss{{margin:0}} @keyframes operation-spin{{to{{transform:rotate(360deg)}}}}
dialog{{color:var(--text);background:var(--surface);border:1px solid var(--border);border-radius:6px;box-shadow:0 8px 30px var(--shadow);max-width:520px;width:calc(100% - 40px)}} dialog::backdrop{{background:#0006}} .dialog-form{{display:grid;gap:10px}} .dialog-form label{{display:block}} .dialog-form input[type="email"]{{width:100%;margin-top:4px}} .dialog-actions{{display:flex;align-items:center;gap:8px;margin-top:8px}} .dialog-actions button{{margin:0}} .smtp-status{{font-size:12px;color:var(--muted)}}
@media (max-width:900px){{.app-header{{position:relative;min-height:0;align-items:flex-start;flex-direction:column}}.header-actions{{justify-content:flex-start;margin-left:0}}th,#inactive-table th{{top:0}}}} @media (max-width:760px){{.xmltv-url-control{{flex-basis:100%;min-width:0}}.xmltv-url-control input{{min-width:0}}.settings-grid{{grid-template-columns:1fr}}.settings-dialog{{width:calc(100% - 16px);max-height:calc(100vh - 16px)}}.settings-form{{max-height:calc(100vh - 18px);padding:12px}}}}
</style>
</head>
<body>
<div id="operation-feedback" class="operation-overlay" hidden role="alertdialog" aria-modal="true" aria-labelledby="operation-message">
  <div class="operation-card">
    <span class="operation-spinner" aria-hidden="true"></span>
    <strong id="operation-message"></strong>
    <button id="operation-dismiss" class="operation-dismiss" type="button" hidden>Dismiss</button>
  </div>
</div>
<header class="app-header">
  <a class="brand" href="/" aria-label="WonkEPG home">
    <img id="brand-logo" src="/static/wonkepg-logo-light.png" alt="WonkEPG">
  </a>
  <nav class="header-actions" aria-label="Primary application actions">
    <button id="save">Save Mappings</button>
    <button id="build">Build XMLTV</button>
    <button id="refresh-sources">Refresh Sources</button>
    <button id="settings" type="button">Settings</button>
    <button id="logout" type="button">Sign Out</button>
    <button id="theme-toggle" class="theme-toggle" type="button"
      aria-label="Switch to dark mode" title="Switch color theme">
      <span class="theme-icon-light" aria-hidden="true">&#9789;</span>
      <span class="theme-icon-dark" aria-hidden="true">&#9728;</span>
    </button>
  </nav>
</header>
<main>
<section class="operations" aria-label="Operations and scheduler">
<div class="controls">
<input id="filter" placeholder="Filter number, name, or group">
<div class="xmltv-url-control">
  <label for="xmltv-url">XMLTV URL</label>
  <input id="xmltv-url" type="text" readonly aria-label="XMLTV URL">
  <button id="copy-xmltv-url" type="button">Copy</button>
  <span id="copy-xmltv-status" class="copy-status" role="status" aria-live="polite"></span>
</div>
<span id="status"></span>
</div>
<div id="operational-summary" class="operational-summary">
  <span id="op-stale-baseline">Stale Baselines: —</span>
  <span id="op-stale-epgshare">Stale {escape(enrichment_1_label)}: —</span>
  <span id="op-stale-epgtalk">Stale {escape(enrichment_2_label)}: —</span>
  <span id="op-no-schedule">No schedule: —</span>
  <span id="op-foundation-drift">Foundation drift: —</span>
  <span id="op-last-refresh">Last source refresh: —</span>
  <span id="op-last-build">Last XMLTV build: —</span>
  <span id="op-next-run">Scheduler next run: —</span>
</div>
<div id="source-summary" class="source-summary"></div>
 </section>
<section class="table-area">
<table id="active-table"><thead><tr><th>Active</th><th>Channel</th><th>Source / Foundation Name</th><th>Pretty Name</th><th>Logo</th><th>Foundation Group /<br>Baseline Schedule</th><th data-heading="enrichment_1">{escape(enrichment_1_label)}</th><th data-heading="enrichment_2">{escape(enrichment_2_label)}</th></tr></thead>
<tbody id="active-body">{"".join(rows)}</tbody></table>
<h2>Inactive / Discovered Channels</h2>
<p class="inactive-help">Check Active and save to append a discovered channel. Unchecked rows are never persisted.</p>
<table id="inactive-table"><thead><tr><th>Active</th><th>Channel</th><th>Source / Foundation Name</th><th>Pretty Name</th><th>Logo</th><th>Foundation Group /<br>Baseline Schedule</th><th data-heading="enrichment_1">{escape(enrichment_1_label)}</th><th data-heading="enrichment_2">{escape(enrichment_2_label)}</th></tr></thead>
<tbody id="inactive-body">{"".join(inactive_rows)}</tbody></table>
</section>
</main>
<dialog id="settings-dialog" class="settings-dialog" aria-labelledby="settings-dialog-title">
  <form class="settings-form" method="dialog">
    <div class="settings-heading">
      <h2 id="settings-dialog-title">Settings</h2>
      <button id="close-settings" type="button" aria-label="Close Settings">&times;</button>
    </div>

    <section class="settings-section" aria-labelledby="foundation-title">
      <h3 id="foundation-title">Channel Foundation</h3>
      <label>Configured M3U
        <input id="configured-m3u" type="text" autocomplete="off"
          placeholder="/path/to/channels.m3u or https://example/channels.m3u">
      </label>
      <div class="field-row">
        <button id="validate-foundation" type="button">Validate</button>
        <span id="foundation-validation" class="validation-state">Not validated</span>
      </div>
      <p class="settings-help">Curated M3U/M3U8 playlist used to establish the channels, numbers, names, groups, and logos that make up the guide.</p>
      <p class="settings-help">Defines which channels make up WonkEPG. Changing this setting does not regenerate or overwrite channels.json.</p>
    </section>

    <section class="settings-section" aria-labelledby="sources-title">
      <h3 id="sources-title">Program Guide Sources</h3>
      <p class="source-flow">Baseline Schedule &rarr; Enrichment 1 &rarr; Enrichment 2</p>
      <h4>Schedule Sources</h4>
      <p>Choose one default source for newly discovered channels. Every channel may select any configured schedule source.</p>
      <div id="schedule-source-list"></div>
      <button id="add-schedule-source" type="button">Add Schedule Source</button>
      <article class="source-card">
        <h4>Enrichment 1 — Artwork &amp; Presentation</h4>
        <p>Best for artwork and presentation metadata.</p>
        <details class="info-popover"><summary aria-label="Enrichment 1 information">&#9432;</summary><p>Choose a source with strong programme artwork, episode labels, categories, descriptions, ratings, and similar presentation metadata. This source enriches matched programmes but does not extend the canonical schedule.</p></details>
        <label>XMLTV URL <input id="enrichment1-url" type="url" autocomplete="off"></label>
        <div class="field-row"><button class="validate-source" type="button" data-url-field="enrichment1-url" data-provider-field="enrichment1-provider">Validate</button><span class="validation-state" data-validation-for="enrichment1-url">Not validated</span></div>
        <label>Provider Name <input id="enrichment1-provider" type="text" maxlength="100"></label>
      </article>
      <article class="source-card">
        <h4>Enrichment 2 — Horizon &amp; Metadata</h4>
        <p>Best for longer horizon and deep metadata.</p>
        <details class="info-popover"><summary aria-label="Enrichment 2 information">&#9432;</summary><p>Choose a source with a longer schedule horizon and rich metadata such as episode IDs, credits, ratings, original-air information, video/subtitle flags, and similar details. This source may extend the baseline schedule where WonkEPG’s existing conservative matching rules allow.</p></details>
        <label>Primary XMLTV URL <input id="enrichment2-primary-url" type="url" autocomplete="off"></label>
        <div class="field-row"><button class="validate-source" type="button" data-url-field="enrichment2-primary-url" data-provider-field="enrichment2-provider">Validate</button><span class="validation-state" data-validation-for="enrichment2-primary-url">Not validated</span></div>
        <label>Secondary / Local XMLTV URL <input id="enrichment2-secondary-url" type="url" autocomplete="off"></label>
        <div class="field-row"><button class="validate-source" type="button" data-url-field="enrichment2-secondary-url" data-provider-field="enrichment2-provider">Validate</button><span class="validation-state" data-validation-for="enrichment2-secondary-url">Not validated</span></div>
        <label>Provider Name <input id="enrichment2-provider" type="text" maxlength="100"></label>
      </article>
      <p class="settings-help">Baseline controls timing. Enrichment 1 enriches matches only. Enrichment 2 may conservatively extend the schedule. Each enrichment remains independent.</p>
    </section>

    <section class="settings-section" aria-labelledby="resolver-title">
      <h3 id="resolver-title">External Episode Resolution</h3>
      <p class="settings-help">Optional and cache-only during guide builds. Provider: TVmaze. Source identities are preserved; complete external identity is added only when the selected conservative resolution mode succeeds.</p>
      <label>Unresolved episode-only programme
        <select id="resolver-candidate"></select>
      </label>
      <label>Resolution mode
        <select id="resolver-mode">
          <option value="strict_episode_match">Strict episode-number match</option>
          <option value="date_anchored_identity">Date-anchored alternate identity</option>
        </select>
      </label>
      <div class="field-row">
        <button id="resolver-search" type="button">Search TVmaze</button>
        <button id="resolver-refresh" type="button">Refresh Confirmed Caches</button>
      </div>
      <label>TVmaze search results
        <select id="resolver-results"></select>
      </label>
      <button id="resolver-confirm" type="button" disabled>Confirm Show Binding</button>
      <div id="resolver-status" class="validation-state" role="status"></div>
      <pre id="resolver-detail" class="validation-state"></pre>
      <p class="settings-help">Search is an explicit onboarding action. Resolver outages and missing/conflicting matches never make XMLTV generation fail. TVmaze data is not bundled; API-derived data is CC BY-SA and requires attribution and ShareAlike compliance. WonkEPG’s Apache-2.0 code license does not relicense provider data.</p>
    </section>

    <section class="settings-section" aria-labelledby="schedule-title">
      <h3 id="schedule-title">EPG Guide Auto Refresh</h3>
      <div class="schedule-controls">
        <label><input id="schedule-enabled" type="checkbox"> Enabled</label>
        <label>Start time <select id="schedule-start-time" aria-label="Schedule start time">{start_time_options}</select></label>
        <label>Repeat every <input id="schedule-interval" type="number" min="1" max="10000" step="1"></label>
        <select id="schedule-unit" aria-label="Schedule interval unit"><option value="hours">hours</option><option value="days">days</option></select>
        <label><input id="schedule-immediate" type="checkbox"> Run immediately on startup</label>
      </div>
      <div id="schedule-run-status" class="schedule-run-status"></div>
    </section>

    <section class="settings-section" aria-labelledby="errors-title">
      <h3 id="errors-title">Error Handling</h3>
      <div class="settings-grid">
        <label><input id="notify-enabled" type="checkbox"> Enable email notifications</label>
        <label>Recipient email address <input id="notify-recipient" type="email" maxlength="254" autocomplete="email" placeholder="operator@example.com"></label>
        <label><input id="notify-source-refresh" type="checkbox"> Notify on source refresh failure</label>
        <label><input id="notify-build" type="checkbox"> Notify on XMLTV build failure</label>
        <label><input id="notify-stale-mappings" type="checkbox"> Notify on stale/missing enrichment mappings</label>
        <label><input id="notify-recovery" type="checkbox"> Notify on recovery</label>
      </div>
      <div id="smtp-status" class="smtp-status"></div>
      <button id="test-error-email" type="button">Send Test Email</button>
    </section>

    <section class="settings-section" aria-labelledby="asset-title">
      <h3 id="asset-title">HTTPS Asset Hosting</h3>
      <p><strong>Mode:</strong> <span id="asset-mode">—</span> · <strong>Host:</strong> <span id="asset-hostname">—</span></p>
      <p>Cloudflare API Token: <strong id="cloudflare-token-status">Not configured</strong></p>
      <div class="field-row">
        <button id="replace-cloudflare-token" type="button">Replace Token</button>
        <input id="cloudflare-token" type="password" autocomplete="new-password" placeholder="Paste new token" hidden>
        <button id="validate-cloudflare-token" type="button">Validate Token</button>
      </div>
      <div id="cloudflare-validation" class="validation-state" role="status"></div>
      <p class="settings-help">Token checks are read-only. Replacing this token does not update an independently deployed Caddy container.</p>
    </section>

    <section class="settings-section" aria-labelledby="bootstrap-title">
      <h3 id="bootstrap-title">Bootstrap Setup</h3>
      <button id="scan-logos" type="button">Scan Logos / Dry Run</button>
      <section id="logo-bootstrap-panel" class="logo-bootstrap" hidden>
        <strong>Logo bootstrap report</strong>
        <div id="bootstrap-summary" class="bootstrap-summary"></div>
        <div id="bootstrap-detail"></div>
        <button id="apply-bootstrap" type="button" disabled>Apply Bootstrap</button>
      </section>
    </section>

    <section class="settings-section" aria-labelledby="maintenance-title">
      <h3 id="maintenance-title">Maintenance</h3>
      <button id="restart-wonkepg" type="button">Restart WonkEPG</button>
      <span id="restart-status" class="validation-state" role="status" aria-live="polite"></span>
      <p class="settings-help">Guide serving will be unavailable briefly while the WonkEPG container restarts.</p>
    </section>

    <section class="settings-section about" aria-labelledby="about-title">
      <h3 id="about-title">About</h3>
      <p><strong>WonkEPG v{escape(version)}</strong><br>Self-hosted XMLTV aggregation, normalization, enrichment, and quality control.</p>
    </section>

    <div id="settings-message" role="status"></div>
    <div class="dialog-actions settings-actions">
      <button id="save-settings" type="button">Save Settings</button>
      <button id="cancel-settings" type="button">Close</button>
    </div>
  </form>
</dialog>
<input id="logo-file" type="file" accept="image/png,.png" hidden>
<script type="application/json" id="epgshare-catalog">{_catalog_json(epgshare)}</script>
<script type="application/json" id="epgtalk-catalog">{_catalog_json(epgtalk)}</script>
<script type="application/json" id="schedule-sources">{schedule_sources_json}</script>
<script type="application/json" id="operational-status">{operations_json}</script>
<script type="application/json" id="provider-names">{provider_names_json}</script>
<div id="combobox-options" class="combo-menu" role="listbox" hidden></div>
<pre id="result"></pre>
<script>
const providerNames=JSON.parse(
  document.getElementById("provider-names").textContent
);
const csrfToken={csrf_json};
async function adminFetch(url,options={{}}){{
  const requestOptions={{...options}};
  const method=String(requestOptions.method || "GET").toUpperCase();
  if(["POST","PUT","PATCH","DELETE"].includes(method)){{
    const headers=new Headers(requestOptions.headers || {{}});
    headers.set("X-WonkEPG-CSRF",csrfToken);
    requestOptions.headers=headers;
  }}
  const response=await window.fetch(url,requestOptions);
  if(response.status===401)window.location.assign("/login");
  return response;
}}
const statusNode=document.getElementById("status");
const resultNode=document.getElementById("result");
const sourceSummary=document.getElementById("source-summary");
const primaryOperationButtons=["save","build","refresh-sources"].map(
  id=>document.getElementById(id)
);
const operationFeedback=(()=>{{
  const overlay=document.getElementById("operation-feedback");
  const message=document.getElementById("operation-message");
  const dismiss=document.getElementById("operation-dismiss");
  let active=false;
  let dismissTimer=null;
  function setButtons(disabled){{
    for(const button of primaryOperationButtons)button.disabled=disabled;
  }}
  function close(){{
    if(active)return;
    overlay.hidden=true;
    overlay.className="operation-overlay";
    if(dismissTimer)window.clearTimeout(dismissTimer);
    dismissTimer=null;
  }}
  dismiss.addEventListener("click",close);
  return {{
    start(text){{
      if(active)return false;
      active=true;
      if(dismissTimer)window.clearTimeout(dismissTimer);
      dismissTimer=null;
      overlay.hidden=false;
      overlay.className="operation-overlay busy";
      message.textContent=text;
      dismiss.hidden=true;
      setButtons(true);
      return true;
    }},
    success(text){{
      active=false;
      setButtons(false);
      overlay.className="operation-overlay success";
      message.textContent=text;
      dismiss.hidden=false;
      dismissTimer=window.setTimeout(close,3500);
    }},
    failure(text){{
      active=false;
      setButtons(false);
      overlay.className="operation-overlay failure";
      message.textContent=text;
      dismiss.hidden=false;
    }},
    isActive(){{return active;}}
  }};
}})();
const operationalInitial=JSON.parse(
  document.getElementById("operational-status").textContent
);
const xmltvUrlInput=document.getElementById("xmltv-url");
const copyXmltvUrl=document.getElementById("copy-xmltv-url");
const copyXmltvStatus=document.getElementById("copy-xmltv-status");
function canonicalXmltvUrl(){{
  return `${{window.location.protocol}}//${{window.location.host}}/xmltv.xml`;
}}
xmltvUrlInput.value=canonicalXmltvUrl();
copyXmltvUrl.addEventListener("click",async()=>{{
  let copied=false;
  try{{
    if(navigator.clipboard && navigator.clipboard.writeText){{
      await navigator.clipboard.writeText(xmltvUrlInput.value);
      copied=true;
    }}
  }}catch(error){{
    copied=false;
  }}
  if(!copied){{
    xmltvUrlInput.focus();
    xmltvUrlInput.select();
    copied=document.execCommand("copy");
  }}
  copyXmltvStatus.textContent=copied ? "Copied" : "Copy failed";
  if(copied)xmltvUrlInput.setSelectionRange(0,xmltvUrlInput.value.length);
  window.setTimeout(()=>{{copyXmltvStatus.textContent="";}},1800);
}});
const themeToggle=document.getElementById("theme-toggle");
const brandLogo=document.getElementById("brand-logo");
function applyTheme(theme,persist=false){{
  document.documentElement.dataset.theme=theme;
  brandLogo.src=theme==="dark"
    ? "/static/wonkepg-logo-dark.png"
    : "/static/wonkepg-logo-light.png";
  themeToggle.setAttribute("aria-label",
    theme==="dark" ? "Switch to light mode" : "Switch to dark mode");
  themeToggle.setAttribute("aria-pressed",String(theme==="dark"));
  if(persist)localStorage.setItem("wonkepg-theme",theme);
}}
applyTheme(document.documentElement.dataset.theme || "light");
themeToggle.addEventListener("click",()=>{{
  applyTheme(document.documentElement.dataset.theme==="dark" ? "light" : "dark",true);
}});
matchMedia("(prefers-color-scheme: dark)").addEventListener("change",event=>{{
  if(!localStorage.getItem("wonkepg-theme"))applyTheme(event.matches ? "dark" : "light");
}});
const bootstrapPanel=document.getElementById("logo-bootstrap-panel");
const bootstrapSummary=document.getElementById("bootstrap-summary");
const bootstrapDetail=document.getElementById("bootstrap-detail");
const applyBootstrap=document.getElementById("apply-bootstrap");
const scheduleEnabled=document.getElementById("schedule-enabled");
const scheduleStartTime=document.getElementById("schedule-start-time");
const scheduleInterval=document.getElementById("schedule-interval");
const scheduleUnit=document.getElementById("schedule-unit");
const scheduleImmediate=document.getElementById("schedule-immediate");
const scheduleRunStatus=document.getElementById("schedule-run-status");
const menu=document.getElementById("combobox-options");
const MAX_RESULTS=40;
const catalogs={{}};
const scheduleSources=JSON.parse(
  document.getElementById("schedule-sources").textContent
);
let baselineCatalogsLoaded=false;
function prepareCatalog(items){{
  return items.map(item=>({{
    ...item,
    display_names:item.display_names || [item.display_name || item.channel_id],
    search:((item.provider_name || "")+" "+
      (item.display_names || [item.display_name || ""]).join(" ")+" "+
      item.channel_id).toLocaleLowerCase()
  }}));
}}
for(const source of ["epgshare","epgtalk"]){{
  catalogs[source]=prepareCatalog(
    JSON.parse(document.getElementById(source+"-catalog").textContent)
  );
}}
catalogs.baseline=[];

async function ensureBaselineCatalogs(){{
  if(baselineCatalogsLoaded)return;
  const available=scheduleSources.filter(source=>source.available);
  const responses=await Promise.all(available.map(async source=>{{
    const response=await adminFetch(
      `/sources/schedule/${{encodeURIComponent(source.source_id)}}/channels`
    );
    if(!response.ok)return [];
    const data=await response.json();
    return (data.channels || []).map(item=>({{
      ...item,
      source_id:source.source_id,
      provider_name:source.provider_name
    }}));
  }}));
  catalogs.baseline=prepareCatalog(responses.flat());
  baselineCatalogsLoaded=true;
}}

let openCombobox=null;
let activeIndex=-1;
let visibleResults=[];

function selectedLabel(combo){{
  const selectedId=combo.input.dataset.selectedId;
  const selected=catalogs[combo.source].find(item=>item.channel_id===selectedId);
  if(combo.source==="baseline"){{
    const sourceId=combo.input.dataset.selectedSource;
    const exact=catalogs.baseline.find(
      item=>item.source_id===sourceId && item.channel_id===selectedId
    );
    const source=scheduleSources.find(item=>item.source_id===sourceId);
    const provider=source ? source.provider_name : sourceId;
    return selectedId
      ? `${{provider}} · ${{exact ? exact.display_name : selectedId}}`
      : "";
  }}
  return selected ? selected.display_name : selectedId;
}}

function restoreSelection(combo){{
  combo.input.value=selectedLabel(combo);
  combo.idNode.textContent=combo.input.dataset.selectedId;
}}

function positionMenu(combo){{
  const rect=combo.input.getBoundingClientRect();
  menu.style.left=`${{rect.left}}px`;
  menu.style.top=`${{rect.bottom+2}}px`;
  menu.style.width=`${{Math.max(rect.width,280)}}px`;
}}

function setActive(index){{
  const options=Array.from(menu.querySelectorAll(".combo-option"));
  if(!options.length){{activeIndex=-1;return;}}
  activeIndex=(index+options.length)%options.length;
  options.forEach((option,i)=>{{
    const isActive=i===activeIndex;
    option.classList.toggle("active",isActive);
    option.setAttribute("aria-selected",String(isActive));
  }});
  const active=options[activeIndex];
  openCombobox.input.setAttribute("aria-activedescendant",active.id);
  active.scrollIntoView({{block:"nearest"}});
}}

async function renderResults(combo,query=""){{
  if(combo.source==="baseline")await ensureBaselineCatalogs();
  const normalized=query.trim().toLocaleLowerCase();
  const matches=catalogs[combo.source].filter(item=>!normalized || item.search.includes(normalized));
  visibleResults=matches.slice(0,MAX_RESULTS);
  menu.replaceChildren();
  for(const [index,item] of visibleResults.entries()){{
    const option=document.createElement("div");
    option.className="combo-option";
    option.id=`combobox-option-${{index}}`;
    option.role="option";
    option.dataset.index=index;
    const name=document.createElement("div");
    name.className="option-name";
    name.textContent=combo.source==="baseline"
      ? `${{item.provider_name}} · ${{item.display_name}}`
      : item.display_name;
    const id=document.createElement("div");
    id.className="option-id";
    id.textContent=item.channel_id;
    option.append(name,id);
    menu.append(option);
  }}
  if(!visibleResults.length || matches.length>MAX_RESULTS){{
    const message=document.createElement("div");
    message.className="combo-message";
    message.textContent=matches.length ? `Showing ${{MAX_RESULTS}} of ${{matches.length}}; type to narrow` : "No matching channels";
    menu.append(message);
  }}
  activeIndex=-1;
  combo.input.removeAttribute("aria-activedescendant");
  positionMenu(combo);
}}

async function openMenu(combo,query=""){{
  if(openCombobox && openCombobox!==combo) restoreSelection(openCombobox);
  openCombobox=combo;
  menu.replaceChildren();
  const loading=document.createElement("div");
  loading.className="combo-message";
  loading.textContent="Loading channels…";
  menu.append(loading);
  menu.hidden=false;
  combo.input.setAttribute("aria-expanded","true");
  await renderResults(combo,query);
}}

function closeMenu(restore=true){{
  if(!openCombobox)return;
  if(restore)restoreSelection(openCombobox);
  openCombobox.input.setAttribute("aria-expanded","false");
  openCombobox.input.removeAttribute("aria-activedescendant");
  openCombobox=null;
  visibleResults=[];
  menu.hidden=true;
}}

function choose(combo,item){{
  combo.input.dataset.selectedId=item.channel_id;
  if(combo.source==="baseline"){{
    combo.input.dataset.selectedSource=item.source_id;
    combo.input.value=`${{item.provider_name}} · ${{item.display_name}}`;
  }}else{{
    combo.input.value=item.display_name;
  }}
  combo.idNode.textContent=item.channel_id;
  combo.clearButton.hidden=false;
  combo.root.querySelector(".stale-selection").hidden=true;
  closeMenu(false);
}}

document.getElementById("filter").addEventListener("input",event=>{{
  const value=event.target.value.trim().toLocaleLowerCase();
  document.querySelectorAll("tbody tr").forEach(row=>{{
    row.hidden=Boolean(value && !row.dataset.search.includes(value));
  }});
}});

document.querySelectorAll(".combobox").forEach(root=>{{
  const combo={{
    root,
    source:root.dataset.source,
    input:root.querySelector(".mapping"),
    clearButton:root.querySelector(".clear"),
    idNode:root.querySelector(".selection-id")
  }};
  combo.input.addEventListener("focus",async()=>{{
    await openMenu(combo);
    combo.input.select();
  }});
  combo.input.addEventListener("click",()=>combo.input.select());
  combo.input.addEventListener("input",async()=>await openMenu(combo,combo.input.value));
  combo.input.addEventListener("keydown",async event=>{{
    if(event.key==="ArrowDown" || event.key==="ArrowUp"){{
      event.preventDefault();
      if(openCombobox!==combo)await openMenu(combo,combo.input.value);
      const direction=event.key==="ArrowDown"?1:-1;
      setActive(activeIndex<0 ? (direction>0 ? 0 : visibleResults.length-1) : activeIndex+direction);
    }}else if(event.key==="Enter" && openCombobox===combo && activeIndex>=0){{
      event.preventDefault();
      choose(combo,visibleResults[activeIndex]);
    }}else if(event.key==="Escape"){{
      event.preventDefault();
      closeMenu();
    }}else if(event.key==="Tab"){{
      closeMenu();
    }}
  }});
  combo.clearButton.addEventListener("click",()=>{{
    combo.input.dataset.selectedId="";
    if(combo.source==="baseline")combo.input.dataset.selectedSource="";
    combo.input.value="";
    combo.idNode.textContent="";
    combo.clearButton.hidden=true;
    combo.root.querySelector(".stale-selection").hidden=true;
    combo.input.focus();
    openMenu(combo);
  }});
}});

document.querySelectorAll(".pretty-lock").forEach(button=>{{
  button.addEventListener("click",()=>{{
    const input=button.closest(".pretty-control").querySelector(".pretty-name");
    const unlocking=input.readOnly;
    input.readOnly=!unlocking;
    button.textContent=unlocking ? "🔓" : "🔒";
    button.title=unlocking ? "Lock Pretty Name" : "Unlock Pretty Name";
    button.setAttribute("aria-label",button.title);
    button.setAttribute("aria-pressed",String(unlocking));
    if(unlocking){{input.focus();input.select();}}
  }});
}});

const logoFile=document.getElementById("logo-file");
let logoRow=null;
document.querySelectorAll(".upload-logo").forEach(button=>{{
  button.addEventListener("click",()=>{{
    closeMenu();
    logoRow=button.closest("tr");
    logoFile.value="";
    logoFile.click();
  }});
}});
logoFile.addEventListener("change",async()=>{{
  const file=logoFile.files[0];
  if(!file || !logoRow)return;
  const targetRow=logoRow;
  const button=targetRow.querySelector(".upload-logo");
  const number=targetRow.dataset.number;
  const form=new FormData();
  form.append("file",file);
  button.disabled=true;
  statusNode.textContent=`Uploading logo for ${{number}}...`;
  try{{
    const inactive=targetRow.dataset.inactive==="true" ? "?inactive=true" : "";
    const response=await adminFetch(`/channels/${{encodeURIComponent(number)}}/logo${{inactive}}`,{{
      method:"POST",
      body:form
    }});
    const data=await response.json();
    if(!response.ok)throw new Error(data.detail || "Logo upload failed");
    const control=targetRow.querySelector(".logo-control");
    const image=control.querySelector(".logo-thumbnail");
    control.dataset.logoUrl=data.logo;
    image.src=`${{data.logo}}?v=${{Date.now()}}`;
    image.hidden=false;
    statusNode.textContent="Logo uploaded";
    resultNode.textContent=JSON.stringify(data,null,2);
  }}catch(error){{
    statusNode.textContent="Logo upload failed";
    resultNode.textContent=error.message;
  }}finally{{
    button.disabled=false;
    logoRow=null;
    logoFile.value="";
  }}
}});

menu.addEventListener("pointerdown",event=>{{
  const option=event.target.closest(".combo-option");
  if(!option || !openCombobox)return;
  event.preventDefault();
  choose(openCombobox,visibleResults[Number(option.dataset.index)]);
}});
document.addEventListener("pointerdown",event=>{{
  if(openCombobox && !openCombobox.root.contains(event.target) && !menu.contains(event.target))closeMenu();
}});
window.addEventListener("resize",()=>{{if(openCombobox)positionMenu(openCombobox)}});
window.addEventListener("scroll",()=>{{if(openCombobox)positionMenu(openCombobox)}},true);

function displayTime(value){{
  return value ? new Date(value).toLocaleString() : "never";
}}

function setOperationalCount(id,label,value){{
  const node=document.getElementById(id);
  const count=value ?? "—";
  node.textContent=label+": "+count;
  node.classList.toggle("operational-warning",Number(value)>0);
}}

function renderOperationalStatus(data){{
  setOperationalCount(
    "op-stale-baseline","Stale Baselines",data.stale_baseline_count
  );
  setOperationalCount(
    "op-stale-epgshare","Stale "+providerNames.epgshare,data.stale_epgshare_count
  );
  setOperationalCount(
    "op-stale-epgtalk","Stale "+providerNames.epgtalk,data.stale_epgtalk_count
  );
  setOperationalCount(
    "op-no-schedule","No schedule",data.channels_with_no_schedule
  );
  if(data.foundation_drift_warnings != null){{
    setOperationalCount(
      "op-foundation-drift","Foundation drift",
      data.foundation_drift_warnings
    );
  }}
  if("last_source_refresh" in data){{
    document.getElementById("op-last-refresh").textContent=
      "Last source refresh: "+displayTime(data.last_source_refresh);
  }}
  if("last_xmltv_build" in data){{
    const lastBuild=data.last_xmltv_build;
    document.getElementById("op-last-build").textContent=lastBuild
      ? "Last XMLTV build: "+displayTime(lastBuild.finished_at)+
        " — "+(lastBuild.success ? "success" : "failed")
      : "Last XMLTV build: never";
  }}
  if("scheduler_next_run" in data){{
    document.getElementById("op-next-run").textContent=
      "Scheduler next run: "+
      (data.scheduler_next_run ? displayTime(data.scheduler_next_run) : "disabled");
  }}
}}

function updateStaleWarnings(data){{
  const stale={{epgshare:new Set(),epgtalk:new Set()}};
  const staleBaselines=new Set();
  for(const item of data.stale_baseline_mappings || []){{
    staleBaselines.add(
      String(item.number)+"|"+item.source+"|"+item.channel_id
    );
  }}
  for(const source of ["epgshare","epgtalk"]){{
    for(const item of data["stale_"+source+"_mappings"] || []){{
      stale[source].add(String(item.number)+"|"+item.channel_id);
    }}
  }}
  document.querySelectorAll("tbody tr").forEach(row=>{{
    for(const root of row.querySelectorAll(".combobox")){{
      const selected=root.querySelector(".mapping").dataset.selectedId;
      const selectedSource=root.querySelector(".mapping").dataset.selectedSource;
      const isStale=root.dataset.source==="baseline"
        ? selected && selectedSource && staleBaselines.has(
          row.dataset.number+"|"+selectedSource+"|"+selected
        )
        : selected && stale[root.dataset.source].has(
          row.dataset.number+"|"+selected
        );
      root.querySelector(".stale-selection").hidden=!isStale;
    }}
  }});
}}

async function loadOperationalStatus(){{
  const response=await adminFetch("/operations/status");
  const data=await response.json();
  if(!response.ok)throw new Error(data.detail || "Operational status failed");
  renderOperationalStatus(data);
  updateStaleWarnings(data);
  return data;
}}

async function reloadCatalogsAndWarnings(){{
  const [shareResponse,talkResponse]=await Promise.all([
    adminFetch("/sources/epgshare/channels"),
    adminFetch("/sources/epgtalk/channels")
  ]);
  const share=await shareResponse.json();
  const talk=await talkResponse.json();
  if(!shareResponse.ok || !talkResponse.ok){{
    throw new Error("Refreshed source catalogs could not be loaded");
  }}
  catalogs.epgshare=prepareCatalog(share.channels || []);
  catalogs.epgtalk=prepareCatalog(talk.channels || []);
  await loadOperationalStatus();
}}

renderOperationalStatus(operationalInitial);
updateStaleWarnings(operationalInitial);

function renderSourceStatus(data){{
  sourceSummary.replaceChildren();
  const refreshTimes=(data.sources || [])
    .map(source=>source.last_successful_refresh).filter(Boolean);
  document.getElementById("op-last-refresh").textContent=
    "Last source refresh: "+displayTime(
      refreshTimes.length ? refreshTimes.sort().at(-1) : null
    );
  for(const source of data.sources || []){{
    const line=document.createElement("span");
    const refreshed=source.last_successful_refresh
      ? new Date(source.last_successful_refresh).toLocaleString()
      : "never";
    line.className=source.success ? "source-ok" : "source-failed";
    line.textContent=`${{providerNames[source.source] || source.source}}: ${{source.success ? "OK" : "failed"}} · ${{source.channel_count}} channels · ${{source.programme_count}} programmes · refreshed ${{refreshed}}`;
    sourceSummary.append(line);
  }}
}}

async function loadSourceStatus(){{
  const response=await adminFetch("/sources/status");
  renderSourceStatus(await response.json());
}}

document.getElementById("refresh-sources").addEventListener("click",async()=>{{
  if(!operationFeedback.start("Refreshing sources…"))return;
  statusNode.textContent="Refreshing sources...";
  try{{
    const response=await adminFetch("/sources/refresh",{{method:"POST"}});
    const data=await response.json();
    if(!response.ok)throw new Error("refresh failed");
    renderSourceStatus(data);
    const successful=(data.sources || []).filter(source=>source.success).length;
    const total=(data.sources || []).length;
    statusNode.textContent=`Sources refreshed: ${{successful}}/${{total}} successful`;
    resultNode.textContent=JSON.stringify(data,null,2);
    operationFeedback.success(`✓ Sources refreshed · ${{successful}}/${{total}} successful`);
    try{{await reloadCatalogsAndWarnings();}}catch(error){{/* Best-effort UI refresh. */}}
  }}catch(error){{
    statusNode.textContent="Source refresh failed";
    resultNode.textContent="Source refresh failed; existing cached sources were preserved.";
    operationFeedback.failure("Source refresh failed");
  }}
}});
loadSourceStatus();

let logoBootstrapScanId=null;
const bootstrapLabels={{
  total_channels:"scanned",valid_png:"valid PNG",already_local:"already local",
  no_logo:"no logo",jpeg:"JPEG skipped",svg:"SVG skipped",webp:"WebP skipped",
  unknown_non_png:"unknown non-PNG",unreachable:"unreachable",
  other_failure:"other failures",duplicate_pngs:"byte-identical duplicates",
  configured_m3u_png_candidates:"Configured M3U PNG",external_png_candidates:"external PNG"
}};

function renderBootstrapScan(data){{
  logoBootstrapScanId=data.scan_id;
  bootstrapPanel.hidden=false;
  bootstrapSummary.replaceChildren();
  for(const [key,label] of Object.entries(bootstrapLabels)){{
    const item=document.createElement("span");
    item.textContent=`${{label}}: ${{data.summary[key] ?? 0}}`;
    bootstrapSummary.append(item);
  }}
  bootstrapDetail.replaceChildren();
  const attention=data.manual_attention || [];
  const heading=document.createElement("div");
  heading.textContent=`Manual attention: ${{attention.length}} channel${{attention.length===1?"":"s"}}`;
  bootstrapDetail.append(heading);
  if(attention.length){{
    const wrapper=document.createElement("div");
    wrapper.className="manual-attention";
    const table=document.createElement("table");
    const head=document.createElement("thead");
    head.innerHTML="<tr><th>Channel</th><th>Name</th><th>Current logo URL</th><th>Reason</th></tr>";
    const body=document.createElement("tbody");
    for(const item of attention){{
      const row=document.createElement("tr");
      for(const [value,className] of [
        [item.number,""],[item.name,""],[item.logo || "—","logo-url"],
        [item.reason || item.category,""]
      ]){{
        const cell=document.createElement("td");
        cell.textContent=value;
        if(className)cell.className=className;
        row.append(cell);
      }}
      body.append(row);
    }}
    table.append(head,body);
    wrapper.append(table);
    bootstrapDetail.append(wrapper);
  }}
  applyBootstrap.hidden=!data.summary.valid_png;
  applyBootstrap.disabled=false;
}}

document.getElementById("scan-logos").addEventListener("click",async event=>{{
  event.target.disabled=true;
  bootstrapPanel.hidden=false;
  applyBootstrap.disabled=true;
  logoBootstrapScanId=null;
  settingsMessage.textContent="Scanning current logos (dry run)...";
  try{{
    const response=await adminFetch("/logos/bootstrap/scan",{{method:"POST"}});
    const data=await response.json();
    if(!response.ok)throw new Error(data.detail || "Logo scan failed");
    renderBootstrapScan(data);
    const eligible=data.summary.valid_png || 0;
    applyBootstrap.hidden=false;
    applyBootstrap.disabled=eligible===0;
    if(!eligible){{
      const none=document.createElement("p");
      none.textContent="No eligible changes.";
      bootstrapDetail.prepend(none);
    }}
    settingsMessage.textContent=`Logo dry run complete: ${{eligible}} eligible change${{eligible===1?"":"s"}}.`;
  }}catch(error){{
    settingsMessage.textContent="Logo scan failed: "+error.message;
  }}finally{{
    event.target.disabled=false;
  }}
}});

applyBootstrap.addEventListener("click",async()=>{{
  if(!logoBootstrapScanId || applyBootstrap.disabled)return;
  if(!window.confirm("Apply the eligible logo bootstrap changes?"))return;
  applyBootstrap.disabled=true;
  settingsMessage.textContent="Applying logo bootstrap...";
  try{{
    const response=await adminFetch("/logos/bootstrap/apply",{{
      method:"POST",headers:{{"Content-Type":"application/json"}},
      body:JSON.stringify({{scan_id:logoBootstrapScanId}})
    }});
    const data=await response.json();
    if(!response.ok)throw new Error(data.detail || "Logo bootstrap failed");
    for(const item of data.migrated_channels || []){{
      const row=document.querySelector(`tbody tr[data-number="${{CSS.escape(item.number)}}"]`);
      if(!row)continue;
      const control=row.querySelector(".logo-control");
      const image=control.querySelector(".logo-thumbnail");
      control.dataset.logoUrl=item.logo;
      image.src=`${{item.logo}}?v=${{Date.now()}}`;
      image.hidden=false;
    }}
    const finalReport=document.createElement("p");
    finalReport.textContent=`Applied ${{data.migrated}} · Skipped/failures ${{data.skipped}}`;
    bootstrapDetail.prepend(finalReport);
    if((data.skipped_channels || []).length){{
      const skippedList=document.createElement("ul");
      for(const item of data.skipped_channels){{
        const entry=document.createElement("li");
        entry.textContent=`${{item.number}} ${{item.name || ""}} — ${{item.reason}}`;
        skippedList.append(entry);
      }}
      bootstrapDetail.append(skippedList);
    }}
    settingsMessage.textContent=`Bootstrap applied: ${{data.migrated}} migrated, ${{data.skipped}} skipped.`;
    resultNode.textContent=JSON.stringify(data,null,2);
  }}catch(error){{
    settingsMessage.textContent="Logo bootstrap failed: "+error.message;
    applyBootstrap.disabled=false;
  }}
}});

const settingsDialog=document.getElementById("settings-dialog");
const settingsMessage=document.getElementById("settings-message");
const foundationInput=document.getElementById("configured-m3u");
const foundationValidation=document.getElementById("foundation-validation");
const scheduleSourceList=document.getElementById("schedule-source-list");
const sourceFields={{
  enrichment_1:{{url:document.getElementById("enrichment1-url"),provider:document.getElementById("enrichment1-provider")}},
  enrichment_2:{{
    primary:document.getElementById("enrichment2-primary-url"),
    secondary:document.getElementById("enrichment2-secondary-url"),
    provider:document.getElementById("enrichment2-provider")
  }}
}};

function markNotValidated(input){{
  const state=document.querySelector(`[data-validation-for="${{input.id}}"]`);
  state.textContent="Not validated";
  state.className="validation-state";
}}
for(const input of [
  sourceFields.enrichment_1.url,
  sourceFields.enrichment_2.primary,sourceFields.enrichment_2.secondary
])input.addEventListener("input",()=>markNotValidated(input));
foundationInput.addEventListener("input",()=>{{
  foundationValidation.textContent="Not validated";
  foundationValidation.className="validation-state";
}});

function updateMappingHeadings(){{
  for(const node of document.querySelectorAll('[data-heading="enrichment_1"]')){{
    node.textContent=providerNames.epgshare;
  }}
  for(const node of document.querySelectorAll('[data-heading="enrichment_2"]')){{
    node.textContent=providerNames.epgtalk;
  }}
}}

function appendLabeledInput(card,labelText,className,type="text"){{
  const label=document.createElement("label");
  label.append(document.createTextNode(labelText));
  const input=document.createElement("input");
  input.type=type;
  input.className=className;
  if(type==="url")input.autocomplete="off";
  label.append(input);
  card.append(label);
  return input;
}}

function addScheduleSourceCard(sourceId,source,isDefault){{
  const card=document.createElement("article");
  card.className="source-card schedule-source-card";
  card.dataset.sourceId=sourceId;
  const heading=document.createElement("h4");
  heading.textContent="Schedule Source";
  card.append(heading);
  const idInput=appendLabeledInput(card,"Source ID","schedule-source-id");
  idInput.value=sourceId;
  idInput.readOnly=true;
  const url=appendLabeledInput(card,"XMLTV URL","schedule-source-url","url");
  url.value=source.url || "";
  url.id=`schedule-source-url-${{sourceId}}`;
  const provider=appendLabeledInput(
    card,"Provider Name","schedule-source-provider"
  );
  provider.value=source.provider_name || "";
  provider.maxLength=100;
  provider.id=`schedule-source-provider-${{sourceId}}`;
  const actions=document.createElement("div");
  actions.className="field-row";
  const validate=document.createElement("button");
  validate.type="button";
  validate.className="validate-source";
  validate.dataset.urlField=url.id;
  validate.dataset.providerField=provider.id;
  validate.textContent="Validate";
  const validation=document.createElement("span");
  validation.className="validation-state";
  validation.dataset.validationFor=url.id;
  const availability=scheduleSources.find(item=>item.source_id===sourceId);
  validation.textContent=availability
    ? (availability.available
      ? `Available · ${{availability.channel_count.toLocaleString()}} channels`
      : "No usable cache")
    : "Not refreshed";
  const defaultLabel=document.createElement("label");
  const radio=document.createElement("input");
  radio.type="radio";
  radio.name="default-schedule-source";
  radio.checked=isDefault;
  defaultLabel.append(radio,document.createTextNode(" Default"));
  const remove=document.createElement("button");
  remove.type="button";
  remove.textContent="Remove";
  remove.addEventListener("click",()=>card.remove());
  actions.append(validate,validation,defaultLabel,remove);
  card.append(actions);
  url.addEventListener("input",()=>markNotValidated(url));
  bindValidationButton(validate);
  scheduleSourceList.append(card);
}}

document.getElementById("add-schedule-source").addEventListener("click",()=>{{
  const sourceId=(window.prompt(
    "Stable source ID (lowercase letters, numbers, hyphens)"
  ) || "").trim();
  if(!sourceId)return;
  if(!/^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$/.test(sourceId)){{
    settingsMessage.textContent="Invalid schedule source ID.";
    return;
  }}
  if(scheduleSourceList.querySelector(`[data-source-id="${{sourceId}}"]`)){{
    settingsMessage.textContent="That schedule source ID already exists.";
    return;
  }}
  addScheduleSourceCard(sourceId,{{url:"",provider_name:""}},false);
}});

function renderSources(data){{
  const redacted=new Set(data.redacted_urls || []);
  foundationInput.value=data.foundation.configured_m3u || "";
  foundationInput.placeholder=redacted.has("foundation.configured_m3u")
    ? "Configured URL hidden; enter a replacement to change it"
    : "/foundation/channels.m3u or https://guide.example.com/channels.m3u";
  foundationValidation.textContent="Not validated";
  foundationValidation.className="validation-state";
  scheduleSourceList.replaceChildren();
  for(const [sourceId,source] of Object.entries(data.schedule_sources || {{}})){{
    addScheduleSourceCard(
      sourceId,source,sourceId===data.baseline.default_source
    );
    const scheduleUrl=document.getElementById(`schedule-source-url-${{sourceId}}`);
    if(redacted.has(`schedule_sources.${{sourceId}}.url`))
      scheduleUrl.placeholder="Configured URL hidden; enter a replacement to change it";
    providerNames[sourceId]=source.provider_name || sourceId;
  }}
  sourceFields.enrichment_1.url.value=data.enrichment_1.url || "";
  if(redacted.has("enrichment_1.url"))
    sourceFields.enrichment_1.url.placeholder="Configured URL hidden; enter a replacement to change it";
  sourceFields.enrichment_1.provider.value=data.enrichment_1.provider_name || "";
  sourceFields.enrichment_2.primary.value=data.enrichment_2.primary_url || "";
  sourceFields.enrichment_2.secondary.value=data.enrichment_2.secondary_url || "";
  if(redacted.has("enrichment_2.primary_url"))
    sourceFields.enrichment_2.primary.placeholder="Configured URL hidden; enter a replacement to change it";
  if(redacted.has("enrichment_2.secondary_url"))
    sourceFields.enrichment_2.secondary.placeholder="Configured URL hidden; enter a replacement to change it";
  sourceFields.enrichment_2.provider.value=data.enrichment_2.provider_name || "";
  providerNames.epgshare=data.enrichment_1.provider_name || "Enrichment 1";
  providerNames.epgtalk=data.enrichment_2.provider_name || "Enrichment 2";
  providerNames.epgtalk_local=providerNames.epgtalk;
  updateMappingHeadings();
  for(const input of [
    sourceFields.enrichment_1.url,
    sourceFields.enrichment_2.primary,sourceFields.enrichment_2.secondary
  ])markNotValidated(input);
}}

function sourcePayload(){{
  const schedule_sources={{}};
  let default_source="";
  for(const card of scheduleSourceList.querySelectorAll(".schedule-source-card")){{
    const sourceId=card.dataset.sourceId;
    schedule_sources[sourceId]={{
      url:card.querySelector(".schedule-source-url").value.trim(),
      provider_name:card.querySelector(".schedule-source-provider").value.trim()
    }};
    if(card.querySelector('input[name="default-schedule-source"]').checked){{
      default_source=sourceId;
    }}
  }}
  return {{
    schema_version:2,
    foundation:{{configured_m3u:foundationInput.value.trim()}},
    baseline:{{default_source}},
    schedule_sources,
    enrichment_1:{{url:sourceFields.enrichment_1.url.value.trim(),provider_name:sourceFields.enrichment_1.provider.value.trim()}},
    enrichment_2:{{
      primary_url:sourceFields.enrichment_2.primary.value.trim(),
      secondary_url:sourceFields.enrichment_2.secondary.value.trim(),
      provider_name:sourceFields.enrichment_2.provider.value.trim()
    }}
  }};
}}

document.getElementById("validate-foundation").addEventListener("click",async event=>{{
  event.target.disabled=true;
  foundationValidation.textContent="Validating...";
  foundationValidation.className="validation-state";
  try{{
    const data=await postJson("/settings/foundation/validate",{{
      configured_m3u:foundationInput.value.trim()
    }});
    let message=`✓ Valid M3U · ${{data.channel_count.toLocaleString()}} channels · ${{data.numbered_channel_count.toLocaleString()}} numbered · ${{data.unique_channel_number_count.toLocaleString()}} unique numbers`;
    if(data.duplicate_channel_number_count){{
      message+=` · ${{data.duplicate_channel_number_count}} duplicate numbers`;
    }}
    if(data.missing_channel_number_count){{
      message+=` · ${{data.missing_channel_number_count}} missing numbers`;
    }}
    if((data.advisories || []).length){{
      message+=" · Advisory: "+data.advisories.join("; ");
    }}
    foundationValidation.textContent=message;
    foundationValidation.className="validation-state success";
  }}catch(error){{
    foundationValidation.textContent=error.message;
    foundationValidation.className="validation-state failure";
  }}finally{{
    event.target.disabled=false;
  }}
}});

function bindValidationButton(button){{
  button.addEventListener("click",async()=>{{
    const input=document.getElementById(button.dataset.urlField);
    const provider=document.getElementById(button.dataset.providerField);
    const state=document.querySelector(`[data-validation-for="${{input.id}}"]`);
    button.disabled=true;
    state.textContent="Validating...";
    state.className="validation-state";
    try{{
      const response=await adminFetch("/settings/sources/validate",{{
        method:"POST",headers:{{"Content-Type":"application/json"}},
        body:JSON.stringify({{url:input.value.trim()}})
      }});
      const data=await response.json();
      if(!response.ok)throw new Error(data.detail || "Validation failed");
      const horizon=data.horizon_days===null ? "" : ` · horizon ${{data.horizon_days}} days`;
      const detected=data.detected_provider_name ? ` · ${{data.detected_provider_name}}` : "";
      state.textContent=`✓ Valid XMLTV · ${{data.channel_count.toLocaleString()}} channels · ${{data.programme_count.toLocaleString()}} programmes${{horizon}}${{detected}}`;
      state.className="validation-state success";
      if(!provider.value.trim())provider.value=data.suggested_provider_name || "XMLTV Source";
    }}catch(error){{
      state.textContent=error.message;
      state.className="validation-state failure";
    }}finally{{
      button.disabled=false;
    }}
  }});
}}
document.querySelectorAll(".validate-source").forEach(bindValidationButton);

function renderScheduleStatus(data){{
  const schedule=data.schedule;
  scheduleEnabled.checked=schedule.enabled;
  scheduleStartTime.value=schedule.start_time;
  scheduleInterval.value=schedule.interval;
  scheduleUnit.value=schedule.unit;
  scheduleImmediate.checked=schedule.run_immediately_on_startup;
  const last=data.last_scheduled_run;
  const lastText=last ? `Last run: ${{new Date(last.finished_at).toLocaleString()}} — ${{last.success ? "success" : "failed"}}` : "Last run: never";
  const nextText=data.next_run_at ? ` · Next: ${{new Date(data.next_run_at).toLocaleString()}}` : "";
  scheduleRunStatus.textContent=lastText+nextText;
  document.getElementById("op-next-run").textContent="Scheduler next run: "+(data.next_run_at ? displayTime(data.next_run_at) : "disabled");
}}

const notifyFields={{
  enabled:document.getElementById("notify-enabled"),
  recipient:document.getElementById("notify-recipient"),
  notify_source_refresh:document.getElementById("notify-source-refresh"),
  notify_build:document.getElementById("notify-build"),
  notify_stale_mappings:document.getElementById("notify-stale-mappings"),
  notify_recovery:document.getElementById("notify-recovery")
}};
function renderErrorHandling(data){{
  for(const [key,field] of Object.entries(notifyFields)){{
    if(field.type==="checkbox")field.checked=Boolean(data[key]);
    else field.value=data[key] || "";
  }}
  const smtp=document.getElementById("smtp-status");
  smtp.textContent=data.smtp.configured ? "SMTP is configured." : "SMTP is not configured. Missing: "+data.smtp.missing.join(", ");
  document.getElementById("test-error-email").disabled=!data.smtp.configured;
}}
function notificationPayload(){{
  const payload={{}};
  for(const [key,field] of Object.entries(notifyFields))payload[key]=field.type==="checkbox" ? field.checked : field.value.trim();
  return payload;
}}
function schedulePayload(){{
  return {{
    enabled:scheduleEnabled.checked,start_time:scheduleStartTime.value,
    interval:Number(scheduleInterval.value),unit:scheduleUnit.value,
    run_immediately_on_startup:scheduleImmediate.checked
  }};
}}
async function postJson(url,payload){{
  const response=await adminFetch(url,{{method:"POST",headers:{{"Content-Type":"application/json"}},body:JSON.stringify(payload)}});
  const data=await response.json();
  if(!response.ok)throw new Error(data.detail || "Request failed");
  return data;
}}
async function loadAssetHosting(){{
  const response=await adminFetch("/settings/asset-hosting");
  const data=await response.json();
  document.getElementById("asset-mode").textContent=data.mode;
  document.getElementById("asset-hostname").textContent=data.asset_hostname;
  document.getElementById("cloudflare-token-status").textContent=data.token.configured ? "Configured" : "Not configured";
  document.getElementById("validate-cloudflare-token").disabled=!data.token.configured;
}}

const resolverCandidate=document.getElementById("resolver-candidate");
const resolverResults=document.getElementById("resolver-results");
const resolverMode=document.getElementById("resolver-mode");
const resolverStatus=document.getElementById("resolver-status");
let resolverCandidates=[];
let resolverSearchResults=[];
function renderResolverStatus(data){{
  resolverCandidates=data.unresolved_shows || [];
  resolverCandidate.replaceChildren();
  for(const item of resolverCandidates){{
    const option=document.createElement("option");
    option.value=item.key;
    option.textContent=`${{item.channel}} — ${{item.title}} (${{item.records}})`;
    resolverCandidate.appendChild(option);
  }}
  if(!resolverCandidates.length){{
    const option=document.createElement("option");
    option.textContent="No unbound episode-only programmes";
    option.value="";resolverCandidate.appendChild(option);
  }}
  const last=data.last_build || {{}};
  resolverStatus.textContent=
    `${{data.confirmed_bindings}} confirmed · ${{data.candidate_count}} candidate records · `+
    `${{last.resolved_records || 0}} resolved · ${{last.conflict_records || 0}} conflicts · `+
    "normal-build API calls: 0";
  const bindings=(data.bindings || []).map(item=>
    `${{item.key}} → ${{item.provider}}:${{item.show_id}} · ${{item.mode}} · cache ${{item.cache_available ? "ready" : "missing"}}`
  );
  const recent=(last.details || []).filter(item=>
    item.status==="episode_number_conflict" || item.status==="resolver_unavailable"
  ).slice(0,10).map(item=>
    `${{item.status}} · ${{item.channel || ""}} · ${{item.title || ""}} · source E${{item.source_episode || "?"}} / external E${{item.external_episode || "?"}}`
  );
  document.getElementById("resolver-detail").textContent=[...bindings,...recent].join("\\n");
}}
async function loadResolverStatus(){{
  const response=await adminFetch("/settings/episode-resolvers");
  const data=await response.json();
  if(!response.ok)throw new Error(data.detail || "Resolver status failed");
  renderResolverStatus(data);
}}
document.getElementById("resolver-search").addEventListener("click",async event=>{{
  const candidate=resolverCandidates.find(item=>item.key===resolverCandidate.value);
  if(!candidate)return;
  event.target.disabled=true;resolverStatus.textContent="Searching TVmaze...";
  try{{
    const data=await postJson("/settings/episode-resolvers/search",{{provider:"tvmaze",query:candidate.title}});
    resolverSearchResults=data.results || [];
    resolverResults.replaceChildren();
    for(const item of resolverSearchResults){{
      const option=document.createElement("option");
      option.value=String(item.show_id);
      option.textContent=`${{item.canonical_name}} · ${{item.network || "No network"}} · ${{item.premiered || "Unknown date"}} · ${{item.status || "Unknown"}}`;
      resolverResults.appendChild(option);
    }}
    document.getElementById("resolver-confirm").disabled=!resolverSearchResults.length;
    resolverStatus.textContent=resolverSearchResults.length ? "Select the verified show and confirm the binding." : "No TVmaze matches.";
  }}catch(error){{resolverStatus.textContent=error.message;}}
  finally{{event.target.disabled=false;}}
}});
document.getElementById("resolver-confirm").addEventListener("click",async event=>{{
  const candidate=resolverCandidates.find(item=>item.key===resolverCandidate.value);
  const result=resolverSearchResults.find(item=>String(item.show_id)===resolverResults.value);
  if(!candidate || !result)return;
  event.target.disabled=true;resolverStatus.textContent="Saving confirmed binding...";
  try{{
    const data=await postJson("/settings/episode-resolvers/bind",{{channel_id:candidate.channel,title:candidate.title,provider:"tvmaze",show_id:result.show_id,canonical_name:result.canonical_name,
      mode:resolverMode.value,timezone:"America/New_York"}});
    renderResolverStatus(data.status);resolverResults.replaceChildren();
  }}catch(error){{resolverStatus.textContent=error.message;}}
  finally{{event.target.disabled=false;}}
}});
document.getElementById("resolver-refresh").addEventListener("click",async event=>{{
  event.target.disabled=true;resolverStatus.textContent="Refreshing confirmed catalogs...";
  try{{
    const response=await adminFetch("/settings/episode-resolvers/refresh",{{method:"POST"}});
    const data=await response.json();
    if(!response.ok)throw new Error(data.detail || "Resolver refresh failed");
    await loadResolverStatus();
    resolverStatus.textContent+=` · refreshed ${{data.refreshed.length}}, failed ${{data.failed.length}}`;
  }}catch(error){{resolverStatus.textContent=error.message;}}
  finally{{event.target.disabled=false;}}
}});

document.getElementById("logout").addEventListener("click",async()=>{{
  const response=await adminFetch("/logout",{{method:"POST"}});
  if(response.redirected)window.location.assign(response.url);
  else window.location.assign("/login");
}});

document.getElementById("settings").addEventListener("click",async()=>{{
  settingsMessage.textContent="Loading settings...";
  settingsDialog.showModal();
  try{{
    const [sourcesResponse,scheduleResponse,errorResponse]=await Promise.all([
      adminFetch("/settings/sources"),adminFetch("/settings/schedule"),adminFetch("/settings/error-handling")
    ]);
    const [sources,schedule,errors]=await Promise.all([
      sourcesResponse.json(),scheduleResponse.json(),errorResponse.json()
    ]);
    if(!sourcesResponse.ok || !scheduleResponse.ok || !errorResponse.ok)throw new Error("Settings load failed");
    renderSources(sources);renderScheduleStatus(schedule);renderErrorHandling(errors);
    await loadAssetHosting();
    await loadResolverStatus();
    settingsMessage.textContent="";
  }}catch(error){{
    settingsMessage.textContent=error.message;
  }}
}});
for(const id of ["close-settings","cancel-settings"]){{
  document.getElementById(id).addEventListener("click",()=>settingsDialog.close());
}}

document.getElementById("save-settings").addEventListener("click",async event=>{{
  const recipient=notifyFields.recipient.value.trim();
  if(recipient && !notifyFields.recipient.checkValidity()){{
    notifyFields.recipient.reportValidity();return;
  }}
  event.target.disabled=true;
  settingsMessage.textContent="Saving settings...";
  try{{
    const [sources,schedule,errors]=await Promise.all([
      postJson("/settings/sources",sourcePayload()),
      postJson("/settings/schedule",schedulePayload()),
      postJson("/settings/error-handling",notificationPayload())
    ]);
    const tokenField=document.getElementById("cloudflare-token");
    if(!tokenField.hidden && tokenField.value.trim()){{
      await postJson("/settings/asset-hosting/token",{{token:tokenField.value.trim()}});
      tokenField.value="";tokenField.hidden=true;
      await loadAssetHosting();
    }}
    renderSources(sources);renderScheduleStatus(schedule);renderErrorHandling(errors);
    settingsMessage.textContent="Settings saved.";
  }}catch(error){{
    settingsMessage.textContent=error.message;
  }}finally{{
    event.target.disabled=false;
  }}
}});

document.getElementById("test-error-email").addEventListener("click",async event=>{{
  event.target.disabled=true;settingsMessage.textContent="Sending test...";
  try{{
    const response=await adminFetch("/settings/error-handling/test",{{method:"POST"}});
    const data=await response.json();
    if(!response.ok)throw new Error(data.detail || "Test email failed");
    settingsMessage.textContent="Test email sent to "+data.recipient+".";
  }}catch(error){{settingsMessage.textContent=error.message;}}
  finally{{event.target.disabled=false;}}
}});
document.getElementById("replace-cloudflare-token").addEventListener("click",()=>{{
  const field=document.getElementById("cloudflare-token");
  field.hidden=false;field.value="";field.focus();
}});
document.getElementById("validate-cloudflare-token").addEventListener("click",async event=>{{
  const output=document.getElementById("cloudflare-validation");
  event.target.disabled=true;output.textContent="Validating token...";output.className="validation-state";
  try{{
    const response=await adminFetch("/settings/asset-hosting/validate",{{method:"POST"}});
    const data=await response.json();
    if(!response.ok)throw new Error(data.detail || "Token validation failed");
    output.textContent=`✓ Token valid for ${{data.domain}} · Zone read · DNS read · Tunnel read`;
    output.className="validation-state success";
  }}catch(error){{output.textContent=error.message;output.className="validation-state failure";}}
  finally{{event.target.disabled=false;}}
}});

function restartDelay(milliseconds){{
  return new Promise(resolve=>window.setTimeout(resolve,milliseconds));
}}
document.getElementById("restart-wonkepg").addEventListener("click",async event=>{{
  if(!window.confirm("Restart WonkEPG? Guide serving will be unavailable briefly."))return;
  const output=document.getElementById("restart-status");
  event.target.disabled=true;
  output.textContent="Restarting WonkEPG…";
  output.className="validation-state";
  try{{
    const response=await adminFetch("/maintenance/restart",{{method:"POST"}});
    const data=await response.json();
    if(!response.ok)throw new Error(data.detail || "Restart request failed");
    const previousInstance=data.instance_id;
    const deadline=Date.now()+90000;
    await restartDelay(1500);
    while(Date.now()<deadline){{
      try{{
        const statusResponse=await adminFetch("/status",{{
          cache:"no-store",headers:{{"Cache-Control":"no-cache"}}
        }});
        if(statusResponse.ok){{
          const status=await statusResponse.json();
          if(status.status==="running" && status.instance_id!==previousInstance){{
            output.textContent="WonkEPG restarted.";
            output.className="validation-state success";
            window.setTimeout(()=>window.location.reload(),700);
            return;
          }}
        }}
      }}catch(error){{
        // Expected while the container is unavailable.
      }}
      await restartDelay(1000);
    }}
    throw new Error("WonkEPG did not return within 90 seconds");
  }}catch(error){{
    output.textContent=error.message;
    output.className="validation-state failure";
    event.target.disabled=false;
  }}
}});

document.getElementById("save").addEventListener("click",async()=>{{
  if(!operationFeedback.start("Saving mappings…"))return;
  closeMenu();
  statusNode.textContent="Saving...";
  try{{
    const mappings=Array.from(document.querySelectorAll("#active-body tr")).map(row=>({{
      number:row.dataset.number,
      pretty_name:row.querySelector(".pretty-name").value,
      baseline:(()=>{{
        const input=row.querySelector('[data-source="baseline"] .mapping');
        return input.dataset.selectedId
          ? {{source:input.dataset.selectedSource,channel_id:input.dataset.selectedId}}
          : null;
      }})(),
      enrichment_1:row.querySelector('[data-source="epgshare"] .mapping').dataset.selectedId || null,
      enrichment_2:row.querySelector('[data-source="epgtalk"] .mapping').dataset.selectedId || null
    }}));
    const activations=Array.from(document.querySelectorAll("#inactive-body tr"))
      .filter(row=>row.querySelector(".active-toggle").checked)
      .map(row=>({{
        number:row.dataset.number,
        pretty_name:row.querySelector(".pretty-name").value,
        logo:row.querySelector(".logo-control").dataset.logoUrl || null,
        baseline:(()=>{{
          const input=row.querySelector('[data-source="baseline"] .mapping');
          return input.dataset.selectedId
            ? {{source:input.dataset.selectedSource,channel_id:input.dataset.selectedId}}
            : null;
        }})(),
        enrichment_1:row.querySelector('[data-source="epgshare"] .mapping').dataset.selectedId || null,
        enrichment_2:row.querySelector('[data-source="epgtalk"] .mapping').dataset.selectedId || null
      }}));
    if([...mappings,...activations].some(row=>row.pretty_name.length>50)){{
      throw new Error("invalid pretty name");
    }}
    const response=await adminFetch("/config/channels/mappings",{{method:"POST",
      headers:{{"Content-Type":"application/json"}},
      body:JSON.stringify({{mappings,activations}})}});
    const data=await response.json();
    if(!response.ok)throw new Error("save failed");
    statusNode.textContent="Mappings saved";
    resultNode.textContent=JSON.stringify(data,null,2);
    operationFeedback.success("✓ Mappings saved");
    if(data.channels_activated)window.setTimeout(()=>window.location.reload(),800);
  }}catch(error){{
    statusNode.textContent="Save failed";
    resultNode.textContent="Mappings were not saved. Check the highlighted values and try again.";
    operationFeedback.failure("Mapping save failed");
  }}
}});
document.getElementById("build").addEventListener("click",async()=>{{
  if(!operationFeedback.start("Building XMLTV…"))return;
  statusNode.textContent="Building...";
  try{{
    const response=await adminFetch("/build",{{method:"POST"}});
    const data=await response.json();
    if(!response.ok)throw new Error("build failed");
    const summary={{
      channels_with_programmes:data.channels_built_with_programmes,
      no_schedule:data.channels_with_no_schedule,
      stale_baselines:data.stale_baseline_count,
      stale_epgshare:data.stale_epgshare_count,
      stale_epgtalk:data.stale_epgtalk_count,
      degraded_to_baseline_only:data.channels_degraded_to_baseline_only,
      total_programmes:data.total_output_programmes,
      final_collisions:data.total_collisions,
      successful_enrichment_matches:data.total_successful_enrichment_matches,
      standard_matches:data.total_standard_matches,
      padding_tolerant_matches:data.total_padding_tolerant_matches,
      ambiguous_relaxed_rejected:data.total_ambiguous_relaxed_matches_rejected,
      relaxed_metadata_conflicts:data.total_metadata_conflict_rejected,
      resolver:data.episode_resolver,
      parse_result:data.parse_validation,
      output_path:data.output_path
    }};
    statusNode.textContent="Build complete";
    resultNode.textContent=JSON.stringify(summary,null,2);
    operationFeedback.success(
      `✓ XMLTV build complete · ${{data.total_output_programmes}} programmes · ${{data.total_collisions}} collisions`
    );
    try{{await loadOperationalStatus();}}catch(error){{/* Best-effort UI refresh. */}}
  }}catch(error){{
    statusNode.textContent="Build failed";
    resultNode.textContent="XMLTV build failed; the last-known-good guide remains available.";
    operationFeedback.failure("XMLTV build failed");
  }}
}});
</script>
</body>
</html>"""
