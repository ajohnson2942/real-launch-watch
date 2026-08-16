"""
notifier.py
-----------
Runs the scraper, figures out which notifications are due right now given
your configured lead times, sends them via ntfy.sh (free push
notifications, no account needed), and writes:

  - data/state.json     -> internal memory of what's already been sent
  - docs/launches.json  -> public feed consumed by the dashboard webpage

This script is meant to be run on a schedule (see
.github/workflows/check-launches.yml). It is safe to run as often as you
like -- it never sends the same notification twice.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

import scraper

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("notifier")

ROOT = Path(__file__).parent
CONFIG_PATH = ROOT / "config.json"
STATE_PATH = ROOT / "data" / "state.json"
PUBLIC_FEED_PATH = ROOT / "docs" / "launches.json"

DISPLAY_TIMEZONE = ZoneInfo("America/Los_Angeles")

# Maps the trailing part of a launch site description (e.g. "...,
# California") to a short tag for notification titles. Falls back to
# whatever the last comma-separated segment says if it's not a known US
# state (e.g. "French Guiana"), so non-US sites still show something
# reasonable instead of nothing.
US_STATE_ABBREV = {
    "california": "CA",
    "florida": "FL",
    "texas": "TX",
    "virginia": "VA",
    "alaska": "AK",
    "new mexico": "NM",
}


def location_tag(site: str) -> str:
    if not site:
        return ""
    last_part = site.split(",")[-1].strip()
    return US_STATE_ABBREV.get(last_part.lower(), last_part)


def load_json(path: Path, default):
    if not path.exists():
        return default
    with path.open() as f:
        return json.load(f)


def save_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        json.dump(data, f, indent=2, default=str)


def rocket_matches_filter(rocket: str, keywords: list[str]) -> bool:
    if not keywords:
        return True
    rocket_l = rocket.lower()
    return any(kw.lower() in rocket_l for kw in keywords)


def send_ntfy(topic: str, title: str, message: str, priority: str = "default"):
    url = f"https://ntfy.sh/{topic}"
    try:
        resp = requests.post(
            url,
            data=message.encode("utf-8"),
            headers={
                "Title": title,
                "Priority": priority,
            },
            timeout=15,
        )
        resp.raise_for_status()
        log.info("Sent notification: %s", title)
        return True
    except Exception as e:
        log.error("Failed to send notification %r: %s", title, e)
        return False


def format_time_local_hint(launch_time_utc: str) -> str:
    """Return the launch time converted to DISPLAY_TIMEZONE, formatted
    plainly, e.g. 'Wed Aug 19, 7:00 PM PDT'."""
    t = dt.datetime.fromisoformat(launch_time_utc)
    local = t.astimezone(DISPLAY_TIMEZONE)
    return local.strftime("%a %b %d, %-I:%M %p %Z")


def main():
    config = load_json(CONFIG_PATH, {})

    # ntfy_topic is intentionally NOT read from config.json. config.json
    # lives in the repo and (if the repo is public) is visible to anyone,
    # which would defeat the point of it being a hard-to-guess private
    # channel name. Instead it's read from the NTFY_TOPIC environment
    # variable, which in GitHub Actions comes from an encrypted repo
    # Secret (Settings -> Secrets and variables -> Actions) that nobody
    # else can view. For local testing, you can instead set it in your
    # own shell: export NTFY_TOPIC=your-topic-name
    ntfy_topic = os.environ.get("NTFY_TOPIC") or config.get("ntfy_topic")
    if not ntfy_topic or ntfy_topic == "CHANGE-ME-TO-SOMETHING-UNIQUE":
        log.error(
            "No ntfy topic configured. Add a repo Secret named NTFY_TOPIC "
            "(Settings -> Secrets and variables -> Actions -> New repository "
            "secret) with your chosen topic name. See README Step 3."
        )
        sys.exit(1)

    lead_times_hours = sorted(set(config.get("lead_times_hours", [24])), reverse=True)
    rocket_filter = config.get("rocket_keywords", [])  # e.g. ["Falcon 9", "Falcon Heavy", "Starship"]
    notify_on_new = config.get("notify_on_new_launch_added", True)
    notify_on_time_change = config.get("notify_on_time_change", True)

    state = load_json(STATE_PATH, {"launches": {}})
    state.setdefault("launches", {})

    now = dt.datetime.now(dt.timezone.utc)

    try:
        html = scraper.fetch_html()
    except Exception as e:
        log.error("Could not fetch schedule page: %s", e)
        sys.exit(1)

    launches = scraper.parse_schedule(html, now=now)
    if not launches:
        log.warning(
            "Parsed 0 launches -- the site's layout may have changed and "
            "the scraper needs updating. No notifications sent this run."
        )

    filtered = [l for l in launches if rocket_matches_filter(l.rocket, rocket_filter)]
    log.info("Parsed %d launches total, %d match your rocket filter", len(launches), len(filtered))

    public_feed = []

    for launch in filtered:
        uid = launch.uid
        prev = state["launches"].get(uid, {})
        prev_notified = set(prev.get("notified_lead_hours", []))
        prev_launch_time = prev.get("launch_time_utc")

        is_new = uid not in state["launches"]
        time_changed = (
            prev_launch_time
            and launch.launch_time_utc
            and prev_launch_time != launch.launch_time_utc
        )

        if is_new and notify_on_new and launch.launch_time_utc:
            loc = location_tag(launch.site)
            send_ntfy(
                ntfy_topic,
                title=f"New launch on schedule: {launch.mission}" + (f" ({loc})" if loc else ""),
                message=(
                    f"{launch.rocket} • {launch.mission}\n"
                    f"{format_time_local_hint(launch.launch_time_utc)}\n"
                    f"Site: {launch.site}"
                ),
            )

        if time_changed and notify_on_time_change:
            loc = location_tag(launch.site)
            send_ntfy(
                ntfy_topic,
                title=f"Launch time changed: {launch.mission}" + (f" ({loc})" if loc else ""),
                message=(
                    f"{launch.rocket} • {launch.mission}\n"
                    f"New time: {format_time_local_hint(launch.launch_time_utc)}\n"
                    f"(was: {format_time_local_hint(prev_launch_time)})"
                ),
            )
            # A time change resets which lead-time reminders are still valid,
            # so re-arm any that are still in the future relative to the new time.
            prev_notified = set()

        notified_now = set(prev_notified)
        if launch.launch_time_utc:
            launch_dt = dt.datetime.fromisoformat(launch.launch_time_utc)
            if launch_dt > now:
                for lead_h in lead_times_hours:
                    if lead_h in prev_notified:
                        continue
                    notify_at = launch_dt - dt.timedelta(hours=lead_h)
                    if notify_at <= now:
                        lead_desc = (
                            f"{lead_h} hours" if lead_h < 48 else f"{lead_h // 24} days"
                        )
                        loc = location_tag(launch.site)
                        send_ntfy(
                            ntfy_topic,
                            title=f"Launching in ~{lead_desc}: {launch.mission}" + (f" ({loc})" if loc else ""),
                            message=(
                                f"{launch.rocket} • {launch.mission}\n"
                                f"{format_time_local_hint(launch.launch_time_utc)}\n"
                                f"Site: {launch.site}"
                            ),
                            priority="high" if lead_h <= 3 else "default",
                        )
                        notified_now.add(lead_h)

        state["launches"][uid] = {
            "rocket": launch.rocket,
            "mission": launch.mission,
            "launch_time_utc": launch.launch_time_utc,
            "date_text": launch.date_text,
            "notified_lead_hours": sorted(notified_now),
            "last_seen_utc": now.isoformat(),
        }

        public_feed.append(
            {
                **launch.to_dict(),
                "notified_lead_hours": sorted(notified_now),
            }
        )

    # Prune launches that are no longer on the schedule at all AND are
    # clearly in the past (keeps state.json from growing forever), but
    # keep anything from the last 3 days in case of re-parsing quirks.
    cutoff = now - dt.timedelta(days=3)
    still_present = {l.uid for l in filtered}
    pruned = {}
    for uid, rec in state["launches"].items():
        if uid in still_present:
            pruned[uid] = rec
            continue
        lt = rec.get("launch_time_utc")
        if lt and dt.datetime.fromisoformat(lt) > cutoff:
            pruned[uid] = rec
    state["launches"] = pruned

    save_json(STATE_PATH, state)

    public_feed.sort(key=lambda l: l["launch_time_utc"] or "9999")
    save_json(
        PUBLIC_FEED_PATH,
        {
            "generated_at_utc": now.isoformat(),
            "rocket_keywords_filter": rocket_filter,
            "launches": public_feed,
        },
    )

    log.info("Done.")


if __name__ == "__main__":
    main()
