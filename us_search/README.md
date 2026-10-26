# US job search feed

Russ's daily job search (search only, never applies). A scheduled Claude task clones this repo every weekday morning, runs the fetcher, scores roles, and reports a shortlist ordered newest first.

## Files

- `watchlist.json`: companies to pull from, grouped by ATS (Greenhouse, Ashby, Lever). The slug is the part of the job board URL after the domain, e.g. `jobs.ashbyhq.com/horizon3ai` is `horizon3ai`. Edit this to add or drop companies.
- `ats_fetch.py`: pulls every open role from the watchlist boards, keeps enablement / customer education / senior learning roles that are US remote or near Edgewood MD, full-time, at least $95k max pay when listed, and posted within the window. Skips anything already in `seen.json`. Writes `candidates.json`, newest first.
- `mark_seen.py`: records reported job URLs in `seen.json` so the same role never shows up twice. Entries expire after 120 days.
- `seen.json`: the reported-roles log, updated by the daily task.

## Run locally

```
cd us_search
python3 ats_fetch.py --days 3
```

Needs network access to `boards-api.greenhouse.io`, `api.lever.co`, and `api.ashbyhq.com`.
