#!/usr/bin/env python3
"""
Periodic status check for a set of tracked pages, with a notification
sent via ntfy.sh the moment a tracked item's status changes.

State is kept in state.json, which this script updates and the GitHub
Actions workflow commits back to the repo after each run.
"""
import json
import os
import re
import sys
import requests

STATE_FILE = os.environ.get("STATE_FILE", "state.json")

# ntfy.sh topic - set this as a GitHub Actions secret NTFY_TOPIC, or
# hardcode a random unguessable string here
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "CHANGE-ME-TO-A-RANDOM-TOPIC")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
    ),
    "Accept-Language": "en-GB,en;q=0.9",
}

# Each entry is one organiser page to check, plus the event-name
# patterns (case-insensitive, re.search) to watch for on that page.
WATCHES = [
    {
        "url": "https://fixr.co/organiser/timepiece",
        "route": "timepiece",
        "patterns": [
            r"sketch",
            r"^saturday",
            r"^tp\s*\d{1,2}\.\d{1,2}",
            r"^wednesday",    # seen used as an alt name for the TP Wed night
        ],
    },
    {
        "url": "https://fixr.co/organiser/4playeventsltd",
        "route": "4playeventsltd",
        "patterns": [
            r"^db.*cavern",   # DB: Cavern Tuesday
            r"^paradise",     # Paradise (Cavern Friday)
            r"^logic",        # any LOGIC-prefixed Monday night
        ],
    },
]


def compile_patterns(patterns):
    return [re.compile(p, re.I) for p in patterns]


def matches(name: str, compiled_patterns) -> bool:
    return any(p.search(name) for p in compiled_patterns)


def fetch_events(url: str, route: str):
    events = []
    page = 1
    while page <= 5:  # safety cap
        page_url = url if page == 1 else f"{url}?route={route}&page={page}"
        resp = requests.get(page_url, headers=HEADERS, timeout=20)
        if resp.status_code != 200:
            print(f"[warn] {url} page {page} returned HTTP {resp.status_code}", file=sys.stderr)
            break

        m = re.search(
            r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', resp.text, re.S
        )
        if not m:
            if page == 1:
                print(f"[error] __NEXT_DATA__ not found for {url} - page structure may "
                      "have changed, or the request was blocked (e.g. Cloudflare).",
                      file=sys.stderr)
                print(resp.text[:1000], file=sys.stderr)
            break

        data = json.loads(m.group(1))
        page_data = data["props"]["pageProps"]["data"]
        page_events = page_data.get("data", [])
        if not page_events:
            break

        events.extend(page_events)

        if page_data.get("next") is None:
            break
        page += 1

    return events


def send_notification(title: str, message: str):
    if NTFY_TOPIC == "CHANGE-ME-TO-A-RANDOM-TOPIC":
        print("[error] NTFY_TOPIC not configured - skipping notification", file=sys.stderr)
        return
    try:
        requests.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=message.encode("utf-8"),
            headers={"Title": title, "Priority": "urgent", "Tags": "rotating_light"},
            timeout=10,
        )
    except requests.RequestException as e:
        print(f"[warn] failed to send notification: {e}", file=sys.stderr)


def main():
    try:
        with open(STATE_FILE) as f:
            prev_state = json.load(f)
    except FileNotFoundError:
        prev_state = {}

    new_state = {}
    changes = []

    for watch in WATCHES:
        compiled = compile_patterns(watch["patterns"])
        events = fetch_events(watch["url"], watch["route"])
        if not events:
            print(f"[warn] no events fetched from {watch['url']} this run")
            continue

        for e in events:
            name = e.get("name", "")
            if not matches(name, compiled):
                continue

            key = str(e["id"])
            # groupIsSoldOut is the reliable "actually buyable right now"
            # signal. soldOut alone is unreliable: a "not yet on sale"
            # event can have soldOut=False and even a populated
            # cheapestTicket while still not being purchasable
            # (confirmed against fixr.co/organiser/4playeventsltd - see
            # "DB: Cavern Tuesday", which showed soldOut=False,
            # cheapestTicket populated, but groupIsSoldOut=True while
            # the site displayed "On sale soon").
            available = not bool(e.get("groupIsSoldOut", True))
            share_url = e.get("shareUrl", "")
            new_state[key] = {"name": name, "available": available, "url": share_url}

            prev = prev_state.get(key)
            was_available = prev.get("available", False) if prev else False

            if available and not was_available:
                changes.append((name, share_url))

    if changes:
        for name, url in changes:
            print(f"[alert] tickets now available: {name} -> {url}")
            send_notification(
                f"Tickets live: {name}",
                url or "Check fixr.co",
            )
    else:
        print(f"[ok] checked {len(new_state)} matching event(s) across {len(WATCHES)} page(s), no change")

    with open(STATE_FILE, "w") as f:
        json.dump(new_state, f, indent=2)


if __name__ == "__main__":
    main()
