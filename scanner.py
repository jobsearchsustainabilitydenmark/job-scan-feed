from __future__ import annotations

import json, re, time, unicodedata
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode
from zoneinfo import ZoneInfo
import requests

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
CONFIG = ROOT / "config.json"
SEEN = DATA / "seen_jobs.json"
LATEST = DATA / "latest.json"
HISTORY = DATA / "history"
JOBINDEX = "https://www.jobindex.dk"

@dataclass
class Job:
    source: str
    source_id: str
    title: str
    company: str | None
    location: str | None
    posted_at: str | None
    deadline: str | None
    url: str
    search_queries: list[str]
    first_seen_at: str | None = None
    last_seen_at: str | None = None

def load(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))

def save(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

def norm(s: str | None):
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).casefold()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", s).split())

def source_key(j: Job):
    return f"{j.source}:{j.source_id}"

def cross_key(j: Job):
    return "|".join([norm(j.title), norm(j.company), norm(j.location)])

def extract_stash(html: str):
    marker = "var Stash = "
    pos = html.find(marker)
    if pos < 0:
        raise ValueError("Jobindex Stash not found")
    start = pos + len(marker)
    depth = 0
    quoted = False
    escaped = False
    for i in range(start, len(html)):
        c = html[i]
        if quoted:
            if escaped:
                escaped = False
            elif c == "\\":
                escaped = True
            elif c == '"':
                quoted = False
        else:
            if c == '"':
                quoted = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return json.loads(html[start:i + 1])
    raise ValueError("Incomplete Jobindex Stash")

def find_search_response(node):
    if isinstance(node, dict):
        sr = node.get("searchResponse")
        if isinstance(sr, dict) and isinstance(sr.get("results"), list):
            return sr
        for value in node.values():
            found = find_search_response(value)
            if found:
                return found
    elif isinstance(node, list):
        for value in node:
            found = find_search_response(value)
            if found:
                return found
    return None

def parse_jobindex(html: str, query: str):
    sr = find_search_response(extract_stash(html))
    if not sr:
        raise ValueError("Jobindex searchResponse not found")
    jobs = []
    for r in sr.get("results", []):
        jid = str(r.get("tid") or "").strip()
        if not jid:
            continue
        company = r.get("company", {}).get("name") if isinstance(r.get("company"), dict) else None
        company = company or r.get("companytext")
        deadline = "ASAP" if r.get("apply_deadline_asap") else (r.get("apply_deadline") or r.get("lastdate"))
        if isinstance(deadline, str) and deadline != "ASAP":
            deadline = deadline[:10]
        jobs.append(Job(
            "jobindex", jid, str(r.get("headline") or "").strip(), company, r.get("area"),
            r.get("firstdate"), deadline, f"{JOBINDEX}/jobannonce/{jid}", [query]
        ))
    return int(sr.get("hitcount") or len(jobs)), jobs

def fetch_jobindex(cfg):
    session = requests.Session()
    session.headers["User-Agent"] = "Mozilla/5.0 personal-job-scanner/1.0"
    combined = {}
    errors = []
    pages_ok = raw = 0
    for q in cfg["queries"]:
        for page in range(1, int(cfg.get("pages_per_query", 2)) + 1):
            url = JOBINDEX + "/jobsoegning?" + urlencode({
                "q": q,
                "page": page,
                "jobage": int(cfg.get("jobage_days", 3)),
                "sort": "date",
            })
            try:
                response = session.get(url, timeout=30)
                response.raise_for_status()
                total, jobs = parse_jobindex(response.text, q)
                pages_ok += 1
                raw += len(jobs)
                for j in jobs:
                    key = source_key(j)
                    if key in combined:
                        combined[key].search_queries = sorted(set(combined[key].search_queries + [q]))
                    else:
                        combined[key] = j
                if page * 20 >= total:
                    break
            except Exception as e:
                errors.append(f"{q} p{page}: {type(e).__name__}: {e}")
            time.sleep(0.35)
    status = "ok" if pages_ok and not errors else ("partial" if pages_ok else "failed")
    return list(combined.values()), {
        "status": status,
        "pages_ok": pages_ok,
        "raw_results": raw,
        "unique_results": len(combined),
        "errors": errors[:10],
    }

def iso(value):
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:
            pass
    text = str(value).strip()
    return None if text.lower() in {"", "nan", "nat", "none"} else text

