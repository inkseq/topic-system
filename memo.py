"""Weekly review memo (Signal / Scoreboard / Strategy / Watchlist) -> data/memos/<week>.md, plus data/bets.json.

Reads the positioning, the previous memos, last week's bet, data/feedback.csv and this week's shortlist.
The scoreboard numbers are computed here, not by the LLM; the LLM writes the memo around them and returns
this week's bet as structured data so next week's memo can grade it.
"""
import csv
import datetime as dt
import json
import statistics
import sys

from angles import LLM, ManualPending, positioning_text, system_prompt
from common import DATA, FEEDBACK, iso_week, load_json, positioning, run_dir, save_json, settings

MEMOS = DATA / "memos"
BETS = DATA / "bets.json"
MAX_WORDS = 1500


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def scoreboard():
    """Our own videos vs our own baseline (median 7-day views). Plain text for the prompt."""
    if not FEEDBACK.exists():
        return "no data yet (data/feedback.csv missing)"
    rows = [r for r in csv.DictReader(open(FEEDBACK, encoding="utf-8")) if _num(r.get("views_7d")) is not None]
    if not rows:
        return "no data yet - no published videos with 7-day views in data/feedback.csv"
    base = statistics.median(_num(r["views_7d"]) for r in rows)
    today = dt.date.today()
    lines = [f"{len(rows)} videos; baseline = median 7-day views {base:,.0f}"]
    for r in sorted(rows, key=lambda r: r["published_date"], reverse=True)[:8]:
        ratio = _num(r["views_7d"]) / base if base else 0
        lines.append(f"- {r['published_date']} {r['topic_id']} ({r.get('package_used')}): 7d {_num(r['views_7d']):,.0f} "
                     f"= {ratio:.1f}x baseline, CTR {r.get('ctr_pct') or '?'}%, AVD {r.get('avg_view_duration_s') or '?'}s, "
                     f"returning {r.get('returning_viewer_pct') or '?'}%")
    old = [r for r in rows if (today - dt.date.fromisoformat(r["published_date"])).days > 30
           and base and _num(r["views_7d"]) / base >= 1.5]
    if old:
        lines.append("Resurfacing candidates (older than 30 days, 1.5x+ baseline): "
                     + ", ".join(r["topic_id"] for r in old))
    return "\n".join(lines)


def previous_memos(week, n=2):
    if not MEMOS.exists():
        return []
    return [(p.stem, p.read_text(encoding="utf-8")) for p in sorted(MEMOS.glob("*.md")) if p.stem < week][-n:]


