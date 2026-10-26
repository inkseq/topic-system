# Topic system (YouTube, fixed cast, edu-entertainment)

A small weekly pipeline that picks episode topics for a channel where one fixed cast (curious asker,
knowledgeable explainer, comic mess-up) acts out a different short story each week, so viewers either learn
one thing or come away feeling good. Each run writes `data/shortlist/YYYY-Www.md` with the top 15 topics.

It uses Python 3.10+ and the standard library only. There is nothing to install and no database.

```
demand.py     YouTube outliers (median of ~10 neighbouring uploads), Wikipedia top articles, Reddit top-of-day
angles.py     LLM: signals -> 40 candidate topics -> 5 packages each, 3-beat outline, facts, scores
shortlist.py  weighted rank, policy gate, category cap, link check -> data/shortlist/YYYY-Www.md
run_weekly.py all three in order
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
| LLM (Claude via OpenRouter, `anthropic/claude-sonnet-5.5`) | `openrouter / openrouter--rig-root` | Returns `401 User not found` (2026-10-27); needs a working profile from the owner | `--manual` mode |

The YouTube step costs 1 quota unit per call: `channels.list` → uploads playlist → `playlistItems.list` →
`videos.list`. It never uses `search.list`, which costs 100 units.

## YouTube API policy limits (read before changing anything)

These rules come from the YouTube API Services Developer Policies
(https://developers.google.com/youtube/terms/developer-policies), checked 2026-10-27.

- **Official API only (III.E.6).**
  - No scraping of YouTube pages, RSS feeds or the site.
  - Channel IDs come from `channels.list?forHandle=`, and statistics come from `videos.list`.
- **30-day limit on other channels' statistics (III.E.4.b/d).**
  - `retention.py` runs before every weekly run.
  - After 30 days it deletes `data/<week>/signals.json` and the `llm/` prompts, strips the evidence from `topics.json`, and replaces the "Demand evidence" lines in the shortlist.
  - What stays is our own notes: topic, takeaway, angles, outline, facts, our 1-5 scores and the decision.
  - Clear any hand-pasted `data/manual_videos.csv` rows on the same schedule.
- **Derived metrics and aggregation (III.E.4.h, III.E.2.b).**
  - The outlier ratio (a video's views ÷ the median of its neighbours) is a metric derived from API data, compared across channels from different owners. The policy forbids this kind of new or derived metric.
  - We accept this risk only for **internal** editorial research. The ratio and the shortlists are never published, displayed in a public product, shared with third parties or sold.
  - This is one more reason runs stay local and `data/` is never committed.
  - The 1-5 demand rating is our coarse internal judgement. If Google or the owner wants a stricter reading, drop the YouTube signal and keep Wikipedia and Reddit.
- **Our own channel.** The numbers in `data/feedback.csv` are fine, whether the owner types them in or a later version pulls them through the authorised YouTube Analytics API. Data pulled that way must be refreshed or re-verified at least every 30 days.

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
| Wikipedia Pageviews, Reddit Atom feeds | Free. No key needed. |
| LLM (Sonnet 5.5 via OpenRouter, USD 2 / 10 per M tokens in/out) | About 1 call for topics plus 5 packaging batches. Roughly 40k tokens in and 60k out, so **about USD 0.70**. There is a hard stop at `llm.max_usd_per_run` = USD 1.80, and answers are cached so reruns are free. |

## How scoring works

Each topic gets six scores from 1 to 5, where 5 is best. Default weights are in brackets.

- **Demand** (0.25): computed in code from the linked signals, not by the LLM.
  - YouTube outlier ×8 or more = 5, ×4 = 4, ×2.5 = 3.
  - Wikipedia views: 1M or more = 5, 300k = 4, 100k = 3.
  - Reddit top-3 post = 3.
  - +1 when two different sources agree.
- **Packaging** (0.20): enticing *and* accurate.
- **Takeaway** (0.15): one clear thing learned or felt.
- **Cast fit** (0.15): one speaker per shot, at most two characters on screen, no crowds or real likenesses.
- **Fact-check** (0.10): every key claim can be checked against public sources in 30 minutes.
- **Policy safety** (0.15): risk under YouTube's July 2026 inauthentic-content rules (repetitive templated AI
  video, emotionally manipulative clips, synthetic personas giving health or finance advice), plus drift
  toward "made for kids".

Hard gates:

- A topic with policy safety below 3 never reaches the shortlist; it is listed under "Held back".
- At most 4 topics per category, so the channel does not look templated.
- Code rejects thumbnail text over 5 words, more than two characters on screen, or a speaker who is off screen.

Packaging rules the LLM is given ("legit bait"):

- The title is true, and the first 3 seconds pay it off.
- No "insane/shocking/you won't believe", no fake urgency, no fear hooks.
- AI, models or production methods are never mentioned.
- Reddit posts are used only as themes; no private person's story is retold.

## Feedback and reweighting

After publishing, add one row per video to `data/feedback.csv`:

- `topic_id`: copy it from the shortlist, for example `2026-W44-01-why-metal-bends-not-snaps`.
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
- **Wikipedia top list:** mostly elections, sport, film and bot noise. The LLM keeps the few learnable items.
- **Link check:** fact links are fetched once. Some publishers block scripts, so a "did not load" link may still
  be fine. Facts are always unverified until a person ticks them.
