"""30-day retention for YouTube data about channels we don't own (API Developer Policies III.E.4.b/d).

For every data/<week>/ whose signals were collected more than `retention.purge_after_days` days ago
(settings.json; null turns the purge off - override privately in data/settings.local.json):
  - delete signals.json (raw views, outlier ratios, Wikipedia/Reddit pulls), llm/ (prompts quote them)
    and graph.json (the Idea Map export);
  - strip evidence and demand notes from topics.json, keeping our own notes (topic, takeaway, packages,
    outline, facts, scores, risk flags);
  - replace the "Demand evidence" lines in data/shortlist/<week>.md;
  - scrub outlier multiples and view counts from data/memos/<week>.md.
Kept: the shortlist decision itself, our 1-5 scores, chosen angles, and data/feedback.csv (our own channel).
run_weekly.py calls this before every run; `python retention.py` runs it on its own.
"""
import datetime as dt
import re
import shutil
import sys

from common import DATA, SHORTLIST, load_json, save_json, settings

NOTE = "**Demand evidence:** removed after the retention period (YouTube API Developer Policies III.E.4)."


def purge(now=None, max_age_days=None):
    max_age_days = max_age_days or settings().get("retention", {}).get("purge_after_days")
    if not max_age_days:
        return []
    now = now or dt.datetime.now(dt.timezone.utc)
    purged = []
    for week_dir in sorted(p for p in DATA.iterdir() if p.is_dir() and (p / "signals.json").exists()):
        collected = dt.datetime.fromisoformat(load_json(week_dir / "signals.json")["collected_at"])
        if (now - collected).days <= max_age_days:
            continue
        (week_dir / "signals.json").unlink()
        shutil.rmtree(week_dir / "llm", ignore_errors=True)
        (week_dir / "graph.json").unlink(missing_ok=True)  # Idea Map export carries YouTube numbers
        topics_path = week_dir / "topics.json"
        if topics_path.exists():
            meta = load_json(topics_path)
            for t in meta["topics"]:
                t.pop("evidence", None)
                t.pop("why_demand", None)
            meta["raw_data_purged"] = now.date().isoformat()
            save_json(topics_path, meta)
        md = SHORTLIST / f"{week_dir.name}.md"
        if md.exists():
            text = re.sub(r"^\*\*Demand evidence:\*\*.*$", NOTE, md.read_text(encoding="utf-8"), flags=re.M)
            text = re.sub(r"^(Generated .*?)Signals: .*?(\d+ candidate topics scored)", r"\1Raw signals purged. \2",
                          text, count=1, flags=re.M)
            md.write_text(text, encoding="utf-8")
        purged.append(week_dir.name)
    # Memos quote outlier multiples and view counts; scrub those numbers once the week is past the limit.
    memos = DATA / "memos"
    for md in sorted(memos.glob("*.md")) if memos.exists() else []:
        try:
            y, w = md.stem.split("-W")
            monday = dt.datetime.combine(dt.date.fromisocalendar(int(y), int(w), 1), dt.time(), dt.timezone.utc)
        except ValueError:
            continue
        if (now - monday).days <= max_age_days:
            continue
        text = md.read_text(encoding="utf-8")
        new = re.sub(r"\(?\bx\d+(?:\.\d+)?\)?", "", text)
        new = re.sub(r"\d[\d,.\-]*\s?[MK]?\+? views", "[views removed]", new)
        if new != text:
            md.write_text(new, encoding="utf-8")
            purged.append(f"memo {md.stem}")
    if purged:
        print(f"[retention] purged raw YouTube/Reddit/Wikipedia data older than {max_age_days} days: {', '.join(purged)}")
    return purged


if __name__ == "__main__":
    days = settings().get("retention", {}).get("purge_after_days")
    if not days:
        print("retention.purge_after_days is null: nothing purged.")
    else:
        purge(max_age_days=days)
        manual = DATA / "manual_videos.csv"
        if manual.exists() and (dt.datetime.now().timestamp() - manual.stat().st_mtime) > days * 86400:
            print(f"data/manual_videos.csv is older than {days} days; clear its rows.", file=sys.stderr)
