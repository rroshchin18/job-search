#!/usr/bin/env python3
"""Pull open roles straight from company ATS boards (Greenhouse, Lever, Ashby),
keep enablement / customer education / senior learning roles, and write candidates.json.

Usage: python3 ats_fetch.py [--days 3] [--watchlist watchlist.json] [--out candidates.json]
Needs network access to boards-api.greenhouse.io, api.lever.co, api.ashbyhq.com.
"""
import argparse, json, re, sys, html
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
import urllib.request, urllib.error

UA = {"User-Agent": "job-search-watchlist/1.0"}

# Title must match one of these (case-insensitive, whole-ish words)
TITLE_INCLUDE = re.compile(
    r"enablement|customer education|education|academy|learning|training|readiness|"
    r"curriculum|instructional|onboarding|knowledge|adoption|certification",
    re.I,
)
# Clearly junior or off-target titles
TITLE_EXCLUDE = re.compile(
    r"\b(intern|internship|coordinator|associate|assistant|junior|jr\.?|entry|"
    r"specialist i\b|machine learning|deep learning|ml engineer|data scientist|"
    r"software engineer|(backend|frontend|full[- ]?stack|platform|data|ml|security|site reliability) engineer|"
    r"engineer,? (ml|machine)|recruiter|sales development|account executive|account manager|solutions? architect|personal train\w*)\b",
    re.I,
)
REMOTE_US = re.compile(r"remote|united states|\bus\b|usa|u\.s\.|anywhere|north america", re.I)
NEAR_HOME = re.compile(r"baltimore|columbia,? md|maryland|washington,? d\.?c|arlington|"
                       r"reston|mclean|tysons|wilmington|annapolis|bethesda|rockville", re.I)
NOT_US = re.compile(r"\b(uk|united kingdom|london|india|bangalore|bengaluru|germany|berlin|"
                    r"ireland|dublin|canada only|australia|singapore|japan|emea|apac|latam|"
                    r"brazil|mexico|poland|netherlands|france|spain|israel|tel aviv|"
                    r"united arab emirates|uae|dubai|gcc|middle east|saudi|qatar|philippines|korea|china|canada|montreal|toronto|vancouver)\b", re.I)
CONTRACT = re.compile(r"contract|temporary|temp\b|fixed[- ]term|freelance|part[- ]time", re.I)
PAY_FLOOR = 95000
# Roles that require (or require the ability to obtain) a security clearance.
# Public Trust is a background check, not a clearance, so it is not excluded.
CLEARANCE = re.compile(
    r"\b(ts/sci|top secret|polygraph)\b|"
    r"\b(secret|security|government|dod|federal|active)\s+clearance\b|"
    r"\bclearance\s+(is\s+)?(required|needed|eligib\w*)\b|"
    r"\b(obtain|hold|maintain)\w*\s+(an?\s+)?(active\s+)?(secret|clearance)\b", re.I)


