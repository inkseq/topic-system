"""Demand layer: YouTube outliers from reference channels, Wikipedia top articles, Reddit top-of-day,
upcoming round-number anniversaries (Wikipedia "On this day") and Google Trends daily searches (US).

YouTube data comes only from the official YouTube Data API v3 (central registry profile in settings.json);
YouTube pages and feeds are never scraped (API Developer Policies III.E.6). Without a usable profile the
YouTube step is skipped; data/manual_videos.csv (hand-pasted rows) still works.
Only outlier hits are stored, and retention.py purges their raw numbers after 30 days (III.E.4).

Outlier score = views / median(views of the ~10 nearest uploads of the same format on that channel).
It is a derived metric (III.E.4.h): internal only, never displayed publicly, shared or sold.
Median-of-neighbours (as in pandich93/youtube-niche-finder, MIT) instead of the all-time channel average,
so a channel that is growing fast does not make every new upload look like an outlier.
"""
import csv
import datetime as dt
import json
import random
import re
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.robotparser
from concurrent.futures import ThreadPoolExecutor
import xml.etree.ElementTree as ET  # Reddit Atom feed

from common import DATA, http_get, iso_week, load_central_api, load_json, private_file, run_dir, save_json, settings

YT_API = "https://www.googleapis.com/youtube/v3/"  # must match the central profile's base_url
NOW = dt.datetime.now(dt.timezone.utc)