def fetch_linkedin(cfg):
    try:
        from jobspy import scrape_jobs
    except Exception as e:
        return [], {"status": "failed", "errors": [f"JobSpy import: {type(e).__name__}: {e}"]}
    combined = {}
    errors = []
    ok = raw = 0
    for q in cfg["queries"]:
        try:
            df = scrape_jobs(
                site_name=["linkedin"],
                search_term=q,
                location=cfg.get("location", "Denmark"),
                results_wanted=int(cfg.get("results_wanted_per_query", 25)),
                hours_old=int(cfg.get("hours_old", 30)),
                linkedin_fetch_description=False,
                verbose=0,
            )
            rows = df.to_dict(orient="records") if df is not None else []
            ok += 1
            raw += len(rows)
            for r in rows:
                url = str(r.get("job_url") or r.get("job_url_direct") or "").strip()
                jid = str(r.get("id") or "").strip()
                match = re.search(r"/jobs/view/(\d+)", url)
                if match:
                    jid = match.group(1)
                if not jid:
                    jid = url or "|".join([norm(r.get("title")), norm(r.get("company")), norm(r.get("location"))])
                if not jid:
                    continue
                j = Job(
                    "linkedin", jid, str(r.get("title") or "").strip(), iso(r.get("company")),
                    iso(r.get("location")), iso(r.get("date_posted")), None, url, [q]
                )
                key = source_key(j)
                if key in combined:
                    combined[key].search_queries = sorted(set(combined[key].search_queries + [q]))
                else:
                    combined[key] = j
        except Exception as e:
            errors.append(f"{q}: {type(e).__name__}: {e}")
        time.sleep(0.4)
    status = "ok" if ok and not errors else ("partial" if ok else "failed")
    return list(combined.values()), {
        "status": status,
        "queries_ok": ok,
        "raw_results": raw,
        "unique_results": len(combined),
        "errors": errors[:10],
    }

def main():
    cfg = load(CONFIG, {})
    now = datetime.now(ZoneInfo(cfg.get("timezone", "Europe/Copenhagen")))
    now_s = now.isoformat(timespec="seconds")
    seen = load(SEEN, {"jobs": {}}).get("jobs", {})
    cutoff = now - timedelta(days=int(cfg.get("history", {}).get("keep_days", 180)))
    seen = {
        k: v for k, v in seen.items()
        if not v.get("last_seen_at") or datetime.fromisoformat(v["last_seen_at"]) >= cutoff
    }

    all_jobs = []
    sources = {}
    for name, fetcher in [("jobindex", fetch_jobindex), ("linkedin", fetch_linkedin)]:
        source_cfg = cfg.get(name, {})
        if source_cfg.get("enabled", True):
            jobs, meta = fetcher(source_cfg)
            all_jobs += jobs
            sources[name] = meta
        else:
            sources[name] = {"status": "disabled"}

    new_jobs = []
    emitted = set()
    for j in all_jobs:
        key = source_key(j)
        old = seen.get(key)
        if old:
            j.first_seen_at = old.get("first_seen_at")
            j.last_seen_at = now_s
            old.update({
                "last_seen_at": now_s,
                "title": j.title,
                "company": j.company,
                "location": j.location,
                "url": j.url,
                "posted_at": j.posted_at,
            })
        else:
            j.first_seen_at = j.last_seen_at = now_s
            seen[key] = {
                "source": j.source,
                "source_id": j.source_id,
                "title": j.title,
                "company": j.company,
                "location": j.location,
                "url": j.url,
                "posted_at": j.posted_at,
                "first_seen_at": now_s,
                "last_seen_at": now_s,
            }
            ckey = cross_key(j)
            if ckey not in emitted:
                new_jobs.append(j)
                emitted.add(ckey)

    expected = set(cfg.get("controls", {}).get("expected_linkedin_job_ids", []))
    found = {j.source_id for j in all_jobs if j.source == "linkedin"}
    latest = {
        "generated_at": now_s,
        "scan_window_hours": int(cfg.get("linkedin", {}).get("hours_old", 30)),
        "sources": sources,
        "counts": {
            "raw_unique_jobs_seen_this_run": len(all_jobs),
            "new_jobs": len(new_jobs),
        },
        "controls": {
            "expected_linkedin_job_ids": sorted(expected),
            "found_linkedin_job_ids": sorted(expected & found),
            "missing_linkedin_job_ids": sorted(expected - found),
        },
        "new_jobs": [
            asdict(j) for j in sorted(new_jobs, key=lambda x: (x.posted_at or "", x.title.casefold()), reverse=True)
        ],
    }
    save(SEEN, {"jobs": seen})
    save(LATEST, latest)
    save(HISTORY / f"{now.date().isoformat()}.json", latest)

    print(json.dumps(latest["counts"], ensure_ascii=False))
    for name, meta in sources.items():
        print(f"{name}: {meta.get('status')} ({meta.get('unique_results', '-')})")
    if latest["controls"]["missing_linkedin_job_ids"]:
        print("CONTROL WARNING:", latest["controls"]["missing_linkedin_job_ids"])

if __name__ == "__main__":
    main()
