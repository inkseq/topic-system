"""Rank packaged topics and write data/shortlist/YYYY-Www.md (top N, gated, category-capped)."""
import concurrent.futures as cf
import sys
import urllib.request

from common import DATA, SHORTLIST, iso_week, load_json, run_dir, save_json, settings

# Weighted criteria. fact_check and policy_safety are pass/fail gates, not points.
CRITERIA = ["demand", "emotional_pull", "packaging", "takeaway", "cast_fit", "timeliness"]
LABELS = {"demand": "Demand", "emotional_pull": "Emotional pull", "packaging": "Packaging", "takeaway": "Takeaway",
          "cast_fit": "Cast fit", "timeliness": "Timeliness"}


def weights():
    """Learned weights (from reweight.py) win over the defaults in settings.json."""
    learned = DATA / "weights.learned.json"
    return load_json(learned)["weights"] if learned.exists() else settings()["weights"]


def total(t, w, series_bonus=0.0):
    s = {**t["scores"], "demand": t["demand"], "timeliness": t.get("timeliness", 3)}
    cs = [c for c in CRITERIA if c in w and c in s]
    base = sum(w[c] * s[c] for c in cs) / sum(w[c] for c in cs)
    return round(base + (series_bonus if t.get("series") else 0), 2)


def gate_failures(t, sl):
    s, out = t["scores"], []
    if not t.get("in_pillar", True):
        out.append("outside the positioning pillars")
    if s.get("policy_safety", 5) < sl["min_policy_safety"]:
        out.append(f"policy gate ({s['policy_safety']})")
    if s.get("fact_check", 5) < sl["min_fact_check"]:
        out.append(f"fact-check gate ({s['fact_check']})")
    return out


def check_url(url, ua):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": ua}, method="GET")
        with urllib.request.urlopen(req, timeout=12) as r:
            return r.status < 400
    except Exception:
        return False


def rank(topics, cfg):
    w = weights()
    sl = cfg["shortlist"]
    scored = [t for t in topics if t.get("scores")]
    for t in scored:
        t["total"] = total(t, w, sl.get("series_bonus", 0))
        t["gates_failed"] = gate_failures(t, sl)
    scored.sort(key=lambda t: (-t["total"], -t["demand"]))
    picked, rejected, per_cat, used = [], [], {}, {}
    for t in scored:
        if len(picked) >= sl["size"]:
            break
        ev = {e.get("video_id") or e.get("url") for e in t.get("evidence", [])}
        dup = next((used[e] for e in ev if e in used), None)
        if t["gates_failed"]:
            rejected.append((t, "; ".join(t["gates_failed"] + (t.get("gate_flags") or []))))
        elif per_cat.get(t["category"], 0) >= sl["max_per_category"]:
            rejected.append((t, f"category cap ({t['category']})"))
        elif dup:  # near-duplicate: same demand signal as a higher-ranked pick
            rejected.append((t, f"same signal as '{dup}'"))
        else:
            picked.append(t)
            per_cat[t["category"]] = per_cat.get(t["category"], 0) + 1
            used.update({e: t["topic"] for e in ev})
    # Date-pinned topics that missed the top N still matter for planning: list them by publish date.
    dated = sorted((t for t in scored if t not in picked and t.get("publish_by") and not t["gates_failed"]),
                   key=lambda t: t["publish_by"])
    return picked, rejected, dated, w


def evidence_line(e):
    if e["source"] == "youtube":
        return f"YouTube: [{e['title']}]({e['url']}) - {e['channel']}, x{e['outlier']} vs neighbouring uploads ({e['format']})"
    if e["source"] == "wikipedia":
        return f"Wikipedia: [{e['title']}]({e['url']}) - {e['views']:,} views yesterday"
    if e["source"] == "anniversary":
        return (f"Anniversary: [{e['page']}]({e['url']}) - {e['years']} years on {e['date']} ({e['year']}: "
                f"{e['text'][:90]}); {e['views_30d']:,} Wikipedia views in 30 days")
    if e["source"] == "google_trends":
        return f"Google Trends (US): [{e['query']}]({e['url']}) - {e['traffic']:,}+ searches; fades fast"
    return f"Reddit r/{e['subreddit']} #{e['rank']} today: [{e['title'][:90]}]({e['url']})"


