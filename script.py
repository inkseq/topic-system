"""Topic -> finished script for the week's ONE main episode (plus its 2-3 Shorts cuts).

Stages: S-draft (LLM writes the script as JSON) -> lint -> S-doctor (a second LLM pass with the script-doctor
checklist) -> lint -> data/scripts/<week>-<topic-id>.md (human-readable) and .json (machine-readable, for a later
import into the story workbench).

Status: draft -> doctored -> facts-verified -> final. Code only ever sets draft or doctored. A person moves it on:
tick each fact ("checked": true) in the .json, then
    python script.py --set-status facts-verified data/scripts/<file>.json
    python script.py --set-status final data/scripts/<file>.json
Both refuse while lint has errors or any fact is unchecked. `python script.py --render <file>.json` rebuilds
the .md (and lint) after hand edits.
"""
import json
import re
import sys
from pathlib import Path

from angles import LLM, ManualPending, system_prompt
from common import DATA, iso_week, load_json, positioning, run_dir, save_json, settings

SCRIPTS = DATA / "scripts"
ROLES = {"asker": "ASKER", "explainer": "EXPLAINER", "messup": "MESS-UP"}
SPINE = ["Once upon a time", "Every day", "One day", "Because of that", "Because of that", "Because of that",
         "Until finally", "And ever since"]
DIALOGUE_FRAMING = {"close", "close-up", "medium", "medium-close", "medium close"}
MODEL_NAMES = re.compile(r"\b(gpt|chatgpt|claude|gemini|sora|veo|kling|runway|midjourney|stable diffusion|"
                         r"seedance|wan ?2|hunyuan|ai[- ]generated|ai model|text[- ]to[- ]video)\b", re.I)
CLICKBAIT = re.compile(r"\b(insane|shocking|you won'?t believe|mind[- ]blowing|gone wrong)\b", re.I)
ADVICE = re.compile(r"\b(you should (take|buy|invest|sell|stop taking)|consult your|dosage|diet plan|"
                    r"financial advice|guaranteed returns)\b", re.I)


def _words(s):
    return len(re.findall(r"[A-Za-z0-9']+", s or ""))


# ---------- inputs ----------

def inputs(week):
    rd = run_dir(week)
    bets = load_json(DATA / "bets.json") if (DATA / "bets.json").exists() else []
    bet = next((b for b in reversed(bets) if b["week"] == week), None)
    if not bet or not bet.get("main_episode"):
        raise RuntimeError(f"no main_episode in data/bets.json for {week} - run the memo first")
    tid = bet["main_episode"]["topic_id"]
    short = load_json(rd / "shortlist.json")
    entry = next(t for t in short["topics"] if t["topic_id"] == tid)
    topic = next(t for t in load_json(rd / "topics.json")["topics"] if t["key"] == entry["key"])
    prod = (load_json(rd / "production.json") if (rd / "production.json").exists() else {}).get(entry["key"], {})
    return tid, entry, topic, prod, bet


# ---------- prompts ----------

SCHEMA = """{"title": "...", "thumbnail_options": [{"kind": "...", "title": "...", "thumb_text": "..."}],
"logline": "one sentence", "takeaway": "one sentence", "target_minutes": 3.5,
"wants": {"asker": "...", "explainer": "...", "messup": "..."},
"beats": [{"spine": "Once upon a time", "text": "...", "connector_to_next": "BUT"}],
"misconception": {"belief": "...", "voiced_by": "messup", "confronted_by": "explainer", "demonstration": "..."},
"retention": {"cold_open": "...", "open_question": {"at_s": 8, "line": "..."},
  "rehooks": [{"at_s": 70, "kind": "laugh|twist|visual", "beat": "..."}], "payoff_s": 185, "end_s": 190},
"short_cuts": [{"id": "S1", "start_s": 0, "end_s": 30, "title": "...", "framing_9x16": "..."}],
"shots": [{"n": 1, "start_s": 0, "scene": "...", "on_screen": ["messup", "explainer"], "speaker": "messup",
  "line_offset_s": 1.0, "direction": "(shouting)", "line": "...", "visual": "...", "framing": "medium",
  "sfx": "...", "short_cut": "S1"}],
"facts": [{"claim": "...", "source": "...", "url": "...", "checked": false}]}"""