def get(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def strip_html(s):
    s = html.unescape(s or "")
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def parse_dt(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):  # Lever: ms epoch
        return datetime.fromtimestamp(v / 1000, tz=timezone.utc)
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None


def greenhouse(slug):
    data = get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true&pay_transparency=true")
    out = []
    for j in data.get("jobs", []):
        pay = None
        for p in j.get("pay_input_ranges") or []:
            lo, hi = p.get("min_cents"), p.get("max_cents")
            if hi:
                pay = (lo / 100 if lo else None, hi / 100)
        out.append(dict(
            company=slug, ats="greenhouse", title=j.get("title", ""),
            location=(j.get("location") or {}).get("name", ""),
            url=j.get("absolute_url"), posted=parse_dt(j.get("first_published") or j.get("updated_at")),
            pay=pay, employment="", description=strip_html(j.get("content", "")),
        ))
    return out


def lever(slug):
    data = get(f"https://api.lever.co/v0/postings/{slug}?mode=json")
    out = []
    for j in data:
        cats = j.get("categories") or {}
        sr = j.get("salaryRange") or {}
        pay = (sr.get("min"), sr.get("max")) if sr.get("max") else None
        loc = cats.get("location", "") or ""
        if j.get("workplaceType") == "remote":
            loc = f"Remote ({loc})" if loc else "Remote"
        out.append(dict(
            company=slug, ats="lever", title=j.get("text", ""), location=loc,
            url=j.get("hostedUrl"), posted=parse_dt(j.get("createdAt")), pay=pay,
            employment=cats.get("commitment", "") or "",
            description=j.get("descriptionPlain") or strip_html(j.get("description", "")),
        ))
    return out


def ashby(slug):
    data = get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true")
    out = []
    for j in data.get("jobs", []):
        if j.get("isListed") is False:
            continue
        pay = None
        comp = j.get("compensation") or {}
        for tier in comp.get("summaryComponents") or []:
            if tier.get("compensationType") == "Salary" and tier.get("maxValue"):
                pay = (tier.get("minValue"), tier.get("maxValue"))
        loc = j.get("location", "") or ""
        if j.get("isRemote"):
            loc = f"Remote ({loc})" if loc else "Remote"
        out.append(dict(
            company=slug, ats="ashby", title=j.get("title", ""), location=loc,
            url=j.get("jobUrl"), posted=parse_dt(j.get("publishedAt")), pay=pay,
            employment=j.get("employmentType", "") or "",
            description=j.get("descriptionPlain") or strip_html(j.get("descriptionHtml", "")),
        ))
    return out


def smartrecruiters(slug):
    base = f"https://api.smartrecruiters.com/v1/companies/{slug}/postings"
    postings, offset = [], 0
    while True:
        page = get(f"{base}?limit=100&offset={offset}")
        postings += page.get("content", [])
        offset += 100
        if offset >= page.get("totalFound", 0) or offset >= 3000:
            break
    out = []
    for p in postings:
        title = p.get("name", "").strip()
        locd = p.get("location") or {}
        loc = locd.get("fullLocation") or ", ".join(
            x for x in (locd.get("city"), locd.get("region"), locd.get("country")) if x)
        if locd.get("remote"):
            loc = f"Remote ({loc})" if loc else "Remote"
        else:  # onsite or hybrid: only useful if near home
            loc = f"ONSITE: {loc}"
        job = dict(company=slug, ats="smartrecruiters", title=title, location=loc,
            url=f"https://jobs.smartrecruiters.com/{slug}/{p.get('id')}",
            posted=parse_dt(p.get("releasedDate")), pay=None,
            employment=(p.get("typeOfEmployment") or {}).get("label", "") or "",
            description="")
        # Only pull the full ad for titles that could pass, to keep requests low.
        if TITLE_INCLUDE.search(title) and not TITLE_EXCLUDE.search(title):
            try:
                d = get(f"{base}/{p.get('id')}")
                job["url"] = d.get("postingUrl") or job["url"]
                secs = (d.get("jobAd") or {}).get("sections") or {}
                job["description"] = " ".join(
                    strip_html((secs.get(k) or {}).get("text", ""))
                    for k in ("jobDescription", "qualifications", "additionalInformation"))
            except Exception:  # noqa: BLE001
                pass
        out.append(job)
    return out


FETCHERS = {"greenhouse": greenhouse, "lever": lever, "ashby": ashby,
            "smartrecruiters": smartrecruiters}


def keep(job, cutoff):
    """Return (keep: bool, reason: str)."""
    t, loc = job["title"], job["location"]
    if not TITLE_INCLUDE.search(t):
        return False, "title"
    if TITLE_EXCLUDE.search(t):
        return False, "junior/off-target title"
    if CONTRACT.search(job["employment"]) or CONTRACT.search(t):
        return False, "not full-time"
    if NOT_US.search(loc) and not REMOTE_US.search(loc.replace("Remote", "")):
        return False, "outside US"
    if loc.startswith("ONSITE:"):
        if not NEAR_HOME.search(loc):
            return False, "onsite, not near home"
    elif not (REMOTE_US.search(loc) or NEAR_HOME.search(loc)):
        return False, "location"
    elif ("," in loc and not re.search(r"remote", loc, re.I)
          and not NEAR_HOME.search(loc)):
        # "City, State, USA" with no remote marker is an onsite role elsewhere
        return False, "onsite, not near home"
    if CLEARANCE.search(job.get("description", "")) or CLEARANCE.search(t):
        return False, "security clearance required"
    if job["pay"] and job["pay"][1] and job["pay"][1] < PAY_FLOOR:
        return False, "pay under floor"
    if job["posted"] and job["posted"] < cutoff:
        return False, "older than window"
    return True, ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--watchlist", default="watchlist.json")
    ap.add_argument("--out", default="candidates.json")
    ap.add_argument("--seen", default="seen.json", help="URLs already reported; skipped")
    args = ap.parse_args()

    wl = json.load(open(args.watchlist))
    boards = [(ats, slug) for ats, slugs in wl.items() if ats in FETCHERS for slug in slugs]
    cutoff = datetime.now(timezone.utc) - timedelta(days=args.days)

    def run(b):
        ats, slug = b
        try:
            return b, FETCHERS[ats](slug), None
        except urllib.error.HTTPError as e:
            return b, [], f"HTTP {e.code}"
        except Exception as e:  # noqa: BLE001
            return b, [], type(e).__name__ + ": " + str(e)[:120]

    with ThreadPoolExecutor(max_workers=12) as ex:
        results = list(ex.map(run, boards))

    try:
        seen = set(json.load(open(args.seen)).get("reported", {}))
    except (OSError, ValueError):
        seen = set()
    candidates, errors, scanned, skipped_seen = [], [], 0, 0
    for (ats, slug), jobs, err in results:
        if err:
            errors.append(f"{ats}:{slug} -> {err}")
            continue
        scanned += len(jobs)
        for j in jobs:
            ok, _ = keep(j, cutoff)
            if ok and j["url"] in seen:
                skipped_seen += 1
                continue
            if ok:
                j = dict(j)
                j["posted"] = j["posted"].isoformat() if j["posted"] else None
                j["description"] = j["description"][:6000]
                candidates.append(j)

    candidates.sort(key=lambda j: j["posted"] or "", reverse=True)
    json.dump({"generated": datetime.now(timezone.utc).isoformat(), "window_days": args.days,
               "boards": len(boards), "jobs_scanned": scanned, "skipped_already_reported": skipped_seen, "board_errors": errors,
               "candidates": candidates}, open(args.out, "w"), indent=1)
    print(f"boards={len(boards)} scanned={scanned} new_candidates={len(candidates)} already_reported={skipped_seen} errors={len(errors)}")
    for e in errors:
        print("  board error:", e, file=sys.stderr)


if __name__ == "__main__":
    main()
