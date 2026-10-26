"""Re-weight the six scoring criteria from real results in feedback.csv (needs 20+ published videos).

Outcome per video = mean z-score of log(views_7d), CTR, average view duration and returning-viewer share
(whichever are filled in). For each criterion we take the Spearman correlation between its score at
selection time and that outcome, nudge the prior weight by it, and blend 50/50 with the prior so a
small sample can't swing the system. Result goes to data/weights.learned.json, which shortlist.py
prefers over settings.json. Also prints how each packaging angle performed.

    python reweight.py                 # write data/weights.learned.json
    python reweight.py --dry-run       # print only
    python reweight.py path/to/other.csv
"""
import csv
import datetime as dt
import math
import statistics
import sys
from pathlib import Path

from common import DATA, FEEDBACK, load_json, save_json, settings

MIN_VIDEOS = 20
CRITERIA = ["demand", "emotional_pull", "packaging", "takeaway", "cast_fit", "timeliness", "taste", "novelty"]  # gates are not weighted
METRICS = ["views_7d", "ctr_pct", "avg_view_duration_s", "returning_viewer_pct"]


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _ranks(xs):
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    r = [0.0] * len(xs)
    i = 0
    while i < len(xs):
        j = i
        while j + 1 < len(xs) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        for k in range(i, j + 1):
            r[order[k]] = (i + j) / 2
        i = j + 1
    return r


def spearman(a, b):
    ra, rb = _ranks(a), _ranks(b)
    if len(set(ra)) < 2 or len(set(rb)) < 2:
        return 0.0
    return statistics.correlation(ra, rb)


def zscores(xs):
    vals = [x for x in xs if x is not None]
    if len(vals) < 2 or statistics.pstdev(vals) == 0:
        return [0.0 if x is not None else None for x in xs]
    m, s = statistics.mean(vals), statistics.pstdev(vals)
    return [(x - m) / s if x is not None else None for x in xs]


def selection_scores():
    """topic_id -> criterion scores and angles, from every week's shortlist.json."""
    out = {}
    for p in DATA.glob("*/shortlist.json"):
        for t in load_json(p)["topics"]:
            out[t["topic_id"]] = t
    return out


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    path = args[0] if args else FEEDBACK
    if not Path(path).exists():
        print(f"{path} not found; copy config/feedback.example.csv there and add rows as videos publish.")
        return
    rows = [r for r in csv.DictReader(open(path, encoding="utf-8")) if _num(r.get("views_7d")) is not None]
    sel = selection_scores()
    rows = [r for r in rows if r["topic_id"] in sel]
    if len(rows) < MIN_VIDEOS:
        print(f"Only {len(rows)} published videos with 7-day views and a known topic_id; need {MIN_VIDEOS}. Weights unchanged.")
        return

    cols = {m: [_num(r.get(m)) for r in rows] for m in METRICS}
    cols["views_7d"] = [math.log10(v + 1) if v is not None else None for v in cols["views_7d"]]
    zs = [zscores(cols[m]) for m in METRICS]
    outcome = []
    for i in range(len(rows)):
        parts = [z[i] for z in zs if z[i] is not None]
        outcome.append(statistics.mean(parts) if parts else 0.0)

    prior = settings()["weights"]
    corr, adjusted = {}, {}
    for c in CRITERIA:
        corr[c] = round(spearman([float(sel[r["topic_id"]].get(c, 3)) for r in rows], outcome), 3)
        adjusted[c] = prior[c] * max(0.25, 1 + corr[c])
    norm = sum(adjusted.values())
    blended = {c: 0.5 * prior[c] + 0.5 * adjusted[c] / norm * sum(prior.values()) for c in CRITERIA}
    total = sum(blended.values())
    new = {c: round(v / total, 3) for c, v in blended.items()}

    print(f"{len(rows)} videos. Criterion -> correlation with outcome -> new weight (prior)")
    for c in CRITERIA:
        print(f"  {c:14s} {corr[c]:+.2f}  {new[c]:.3f} ({prior[c]:.2f})")

    by_angle = {}
    for r, o in zip(rows, outcome):
        by_angle.setdefault((r.get("package_used") or "?").strip(), []).append(o)
    print("Packaging angle -> mean outcome z (n)")
    for a, os_ in sorted(by_angle.items(), key=lambda kv: -statistics.mean(kv[1])):
        print(f"  {a:12s} {statistics.mean(os_):+.2f} ({len(os_)})")

    if "--dry-run" not in sys.argv:
        save_json(DATA / "weights.learned.json", {
            "updated": dt.date.today().isoformat(), "videos": len(rows), "correlations": corr,
            "prior": prior, "weights": new,
            "angle_outcome": {a: round(statistics.mean(v), 3) for a, v in by_angle.items()}})
        print(f"wrote {DATA / 'weights.learned.json'}")


if __name__ == "__main__":
    main()
