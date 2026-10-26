"""Shared helpers: paths, settings, polite HTTP, ISO week, central-registry loading."""
import datetime as dt
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "config"
DATA = ROOT / "data"  # every output and private input lives here; the folder is gitignored
SHORTLIST = DATA / "shortlist"
FEEDBACK = DATA / "feedback.csv"
for _stream in (sys.stdout, sys.stderr):  # Windows consoles default to cp1252; titles are often non-ASCII
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

REGISTRY_ROOT = Path(os.environ.get("AI_REGISTRY_ROOT", Path.home() / "Desktop" / "AI-Registry"))


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def _merge(base, over):
    for k, v in over.items():
        base[k] = _merge(base.get(k, {}), v) if isinstance(v, dict) and isinstance(base.get(k), dict) else v
    return base


def settings():
    """config/settings.json, overridden by the private data/settings.local.json when present."""
    cfg = load_json(CONFIG / "settings.json")
    local = DATA / "settings.local.json"
    return _merge(cfg, load_json(local)) if local.exists() else cfg


def private_file(name, example):
    """The real file in data/ when present, else the tracked template in config/."""
    real = DATA / name
    if real.exists():
        return real
    print(f"  note: data/{name} not found, using config/{example}")
    return CONFIG / example


def iso_week(day=None):
    day = day or dt.date.today()
    y, w, _ = day.isocalendar()
    return f"{y}-W{w:02d}"


def run_dir(week):
    d = DATA / week
    d.mkdir(parents=True, exist_ok=True)
    return d


def http_get(url, user_agent, timeout=20, retries=2, accept=None):
    """GET with a small retry; returns bytes or raises the last error."""
    headers = {"User-Agent": user_agent}
    if accept:
        headers["Accept"] = accept
    last = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            last = e
            if e.code in (400, 401, 403, 404):
                break
            if e.code == 429:  # rate limited: wait as asked, or a polite fallback
                time.sleep(min(int(e.headers.get("Retry-After") or 10), 60))
                continue
        except Exception as e:  # network hiccup
            last = e
        time.sleep(1.5 * (attempt + 1))
    raise last


def load_central_api(service, profile=None):
    """Load a profile from the central AI registry. Never print the returned object's secrets."""
    if str(REGISTRY_ROOT) not in sys.path:
        sys.path.insert(0, str(REGISTRY_ROOT))
    from api_registry import load_api  # noqa: E402

    return load_api(service, profile=profile) if profile else load_api(service)
