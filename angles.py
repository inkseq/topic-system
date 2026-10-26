"""Angle layer: turn raw demand signals into candidate topics, then package and score each one with an LLM.

LLM access goes through the central registry loader (OpenRouter profile routing to a Claude model).
The key is passed straight into the request header and never printed or written anywhere.
"""
import json
import re
import sys
import urllib.error
import urllib.request

from common import CONFIG, iso_week, load_central_api, load_json, run_dir, save_json, settings

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


class ManualPending(Exception):
    """Raised in manual mode when a prompt is waiting for a pasted answer."""


class LLM:
    """One run's LLM access with a hard spend cap.

    mode "api": central-registry OpenRouter profile. mode "manual": each prompt is written to
    data/<week>/llm/<name>.prompt.md; paste the model's JSON answer into <name>.response.json and rerun.
    Either way every answer is kept in <name>.response.json, so a rerun never pays twice.
    """

    def __init__(self, cfg, rd, mode):
        self.cfg, self.dir, self.mode = cfg, rd / "llm", mode
        self.max_usd, self.spent, self.calls = cfg["max_usd_per_run"], 0.0, 0
        self.dir.mkdir(parents=True, exist_ok=True)

    def chat(self, name, system, user, max_tokens, est_usd):
        resp = self.dir / f"{name}.response.json"
        if resp.exists():
            return _json_from(resp.read_text(encoding="utf-8"))
        if self.mode == "manual":
            (self.dir / f"{name}.prompt.md").write_text(f"SYSTEM\n{system}\n\nUSER\n{user}\n", encoding="utf-8")
            raise ManualPending(name)
        if self.spent + est_usd > self.max_usd:
            raise RuntimeError(f"LLM budget stop: spent ${self.spent:.3f}, next call ~${est_usd:.2f}, cap ${self.max_usd}")
        api = load_central_api(self.cfg["service"], self.cfg["profile"])
        if api.base_url.rstrip("/") != "https://openrouter.ai/api/v1":
            raise RuntimeError("central profile endpoint does not match OpenRouter; refusing to fall back")
        body = json.dumps({
            "model": self.cfg["model"], "max_tokens": max_tokens, "temperature": 0.7,
            "usage": {"include": True},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }).encode()
        req = urllib.request.Request(OPENROUTER_URL, data=body, headers={
            "Authorization": f"Bearer {api.api_key}", "Content-Type": "application/json",
            "X-Title": "inkseq topic research"})
        try:
            with urllib.request.urlopen(req, timeout=600) as r:
                res = json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code in (401, 402, 403):
                raise RuntimeError(f"OpenRouter refused the central profile ({e.code}: "
                                   f"{e.read()[:160].decode('utf-8', 'replace')}). Rerun with --manual, "
                                   "or ask the owner for a working profile.") from None
            raise
        usage = res.get("usage", {})
        self.spent += float(usage.get("cost") or 0)
        self.calls += 1
        print(f"  llm {name}: in={usage.get('prompt_tokens')} out={usage.get('completion_tokens')} "
              f"cost=${usage.get('cost', 0):.4f} total=${self.spent:.4f}")
        text = res["choices"][0]["message"]["content"]
        resp.write_text(text, encoding="utf-8")
        return _json_from(text)


def _json_from(text):
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return json.loads(text[text.index("{"): text.rindex("}") + 1])


# ---------- step A: signals -> candidate topics ----------

def signal_lines(signals):
    lines, index = [], {}
    for i, o in enumerate(signals["youtube"]["outliers"], 1):
        sid = f"Y{i:02d}"
        index[sid] = {"source": "youtube", **o}
        lines.append(f"{sid} [outlier x{o['outlier']} {o['format']}, {o['channel']}, {o['views']:,} views] {o['title']}")
    for i, a in enumerate(signals["wikipedia"]["articles"], 1):
        sid = f"W{i:02d}"
        index[sid] = {"source": "wikipedia", **a}
        lines.append(f"{sid} [wiki #{a['rank']}, {a['views']:,} views yesterday] {a['title']}")
    for i, p in enumerate(signals["reddit"], 1):
        sid = f"R{i:02d}"
        index[sid] = {"source": "reddit", **p}
        lines.append(f"{sid} [r/{p['subreddit']} top-of-day #{p['rank']}] {p['title']}")
    return lines, index


CHANNEL_BRIEF = """You are the topic researcher for a YouTube channel in edu-entertainment.
Format: one fixed cast of 2-3 animated characters acts out a different short story each episode
(curious asker / knowledgeable explainer / comic mess-up). Each episode leaves an adult/general viewer
having learned one clear thing or feeling good. It is NOT made for kids.
House rules: respectful, non-manipulative copy; accurate "legit bait" packaging (enticing but true,
the story pays off the title in the first 3 seconds); never mention AI, models or how videos are made;
no tragedy-chasing, politics, elections, celebrity gossip, adult content, or real private individuals;
no health, finance or legal advice delivered by a character (explaining how something works is fine)."""