RULES = """SCRIPT RULES
- Story spine, compressed to 5-8 beats ("Once upon a time... Every day... One day... Because of that... Until
  finally..."). Consecutive beats connect with BUT or THEREFORE only - never "and then". Put the connector in
  connector_to_next; the last beat has null.
- Teach through the misconception: the asker or mess-up says the common wrong belief out loud; the explainer
  challenges it in dialogue and with a demonstration, not a lecture. At most one explanatory line per shot.
- Retention (scaled MrBeast curve): shot 1 (0-5 s) is a cold open that pays off the thumbnail promise - no intro;
  an open question is planted by 10 s; a re-hook (twist, laugh or visual surprise) at about each third; the payoff,
  then the video ends within 5 s.
- 2-3 Shorts cuts, each 20-50 s, on 5-second boundaries, understandable without context, with 9:16 framing notes.
- Production limits: every shot is a 5-second window (start_s = 0, 5, 10...); at most 2 characters on screen; ONE
  speaker per shot (or none); the spoken line starts at least 1 s into the window (line_offset_s >= 1); dialogue in
  medium or close framing; at most 12 English words per line; never a three-person dialogue shot.
- About 150 spoken words per minute; target 3-6 minutes.
- Facts: every factual claim in the script gets a fact entry with a source URL; all start unchecked.
- Tone: respectful, non-clickbait, no model names or production methods, no health or finance advice. Keep the
  cast's personalities and running gags from the positioning."""


def draft_prompt(entry, topic, prod, bet, pos):
    facts = "\n".join(f"- {f['claim']} ({f['source']}: {f['url']})" for f in topic.get("facts", []))
    thumbs = "\n".join(f"- {t['kind']}: {t['title']} / \"{t['thumb_text']}\" / first 5 s: {t['first_5s']}"
                       for t in prod.get("thumbnails", []))
    shorts = "\n".join(f"- {s['moment']} (hook: {s['hook']}; payoff: {s['payoff']})" for s in prod.get("short_cuts", []))
    rehooks = "\n".join(f"- ~{r['at']}: {r['beat']}" for r in prod.get("rehooks", []))
    cd = entry.get("ccn_detail") or {}
    return f"""Write the full script for this week's main episode.

EPISODE: {topic['topic']} ({entry['topic_id']}, pillar {topic.get('pillar')})
TAKEAWAY: {topic['takeaway']}
CONFLICT: {json.dumps(topic.get('conflict'), ensure_ascii=False)}
CORE/CASUAL/NEW: {', '.join(f"{g} {cd[g]['score']}" for g in cd) or 'n/a'}
THUMBNAIL OPTIONS (keep the promise of all three; the cold open must pay them off):
{thumbs}
PLANNED RE-HOOKS:
{rehooks}
PLANNED SHORTS CUTS:
{shorts}
KNOWN FACTS AND SOURCES (verify-able; add any new claim you use, unchecked):
{facts}
MEMO BET: {bet.get('hypothesis', '')}

{RULES}

Return JSON only, in this shape:
{SCHEMA}"""


def doctor_prompt(draft, report):
    return f"""You are the script doctor. Revise this draft and return the full revised script as JSON in the same
shape, plus "doctor_notes": a list of what you changed.

CHECKLIST
- Cut about 20% (fewer shots and words) without losing the payoff.
- Sharpen the hook: the first 5 seconds must pay off the thumbnail promise with a conflict.
- Keep the cast voices consistent with the positioning (personalities, wants, running gags).
- No "and then" connectors; every beat link is BUT or THEREFORE.
- Word count against the target: about 150 spoken words per minute, 3-6 minutes.
- Read every line aloud: fix tongue-twisters, stacked clauses, lines over 12 words.
- No model names or production talk; no health or finance advice; respectful, non-clickbait tone.
- Fix every lint error below.

LINT REPORT ON THE DRAFT
{json.dumps(report, ensure_ascii=False, indent=1)}

{RULES}

DRAFT
{json.dumps(draft, ensure_ascii=False)}"""


# ---------- lint ----------

