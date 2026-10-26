"""Weekly run: positioning -> demand signals -> 100+ ideas -> packaging -> data/shortlist/YYYY-Www.md,
data/<week>/graph.json and the weekly memo (data/memos/YYYY-Www.md).

    python run_weekly.py              # this ISO week, LLM via the central registry
    python run_weekly.py --manual     # no LLM key: writes prompts to data/<week>/llm/, rerun after answering
    python run_weekly.py 2026-W44     # re-render a past week (cached signals and LLM answers are reused)
"""
import shutil
import sys

import angles
import demand
import graph
import memo
import retention
import shortlist
from common import CONFIG, DATA, FEEDBACK, iso_week

args = [a for a in sys.argv[1:] if not a.startswith("--")]
week = args[0] if args else iso_week()
mode = "manual" if "--manual" in sys.argv else "api"

DATA.mkdir(exist_ok=True)
retention.purge()
if not FEEDBACK.exists():
    shutil.copy(CONFIG / "feedback.example.csv", FEEDBACK)
if not (DATA / week / "signals.json").exists() or "--refresh" in sys.argv:
    demand.collect(week)
if angles.build(week, mode) is None:
    sys.exit("Stopped before the shortlist: see the message above.")
shortlist.build(week)
graph.build(week)
memo.build(week, mode)
