"""Render the private Idea Map page from a week's graph.json.

    python idea_map/build.py            # latest week with a graph.json
    python idea_map/build.py 2026-W45   # a specific week

Writes data/idea-map.html (gitignored). Publishing it to the private claude.ai page is done by Claude,
not by this script.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

weeks = sorted(p.parent.name for p in DATA.glob("*/graph.json"))
if not weeks:
    sys.exit("No data/<week>/graph.json yet: run run_weekly.py first.")
week = sys.argv[1] if len(sys.argv) > 1 else weeks[-1]
graph = (DATA / week / "graph.json").read_text(encoding="utf-8").replace("</", "<" + chr(92) + "/")
page = (ROOT / "idea_map" / "template.html").read_text(encoding="utf-8").replace("__DATA__", graph)
out = DATA / "idea-map.html"
out.write_text(page, encoding="utf-8")
print(f"Wrote {out} from {week}")