def candidate_topics(signals, llm):
    lines, index = signal_lines(signals)
    n = llm.cfg["candidate_topics"]
    user = f"""Below are this week's demand signals: YouTube videos that beat their own channel's median
(outlier multiple shown), yesterday's most-read English Wikipedia articles, and top-of-day Reddit posts from
learning/story subreddits. Most signals are noise for us (elections, sport, films, gossip, channel promos).

Propose {n} distinct candidate topics for our channel. Rules:
- Each topic must teach one concrete, verifiable thing or leave a warm feeling, and be actable by our cast.
- Prefer topics backed by signals; cite the signal ids that show demand for the underlying curiosity.
  Do not copy another creator's video - find our own angle on the same curiosity.
- At most 8 topics may have no signal (evergreen ideas you are confident have broad demand).
- Reddit posts are research only: use the general theme, never re-tell a specific person's post.
- Spread across categories (science, history, human behaviour, engineering, nature, everyday life, language,
  space); no more than 8 per category.

Return JSON only: {{"topics": [{{"key": "kebab-slug", "topic": "short working title", "takeaway": "the one
thing the viewer learns or feels, one sentence", "category": "...", "signal_ids": ["Y03", "W12"],
"why_demand": "one line"}}]}}

SIGNALS
""" + "\n".join(lines)
    out = llm.chat("A-topics", CHANNEL_BRIEF, user, max_tokens=9000, est_usd=0.15)
    return out["topics"], index


# ---------- demand score (deterministic, from linked signals) ----------

def demand_score(signal_ids, index):
    best, sources = 1, set()
    for sid in signal_ids:
        s = index.get(sid)
        if not s:
            continue
        sources.add(s["source"])
        if s["source"] == "youtube":
            x = s["outlier"]
            v = 5 if x >= 8 else 4 if x >= 4 else 3 if x >= 2.5 else 2
        elif s["source"] == "wikipedia":
            x = s["views"]
            v = 5 if x >= 1_000_000 else 4 if x >= 300_000 else 3 if x >= 100_000 else 2
        else:
            v = 3 if s["rank"] <= 3 else 2
        best = max(best, v)
    return min(5, best + (1 if len(sources) >= 2 else 0))


# ---------- step B: packaging, outline, facts, scores ----------

RUBRIC = """Score each 1-5 (5 = best):
- packaging: would a curious adult click, and is it fully accurate? (5 = irresistible and true)
- takeaway: is there exactly one clear thing learned or felt? (5 = one crisp sentence a viewer could repeat)
- cast_fit: can asker/explainer/mess-up act it out with one speaker per shot and at most two characters on
  screen, without crowds, real people's likeness or effects our animation can't sell? (5 = natural fit)
- fact_check: can every key claim be checked against reputable public sources in under 30 minutes? (5 = easy)
- policy_safety: risk under YouTube's July 2026 inauthentic-content rules (repetitive templated AI video,
  emotionally manipulative clips, synthetic personas giving health/finance advice) and made-for-kids drift.
  5 = evergreen, informational, original story, adult framing; 3 = touches health/money/current events but
  only explains; 1 = advice from a character, distress/fear hook, real private people, or kid-targeted."""


def package_batch(name, topics, llm, angles, cast):
    angle_text = "\n".join(f"- {a['id']}: {a['name']} - {a['pattern']}" for a in angles)
    roles = "\n".join(f"- {k}: {v}" for k, v in cast["roles"].items())
    items = "\n".join(f"- key={t['key']} | {t['topic']} | takeaway: {t['takeaway']}" for t in topics)
    user = f"""For each topic below produce packaging, a story outline, facts to verify and scores.

ANGLE LIBRARY (use 5 different angles per topic, pick the 5 that fit best)
{angle_text}

CAST ROLES
{roles}
Constraints: {"; ".join(cast["constraints"])}.

PACKAGING RULES: title <= 60 characters, sentence case, no clickbait words ("insane", "shocking", "you won't
believe"), no fake urgency or fear. thumb_text <= 5 words. first_3s says exactly how the opening 3 seconds
pay off the title. The two strongest packages go in "best" (0-based indices).

OUTLINE: exactly 3 beats (hook, turn, payoff). Each beat has 1-3 shots; each shot has one speaker and
on_screen with at most two roles; the speaker must be on screen.

FACTS: the 3-5 claims the episode depends on, each with the best reputable public source you know
(name + URL; prefer the specific Wikipedia article, a university, a government or museum page).

{RUBRIC}

RISK FLAGS: short strings, e.g. "health-adjacent", "current-event", "made-for-kids drift",
"real-person likeness", "repetitive-format risk", "contested facts". Empty list if none.

Return JSON only: {{"topics": [{{"key": "...", "packages": [{{"angle": "<angle id>", "title": "...",
"thumb_text": "...", "thumb_visual": "...", "first_3s": "..."}}], "best": [0, 1], "outline": [{{"beat":
"hook", "shots": [{{"speaker": "asker", "on_screen": ["asker", "explainer"], "action": "..."}}]}}],
"facts": [{{"claim": "...", "source": "...", "url": "..."}}], "scores": {{"packaging": 4, "takeaway": 4,
"cast_fit": 4, "fact_check": 4, "policy_safety": 5}}, "score_note": "one line", "risk_flags": []}}]}}

TOPICS
{items}"""
    est = 0.03 + 0.02 * len(topics)
    return llm.chat(name, CHANNEL_BRIEF, user, max_tokens=2600 * len(topics) + 1000, est_usd=est)["topics"]


