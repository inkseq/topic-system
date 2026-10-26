"""Export data/<week>/graph.json for the private Idea Map page.

Nodes: topic, category, angle, and evidence kinds youtube / wikipedia / reddit / anniversary / trend.
Links: topic->category, topic->angle (best packages), topic->evidence, and topic<->topic "related" when two
topics share a piece of evidence. Only packaged topics are drawn; meta carries the funnel counts.
"""
import sys

from common import iso_week, load_json, run_dir, save_json, settings
from shortlist import total, weights

ANGLE = {"counter": "Counter-intuitive", "hidden": "Hidden reason", "mystery": "Mystery", "livesit": "Cast lives it",
         "extreme": "Extreme / record", "thennow": "Then vs now", "whatif": "What if", "stakes": "Stakes / countdown"}


def evidence_node(e):
    kind = e.get("source")
    if kind == "youtube":
        return {"id": "y:" + e["video_id"], "kind": "youtube", "name": e["title"], "channel": e.get("channel", ""),
                "views": e.get("views"), "outlier": e.get("outlier"), "format": e.get("format"), "url": e.get("url")}
    if kind == "wikipedia":
        return {"id": "w:" + e["title"], "kind": "wikipedia", "name": e["title"], "views": e.get("views"),
                "url": e.get("url")}
    if kind == "reddit":
        return {"id": "r:" + (e.get("url") or e["title"]), "kind": "reddit", "name": e["title"],
                "subreddit": e.get("subreddit"), "url": e.get("url")}
    if kind == "anniversary":
        return {"id": f"n:{e['date']}:{e['page']}", "kind": "anniversary", "name": f"{e['years']} years: {e['text']}",
                "date": e["date"], "views": e.get("views_30d"), "url": e.get("url")}
    if kind == "google_trends":
        return {"id": "g:" + e["query"], "kind": "trend", "name": e["query"], "traffic": e.get("traffic"),
                "publish_by": e.get("publish_by"), "url": e.get("url")}
    return None


def build(week=None):
    week = week or iso_week()
    rd = run_dir(week)
    meta_in = load_json(rd / "topics.json")
    sig = load_json(rd / "signals.json")
    short = load_json(rd / "shortlist.json")
    w = short.get("weights") or weights()
    bonus = settings()["shortlist"].get("series_bonus", 0)
    rank = {t["key"]: i + 1 for i, t in enumerate(short["topics"])}

    nodes, links, seen = [], [], set()

    def add(n):
        if n["id"] not in seen:
            seen.add(n["id"])
            nodes.append(n)

    for t in meta_in["topics"]:
        if not t.get("scores"):
            continue
        tid = "t:" + t["key"]
        best = [t["packages"][i] for i in t.get("best", []) if i < len(t["packages"])]
        add({"id": tid, "kind": "topic", "name": t["topic"], "category": t["category"], "pillar": t.get("pillar"),
             "series": bool(t.get("series")), "score": total(t, w, bonus), "rank": rank.get(t["key"]),
             "takeaway": t["takeaway"], "why": t.get("why_demand", ""), "conflict": t.get("conflict"),
             "publish_by": t.get("publish_by"),
             "scores": dict(t["scores"], demand=t["demand"], timeliness=t.get("timeliness", 3)),
             "flags": t.get("risk_flags", []), "packages": best or t["packages"][:2],
             "outline": [{"beat": b["beat"], "lines": [f'{s.get("speaker", "")}: {s.get("action", "")}'
                                                       for s in b.get("shots", [])]} for b in t.get("outline", [])],
             "facts": [{"claim": f.get("claim", ""), "source": f.get("source", ""), "url": f.get("url", "")}
                       for f in t.get("facts", [])]})
        cid = "c:" + t["category"]
        add({"id": cid, "kind": "category", "name": t["category"].replace("-", " ")})
        links.append({"source": tid, "target": cid, "kind": "category"})
        for p in best:
            aid = "a:" + p["angle"]
            add({"id": aid, "kind": "angle", "name": ANGLE.get(p["angle"], p["angle"])})
            links.append({"source": tid, "target": aid, "kind": "angle"})
        for e in t.get("evidence", []):
            n = evidence_node(e)
            if n:
                add(n)
                links.append({"source": tid, "target": n["id"], "kind": "evidence"})

    by_e = {}
    for link in links:
        if link["kind"] == "evidence":
            by_e.setdefault(link["target"], []).append(link["source"])
    for tids in by_e.values():
        for i in range(len(tids)):
            for j in range(i + 1, len(tids)):
                links.append({"source": tids[i], "target": tids[j], "kind": "related"})

    kinds = {}
    for n in nodes:
        kinds[n["kind"]] = kinds.get(n["kind"], 0) + 1
    meta = {"week": week, "counts": kinds, "positioning": meta_in.get("positioning"),
            "funnel": {"ideas": meta_in.get("ideas_total"), "in_pillar": meta_in.get("ideas_in_pillar"),
                       "packaged": sum(1 for t in meta_in["topics"] if t.get("scores")), "shortlisted": len(rank)},
            "signals": {"youtube_videos": sig["youtube"].get("videos_read"),
                        "youtube_channels": sig["youtube"].get("channels_read"),
                        "wikipedia": len(sig["wikipedia"]["articles"]), "reddit": len(sig["reddit"]),
                        "anniversaries": len(sig.get("anniversaries", {}).get("events", [])),
                        "trends": len(sig.get("google_trends", []))}}
    save_json(rd / "graph.json", {"meta": meta, "nodes": nodes, "links": links})
    print(f"[graph] wrote {rd / 'graph.json'} ({len(nodes)} nodes, {len(links)} links)")


if __name__ == "__main__":
    build(sys.argv[1] if len(sys.argv) > 1 else None)
