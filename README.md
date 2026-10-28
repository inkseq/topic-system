# Topic system (YouTube, fixed cast, edu-entertainment)

A small weekly pipeline that picks episode topics for a channel where one fixed cast (curious asker,
knowledgeable explainer, comic mess-up) acts out a different short story each week, so viewers either learn
one thing or come away feeling good.

Each run does the following, in order:

1. Reads the channel positioning.
2. Collects demand signals.
3. Brainstorms more than 100 raw ideas.
4. Packages the best 30.
5. Writes these outputs:
   - `data/shortlist/YYYY-Www.md` with the top 15;
   - `data/<week>/graph.json` for the Idea Map;
   - a weekly strategy memo at `data/memos/YYYY-Www.md`.

It uses Python 3.10+ and the standard library only. There is nothing to install and no database.

```
demand.py     YouTube outliers (median of ~10 neighbouring uploads), Wikipedia top articles, Reddit top-of-day,
              round-number anniversaries 3-6 weeks ahead, Google Trends daily searches (US), "dense"
              sources (Wikipedia Unusual articles, Public Domain Review, Atlas Obscura, Gutenberg) and
              the team's favourites inbox
angles.py     LLM: signals -> 120+ raw ideas (pillar, emotional pull, series) -> top 30 packaged
              (5 packages, conflict, want/trouble/payoff outline, facts) -> core/casual/new check ->
              calibrated scores
shortlist.py  weighted rank, pass/fail gates, category cap, near-duplicate rule, fresh slots, then a
              production plan for the 15 (3 thumbnail options, Shorts cuts, re-hooks), link check
graph.py      data/<week>/graph.json for the private Idea Map page
memo.py       weekly strategy memo + data/bets.json (picks the ONE main episode)
script.py     the deliverable: full script for that episode + its Shorts cuts (draft -> script doctor -> lint)
run_weekly.py everything in order
reweight.py   learns new weights from data/feedback.csv once 20+ videos are published
retention.py  purges raw third-party YouTube data after 30 days (runs at the start of every weekly run)
config/       generic settings, angle library, cast roles, and *.example.* templates
data/         private and generated, gitignored (see below)
```

## Public repo layout

This repo is public, so git tracks only code, this README and generic templates. `.gitignore` works as an
allowlist: `*` ignores everything, and only the files named with `!` are tracked. Before tracking a new file,
add a `!/path` line for it and check with `git ls-files`.

Everything private or generated lives in `data/`, which is never committed:

| File | What it is |
|---|---|
| `data/seed_channels.csv` | The real reference-channel list. If it's missing, the run falls back to `config/seed_channels.example.csv`. |
| `data/feedback.csv` | Published-video results, created from `config/feedback.example.csv` on the first run. |
| `data/manual_videos.csv` | Optional hand-pasted uploads; the format is in `config/manual_videos.example.csv`. |
| `data/channel_ids.json` | Cached handle → channel id lookups (from the API). |
| `data/<week>/signals.json` | Raw YouTube, Wikipedia and Reddit data. |
| `data/<week>/llm/` | Every prompt and answer, including Reddit-derived text. |
| `data/<week>/topics.json`, `shortlist.json` | Scored topics. |
| `data/shortlist/YYYY-Www.md` | The weekly shortlist. |
| `data/weights.learned.json` | Weights learned from feedback. |
| `data/settings.local.json` | Optional private overrides, merged over `config/settings.json`. |
| `data/favorites.md` | The team's favourites inbox (one item per line or bullet); template in `config/favorites.example.md`. |
| `data/positioning.json` | The channel positioning every step reads; template in `config/positioning.example.json`. |
| `data/positioning-options.md` | The positioning options for the owner to choose from. |
| `data/<week>/graph.json` | Idea Map export. |
| `data/memos/YYYY-Www.md`, `data/bets.json` | Weekly memos and each week's structured bet. |
| `data/scripts/<topic-id>.md`, `.json` | The week's finished script (human-readable, and machine-readable for a later import into the story workbench). |

Runs stay **local** (Windows Task Scheduler), not in GitHub Actions: Actions logs and artifacts on a public
repo are public.

## Running it

```bash
python run_weekly.py
```

Other forms:

- `python run_weekly.py --manual`: run without an LLM key. Each prompt is written to
  `data/<week>/llm/<name>.prompt.md`. Paste it into any Claude chat, save the JSON answer as
  `<name>.response.json` next to it, then rerun.