def _parse_time(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def _iso_duration_seconds(s):
    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", s or "")
    if not m:
        return None
    d, h, mi, se = (int(x or 0) for x in m.groups())
    return ((d * 24 + h) * 60 + mi) * 60 + se


# ---------- outlier maths ----------

def outlier_scores(videos, cfg):
    """videos: list of dicts with published (datetime), views, format. Adds 'baseline' and 'outlier'."""
    out = []
    for fmt in ("long", "short"):
        group = sorted((v for v in videos if v["format"] == fmt), key=lambda v: v["published"])
        for v in group:
            age = (NOW - v["published"]).days
            if age < cfg["min_age_days"] or age > cfg["lookback_days"]:
                continue
            others = sorted((o for o in group if o is not v),
                            key=lambda o: abs((o["published"] - v["published"]).total_seconds()))
            neigh = [o["views"] for o in others[: cfg["neighbours"]]]
            if len(neigh) < cfg["min_neighbours"]:
                continue
            base = statistics.median(neigh)
            if base <= 0:
                continue
            out.append({**v, "baseline": base, "outlier": round(v["views"] / base, 2),
                        "neighbours_used": len(neigh), "age_days": age})
    return out


# ---------- channel ids ----------

def resolve_channel_ids(handles, ua, api_key):
    """Handle -> channel id via channels.list(forHandle), 1 quota unit each; cached in data/."""
    cache_path = DATA / "channel_ids.json"
    cache = load_json(cache_path) if cache_path.exists() else {}
    for h in handles:
        if h in cache:
            continue
        cid = None
        try:
            q = urllib.parse.urlencode({"part": "id", "forHandle": "@" + h, "key": api_key})
            items = json.loads(http_get(YT_API + "channels?" + q, ua)).get("items", [])
            cid = items[0]["id"] if items else None
        except Exception as e:  # urllib errors carry the URL's path, not the query string
            print(f"  ! could not resolve @{h}: {type(e).__name__} {getattr(e, 'code', '')}", file=sys.stderr)
        if cid:
            cache[h] = cid
    save_json(cache_path, cache)
    return cache


# ---------- YouTube sources ----------

def fetch_api(channel_id, key, ua, lookback_days):
    def get(endpoint, **params):
        params["key"] = key
        return json.loads(http_get(YT_API + endpoint + "?" + urllib.parse.urlencode(params), ua))

    ch = get("channels", part="contentDetails,snippet", id=channel_id)["items"][0]
    uploads = ch["contentDetails"]["relatedPlaylists"]["uploads"]
    title = ch["snippet"]["title"]
    # one extra month so the oldest videos in the window still have neighbours on both sides
    cutoff = NOW - dt.timedelta(days=lookback_days + 30)
    ids, token = [], None
    for _ in range(6):
        params = {"part": "contentDetails", "playlistId": uploads, "maxResults": 50}
        if token:
            params["pageToken"] = token
        page = get("playlistItems", **params)
        for it in page.get("items", []):
            pub = it["contentDetails"].get("videoPublishedAt")
            if pub and _parse_time(pub) >= cutoff:
                ids.append(it["contentDetails"]["videoId"])
        token = page.get("nextPageToken")
        last = page.get("items", [{}])[-1].get("contentDetails", {}).get("videoPublishedAt")
        if not token or (last and _parse_time(last) < cutoff):
            break
    videos = []
    for i in range(0, len(ids), 50):
        res = get("videos", part="snippet,statistics,contentDetails", id=",".join(ids[i:i + 50]))
        for v in res.get("items", []):
            secs = _iso_duration_seconds(v["contentDetails"].get("duration"))
            videos.append({
                "channel": title, "video_id": v["id"], "title": v["snippet"]["title"],
                "published": _parse_time(v["snippet"]["publishedAt"]),
                "views": int(v["statistics"].get("viewCount", 0)),
                # Shorts can run to 3 minutes; duration is the only signal the API gives.
                "format": "short" if secs is not None and secs <= 180 else "long",
            })
    return videos


def load_manual():
    path = DATA / "manual_videos.csv"
    if not path.exists():
        return []
    rows = [ln for ln in open(path, encoding="utf-8") if ln.strip() and not ln.startswith("#")]
    videos = []
    for r in csv.DictReader(rows):
        videos.append({"channel": r["channel"], "video_id": r["video_id"], "title": r["title"],
                       "published": dt.datetime.fromisoformat(r["published"]).replace(tzinfo=dt.timezone.utc),
                       "views": int(r["views"]), "format": r.get("format") or "long"})
    return videos


def youtube_outliers(cfg, ua):
    ocfg = cfg["outlier"]
    seeds = list(csv.DictReader(open(private_file("seed_channels.csv", "seed_channels.example.csv"), encoding="utf-8")))
    category = {s["handle"]: s["category"] for s in seeds}
    key, mode = None, "none"
    ycfg = cfg["youtube"]
    try:
        api = load_central_api(ycfg["service"], ycfg["profile"])
        if (api.base_url or "").rstrip("/") + "/" != YT_API:
            raise RuntimeError("profile base_url does not match the YouTube Data API")
        key, mode = api.api_key, "api"
    except Exception as e:  # message only; never the key
        print(f"  YouTube profile {ycfg['profile']} unusable ({type(e).__name__}: {e}) -> skipping the YouTube API step")

    all_videos = []
    ids = resolve_channel_ids([s["handle"] for s in seeds], ua, key) if key else {}
    for handle, cid in ids.items():
        if handle not in category:
            continue
        try:
            vids = fetch_api(cid, key, ua, ocfg["lookback_days"])
        except Exception as e:
            print(f"  ! {handle}: {type(e).__name__} {getattr(e, 'code', '')}", file=sys.stderr)
            continue
        for v in vids:
            v["handle"], v["category"] = handle, category[handle]
        all_videos.append(vids)
    manual = load_manual()
    if manual:
        mode += "+manual"
        by_chan = {}
        for v in manual:
            v["handle"], v["category"] = v["channel"], "manual"
            by_chan.setdefault(v["channel"], []).append(v)
        all_videos.extend(by_chan.values())

    scored = []
    for vids in all_videos:
        hits = [s for s in outlier_scores(vids, ocfg) if s["outlier"] >= ocfg["min_score"]]
        hits.sort(key=lambda s: -s["outlier"])
        scored.extend(hits[: ocfg["max_per_channel"]])
    scored.sort(key=lambda s: -s["outlier"])
    for s in scored:
        s["published"] = s["published"].date().isoformat()
        s["url"] = f"https://www.youtube.com/watch?v={s['video_id']}"
    return {"mode": mode, "channels_read": len(all_videos), "videos_read": sum(len(v) for v in all_videos),
            "outliers": scored}


# ---------- Wikipedia ----------

SKIP_PREFIX = ("Special:", "Wikipedia:", "File:", "Portal:", "Talk:", "Help:", "Category:", "Template:", "User:")
SKIP_EXACT = {"Main_Page", "-", "Undefined", "XXX", "Cleopatra", "Bible"}


def wikipedia_top(cfg, ua):
    wcfg = cfg["wikipedia"]
    for back in (1, 2, 3):
        day = (NOW - dt.timedelta(days=back)).date()
        url = (f"https://wikimedia.org/api/rest_v1/metrics/pageviews/top/{wcfg['project']}/all-access/"
               f"{day:%Y/%m/%d}")
        try:
            arts = json.loads(http_get(url, ua))["items"][0]["articles"]
            break
        except Exception:
            continue
    else:
        return {"day": None, "articles": []}
    keep = [a for a in arts if a["article"] not in SKIP_EXACT and not a["article"].startswith(SKIP_PREFIX)]
    return {"day": day.isoformat(),
            "articles": [{"title": a["article"].replace("_", " "), "views": a["views"], "rank": a["rank"],
                          "url": "https://en.wikipedia.org/wiki/" + urllib.parse.quote(a["article"])}
                         for a in keep[: wcfg["top_n"]]]}


# ---------- Reddit (research only: themes, never re-tell a private person's post) ----------

def reddit_top(cfg, ua):
    ns = {"a": "http://www.w3.org/2005/Atom"}
    out = []
    for i, sub in enumerate(cfg["reddit"]["subreddits"]):
        if i:
            time.sleep(6)  # unauthenticated feeds are rate limited; one request every few seconds
        # top.json now answers 403 without OAuth; the public Atom feed of the same listing still works.
        try:
            root = ET.fromstring(http_get(f"https://www.reddit.com/r/{sub}/top/.rss?t=day", ua))
        except Exception as e:
            print(f"  ! r/{sub}: {e}", file=sys.stderr)
            continue
        for rank, e in enumerate(root.findall("a:entry", ns)[: cfg["reddit"]["per_subreddit"]], 1):
            out.append({"subreddit": sub, "rank": rank, "title": e.findtext("a:title", namespaces=ns),
                        "url": e.find("a:link", ns).get("href")})
    return out


# ---------- Anniversaries (Wikipedia "On this day", 3-6 weeks ahead so there is time to produce) ----------

# Tragedies, wars and deaths are never the hook (policy safety, respectful tone); the LLM step filters the rest.
GRIM = re.compile(r"\b(defeat|captur|conquer|kill|dies|died|death|dead|murder|massacre|war|wars|battle|invad|invasion|siege|bomb|attack|"
                  r"terror|assassin|execut|genocide|earthquake|tsunami|hurricane|cyclone|flood|famine|crash|disaster|"
                  r"explo|sink|sank|fire|shoot|riot|coup|hostage|plague|epidemic|pandemic|slave|deport|persecut)", re.I)


def is_round(years):
    return years > 0 and ((years <= 100 and years % 25 == 0) or years % 50 == 0)


def _pageviews_30d(title, ua):
    end = NOW.date() - dt.timedelta(days=1)
    start = end - dt.timedelta(days=29)
    url = ("https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/en.wikipedia/all-access/user/"
           f"{urllib.parse.quote(title.replace(' ', '_'), safe='')}/daily/{start:%Y%m%d}/{end:%Y%m%d}")
    try:
        return sum(i["views"] for i in json.loads(http_get(url, ua, retries=1))["items"])
    except Exception:
        return 0


def anniversaries(cfg, ua):
    acfg = cfg["anniversaries"]
    today = NOW.date()
    cands = []
    for lead in range(acfg["lead_days_min"], acfg["lead_days_max"] + 1):
        day = today + dt.timedelta(days=lead)
        try:
            events = json.loads(http_get(
                f"https://api.wikimedia.org/feed/v1/wikipedia/en/onthisday/events/{day:%m}/{day:%d}", ua))["events"]
        except Exception as e:
            print(f"  ! on-this-day {day:%m-%d}: {e}", file=sys.stderr)
            continue
        for e in events:
            years = day.year - int(e["year"])
            if not is_round(years) or GRIM.search(e["text"]) or not e.get("pages"):
                continue
            titles = [p["titles"]["normalized"] for p in e["pages"][:5]]
            # The first linked page is often generic ("Hydrogen"); prefer one named verbatim in the event text.
            named = [t for t in titles if t in e["text"]]
            page = named[0] if named else titles[0]
            cands.append({"date": day.isoformat(), "year": int(e["year"]), "years": years, "text": e["text"],
                          "page": page, "pages": titles,
                          "url": "https://en.wikipedia.org/wiki/" + urllib.parse.quote(page.replace(" ", "_")),
                          "publish_by": day.isoformat()})
    titles = sorted({t for c in cands for t in c["pages"]})
    with ThreadPoolExecutor(4) as ex:  # polite: a handful of parallel pageview lookups
        views = dict(zip(titles, ex.map(lambda t: _pageviews_30d(t, ua), titles)))
    for c in cands:
        # median across the event's pages, so one very popular generic page can't inflate demand
        c["views_30d"] = int(statistics.median(views[t] for t in c.pop("pages")))
    keep = [c for c in cands if c["views_30d"] >= acfg["min_pageviews_30d"]]
    keep.sort(key=lambda c: -c["views_30d"])
    return {"window": [str(today + dt.timedelta(days=acfg["lead_days_min"])),
                       str(today + dt.timedelta(days=acfg["lead_days_max"]))],
            "round_events_seen": len(cands), "events": keep[: acfg["max_events"]]}


# ---------- Google Trends (daily trending searches RSS; light, research only) ----------

def _traffic(s):
    m = re.match(r"([\d.]+)\s*([KM]?)", (s or "").replace(",", ""))
    if not m:
        return 0
    return int(float(m.group(1)) * {"": 1, "K": 1_000, "M": 1_000_000}[m.group(2)])


def google_trends(cfg, ua):
    gcfg = cfg["google_trends"]
    ns = {"ht": "https://trends.google.com/trending/rss"}
    try:
        root = ET.fromstring(http_get(f"https://trends.google.com/trending/rss?geo={gcfg['geo']}", ua))
    except Exception as e:
        print(f"  ! google trends: {e}", file=sys.stderr)
        return []
    publish_by = (NOW.date() + dt.timedelta(days=gcfg["publish_within_days"])).isoformat()
    out = []
    for it in root.iter("item"):
        title = it.findtext("title") or ""
        if re.search(r"\bvs\.?\b", title, re.I):  # match-ups are sports results, never our topic
            continue
        out.append({"query": title, "traffic": _traffic(it.findtext("ht:approx_traffic", namespaces=ns)),
                    "news": [n.findtext("ht:news_item_title", namespaces=ns) for n in it.findall("ht:news_item", ns)][:2],
                    "url": "https://trends.google.com/trends/explore?geo=" + gcfg["geo"] + "&q=" + urllib.parse.quote(title),
                    "publish_by": publish_by})
    out.sort(key=lambda x: -x["traffic"])
    return out[: gcfg["max_items"]]


# ---------- dense sources (high idea density; research only, lightly fetched, robots.txt respected) ----------

_ROBOTS = {}


def robots_allowed(url, ua):
    parts = urllib.parse.urlsplit(url)
    base = f"{parts.scheme}://{parts.netloc}"
    if base not in _ROBOTS:
        rp = urllib.robotparser.RobotFileParser()
        try:
            rp.parse(http_get(base + "/robots.txt", ua, retries=1).decode("utf-8", "ignore").splitlines())
        except urllib.error.HTTPError as e:
            rp.parse([] if e.code == 404 else ["User-agent: *", "Disallow: /"])  # no robots.txt = allowed
        except Exception:
            rp.parse(["User-agent: *", "Disallow: /"])  # unreachable: stay out
        _ROBOTS[base] = rp
    return _ROBOTS[base].can_fetch(ua, url)


def _polite_get(url, ua):
    if not robots_allowed(url, ua):
        raise PermissionError(f"robots.txt disallows {url}")
    time.sleep(1)
    return http_get(url, ua)


def _clean(s):
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = re.sub(r"\{\{[^}]*\}\}|'''?|\[\[(?:[^\]|]*\|)?([^\]]*)\]\]", r"\1", s)
    return re.sub(r"\s+", " ", s).strip()


def unusual_articles(dcfg, ua, week_no):
    """A rotating sample of Wikipedia's 'Unusual articles' sections (raw wikitext via /wiki/...?action=raw)."""
    secs = dcfg["unusual_sections"]
    pick = [secs[(week_no + i) % len(secs)] for i in range(dcfg["unusual_sections_per_run"])]
    rng = random.Random(week_no)
    out = []
    for sec in pick:
        url = ("https://en.wikipedia.org/wiki/" + urllib.parse.quote(f"Wikipedia:Unusual articles/{sec}".replace(" ", "_"))
               + "?action=raw")
        try:
            raw = _polite_get(url, ua).decode("utf-8", "ignore")
        except Exception as e:
            print(f"  ! unusual articles {sec}: {e}", file=sys.stderr)
            continue
        rows = re.findall(r"'''\[\[([^\]|]+)(?:\|[^\]]*)?\]\]'''\s*\n\|\s*(?:width=\"\d+%\"\s*\|\s*)?(.+)", raw)
        for title, blurb in rng.sample(rows, min(dcfg["unusual_per_section"], len(rows))):
            out.append({"source": "dense", "feed": "Wikipedia Unusual articles", "section": sec, "title": title,
                        "blurb": _clean(blurb)[:200],
                        "url": "https://en.wikipedia.org/wiki/" + urllib.parse.quote(title.replace(" ", "_"))})
    return out


def rss_items(url, feed, ua, limit):
    try:
        root = ET.fromstring(_polite_get(url, ua))
    except Exception as e:
        print(f"  ! {feed}: {e}", file=sys.stderr)
        return []
    out = []
    for it in list(root.iter("item"))[:limit]:
        out.append({"source": "dense", "feed": feed, "title": _clean(it.findtext("title")),
                    "blurb": _clean(it.findtext("description"))[:200], "url": (it.findtext("link") or "").strip()})
    return out


def gutenberg_top(ua, limit):
    """Most-downloaded public-domain books yesterday: seeds for old-book stories and folklore."""
    url = "https://www.gutenberg.org/browse/scores/top"
    try:
        html = _polite_get(url, ua).decode("utf-8", "ignore")
    except Exception as e:
        print(f"  ! gutenberg: {e}", file=sys.stderr)
        return []
    block = html[html.find("Top 100 EBooks yesterday"):]
    block = block[: block.find("</ol>")]
    out = []
    for book_id, text in re.findall(r'<a href="/ebooks/(\d+)">([^<]+)</a>', block)[:limit]:
        title = re.sub(r"\s*\(\d+\)$", "", text).strip()
        out.append({"source": "dense", "feed": "Project Gutenberg top 100", "title": title, "blurb": "public-domain book",
                    "url": f"https://www.gutenberg.org/ebooks/{book_id}"})
    return out


def dense_sources(cfg, ua):
    dcfg = cfg["dense"]
    week_no = NOW.isocalendar()[1]
    items = unusual_articles(dcfg, ua, week_no)
    items += rss_items("https://publicdomainreview.org/rss.xml", "Public Domain Review", ua, dcfg["rss_items"])
    items += rss_items("https://www.atlasobscura.com/feeds/latest", "Atlas Obscura", ua, dcfg["rss_items"])
    items += gutenberg_top(ua, dcfg["gutenberg_items"])
    return items


# ---------- favorites (the owner's and team's taste inbox) ----------

def favorites():
    """data/favorites.md: one favourite per line or bullet; '#' lines and <!-- comments --> are ignored."""
    path = DATA / "favorites.md"
    if not path.exists():
        return []
    text = re.sub(r"<!--.*?-->", "", path.read_text(encoding="utf-8"), flags=re.S)  # drop (multi-line) comments
    out = []
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        s = re.sub(r"^[-*+]\s+|^\d+[.)]\s+", "", s)
        if s:
            url = re.search(r"https?://\S+", s)
            out.append({"source": "favorite", "text": s, "url": url.group(0).rstrip(").,") if url else None})
    return out


def collect(week=None):
    cfg = settings()
    ua = cfg["user_agent"]
    week = week or iso_week()
    print("[demand] YouTube outliers ...")
    yt = youtube_outliers(cfg, ua)
    print(f"  mode={yt['mode']} channels={yt['channels_read']} videos={yt['videos_read']} outliers={len(yt['outliers'])}")
    print("[demand] Wikipedia top ...")
    wiki = wikipedia_top(cfg, ua)
    print(f"  day={wiki['day']} articles={len(wiki['articles'])}")
    print("[demand] Reddit top of day ...")
    red = reddit_top(cfg, ua)
    print(f"  posts={len(red)}")
    print("[demand] Anniversaries 3-6 weeks out ...")
    ann = anniversaries(cfg, ua)
    print(f"  window={ann['window'][0]}..{ann['window'][1]} round={ann['round_events_seen']} kept={len(ann['events'])}")
    print("[demand] Google Trends ...")
    trends = google_trends(cfg, ua)
    print(f"  queries={len(trends)}")
    print("[demand] Dense sources (Unusual articles, Public Domain Review, Atlas Obscura, Gutenberg) ...")
    dense = dense_sources(cfg, ua)
    print(f"  items={len(dense)}")
    favs = favorites()
    print(f"[demand] Favorites inbox: {len(favs)} item(s)" + ("" if favs else " - data/favorites.md is empty"))
    signals = {"week": week, "collected_at": NOW.isoformat(), "youtube": yt, "wikipedia": wiki, "reddit": red,
               "anniversaries": ann, "google_trends": trends, "dense": dense, "favorites": favs}
    save_json(run_dir(week) / "signals.json", signals)
    return signals


if __name__ == "__main__":
    collect(sys.argv[1] if len(sys.argv) > 1 else None)
