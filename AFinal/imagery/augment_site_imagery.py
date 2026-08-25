"""Add the satellite imagery layer to the compendium website.

Mirrors AFinal/impacts/augment_site.py: every episode in the embedded
WIZARD_DATA gains imagery_grade, has_usable_imagery, n_overpasses_during and a
compact overpasses list, and imagery_site_addon.js is injected before </body>.

Compact overpass keys keep the embedded payload small:
  t = overpass time UTC, p = platform, s = sensor type, v = AOI coverage,
  c = cloud percent (blank for radar), h = hours from episode begin,
  w = window label (pre, during, post)

Idempotent: re-running replaces the previous injection. Never rebuilds the
page's WIZARD_DATA from the repo copy, since the published build carries a
newer schema than the committed generator emits.

Run after episode_satellite_overpasses.py and build_imagery_index.py.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
WIZARD_JSON = REPO / "AFinal" / "locations" / "choropleth" / "wizard_data.json"
DOCS_HTML = REPO / "docs" / "index.html"
BUILDER = REPO / "AFinal" / "locations" / "build_wizard_html.py"
ADDON = HERE / "imagery_site_addon.js"
MARKER = "<!-- imagery-addon -->"
MAX_OVERPASSES = 24   # per episode, newest-first trim keeps the page small


def load_imagery():
    ov = pd.read_csv(HERE / "data" / "episode_overpasses.csv")
    idx = pd.read_csv(HERE / "data" / "episode_imagery_index.csv")
    ov["cloud_pct"] = pd.to_numeric(ov.cloud_pct, errors="coerce")

    per_ep = {}
    for eid, sub in ov.groupby("episode_id"):
        sub = sub.sort_values("overpass_utc")
        if len(sub) > MAX_OVERPASSES:
            # keep the flood-window passes first, they are the useful ones
            flood = sub[sub.window_label.isin(["during", "post"])]
            sub = pd.concat([flood, sub[~sub.index.isin(flood.index)]]) \
                .head(MAX_OVERPASSES).sort_values("overpass_utc")
        per_ep[eid] = [{
            "t": r.overpass_utc,
            "p": r.platform,
            "s": r.sensor_type,
            "v": float(r.aoi_coverage),
            "c": ("" if pd.isna(r.cloud_pct) else round(float(r.cloud_pct), 1)),
            "h": float(r.hours_from_begin),
            "w": r.window_label,
        } for r in sub.itertuples(index=False)]

    meta = {r.episode_id: r for r in idx.itertuples(index=False)}
    return per_ep, meta


def add_imagery(data: dict, per_ep, meta) -> dict:
    for eid, ep in data["episodes"].items():
        m = meta.get(eid)
        ep["imagery_grade"] = m.imagery_grade if m is not None else "none"
        ep["has_flood_imagery"] = bool(m.has_flood_imagery) if m is not None else False
        ep["has_baseline_imagery"] = bool(m.has_baseline_imagery) if m is not None else False
        ep["n_overpasses_flood"] = int(m.n_flood_window) if m is not None else 0
        ep["overpasses"] = per_ep.get(eid, [])
    return data


def find_wizard_json_span(html: str) -> tuple[int, int]:
    key = "const WIZARD_DATA = "
    start = html.index(key) + len(key)
    i = html.index("{", start)
    depth, in_str, escape = 0, False, False
    while True:
        ch = html[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return start, i + 1
        i += 1


def inject(text: str, addon: str, at_end_of: str) -> str:
    if MARKER in text:
        pre, _, rest = text.partition(MARKER)
        _, _, tail = rest.partition("</script>\n")
        return pre + addon + tail
    idx = text.rindex(at_end_of)
    return text[:idx] + addon + text[idx:]


def main() -> None:
    per_ep, meta = load_imagery()
    addon = f"{MARKER}\n<script>\n{ADDON.read_text(encoding='utf-8')}\n</script>\n"

    data = add_imagery(json.loads(WIZARD_JSON.read_text(encoding="utf-8")),
                       per_ep, meta)
    WIZARD_JSON.write_text(json.dumps(data, separators=(",", ":")),
                           encoding="utf-8")
    n_pts = sum(len(v) for v in per_ep.values())
    print(f"wizard_data.json augmented ({n_pts} overpasses)")

    html = DOCS_HTML.read_text(encoding="utf-8")
    s, e = find_wizard_json_span(html)
    embedded = add_imagery(json.loads(html[s:e]), per_ep, meta)
    html = html[:s] + json.dumps(embedded, separators=(",", ":")) + html[e:]
    html = inject(html, addon, "</body>")
    DOCS_HTML.write_text(html, encoding="utf-8")
    print(f"docs/index.html patched ({len(html)/1e6:.1f} MB)")

    src = BUILDER.read_text(encoding="utf-8")
    BUILDER.write_text(inject(src, addon, "</body>"), encoding="utf-8")
    print("build_wizard_html.py template patched (imagery add-on current)")


if __name__ == "__main__":
    main()