- `python run_weekly.py 2026-W44`: re-render a past week. Cached signals and LLM answers are reused, so a rerun
  costs nothing.
- `python run_weekly.py --refresh`: collect this week's signals again.

## Keys

Keys come only from the central AI Registry loader (`api_registry.load_api`). The default location is
`~/Desktop/AI-Registry`; set `AI_REGISTRY_ROOT` to point elsewhere. There is no `.env`, and keys never appear
in code, config or logs. Each key goes straight into the request and the code never prints it. The profile
names are in `config/settings.json`.

| Need | Profile | Status | Fallback |
|---|---|---|---|
| YouTube Data API v3 | `youtube / youtube--topic-system-20261027--v2`; base_url must be `https://www.googleapis.com/youtube/v3` | Works (2026-10-27): 32 channels, 1,449 uploads | The YouTube step is skipped with a message. Hand-pasted rows in `data/manual_videos.csv` still work. YouTube pages and feeds are never scraped. |
| LLM (Claude via OpenRouter, `anthropic/claude-sonnet-5.5`) | `openrouter / openrouter--topic-system-20261028` | Dedicated key in the Inkseq OpenRouter workspace; works (live test 2026-10-28). The old `openrouter--rig-root` and `openrouter--inkseq` profiles return 401. | `--manual` mode |

The YouTube step costs 1 quota unit per call: `channels.list` → uploads playlist → `playlistItems.list` →
`videos.list`. It never uses `search.list`, which costs 100 units.

## YouTube API policy limits (read before changing anything)

