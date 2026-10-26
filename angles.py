"""Angle layer: turn raw demand signals into candidate topics, then package and score each one with an LLM.

LLM access goes through the central registry loader (OpenRouter profile routing to a Claude model).
The key is passed straight into the request header and never printed or written anywhere.
"""
import json
import re
import sys
import urllib.error
import urllib.request

from common import CONFIG, iso_week, load_central_api, load_json, positioning, run_dir, save_json, settings

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
    for i, a in enumerate(signals.get("anniversaries", {}).get("events", []), 1):
        sid = f"A{i:02d}"
        index[sid] = {"source": "anniversary", **a}
        lines.append(f"{sid} [{a['years']}th anniversary on {a['date']}, {a['views_30d']:,} wiki views/30d] "
                     f"{a['year']}: {a['text']}")
    for i, g in enumerate(signals.get("google_trends", []), 1):
        sid = f"G{i:02d}"
        index[sid] = {"source": "google_trends", **g}
        news = "; ".join(n for n in g["news"] if n)
        lines.append(f"{sid} [US Google trend, {g['traffic']:,}+ searches, fades fast] {g['query']}"
                     + (f" (news: {news[:120]})" if news else ""))
    return lines, index


CHANNEL_BRIEF = """You are the topic researcher for a YouTube channel in edu-entertainment.
Format: one fixed cast of 2-3 animated characters acts out a different short story each episode
(curious asker / knowledgeable explainer / comic mess-up). Each episode leaves an adult/general viewer
having learned one clear thing or feeling good. It is NOT made for kids.
House rules: respectful, non-manipulative copy; accurate "legit bait" packaging (enticing but true,
the story pays off the title in the first 3 seconds); never mention AI, models or how videos are made;
no tragedy-chasing, politics, elections, celebrity gossip, adult content, or real private individuals;
no health, finance or legal advice delivered by a character (explaining how something works is fine)."""


def positioning_text(pos):
    pillars = "\n".join(f"- {p['id']}: {p['name']} | in: {', '.join(p['in'])} | out: {', '.join(p['out'])}"
                        for p in pos["pillars"])
    cast = "\n".join(f"- {c['role']}: {c['personality']}; wants {c['wants']}; running gags: "
                     f"{', '.join(c['running_gags']) or '-'}" for c in pos["cast"])
    return f"""CHANNEL POSITIONING ({pos['status']}): "{pos['name']}"
Audience: {pos['audience']['who']} ({pos['audience']['age_band']}); they want to feel {', '.join(pos['audience']['wants_to_feel'])}.
Promise: {pos['promise']}
Pillars (every idea must belong to exactly one):
{pillars}
Cast:
{cast}
Relationships: {pos['relationships']}
Tone rules: {'; '.join(pos['tone_rules'])}
No-go: {'; '.join(pos['no_go'])}
Possible series: {', '.join(pos.get('series', [])) or '-'}"""


def system_prompt(pos):
    return CHANNEL_BRIEF + "\n\n" + positioning_text(pos)


# ---------- step A: signals -> 100+ raw ideas (packaging-first: many ideas per winner) ----------

def raw_ideas(signals, llm, pos):
    lines, index = signal_lines(signals)
    n = llm.cfg["raw_ideas"]
    user = f"""Below are this week's demand signals: YouTube videos that beat their own channel's median
(outlier multiple shown), yesterday's most-read English Wikipedia articles, top-of-day Reddit posts,
round-number anniversaries 3-6 weeks ahead (A), and US Google daily search trends (G).
Most signals are noise for us (elections, sport, films, gossip, channel promos).

Brainstorm {n} raw episode ideas. Quantity first: strong channels write about 100 ideas per winner, so
include bold and odd ones. Rules:
- Each idea belongs to exactly one pillar id from the positioning; drop anything that fits none.
- Conflict first: someone in the cast wants something, something goes wrong, and the true fact is the
  payoff - not the premise. Write that arc in "hook" in one line.
- Prefer surprise, stakes, awe and humour. Plain textbook trivia ("how X works") needs a twist or it's out.
- Prefer ideas backed by signals and cite their ids. Do not copy another creator's video - find our own
  angle on the same curiosity. At most a quarter of the ideas may have no signal.
- Reddit posts are research only: use the theme, never re-tell a specific person's post.
- Anniversaries (A): only discoveries, inventions, firsts, buildings, culture and science; never a war,
  battle, disaster, death, crime, scandal or politician as the hook. The episode publishes on the date.
- Google trends (G): only when the trend points at an evergreen "why/how" question our cast can act out.
  Skip celebrities, news events, sports results and anything needing a real person's likeness.
- emotional_pull 1-5 (surprise, stakes, awe, humour). Calibrate: at most 1 idea in 5 gets a 5; 3 is average.
- series: true when the idea could be a recurring format (one of the possible series or a new one).

Return JSON only: {{"ideas": [{{"key": "kebab-slug", "idea": "working title", "hook": "who wants what ->
what goes wrong -> the fact that pays it off", "takeaway": "one sentence", "pillar": "<pillar id>",
"category": "...", "signal_ids": ["Y03"], "emotional_pull": 3, "series": false, "why_demand": "one line"}}]}}

SIGNALS
""" + "\n".join(lines)
    out = llm.chat("A-ideas", system_prompt(pos), user, max_tokens=24000, est_usd=0.30)
    return out["ideas"], index