def lint(s, cfg):
    sc = cfg["script"]
    err, warn = [], []
    beats = s.get("beats", [])
    if not 5 <= len(beats) <= 8:
        err.append(f"beat sheet has {len(beats)} beats (need 5-8)")
    for i, b in enumerate(beats):
        c = (b.get("connector_to_next") or "").strip()
        if i < len(beats) - 1:
            if "and then" in c.lower():
                err.append(f"beat {i + 1}: 'and then' connector")
            elif c.upper() not in ("BUT", "THEREFORE"):
                err.append(f"beat {i + 1}: connector must be BUT or THEREFORE, got '{c}'")
        elif c:
            warn.append("last beat should have no connector")
    shots = s.get("shots", [])
    runtime = 5 * len(shots)
    words = 0
    for i, sh in enumerate(shots):
        tag = f"shot {sh.get('n', i + 1)}"
        if sh.get("start_s") != 5 * i:
            err.append(f"{tag}: start {sh.get('start_s')} s, expected {5 * i} s (5-second windows)")
        on = sh.get("on_screen", [])
        if len(on) > 2:
            err.append(f"{tag}: {len(on)} characters on screen (max 2)")
        spk, line = sh.get("speaker"), (sh.get("line") or "").strip()
        if line:
            n = _words(line)
            words += n
            if not spk:
                err.append(f"{tag}: line without a speaker")
            elif spk not in on:
                err.append(f"{tag}: speaker {spk} not on screen")
            if n > sc["max_words_per_line"]:
                err.append(f"{tag}: {n} words (max {sc['max_words_per_line']})")
            if (sh.get("line_offset_s") or 0) < 1:
                err.append(f"{tag}: line starts before 1 s into the window")
            if (sh.get("framing") or "").lower() not in DIALOGUE_FRAMING:
                err.append(f"{tag}: dialogue framing '{sh.get('framing')}' (use medium or close)")
        text = " ".join(str(sh.get(k, "")) for k in ("line", "visual", "direction"))
        if MODEL_NAMES.search(text):
            err.append(f"{tag}: mentions a model or production method")
        if CLICKBAIT.search(text):
            err.append(f"{tag}: clickbait wording")
        if ADVICE.search(text):
            err.append(f"{tag}: reads like health or finance advice")
    for t in [s.get("title", "")] + [o.get("title", "") for o in s.get("thumbnail_options", [])]:
        if CLICKBAIT.search(t) or MODEL_NAMES.search(t):
            err.append(f"title wording: {t}")
    lo, hi = sc["target_minutes"]
    if not lo * 60 <= runtime <= hi * 60:
        err.append(f"runtime {runtime} s is outside {lo}-{hi} minutes")
    spoken_min = words / sc["words_per_minute"]
    if runtime and spoken_min > runtime / 60:
        err.append(f"{words} words need {spoken_min:.1f} min at {sc['words_per_minute']} wpm but the cut is {runtime / 60:.1f} min")
    wpm = words / (runtime / 60) if runtime else 0
    if runtime and wpm < sc.get("min_wpm", 90):
        warn.append(f"sparse dialogue: {wpm:.0f} spoken words per minute of runtime (target about "
                    f"{sc['words_per_minute']}); trim silent shots or let lines carry more")
    r = s.get("retention", {})
    first = shots[0] if shots else {}
    if not (first.get("line") or first.get("visual")):
        err.append("no cold open in shot 1")
    oq = (r.get("open_question") or {}).get("at_s")
    if oq is None or oq > 10:
        err.append(f"open question at {oq} s (must be planted by 10 s)")
    reh = [x.get("at_s", -1) for x in r.get("rehooks", [])]
    for frac in (1 / 3, 2 / 3):
        if runtime and not any(abs(a - frac * runtime) <= 0.12 * runtime for a in reh):
            err.append(f"no re-hook near {int(frac * runtime)} s ({'1/3' if frac < .5 else '2/3'})")
    if runtime and (r.get("end_s") or 0) - (r.get("payoff_s") or 0) > 5:
        err.append("more than 5 s between payoff and end")
    if runtime and abs((r.get("end_s") or 0) - runtime) > 5:
        warn.append(f"retention end_s {r.get('end_s')} doesn't match the cut ({runtime} s)")
    cuts = s.get("short_cuts", [])
    if not 2 <= len(cuts) <= 3:
        err.append(f"{len(cuts)} Shorts cuts (need 2-3)")
    for c in cuts:
        d = c.get("end_s", 0) - c.get("start_s", 0)
        if not 20 <= d <= 50:
            err.append(f"Short {c.get('id')}: {d} s (need 20-50)")
        if c.get("start_s", 0) % 5 or c.get("end_s", 0) % 5:
            err.append(f"Short {c.get('id')}: not on 5-second boundaries")
        if c.get("end_s", 0) > runtime:
            err.append(f"Short {c.get('id')}: runs past the end")
        if not c.get("framing_9x16"):
            err.append(f"Short {c.get('id')}: no 9:16 framing note")
    unchecked = sum(1 for f in s.get("facts", []) if not f.get("checked"))
    if unchecked:
        warn.append(f"{unchecked} fact(s) unchecked - status can't pass 'doctored'")
    return {"errors": err, "warnings": warn,
            "stats": {"shots": len(shots), "runtime_s": runtime, "spoken_words": words, "wpm": round(wpm),
                      "spoken_minutes_at_wpm": round(spoken_min, 2), "facts": len(s.get("facts", [])),
                      "facts_unchecked": unchecked}}