These rules come from the YouTube API Services Developer Policies
(https://developers.google.com/youtube/terms/developer-policies), checked 2026-10-27.

- **Official API only (III.E.6).**
  - No scraping of YouTube pages, RSS feeds or the site.
  - Channel IDs come from `channels.list?forHandle=`, and statistics come from `videos.list`.
- **30-day limit on other channels' statistics (III.E.4.b/d).**
  - `retention.py` runs before every weekly run, using `retention.purge_after_days` (default 30).
  - After 30 days it deletes `data/<week>/signals.json` and the `llm/` prompts, strips the evidence from `topics.json`, replaces the "Demand evidence" lines in the shortlist, scrubs multiples and view counts from that week's memo, and deletes that week's `graph.json`.
  - What stays is our own notes: topic, takeaway, angles, outline, facts, our 1-5 scores and the decision.
  - Clear any hand-pasted `data/manual_videos.csv` rows on the same schedule.
- **Derived metrics and aggregation (III.E.4.h, III.E.2.b).**
  - The outlier ratio (a video's views ÷ the median of its neighbours) is a metric derived from API data, compared across channels from different owners. The policy forbids this kind of new or derived metric.
  - We accept this risk only for **internal** editorial research. The ratio and the shortlists are never published, displayed in a public product, shared with third parties or sold.
  - This is one more reason runs stay local and `data/` is never committed.
  - The 1-5 demand rating is our coarse internal judgement. If Google or the owner wants a stricter reading, drop the YouTube signal and keep Wikipedia and Reddit.
- **Our own channel.** The numbers in `data/feedback.csv` are fine, whether the owner types them in or a later version pulls them through the authorised YouTube Analytics API. Data pulled that way must be refreshed or re-verified at least every 30 days.

## Weekly schedule (Claude desktop scheduled task, active)

The owner prefers to spend the Claude subscription rather than API credit, so the weekly run is a Claude
desktop scheduled task ("Weekly topic → script run", Mondays 08:00 local). It runs
`python run_weekly.py --manual`, answers every `data/<week>/llm/*.prompt.md` itself by writing the matching
`*.response.json`, reruns until the shortlist, memo and script exist, then renders
`python idea_map/build.py` and republishes the private Idea Map page. The OpenRouter profile stays as a
fallback for unattended script-only runs. The desktop app must be open; a missed run starts on next launch.

## Weekly schedule (Windows Task Scheduler; not installed)

This runs every Monday at 08:00 local time. Install it only once the LLM profile works: an unattended run
can't answer manual prompts. Replace the two paths with your own:

```bash
schtasks /Create /TN "Topic shortlist" /SC WEEKLY /D MON /ST 08:00 /TR "\"C:\path\to\python.exe\" \"C:\path\to\topic-system\run_weekly.py\""
```

To remove it: `schtasks /Delete /TN "Topic shortlist" /F`.

## Costs per weekly run

| Item | Cost |
|---|---|
| YouTube Data API | Free. About 32 channels × 3–8 units, roughly 200 of the 10,000 free daily units. |
| Wikipedia Pageviews and On-this-day, Reddit Atom feeds, Google Trends RSS | Free. No key needed. About 22 On-this-day requests plus about 40 pageview lookups, and 1 Trends request. |
| Dense sources | Free. 3 Wikipedia pages, 2 RSS feeds and 1 Gutenberg page per run, each after a robots.txt check and a 1-second pause. |
| LLM (Sonnet 5.5 via OpenRouter, USD 2 / 10 per M tokens in/out) | 1 ideas call (about 120 ideas), 4 packaging batches, 1 audience check, 1 production plan for the shortlist, 1 memo and 2 script passes. Roughly 90k tokens in and 110k out, so **about USD 1.30**; the script stage has its own cap of USD 3. There is a hard stop at `llm.max_usd_per_run` = USD 1.80, and answers are cached so reruns are free. |

## Favourites and dense sources

**Favourites** are the "loved" half of the idea pair.

- The owner and team add things they personally love to `data/favorites.md`: one per line or bullet, with a link
  or note.
- Each run passes them to the ideas step as `F` signals. The LLM tags each with a pillar and tries to pair it
  with a validated signal (`Y`, `W`, `R`, `A`, `G`) through the cast's point of view.
- A topic that grows from a favourite gets taste 5, and the shortlist shows "From favourite: ...".
- When the inbox is empty, the memo says so and suggests three things to add.

**Dense sources** have high idea density and are not the feeds everyone reads. They are research only and
lightly fetched with a descriptive User-Agent. robots.txt is checked first, and raw responses are stored only in
`data/<week>/signals.json`.

| Source | How it's read |
|---|---|
| Wikipedia "Unusual articles" | 3 sections per week, rotating by ISO week, read through `/wiki/...?action=raw` because robots.txt disallows the API paths. 12 sampled entries per section. |
| Public Domain Review | RSS feed. |
| Atlas Obscura | RSS feed. |
| Project Gutenberg | Top-100 "yesterday" list, used as seeds for old-book stories and folklore. Public-domain texts can be adapted. |

Atlas Obscura and Public Domain Review are leads only: their writing is never copied.

## Angle library

`config/angles.json` has 12 angles:

| Angle | What it does |
|---|---|
| counter | Counter-intuitive fact. |
| what-if | A hypothetical scenario. |
| extreme | The biggest, smallest, fastest or record case. |
| hidden reason | The reason behind something familiar. |
| stakes | A countdown or something to lose. |
| cast lives it | A cast member goes through the situation. |
| then vs now | How it was compared with how it is. |
| mystery | A puzzle the viewer solves along with the cast. |
| **disagree** | State the common belief, then where we disagree and why. Grounded and fact-checked, never contrarian for its own sake; voiced by the explainer, who now politely challenges common wisdom. |
| **redefine** | Reframe what the thing really is. |
| **past self** | How a cast member would explain it to their younger self. |
| **why it matters** | Why the everyday viewer should care. |

## Channel positioning

The positioning lives in `data/positioning.json` because it is strategy, so it stays private. It is read
before anything else. It sets:

- the audience and what they want to feel;
- the one-line promise;
- 3–4 pillars, each with what's in and what's out;
- the cast's personalities, wants and running gags;
- tone rules, the no-go list, possible series and a seed list of the first 20 episodes.

`data/positioning-options.md` holds the options for the owner to choose from. The run uses option 1, marked
`"status": "provisional"`, until the owner picks.

Every idea must name a pillar. Ideas outside the pillars fail the positioning-fit gate, and ideas that could
become a series get a small bonus.

## From 120 ideas to 15 (packaging first)

1. **Raw ideas.** The LLM writes about 120 ideas, each with:
   - a conflict arc: who wants what, what goes wrong, and the fact that pays it off;
   - a pillar;
   - an emotional-pull rating (surprise, stakes, awe, humour);
   - a series flag.
2. **Preselect.** Code keeps the 30 best in-pillar ideas by demand and pull, with small bonuses for series and
   dated ideas and at most 6 per category.
3. **Package.** Each of the 30 gets:
   - 5 packages from the angle library;
   - a `conflict` and a want / trouble / payoff outline (the fact is the payoff, not the premise);
   - facts with sources;
   - scores.
4. **Calibrate.** On every scored dimension at most 20% of topics keep a 5. Extra 5s become 4s, with ties broken by
   the other scores, so the scores actually spread. The shortlist header prints the share of 5s per
   dimension.

## How scoring works

The weighted criteria are scored 1–5, where 5 is best. Default weights are in brackets.

- **Demand**: computed in code from the linked signals.
  - YouTube outlier ×8 or more = 5, ×4 = 4, ×2.5 = 3.
  - Wikipedia views: 1M or more = 5, 300k = 4, 100k = 3.
  - Reddit top-3 post = 3.
  - Anniversary: median 30-day views across the event's pages: 1M = 5, 200k = 4, 40k = 3.
  - Google Trends: 500k = 5, 100k = 4, 20k = 3.
  - Dense source: 3. It is a curated oddity with no popularity number.
  - +1 when two different sources agree.
  - Each source is multiplied by `demand_source_weights`.
- **Emotional pull**: surprise, stakes, awe or humour. Would they send it to a friend?
- **Packaging**: enticing *and* accurate.
- **Takeaway**: one clear thing learned or felt.
- **Cast fit**: one speaker per shot, at most two characters on screen, no crowds or real likenesses.
- **Timeliness**: 5 = dated anniversary, 4 = live trend, 3 = evergreen.
- **Core / casual / new** (`ccn`, Paddy Galloway's check): three sub-scores, each with a one-line reason.
  - Core: would people who watch most of our videos love it? Until the channel has videos, core means the
    positioning's audience.
  - Casual: would occasional viewers click?
  - New: would someone who has never seen the channel understand it and click?
  - `ccn` is their rounded mean.
  - A topic that serves only one group (one score of 4 or more, the other two 2 or less) is flagged
    "core-only", "casual-only" or "new-only".
- **Taste**: 5 when the idea grows from a team favourite, otherwise 3.
- **Novelty**: 5 when a dense source supplies a subject no YouTube outlier covers, 4 when dense and YouTube
  overlap, otherwise 3.

Current default weights are in `config/settings.json`:

| Criterion | Weight |
|---|---|
| Emotional pull | 0.25 |
| Demand | 0.18 |
| Core / casual / new | 0.15 |
| Packaging | 0.14 |
| Timeliness | 0.06 |
| Taste | 0.06 |
| Novelty | 0.06 |
| Takeaway | 0.05 |
| Cast fit | 0.05 |
- **Series bonus:** +0.1 to the total.

**Gates (pass/fail, not points).** A topic is held back if any of these fail:

- positioning fit: it is inside a pillar;
- policy safety is at least 3. This covers YouTube's July 2026 inauthentic-content rules, made-for-kids drift
  and the no-go list;
- fact-check is at least 3.

**Other rules:**

- At most 4 topics per category.
- If a topic shares its demand signal with a higher-ranked pick, it is held back as a near-duplicate.
- **Fresh slots.** Up to `shortlist.fresh_slots` (3) of the 15 places are kept for the best "fresh" topics: novelty
  5, gates passed. A fresh topic that earns its place on merit also counts toward the 3. Reserved picks are marked
  "(fresh slot)".
- Code flags any of these:
  - thumbnail text over 5 words;
  - more than two characters on screen;
  - an off-screen speaker;
  - an incomplete conflict;
  - outline beats other than want / trouble / payoff.

Packaging rules the LLM is given ("legit bait"):

- The title is true, and the first 3 seconds pay it off.
- No "insane/shocking/you won't believe", no fake urgency, no fear hooks.
- AI, models or production methods are never mentioned.
- Reddit posts are used only as themes; no private person's story is retold.

## Operating model and production plan

`data/positioning.json` holds an `operating_model`. The default is `weekly_operator`:

- One long-form episode a week, 3–6 minutes long.
- 2–3 vertical Shorts are cut from it for YouTube Shorts, TikTok and Reels.
- **Retention curve** (MrBeast's, scaled down):
  - the promise is paid off in the first 5 seconds;
  - a twist or laugh re-hooks the viewer about every third;
  - the video ends right after the payoff.
- **Phase 1:** 30–50 episodes before heavy optimisation.

Because only one episode ships each week, the 15-topic shortlist works as a **bench** of candidate episodes. After
ranking, a production step plans each of the 15:

- **Three thumbnail options** of different *kinds*, for example a cast reaction close-up, the object or result, and
  a before/after contrast. They are not wording variants; they are meant for YouTube's built-in title and
  thumbnail A/B test. Each option has:
  - a title;
  - thumbnail text of 4 words or fewer;
  - 1–3 focal points;
  - a one-line "glance test";
  - how the first 5 seconds pay off that exact promise (legit bait).
- **2–3 Shorts cuts** that work on their own.
- **Re-hooks** at about one third and two thirds of the runtime.

Code flags missing kinds, thumbnail text over 4 words, focal-point counts outside 1–3, and the wrong number of
Shorts cuts. Any new fact in a re-hook or Shorts cut needs checking like the main facts.

## Weekly memo and bets

`memo.py` writes `data/memos/YYYY-Www.md`, at most about 1,500 words. It reads:

- the positioning;
- the last two memos;
- last week's bet from `data/bets.json`;
- `data/feedback.csv`;
- this week's shortlist and evidence.

| Section | What it contains |
|---|---|
| **Signal** | Up to 3 trends in our pillars, each tagged new / building / saturating / disproven, with evidence. |
| **Scoreboard** | Our videos against our own baseline (median 7-day views), computed in code. Says "no data yet" until videos exist, and lists resurfacing candidates. |
| **Strategy** | Grades last week's bet A–F with the reason. Then, following the operating model, picks exactly **one main episode** with its core/casual/new scores, three thumbnail options, re-hooks and Shorts cuts, plus two bench episodes, one experiment and one thing to stop. |
| **Watchlist** | 2–4 things to check next week. |

This week's bet is stored as structured data in `data/bets.json` (main move, hypothesis, metric, target,
main episode with thumbnail options and Shorts cuts, bench, experiment, stop), so next week's memo can grade
it.

## Scripts (topic -> finished script)

The script is the deliverable; the shortlist and memo feed it. Each week, `script.py` writes the full script
for the memo's one main episode to `data/scripts/<topic-id>.md` and `.json`. It runs in two LLM passes:

1. **S-draft** writes the script.
2. Code lints the draft.
3. **S-doctor** revises it against a checklist and the draft's lint report:
   - cut about 20%;
   - sharpen the hook;
   - keep the cast's voices consistent with the positioning;
   - no "and then" connectors;
   - word count against the target (about 150 words a minute);
   - read every line aloud;
   - no model names, no health or finance advice, a respectful tone.
4. Code lints it again.

The run's script budget is `script.max_usd` (USD 3).

**What a script contains, in order:**

1. **Header:** title and the 3 thumbnail options, logline, one takeaway, pillar, core/casual/new, target length
   (3–6 minutes), and each character's want in this episode.
2. **Beat sheet** on Pixar's story spine ("Once upon a time… Every day… One day… Because of that… Until
   finally…"), 5–8 beats. Every link between beats is written out as **BUT** or **THEREFORE** (the
   Parker/Stone rule).
3. **Misconception:** the asker or mess-up says the common wrong belief, and the explainer confronts it in
   dialogue with a demonstration (Derek Muller's finding).
4. **Retention map:**
   - a 0–5 s cold open that pays off the thumbnail;
   - an open question by 10 s;
   - a re-hook at about each third;
   - the payoff, then the end within 5 s;
   - 2–3 Shorts ranges of 20–50 s each, with 9:16 framing notes.
5. **Shot list:** 5-second windows, at most 2 characters on screen, one speaker per shot, the line starting at
   least 1 s in, medium or close framing for dialogue, 12 words or fewer per line. These are the current
   production limits, including avoiding 3-person dialogue shots.
6. **Dialogue script** in screenplay form, for the voice pass.
7. **Fact sheet** with a source and a checkbox for every claim.

**Lint fails** on any of these:

- an "and then" connector, or any connector other than BUT/THEREFORE;
- fewer than 5 or more than 8 beats;
- shot windows off the 5-second grid;
- 3 or more characters on screen;
- a speaker who isn't on screen;
- an early line start, wrong framing, or too many words;
- runtime outside 3–6 minutes;
- dialogue that can't fit the cut;
- no cold open, a late open question, or missing re-hooks;
- more than 5 s between the payoff and the end;
- bad Shorts ranges;
- model names, clickbait wording, or advice-like wording.

It warns on sparse dialogue (under 90 words a minute) and on unchecked facts.

**Status:** draft → doctored → facts-verified → final. Code only sets `doctored`. A person moves it on: tick
each fact (`"checked": true`) in the `.json`, then run:

```bash
python script.py --set-status facts-verified data/scripts/<topic-id>.json
```

and later `--set-status final`. Both refuse while lint has errors or any fact is unchecked. After hand edits,
`python script.py --render <file>.json` rebuilds the `.md`.

`config/example_script.json` and `.md` show the format. They are a made-up episode on Aesop's public-domain
fable "The Crow and the Pitcher".

## Idea Map export

`graph.py` writes `data/<week>/graph.json`:

- **Nodes:** topic, category, angle, and the evidence kinds youtube, wikipedia, reddit, anniversary, trend,
  dense and favorite. Topic nodes carry core/casual/new, the three thumbnail options and the Shorts cuts.
- **Links:** category, angle, evidence, and "related" when two topics share evidence.
- **Meta:** funnel counts and signal totals.

The private Idea Map page reads this file. It contains YouTube statistics, so it stays local.

## Timely sources

- **Anniversaries.**
  - Wikipedia's "On this day" feed is read for every day 21-42 days ahead (`anniversaries` in settings), so there is time to produce.
  - Only round anniversaries are kept: 25, 50, 75, 100, then every 50 years.
  - Events about wars, battles, disasters, deaths, crimes and the like are dropped in code. The LLM also skips politics and scandals, and never uses a tragedy as the hook.
  - The linked page is the one named verbatim in the event text, because the first linked page is often generic. Demand uses the median views across the event's pages.
- **Google Trends.**
  - The US daily trending-searches RSS is read once per run, for research only.
  - Sports match-ups ("x vs y") are dropped in code. The LLM keeps a trend only when it points at an evergreen why/how question, and skips celebrities, news, sports results and real-person likeness.
  - Trends fade fast, so the topic gets a "publish by" date.
- **Dated picks.** Anniversary and trend topics that pass the policy gate but miss the top 15 are listed under "Dated picks outside the top list", sorted by publish date, so calendar slots aren't lost.

## Feedback and reweighting

After publishing, add one row per video to `data/feedback.csv`:

- `topic_id`: copy it from the shortlist, for example `2026-W44-01-silly-string-tripwires`.
- `package_used`: the angle id, such as `counter`, `whatif` or `livesit`.
- Views at 48 hours and 7 days, CTR, average view duration, and returning-viewer share from YouTube Studio.

Once there are 20 or more rows with 7-day views, run:

```bash
python reweight.py
```

It correlates each criterion with a combined outcome (log views, CTR, average view duration, returning share)
and blends the result 50/50 with the defaults. It writes `data/weights.learned.json`, which `shortlist.py`
uses from then on. Use `--dry-run` to preview, and delete that file to go back to the defaults. It also prints
which packaging angles performed best.

## Why we didn't reuse an existing repo

| | `pandich93/youtube-niche-finder` | `luzcypher/yt-outlier-finder` |
|---|---|---|
| Licence | MIT | **None** (all rights reserved, so it can't be reused) |
| Last commit | 2026-10-24, active | 2026-10-24, a single HTML file |
| Setup weight | Docker Compose, PostgreSQL + pgvector, worker, HTTP API, 100 MCP tools, dashboard, extension | Browser page |
| Fit | Built for niche research at scale; heavy to run for a 2–3 person team pulling 30 channels a week | Can't legally reuse |

We kept pandich93's idea and wrote about 150 lines here. `demand.outlier_scores` uses the same method: a video's
views divided by the median of about 10 neighbouring uploads of the same format (Shorts vs long). This follows
`backend/domain/metrics.py` in that repo (MIT). If the team later wants a dashboard, alerts or
competitor tracking, niche-finder is the upgrade path, and the same YouTube key works there.

## Known limits

- **Shorts detection:** API mode treats videos of 3 minutes or less as Shorts, because the API exposes no
  Shorts flag. Videos younger than 3 days are skipped.
- **High-volume channels:** channels such as Bright Side produce large multiples on catastrophe-style titles.
  The LLM step and the policy gate filter those.
- **Reddit:** unauthenticated feeds are rate-limited. Expect some subreddits to return 429 on a given day. The
  run continues without them.
- **On-this-day text can be wrong.** The 2001 Hubble entry says "hydrogen"; the first detection was sodium. Treat event text as a lead, never a fact.
- **Google Trends:** the RSS carries only a few dozen items, mostly sports and celebrities. Many weeks will produce no usable trend topic.
- **Wikipedia top list:** mostly elections, sport, film and bot noise. The LLM keeps the few learnable items.
- **Link check:** fact links are fetched once. Some publishers block scripts, so a "did not load" link may still
  be fine. Facts are always unverified until a person ticks them.