def validate(t, cast):
    """Code-side checks so the shortlist can't silently break the format rules."""
    problems = []
    for p in t.get("packages", []):
        if len(p.get("thumb_text", "").split()) > 5:
            problems.append(f"thumb text over 5 words: {p['thumb_text']}")
        if len(p.get("title", "")) > 70:
            problems.append(f"title long ({len(p['title'])} chars)")
    for beat in t.get("outline", []):
        for s in beat.get("shots", []):
            if len(s.get("on_screen", [])) > 2:
                problems.append(f"{beat.get('beat')}: more than two on screen")
            if s.get("speaker") not in s.get("on_screen", []):
                problems.append(f"{beat.get('beat')}: speaker not on screen")
            if s.get("speaker") not in cast["roles"]:
                problems.append(f"{beat.get('beat')}: unknown speaker {s.get('speaker')}")
    return problems


def build(week=None, mode="api"):
    cfg = settings()
    week = week or iso_week()
    rd = run_dir(week)
    signals = load_json(rd / "signals.json")
    llm = LLM(cfg["llm"], rd, mode)
    angles, cast = load_json(CONFIG / "angles.json"), load_json(CONFIG / "cast.json")

    print(f"[angles] candidate topics ({mode}) ...")
    try:
        topics, index = candidate_topics(signals, llm)
    except ManualPending as p:
        print(f"  waiting for {llm.dir / (str(p) + '.response.json')} - answer the .prompt.md next to it, then rerun")
        return None
    except RuntimeError as e:
        print(f"  stopped: {e}")
        return None
    seen = set()
    for t in topics:
        base = re.sub(r"[^a-z0-9-]+", "-", t["key"].lower()).strip("-")[:48] or "topic"
        k, n = base, 2
        while k in seen:
            k, n = f"{base}-{n}", n + 1
        seen.add(k)
        t["key"] = k
        t["demand"] = demand_score(t.get("signal_ids", []), index)
        t["evidence"] = [index[s] for s in t.get("signal_ids", []) if s in index]

    print(f"[angles] packaging {len(topics)} topics ...")
    by_key = {t["key"]: t for t in topics}
    bs = llm.cfg["batch_size"]
    batches = [(f"B{i // bs + 1}", topics[i:i + bs]) for i in range(0, len(topics), bs)]
    retried, pending = False, []
    while batches:
        name, batch = batches.pop(0)
        try:
            results = package_batch(name, batch, llm, angles, cast)
        except ManualPending:
            pending.append(name)
            continue
        except (RuntimeError, json.JSONDecodeError, ValueError) as e:  # budget stop, refused key or bad JSON
            print(f"  {name} skipped: {e}")
            if "budget stop" in str(e) or "refused" in str(e):
                break
            results = []
        for r in results:
            t = by_key.get(r.get("key"))
            if t:
                t.update({k: r.get(k) for k in ("packages", "best", "outline", "facts", "scores", "score_note",
                                                 "risk_flags")})
                t["format_problems"] = validate(t, cast)
        if not batches and not retried and not pending:  # one retry pass for topics a batch dropped
            retried = True
            missing = [t for t in topics if not t.get("scores")]
            batches = [(f"R{i // bs + 1}", missing[i:i + bs]) for i in range(0, len(missing), bs)]
    if pending:
        print(f"  waiting for answers to {', '.join(pending)} in {llm.dir}; rerun when they are in place")
    save_json(rd / "topics.json", {"week": week, "llm_mode": mode, "llm_model": llm.cfg["model"] if mode == "api" else "manual",
                                   "llm_cost_usd": round(llm.spent, 4), "llm_calls": llm.calls,
                                   "pending": pending, "topics": topics})
    print(f"[angles] done, LLM spend ${llm.spent:.3f}")
    return None if pending else topics


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    build(args[0] if args else None, "manual" if "--manual" in sys.argv else "api")
