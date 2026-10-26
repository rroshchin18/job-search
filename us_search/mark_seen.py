#!/usr/bin/env python3
"""Record reported job URLs so future runs skip them.
Usage: python3 mark_seen.py URL [URL ...]"""
import json, sys
from datetime import date
path = "seen.json"
try:
    data = json.load(open(path))
except (OSError, ValueError):
    data = {"reported": {}}
for url in sys.argv[1:]:
    data["reported"].setdefault(url, date.today().isoformat())
# keep the file small: drop entries older than 120 days
cut = date.fromordinal(date.today().toordinal() - 120).isoformat()
data["reported"] = {u: d for u, d in data["reported"].items() if d >= cut}
json.dump(data, open(path, "w"), indent=1, sort_keys=True)
print(f"seen.json now holds {len(data['reported'])} URLs")