# ---------- demand score (deterministic, from linked signals) ----------

def demand_score(signal_ids, index, source_weights=None):
    source_weights = source_weights or {}
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
        elif s["source"] == "anniversary":
            x = s["views_30d"]
            v = 5 if x >= 1_000_000 else 4 if x >= 200_000 else 3 if x >= 40_000 else 2
        elif s["source"] == "google_trends":
            x = s["traffic"]
            v = 5 if x >= 500_000 else 4 if x >= 100_000 else 3 if x >= 20_000 else 2
        else:
            v = 3 if s["rank"] <= 3 else 2
        best = max(best, round(v * source_weights.get(s["source"], 1.0)))
    return max(1, min(5, best + (1 if len(sources) >= 2 else 0)))


def timeliness(evidence, cfg):
    """5 = dated anniversary (deadline = the date), 4 = live search trend (publish within N days), 3 = evergreen."""
    ann = sorted(e["publish_by"] for e in evidence if e["source"] == "anniversary")
    if ann:
        e = next(x for x in evidence if x["source"] == "anniversary" and x["publish_by"] == ann[0])
        return 5, ann[0], f"{e['years']}th anniversary on {e['date']}"
    tr = sorted(e["publish_by"] for e in evidence if e["source"] == "google_trends")
    if tr:
        return 4, tr[0], f"search trend; publish within {cfg['google_trends']['publish_within_days']} days"
    return 3, None, None


def preselect(ideas, cfg):
    """Keep the best `package_top` in-pillar ideas by demand and emotional pull, with a per-category cap."""
    p = cfg["preselect"]
    ranked = sorted(ideas, key=lambda t: -(p["w_demand"] * t["demand"] + p["w_pull"] * t.get("emotional_pull", 3)
                                           + (p["series_bonus"] if t.get("series") else 0)
                                           + (p["timely_bonus"] if t["timeliness"] >= 4 else 0)))
    keep, per_cat = [], {}
    for t in ranked:
        if per_cat.get(t["category"], 0) >= p["max_per_category"]:
            continue
        keep.append(t)
        per_cat[t["category"]] = per_cat.get(t["category"], 0) + 1
        if len(keep) >= cfg["llm"]["package_top"]:
            break
    return keep


# ---------- step B: packaging, conflict outline, facts, scores ----------

RUBRIC = """Score 1-5. Calibrate: across the whole run a 5 is rare (at most 1 topic in 5 per dimension) and 3 is a
solid average - use the full range, or the scores can't separate topics.
- emotional_pull: surprise, stakes, awe or humour the viewer feels (5 = they'd send it to a friend)
- packaging: would a curious adult click, and is it fully accurate? (5 = irresistible and true)
- takeaway: is there one clear thing learned or felt? (5 = one crisp sentence a viewer could repeat)
- cast_fit: can the cast act it with one speaker per shot and at most two characters on screen, without
  crowds, real people's likeness or effects our animation can't sell?
Gates (pass at 3 or more; give the reason for any score below 3 in gate_flags):
- fact_check: can every key claim be checked against reputable public sources in under 30 minutes?
- policy_safety: YouTube's July 2026 inauthentic-content rules (repetitive templated AI video, emotionally
  manipulative clips, synthetic personas giving health/finance advice), made-for-kids drift, no-go list."""