# ---------- render ----------

def screenplay(s):
    out, scene = [], None
    for sh in s.get("shots", []):
        if sh.get("scene") and sh["scene"] != scene:
            scene = sh["scene"]
            out += ["", scene.upper(), ""]
        if sh.get("line"):
            out.append(f"{ROLES.get(sh['speaker'], sh['speaker'].upper()):>20}")
            if sh.get("direction"):
                out.append(f"{'':>14}{sh['direction']}")
            out.append(f"{'':>10}{sh['line']}")
            out.append("")
        else:
            out += [f"[{sh.get('visual', '')}]", ""]
    return "\n".join(out)


def _ts(sec):
    return f"{sec // 60}:{sec % 60:02d}"


def render(s, report):
    ccn = s.get("ccn") or {}
    lines = [f"# {s['title']}", "",
             f"Status: **{s['status']}** | Episode `{s.get('topic_id')}` | pillar {s.get('pillar')} | target "
             f"{s.get('target_minutes')} min | cut {_ts(report['stats']['runtime_s'])} | "
             f"{report['stats']['spoken_words']} spoken words", "",
             "## 1. Header", "",
             f"**Logline:** {s['logline']}", "", f"**Takeaway:** {s['takeaway']}", "",
             "**Core / casual / new:** " + ("; ".join(f"{g} {v['score']} - {v['reason']}" for g, v in ccn.items()) or "n/a"),
             "", "**Thumbnail options (A/B):**", ""]
    lines += [f"- *{o['kind']}* - {o['title']} / \"{o['thumb_text']}\"" for o in s.get("thumbnail_options", [])]
    lines += ["", "**Wants this episode:**", ""]
    lines += [f"- {ROLES.get(k, k)}: {v}" for k, v in s.get("wants", {}).items()]
    lines += ["", "## 2. Beat sheet (story spine, BUT / THEREFORE)", ""]
    for i, b in enumerate(s.get("beats", []), 1):
        lines.append(f"{i}. **{b['spine']}...** {b['text']}")
        if b.get("connector_to_next"):
            lines.append(f"   - **{b['connector_to_next'].upper()}**")
    m = s.get("misconception", {})
    lines += ["", "## 3. Misconception", "",
              f"- Wrong belief, said by {ROLES.get(m.get('voiced_by'), m.get('voiced_by'))}: \"{m.get('belief')}\"",
              f"- Confronted by {ROLES.get(m.get('confronted_by'), m.get('confronted_by'))} with: {m.get('demonstration')}",
              "", "## 4. Retention map", ""]
    r = s.get("retention", {})
    lines += [f"- **0:00-0:05 cold open:** {r.get('cold_open')}",
              f"- **{_ts((r.get('open_question') or {}).get('at_s', 0))} open question:** {(r.get('open_question') or {}).get('line')}"]
    lines += [f"- **{_ts(x['at_s'])} re-hook ({x.get('kind')}):** {x['beat']}" for x in r.get("rehooks", [])]
    lines += [f"- **{_ts(r.get('payoff_s', 0))} payoff**, end at {_ts(r.get('end_s', 0))}", "", "**Shorts cuts (9:16)**", ""]
    lines += [f"- **{c['id']}** {_ts(c['start_s'])}-{_ts(c['end_s'])} ({c['end_s'] - c['start_s']} s) - {c['title']} - "
              f"framing: {c['framing_9x16']}" for c in s.get("short_cuts", [])]
    lines += ["", "## 5. Shot list", "",
              "| # | Start | Window | On screen | Speaker | Line | Visual | Framing | SFX / music | Short |",
              "|---|---|---|---|---|---|---|---|---|---|"]
    for sh in s.get("shots", []):
        lines.append(f"| {sh['n']} | {_ts(sh['start_s'])} | {sh['start_s']}-{sh['start_s'] + 5} s | "
                     f"{', '.join(sh.get('on_screen', []))} | {sh.get('speaker') or '-'} | "
                     f"{(sh.get('line') or '-')}{' (from ' + str(sh.get('line_offset_s')) + ' s)' if sh.get('line') else ''} | "
                     f"{sh.get('visual', '')} | {sh.get('framing', '')} | {sh.get('sfx', '') or '-'} | {sh.get('short_cut') or '-'} |")
    lines += ["", "## 6. Dialogue script (for the voice pass)", "", "```", screenplay(s), "```", "",
              "## 7. Fact sheet", ""]
    lines += [f"- [{'x' if f.get('checked') else ' '}] {f['claim']} - [{f['source']}]({f['url']})"
              + ("" if f.get("checked") else " **(unverified)**") for f in s.get("facts", [])]
    lines += ["", "## Lint", "", f"Stats: {json.dumps(report['stats'])}", ""]
    lines += [f"- ERROR: {e}" for e in report["errors"]] or ["- no errors"]
    lines += [f"- warning: {w}" for w in report["warnings"]]
    if s.get("doctor_notes"):
        lines += ["", "## Script doctor notes", ""] + [f"- {n}" for n in s["doctor_notes"]]
    return "\n".join(lines) + "\n"