def spread(topics):
    """Share of 5s per dimension, to show the calibration is working."""
    n = len(topics) or 1
    dims = ["demand", "emotional_pull", "packaging", "takeaway", "cast_fit"]
    return {d: round(sum(1 for t in topics if (t["demand"] if d == "demand" else t["scores"].get(d)) == 5) / n, 2)
            for d in dims}


def render(week, picked, rejected, dated, w, meta, signals, link_ok):
    yt = signals["youtube"]
    packaged = [t for t in meta["topics"] if t.get("scores")]
    pos = meta.get("positioning", {})
    lines = [
        f"# Topic shortlist {week}",
        "",
        f"Positioning: **{pos.get('name', '?')}** ({pos.get('status', '?')}). Generated {signals['collected_at'][:10]}.",
        "",
        f"Signals: {yt['videos_read']} uploads from {yt['channels_read']} reference channels (source: {yt['mode']}), "
        f"{len(signals['wikipedia']['articles'])} Wikipedia top articles ({signals['wikipedia']['day']}), "
        f"{len(signals['reddit'])} Reddit posts, {len(signals.get('anniversaries', {}).get('events', []))} upcoming "
        f"round anniversaries, {len(signals.get('google_trends', []))} Google trends.",
        "",
        f"Funnel: {meta.get('ideas_total', '?')} raw ideas -> {meta.get('ideas_in_pillar', '?')} inside a pillar -> "
        f"{len(packaged)} packaged -> {len(picked)} shortlisted. LLM mode {meta['llm_mode']}, spend ${meta['llm_cost_usd']:.2f}.",
        "",
        "Weights: " + ", ".join(f"{LABELS[c]} {w[c]:.2f}" for c in CRITERIA if c in w)
        + ". Series bonus +" + str(settings()["shortlist"].get("series_bonus", 0)) + ". Gates (pass/fail): pillar fit, "
        "policy safety >= 3, fact-check >= 3. Share of 5s after calibration: "
        + ", ".join(f"{k} {int(v * 100)}%" for k, v in spread(packaged).items()) + ".",
        "",
        "| # | Topic | Pillar | Total | Dem | Pull | Pack | Take | Cast | Time | Publish by | Flags |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for i, t in enumerate(picked, 1):
        s = t["scores"]
        lines.append(f"| {i} | {t['topic']}{' (series)' if t.get('series') else ''} | {t.get('pillar', '-')} | "
                     f"**{t['total']}** | {t['demand']} | {s['emotional_pull']} | {s['packaging']} | {s['takeaway']} | "
                     f"{s['cast_fit']} | {t.get('timeliness', 3)} | {t.get('publish_by') or 'any time'} | "
                     f"{', '.join(t.get('risk_flags') or []) or '-'} |")
    lines.append("")
    for i, t in enumerate(picked, 1):
        c = t.get("conflict") or {}
        lines += [f"## {i}. {t['topic']}", "",
                  f"`{t['topic_id']}` - {t.get('pillar')} / {t['category']} - total **{t['total']}**", "",
                  f"**Takeaway:** {t['takeaway']}", "",
                  f"**Conflict:** {c.get('who', '?')} wants {c.get('wants', '?')}; {c.get('goes_wrong', '?')}; "
                  f"payoff: {c.get('payoff_fact', '?')}", ""]
        if t.get("publish_by"):
            lines += [f"**Publish by:** {t['publish_by']} ({t['time_reason']})", ""]
        lines += ["**Best packages**", ""]
        for j in (t.get("best") or [0, 1])[:2]:
            p = t["packages"][j]
            lines.append(f"- *{p['angle']}* - **{p['title']}** | thumbnail text: \"{p['thumb_text']}\" | "
                         f"{p['thumb_visual']} | first 3 s: {p['first_3s']}")
        lines += ["", "**Story (want / trouble / payoff)**", ""]
        for b in t["outline"]:
            shots = " / ".join(f"{s['speaker']}{' + ' + ', '.join(x for x in s['on_screen'] if x != s['speaker']) if len(s['on_screen']) > 1 else ''}: {s['action']}"
                               for s in b["shots"])
            lines.append(f"- **{b['beat'].title()}** - {shots}")
        lines += ["", "**Facts to verify**", ""]
        for f in t["facts"]:
            ok = link_ok.get(f["url"])
            mark = "" if ok else " (link did not load for the script - some publishers block bots; open it by hand)"
            lines.append(f"- [ ] {f['claim']} - [{f['source']}]({f['url']}){mark}")
        lines += ["", "**Demand evidence:** " + ("; ".join(evidence_line(e) for e in t["evidence"]) or "none (evergreen pick)"),
                  "", f"**Risk flags:** {', '.join(t.get('risk_flags') or []) or 'none'}"
                  + (f" | calibrated 5->4: {', '.join(t['calibrated'])}" if t.get("calibrated") else "")
                  + (f" | format issues: {'; '.join(t['format_problems'])}" if t.get("format_problems") else "")
                  + f" | note: {t.get('score_note', '')}", ""]
    if dated:
        lines += ["## Dated picks outside the top list", "",
                  "Anniversary or trend topics that passed the gates but ranked lower; plan them by date.", ""]
        for t in dated:
            p = t["packages"][(t.get("best") or [0])[0]]
            lines.append(f"- **{t['publish_by']}** - {t['topic']} (total {t['total']}; {t['time_reason']}) - "
                         f"best package: **{p['title']}** / \"{p['thumb_text']}\"")
        lines.append("")
    if rejected:
        lines += ["## Held back", ""]
        for t, why in rejected:
            lines.append(f"- {t['topic']} ({t['total']}) - {why}")
        lines.append("")
    return "\n".join(lines)


def build(week=None):
    cfg = settings()
    week = week or iso_week()
    rd = run_dir(week)
    meta = load_json(rd / "topics.json")
    signals = load_json(rd / "signals.json")
    picked, rejected, dated, w = rank(meta["topics"], cfg)
    for i, t in enumerate(picked, 1):
        t["topic_id"] = f"{week}-{i:02d}-{t['key']}"
    urls = sorted({f["url"] for t in picked for f in t["facts"]})
    with cf.ThreadPoolExecutor(8) as ex:
        link_ok = dict(zip(urls, ex.map(lambda u: check_url(u, cfg["user_agent"]), urls)))
    SHORTLIST.mkdir(parents=True, exist_ok=True)
    out = SHORTLIST / f"{week}.md"
    out.write_text(render(week, picked, rejected, dated, w, meta, signals, link_ok), encoding="utf-8")
    save_json(rd / "shortlist.json", {"week": week, "weights": w, "topics": [
        {"topic_id": t["topic_id"], "key": t["key"], "topic": t["topic"], "category": t["category"],
         "pillar": t.get("pillar"), "series": bool(t.get("series")),
         "total": t["total"], "demand": t["demand"], "timeliness": t.get("timeliness", 3),
         "publish_by": t.get("publish_by"), **t["scores"],
         "angles": [t["packages"][j]["angle"] for j in (t.get("best") or [0, 1])[:2]]} for t in picked]})
    bad = [u for u, ok in link_ok.items() if not ok]
    print(f"[shortlist] wrote {out} ({len(picked)} topics, {len(bad)}/{len(urls)} source links did not load)")
    return out


if __name__ == "__main__":
    build(sys.argv[1] if len(sys.argv) > 1 else None)
