"""Add the impacts layer to the compendium website.

Three targets, all idempotent (safe to re-run after each data refresh):

1. choropleth/wizard_data.json  - every episode gains n_impacts,
   n_impacts_crowd, has_crowdsource, impact_points (the pipeline's source of
   truth, used by future wizard rebuilds).
2. docs/index.html              - the published GitHub Pages site: the
   embedded WIZARD_DATA is re-emitted with the impacts fields and the add-on
   script (impacts_site_addon.js) is injected before </body>.
3. locations/build_wizard_html.py - the add-on is inserted into HTML_TEMPLATE
   so regenerated pages include the layer natively.

Run after episode_impacts_extract.py, build_news_impacts.py, and
build_impact_index.py.
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
ADDON = HERE / "impacts_site_addon.js"
MARKER = "<!-- impacts-addon -->"


def load_impact_points() -> tuple[dict, dict]:
    frames = []
    for name in ("impacts_noaa.csv", "impacts_news.csv"):
        p = HERE / "data" / name
        if p.exists():
            frames.append(pd.read_csv(p))
    imp = pd.concat(frames, ignore_index=True)
    imp = imp[imp.episode_id.notna() & (imp.episode_id != "")]
    points: dict[str, list] = {}
    counts: dict[str, dict] = {}
    for eid, sub in imp.groupby("episode_id"):
        pts = []
        for r in sub.itertuples(index=False):
            desc = r.location_raw if isinstance(r.location_raw, str) \
                and r.location_raw else r.location_name
            pts.append({
                "lat": r.lat, "lon": r.lon, "t": r.impact_type,
                "sv": int(r.severity), "src": r.source_type,
                "d": str(desc)[:160], "dt": r.start_date,
                "tx": str(r.text_span)[:220] if isinstance(r.text_span, str)
                      else "",
                "u": r.source_ref if r.source_type in ("local_news",
                                                       "agency_web") else "",
            })
        crowd = int(sub.source_type.isin(["local_news", "agency_web"]).sum())
        points[eid] = pts
        counts[eid] = {"n": len(pts), "crowd": crowd}
    return points, counts


def add_impacts(data: dict, points, counts) -> dict:
    for eid, ep in data["episodes"].items():
        c = counts.get(eid, {"n": 0, "crowd": 0})
        ep["n_impacts"] = c["n"]
        ep["n_impacts_crowd"] = c["crowd"]
        ep["has_crowdsource"] = c["crowd"] > 0
        ep["impact_points"] = points.get(eid, [])
    return data


def augment_wizard_json(points, counts) -> None:
    data = add_impacts(json.loads(WIZARD_JSON.read_text(encoding="utf-8")),
                       points, counts)
    WIZARD_JSON.write_text(json.dumps(data, separators=(",", ":")),
                           encoding="utf-8")
    print(f"wizard_data.json augmented "
          f"({sum(c['n'] for c in counts.values())} impact points)")


def find_wizard_json_span(html: str) -> tuple[int, int]:
    """Return (start, end) of the JSON object in 'const WIZARD_DATA = {...};'"""
    key = "const WIZARD_DATA = "
    start = html.index(key) + len(key)
    i = html.index("{", start)
    depth = 0
    in_str = False
    escape = False
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


def patch_docs(points, counts) -> None:
    """Augment the page's OWN embedded WIZARD_DATA (the published build may
    carry a newer schema than the repo's wizard_data.json, so never replace
    it wholesale - only add the impacts fields)."""
    html = DOCS_HTML.read_text(encoding="utf-8")
    s, e = find_wizard_json_span(html)
    embedded = add_impacts(json.loads(html[s:e]), points, counts)
    html = html[:s] + json.dumps(embedded, separators=(",", ":")) + html[e:]
    addon = f"{MARKER}\n<script>\n{ADDON.read_text(encoding='utf-8')}\n</script>\n"
    if MARKER in html:
        # replace previous injection (marker .. </script> before </body>)
        pre, _, rest = html.partition(MARKER)
        _, _, tail = rest.partition("</script>")
        html = pre + addon.rstrip("\n").rsplit("</script>", 1)[0] \
            + "</script>" + tail
    else:
        html = html.replace("</body>", addon + "</body>", 1)
    DOCS_HTML.write_text(html, encoding="utf-8")
    print(f"docs/index.html patched ({len(html)/1e6:.1f} MB)")


def patch_builder() -> None:
    src = BUILDER.read_text(encoding="utf-8")
    if MARKER in src:
        print("build_wizard_html.py already contains the add-on")
        return
    addon = f"{MARKER}\n<script>\n{ADDON.read_text(encoding='utf-8')}\n</script>\n"
    idx = src.rindex("</body>")
    src = src[:idx] + addon + src[idx:]
    BUILDER.write_text(src, encoding="utf-8")
    print("build_wizard_html.py template patched")


def main() -> None:
    points, counts = load_impact_points()
    augment_wizard_json(points, counts)
    patch_docs(points, counts)
    patch_builder()


if __name__ == "__main__":
    main()