def package_batch(name, topics, llm, angles, cast, pos):
    angle_text = "\n".join(f"- {a['id']}: {a['name']} - {a['pattern']}" for a in angles)
    items = "\n".join(f"- key={t['key']} | pillar={t['pillar']} | {t['idea']} | arc: {t.get('hook', '')} | "
                      f"takeaway: {t['takeaway']}" for t in topics)
    user = f"""For each topic below produce packaging, a conflict-first story outline, facts to verify and scores.

ANGLE LIBRARY (use 5 different angles per topic; favour stakes, what-if and counter when they fit)
{angle_text}

Constraints: {"; ".join(cast["constraints"])}.

PACKAGING RULES: title <= 60 characters, sentence case, no clickbait words ("insane", "shocking", "you won't
believe"), no fake urgency or fear. thumb_text <= 5 words. first_3s says exactly how the opening 3 seconds
pay off the title. The two strongest packages go in "best" (0-based indices).

CONFLICT: every story needs one. "conflict" names who wants what, what goes wrong, and the true fact that pays
it off. The fact is the payoff, not the premise.
OUTLINE: exactly 3 beats - "want", "trouble", "payoff". Each beat has 1-3 shots; each shot has one speaker and
on_screen with at most two roles; the speaker must be on screen. Use the cast's personalities and gags.

FACTS: the 3-5 claims the episode depends on, each with the best reputable public source you know
(name + URL; prefer the specific Wikipedia article, a university, a government or museum page).

{RUBRIC}

RISK FLAGS: short strings, e.g. "health-adjacent", "current-event", "made-for-kids drift",
"real-person likeness", "contested facts". Empty list if none.

Return JSON only: {{"topics": [{{"key": "...", "packages": [{{"angle": "<angle id>", "title": "...",
"thumb_text": "...", "thumb_visual": "...", "first_3s": "..."}}], "best": [0, 1], "conflict": {{"who":
"messup", "wants": "...", "goes_wrong": "...", "payoff_fact": "..."}}, "outline": [{{"beat": "want",
"shots": [{{"speaker": "messup", "on_screen": ["messup", "asker"], "action": "..."}}]}}], "facts": [{{"claim":
"...", "source": "...", "url": "..."}}], "scores": {{"emotional_pull": 3, "packaging": 3, "takeaway": 3,
"cast_fit": 3, "fact_check": 4, "policy_safety": 4}}, "gate_flags": [], "score_note": "one line",
"risk_flags": []}}]}}

TOPICS
{items}"""
    est = 0.03 + 0.02 * len(topics)
    return llm.chat(name, system_prompt(pos), user, max_tokens=2800 * len(topics) + 1000, est_usd=est)["topics"]


def validate(t, cast):
    """Code-side checks so the shortlist can't silently break the format rules."""
    problems = []
    for p in t.get("packages", []):
        if len(p.get("thumb_text", "").split()) > 5:
            problems.append(f"thumb text over 5 words: {p['thumb_text']}")
        if len(p.get("title", "")) > 70:
            problems.append(f"title long ({len(p['title'])} chars)")
    c = t.get("conflict") or {}
    if not all(c.get(k) for k in ("who", "wants", "goes_wrong", "payoff_fact")):
        problems.append("conflict incomplete")
    if [b.get("beat") for b in t.get("outline", [])] != ["want", "trouble", "payoff"]:
        problems.append("outline beats are not want/trouble/payoff")
    for beat in t.get("outline", []):
        for s in beat.get("shots", []):
            if len(s.get("on_screen", [])) > 2:
                problems.append(f"{beat.get('beat')}: more than two on screen")
            if s.get("speaker") not in s.get("on_screen", []):
                problems.append(f"{beat.get('beat')}: speaker not on screen")
            if s.get("speaker") not in cast["roles"]:
                problems.append(f"{beat.get('beat')}: unknown speaker {s.get('speaker')}")
    return problems


CALIBRATED = ["demand", "emotional_pull", "packaging", "takeaway", "cast_fit"]