def build(week=None, mode="api"):
    cfg = settings()
    week = week or iso_week()
    rd = run_dir(week)
    pos = positioning()
    short = load_json(rd / "shortlist.json")
    meta = load_json(rd / "topics.json")
    bets = load_json(BETS) if BETS.exists() else []
    last_bet = next((b for b in reversed(bets) if b["week"] < week), None)
    prev = previous_memos(week)

    ideas = meta.get("ideas", [])
    pillar_counts = {}
    for i in ideas:
        if i.get("in_pillar"):
            pillar_counts[i["pillar"]] = pillar_counts.get(i["pillar"], 0) + 1
    top = "\n".join(f"- {t['topic_id']} | {t['topic']} | pillar {t.get('pillar')} | total {t['total']} | "
                    f"pull {t.get('emotional_pull')} | demand {t['demand']} | angles {', '.join(t['angles'])}"
                    + (f" | publish by {t['publish_by']}" if t.get("publish_by") else "") for t in short["topics"])
    evidence = []
    for t in meta["topics"]:
        for e in t.get("evidence", [])[:2]:
            if e["source"] == "youtube":
                evidence.append(f"[{t.get('pillar')}] YouTube {e['channel']}: {e['title']} (x{e['outlier']})")
            elif e["source"] == "anniversary":
                evidence.append(f"[{t.get('pillar')}] Anniversary {e['date']}: {e['text'][:80]}")
    sig = load_json(rd / "signals.json")
    favs = sig.get("favorites", [])
    fav_used = sorted({t["favorite"] for t in meta["topics"] if t.get("favorite")})
    fav_text = ("EMPTY - say in one line under Watchlist that data/favorites.md is empty and suggest 3 specific "
                "things the owner could add that fit our pillars." if not favs else
                f"{len(favs)} favourites; used in topics: {fav_used or 'none'}. Mention any strong favourite + "
                "validated-signal pairing under Strategy.")
    dense_n = sum(1 for t in meta["topics"] if t.get("novelty", 3) >= 4)
    user = f"""Write this week's strategy memo for our channel, modelled on a "weekly strategist" review.
Maximum {MAX_WORDS} words, markdown, plain and specific. Never name AI models or say how videos are made.

Sections, in this order:
1. ## Signal - up to 3 trends inside our pillars. Tag each new / building / saturating / disproven and cite
   example evidence from the list below.
2. ## Scoreboard - use the numbers below exactly. If they say no data yet, say so in one line and name what we
   will measure first.
3. ## Strategy - grade last week's bet A-F with the reason (if there was none, say "first week - no bet to
   grade"); ONE main move; 3 episode plays from the shortlist, each with its first-3-seconds line; one
   experiment; one thing to stop.
4. ## Watchlist - 2-4 things to check next week.

Then return this week's bet so next week can grade it.

Return JSON only: {{"memo_markdown": "...", "bet": {{"main_move": "...", "hypothesis": "...",
"metric": "...", "target": "...", "plays": [{{"topic_id": "...", "title": "...", "first_3s": "..."}}],
"experiment": "...", "stop": "..."}}, "last_bet_grade": {{"grade": "A-F or n/a", "reason": "..."}}}}

POSITIONING STATUS: {pos['status']} (option {pos.get('option')}). If provisional, say so in one line.

FAVOURITES INBOX: {fav_text}
DENSE SOURCES: {len(sig.get('dense', []))} items read; {dense_n} packaged topics use them.

SCOREBOARD (computed from data/feedback.csv)
{scoreboard()}

LAST WEEK'S BET
{json.dumps(last_bet, ensure_ascii=False) if last_bet else "none - this is the first memo"}

THIS WEEK'S FUNNEL: {meta.get('ideas_total')} ideas -> {meta.get('ideas_in_pillar')} in pillars -> {len(short['topics'])} shortlisted.
Ideas per pillar: {json.dumps(pillar_counts)}

SHORTLIST
{top}

EVIDENCE SAMPLE
{chr(10).join(evidence[:25])}

PREVIOUS MEMOS
{chr(10).join(f'--- {w} ---{chr(10)}{text[:3000]}' for w, text in prev) or 'none'}"""
    llm = LLM(cfg["llm"], rd, mode)
    print("[memo] weekly strategy memo ...")
    try:
        out = llm.chat("M-memo", system_prompt(pos), user, max_tokens=6000, est_usd=0.10)
    except ManualPending as p:
        print(f"  waiting for {llm.dir / (str(p) + '.response.json')} - answer the .prompt.md next to it, then rerun")
        return None
    except RuntimeError as e:
        print(f"  stopped: {e}")
        return None
    text = out["memo_markdown"].strip()
    words = len(text.split())
    if words > MAX_WORDS:
        print(f"  note: memo is {words} words (limit {MAX_WORDS})")
    MEMOS.mkdir(parents=True, exist_ok=True)
    path = MEMOS / f"{week}.md"
    path.write_text(f"# Weekly memo {week}\n\n_Positioning: {pos['name']} ({pos['status']})._\n\n{text}\n", encoding="utf-8")
    bet = {"week": week, **out["bet"], "graded_last": out.get("last_bet_grade")}
    bets = [b for b in bets if b["week"] != week] + [bet]
    save_json(BETS, sorted(bets, key=lambda b: b["week"]))
    print(f"[memo] wrote {path} ({words} words); bet saved to {BETS}")
    return path


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    build(args[0] if args else None, "manual" if "--manual" in sys.argv else "api")