def write(s, report, path):
    s["lint"] = report
    save_json(path.with_suffix(".json"), s)
    path.with_suffix(".md").write_text(render(s, report), encoding="utf-8")


# ---------- pipeline ----------

def build(week=None, mode="api"):
    cfg = settings()
    week = week or iso_week()
    rd = run_dir(week)
    pos = positioning()
    try:
        tid, entry, topic, prod, bet = inputs(week)
    except (RuntimeError, StopIteration, FileNotFoundError) as e:
        print(f"[script] skipped: {e}")
        return None
    llm = LLM({**cfg["llm"], "max_usd_per_run": cfg["script"]["max_usd"]}, rd, mode)
    print(f"[script] {tid} ...")
    try:
        draft = llm.chat("S-draft", system_prompt(pos), draft_prompt(entry, topic, prod, bet, pos),
                         max_tokens=16000, est_usd=0.30)
        draft_report = lint(draft, cfg)
        doctored = llm.chat("S-doctor", system_prompt(pos), doctor_prompt(draft, draft_report),
                            max_tokens=16000, est_usd=0.40)
    except ManualPending as p:
        print(f"  waiting for {llm.dir / (str(p) + '.response.json')} - answer the .prompt.md next to it, then rerun")
        return None
    except RuntimeError as e:
        print(f"  stopped: {e}")
        return None
    meta = {"topic_id": tid, "week": week, "pillar": topic.get("pillar"), "ccn": entry.get("ccn_detail"),
            "status": "doctored"}
    s = {**doctored, **meta}
    report = lint(s, cfg)
    report["stats"]["draft_runtime_s"] = draft_report["stats"]["runtime_s"]
    report["stats"]["draft_words"] = draft_report["stats"]["spoken_words"]
    report["stats"]["draft_lint_errors"] = len(draft_report["errors"])
    report["stats"]["cut_pct"] = round(100 * (1 - report["stats"]["runtime_s"] / max(1, draft_report["stats"]["runtime_s"])), 1)
    if report["stats"]["cut_pct"] < 10:
        report["warnings"].append(f"doctor cut only {report['stats']['cut_pct']}% of the runtime (aim for about 20%)")
    SCRIPTS.mkdir(parents=True, exist_ok=True)
    path = SCRIPTS / f"{tid}.md"
    write(s, report, path)
    print(f"[script] wrote {path.with_suffix('.md')} and .json - status doctored, "
          f"{len(report['errors'])} lint error(s), {len(report['warnings'])} warning(s), LLM spend ${llm.spent:.3f}")
    return path


def set_status(path, status):
    s = load_json(path)
    report = lint(s, settings())
    order = ["draft", "doctored", "facts-verified", "final"]
    if status not in order:
        sys.exit(f"unknown status {status}")
    if order.index(status) >= 2:
        if report["errors"]:
            sys.exit(f"refused: {len(report['errors'])} lint error(s)")
        if report["stats"]["facts_unchecked"]:
            sys.exit(f"refused: {report['stats']['facts_unchecked']} fact(s) unchecked")
    if status == "final" and s.get("status") != "facts-verified":
        sys.exit("refused: only a facts-verified script can be made final")
    s["status"] = status
    write(s, report, Path(path))
    print(f"{path}: status -> {status}")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if "--set-status" in sys.argv:
        set_status(Path(args[1]), args[0])
    elif "--render" in sys.argv:
        s = load_json(args[0])
        write(s, lint(s, settings()), Path(args[0]))
        print(f"rendered {Path(args[0]).with_suffix('.md')}")
    else:
        build(args[0] if args else None, "manual" if "--manual" in sys.argv else "api")