def calibrate(topics, cap):
    """At most `cap` of topics keep a 5 on any dimension; the rest of the 5s become 4 (ties broken by the other scores)."""
    n = len(topics)
    allowed = max(1, int(cap * n))
    for dim in CALIBRATED:
        fives = [t for t in topics if (t["demand"] if dim == "demand" else t["scores"].get(dim)) == 5]
        if len(fives) <= allowed:
            continue
        fives.sort(key=lambda t: -(sum(v for k, v in t["scores"].items() if k in CALIBRATED and k != dim)
                                   + (t["demand"] if dim != "demand" else 0)))
        for t in fives[allowed:]:
            if dim == "demand":
                t["demand"] = 4
            else:
                t["scores"][dim] = 4
            t.setdefault("calibrated", []).append(dim)


def build(week=None, mode="api"):
    cfg = settings()
    week = week or iso_week()
    rd = run_dir(week)
    signals = load_json(rd / "signals.json")
    llm = LLM(cfg["llm"], rd, mode)
    angles, cast, pos = load_json(CONFIG / "angles.json"), load_json(CONFIG / "cast.json"), positioning()
    pillar_ids = {p["id"] for p in pos["pillars"]}

    print(f"[angles] raw ideas ({mode}, positioning '{pos['name']}' {pos['status']}) ...")
    try:
        ideas, index = raw_ideas(signals, llm, pos)
    except ManualPending as p:
        print(f"  waiting for {llm.dir / (str(p) + '.response.json')} - answer the .prompt.md next to it, then rerun")
        return None
    except RuntimeError as e:
        print(f"  stopped: {e}")
        return None
    seen = set()
    for t in ideas:
        base = re.sub(r"[^a-z0-9-]+", "-", t["key"].lower()).strip("-")[:48] or "idea"
        k, n = base, 2
        while k in seen:
            k, n = f"{base}-{n}", n + 1
        seen.add(k)
        t["key"], t["topic"] = k, t["idea"]
        t["demand"] = demand_score(t.get("signal_ids", []), index, cfg.get("demand_source_weights"))
        t["evidence"] = [index[s] for s in t.get("signal_ids", []) if s in index]
        t["timeliness"], t["publish_by"], t["time_reason"] = timeliness(t["evidence"], cfg)
        t["in_pillar"] = t.get("pillar") in pillar_ids  # positioning_fit gate
    fit = [t for t in ideas if t["in_pillar"]]
    topics = preselect(fit, cfg)
    for t in ideas:
        t["packaged"] = t in topics
    print(f"  {len(ideas)} ideas, {len(fit)} in a pillar, packaging the top {len(topics)}")

    by_key = {t["key"]: t for t in topics}
    bs = llm.cfg["batch_size"]
    batches = [(f"P{i // bs + 1}", topics[i:i + bs]) for i in range(0, len(topics), bs)]
    retried, pending = False, []
    while batches:
        name, batch = batches.pop(0)
        try:
            results = package_batch(name, batch, llm, angles, cast, pos)
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
                t.update({k: r.get(k) for k in ("packages", "best", "conflict", "outline", "facts", "scores",
                                                 "gate_flags", "score_note", "risk_flags")})
                t["raw_scores"] = dict(t["scores"] or {})
                t["format_problems"] = validate(t, cast)
        if not batches and not retried and not pending:  # one retry pass for topics a batch dropped
            retried = True
            missing = [t for t in topics if not t.get("scores")]
            batches = [(f"PR{i // bs + 1}", missing[i:i + bs]) for i in range(0, len(missing), bs)]
    if pending:
        print(f"  waiting for answers to {', '.join(pending)} in {llm.dir}; rerun when they are in place")
    else:
        calibrate([t for t in topics if t.get("scores")], cfg["calibration"]["max_share_of_fives"])
    save_json(rd / "topics.json", {"week": week, "llm_mode": mode, "llm_model": llm.cfg["model"] if mode == "api" else "manual",
                                   "llm_cost_usd": round(llm.spent, 4), "llm_calls": llm.calls,
                                   "positioning": {"name": pos["name"], "status": pos["status"]},
                                   "pending": pending, "ideas_total": len(ideas), "ideas_in_pillar": len(fit),
                                   "ideas": [{k: t.get(k) for k in ("key", "idea", "hook", "pillar", "category",
                                              "emotional_pull", "series", "demand", "in_pillar", "packaged")}
                                             for t in ideas],
                                   "topics": topics})
    print(f"[angles] done, LLM spend ${llm.spent:.3f}")
    return None if pending else topics


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    build(args[0] if args else None, "manual" if "--manual" in sys.argv else "api")
