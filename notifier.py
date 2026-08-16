"""
notifier.py
-----------
Refreshes the launch schedule, sends California/Florida notifications through
ntfy, remembers what has already been sent, and publishes docs/launches.json
for the dashboard.

NOTIFICATION TOPICS
-------------------
The app uses one topic per launch coast:

    <NTFY_TOPIC>-CA   California launches
    <NTFY_TOPIC>-FL   Florida launches

A person who wants both simply subscribes to both topics. Each location topic
receives the normal reminders plus new-launch and schedule-change alerts.
That means a delay notification always reaches the same people who asked for
that location's launch reminders.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

import scraper

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

log = logging.getLogger("notifier")

ROOT = Path(__file__).parent
CONFIG_PATH = ROOT / "config.json"
STATE_PATH = ROOT / "data" / "state.json"
PUBLIC_FEED_PATH = ROOT / "docs" / "launches.json"

PACIFIC = ZoneInfo("America/Los_Angeles")
EASTERN = ZoneInfo("America/New_York")

OFFERED_LEAD_TIMES_HOURS = [24, 3]


def load_json(path: Path, default):
    if not path.exists():
        return default

    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def save_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8") as handle:
        json.dump(
            data,
            handle,
            indent=2,
            default=str,
        )


def rocket_matches_filter(
    rocket: str,
    keywords: list[str],
) -> bool:
    if not keywords:
        return True

    rocket_l = rocket.lower()

    return any(
        keyword.lower() in rocket_l
        for keyword in keywords
    )


def topic_for_location(
    base_topic: str,
    location_code: str,
) -> str:
    return f"{base_topic}-{location_code.upper()}"


def send_ntfy(
    topic: str,
    title: str,
    message: str,
    priority: str = "default",
) -> bool:
    try:
        response = requests.post(
            f"https://ntfy.sh/{topic}",
            data=message.encode("utf-8"),
            headers={
                "Title": title,
                "Priority": priority,
            },
            timeout=15,
        )

        response.raise_for_status()

        log.info(
            "Sent notification to %s: %s",
            topic,
            title,
        )

        return True

    except Exception as exc:
        log.error(
            "Failed to send notification to %s (%r): %s",
            topic,
            title,
            exc,
        )

        return False


def timezone_for_launch(
    launch: scraper.Launch,
) -> ZoneInfo:
    return (
        PACIFIC
        if launch.location_code == "CA"
        else EASTERN
    )


def format_launch_time(
    launch_time_utc: str,
    location_code: str | None,
) -> str:
    launch_dt = dt.datetime.fromisoformat(
        launch_time_utc
    )

    zone = (
        PACIFIC
        if location_code == "CA"
        else EASTERN
        if location_code == "FL"
        else PACIFIC
    )

    local = launch_dt.astimezone(zone)

    return local.strftime(
        "%A, %B %-d at %-I:%M %p %Z"
    )


def describe_when(
    launch: scraper.Launch,
) -> str:
    if launch.launch_time_utc:
        return format_launch_time(
            launch.launch_time_utc,
            launch.location_code,
        )

    if launch.scheduled_date:
        date_value = dt.date.fromisoformat(
            launch.scheduled_date
        )

        return (
            f"{date_value.strftime('%A, %B')} "
            f"{date_value.day} "
            f"(exact time TBD)"
        )

    return (
        f"{launch.date_text} "
        f"(exact time TBD)"
    )


def time_change_message(
    launch: scraper.Launch,
    previous_time: str | None,
) -> tuple[str, str]:
    if launch.launch_time_utc:
        new_time = format_launch_time(
            launch.launch_time_utc,
            launch.location_code,
        )

        title = (
            f"Launch delayed/changed: "
            f"{launch.mission} "
            f"({launch.location_code})"
        )

        if previous_time:
            old_time = format_launch_time(
                previous_time,
                launch.location_code,
            )

            body = (
                f"{launch.rocket} • {launch.mission}\n"
                f"Updated launch time: {new_time}\n"
                f"Previously: {old_time}\n"
                f"Site: {launch.site}"
            )

        else:
            body = (
                f"{launch.rocket} • {launch.mission}\n"
                f"A precise launch time is now available: "
                f"{new_time}\n"
                f"Site: {launch.site}"
            )

    else:
        title = (
            f"Launch schedule changed: "
            f"{launch.mission} "
            f"({launch.location_code})"
        )

        old = (
            format_launch_time(
                previous_time,
                launch.location_code,
            )
            if previous_time
            else "previous schedule"
        )

        body = (
            f"{launch.rocket} • {launch.mission}\n"
            f"The previous time ({old}) is no longer current.\n"
            f"New schedule: {describe_when(launch)}\n"
            f"Site: {launch.site}"
        )

    return title, body


def main():
    config = load_json(
        CONFIG_PATH,
        {},
    )

    base_topic = (
        os.environ.get("NTFY_TOPIC")
        or config.get("ntfy_topic")
    )

    if (
        not base_topic
        or base_topic
        == "CHANGE-ME-TO-SOMETHING-UNIQUE"
    ):
        log.error(
            "No ntfy topic configured. "
            "Add a repo Secret named NTFY_TOPIC "
            "with the base topic used by the dashboard."
        )
        sys.exit(1)

    rocket_filter = config.get(
        "rocket_keywords",
        [],
    )

    notify_on_new = config.get(
        "notify_on_new_launch_added",
        True,
    )

    notify_on_time_change = config.get(
        "notify_on_time_change",
        True,
    )

    state = load_json(
        STATE_PATH,
        {"launches": {}},
    )

    state.setdefault(
        "launches",
        {},
    )

    now = dt.datetime.now(
        dt.timezone.utc
    )

    try:
        launches = scraper.parse_schedule(
            scraper.fetch_html(),
            now=now,
        )

    except Exception as exc:
        log.error(
            "Could not fetch schedule page: %s",
            exc,
        )
        sys.exit(1)

    if not launches:
        log.warning(
            "Parsed 0 launches -- "
            "the source layout may have changed. "
            "No notifications sent this run."
        )

    filtered = [
        launch
        for launch in launches
        if rocket_matches_filter(
            launch.rocket,
            rocket_filter,
        )
    ]

    log.info(
        "Parsed %d launches total, "
        "%d match the rocket filter",
        len(launches),
        len(filtered),
    )

    public_feed = []

    for launch in filtered:
        uid = launch.uid

        previous = state["launches"].get(
            uid,
            {},
        )

        is_new = (
            uid not in state["launches"]
        )

        previous_time = previous.get(
            "launch_time_utc"
        )

        previous_date = previous.get(
            "scheduled_date"
        )

        previous_location = previous.get(
            "location_code"
        )

        notified_leads = set(
            previous.get(
                "notified_lead_hours",
                [],
            )
        )

        time_changed = (
            not is_new
            and previous_time
            != launch.launch_time_utc
        )

        date_changed = (
            not is_new
            and "scheduled_date" in previous
            and previous_date
            != launch.scheduled_date
        )

        time_or_date_changed = (
            time_changed
            or date_changed
        )

        location_changed = (
            not is_new
            and previous_location
            != launch.location_code
        )

        # Only CA and FL are push-notification locations.
        # Other SpaceX launches can still appear on the
        # dashboard/feed without sending a push.
        location_code = (
            launch.location_code
        )

        topic = (
            topic_for_location(
                base_topic,
                location_code,
            )
            if location_code in {"CA", "FL"}
            else None
        )

        if (
            is_new
            and notify_on_new
            and topic
        ):
            send_ntfy(
                topic,
                title=(
                    f"New launch scheduled: "
                    f"{launch.mission} "
                    f"({location_code})"
                ),
                message=(
                    f"{launch.rocket} • "
                    f"{launch.mission}\n"
                    f"Launch: "
                    f"{describe_when(launch)}\n"
                    f"Site: {launch.site}"
                ),
            )

        if (
            time_or_date_changed
            and notify_on_time_change
            and topic
        ):
            title, message = (
                time_change_message(
                    launch,
                    previous_time,
                )
            )

            send_ntfy(
                topic,
                title=title,
                message=message,
                priority="high",
            )

            # Re-arm reminders against the new schedule.
            # If a launch moves later, a fresh
            # 24-hour/3-hour reminder can be sent
            # at the correct new time.
            notified_leads = set()

        if location_changed:
            notified_leads = set()

        notified_now = set(
            notified_leads
        )

        if (
            topic
            and launch.launch_time_utc
        ):
            launch_dt = dt.datetime.fromisoformat(
                launch.launch_time_utc
            )

            if launch_dt > now:
                for lead_hours in (
                    OFFERED_LEAD_TIMES_HOURS
                ):
                    if (
                        lead_hours
                        in notified_leads
                    ):
                        continue

                    notify_at = (
                        launch_dt
                        - dt.timedelta(
                            hours=lead_hours
                        )
                    )

                    if notify_at <= now:
                        exact_time = (
                            format_launch_time(
                                launch.launch_time_utc,
                                location_code,
                            )
                        )

                        label = (
                            "3 hours"
                            if lead_hours == 3
                            else "24 hours"
                        )

                        send_ntfy(
                            topic,
                            title=(
                                f"Launch in {label}: "
                                f"{launch.mission} "
                                f"({location_code})"
                            ),
                            message=(
                                f"{launch.rocket} • "
                                f"{launch.mission}\n"
                                f"Launch in {label} "
                                f"at {exact_time}.\n"
                                f"Site: {launch.site}"
                            ),
                            priority=(
                                "high"
                                if lead_hours == 3
                                else "default"
                            ),
                        )

                        notified_now.add(
                            lead_hours
                        )

        state["launches"][uid] = {
            "rocket": launch.rocket,
            "mission": launch.mission,
            "launch_time_utc": (
                launch.launch_time_utc
            ),
            "scheduled_date": (
                launch.scheduled_date
            ),
            "location_code": (
                launch.location_code
            ),
            "date_text": launch.date_text,
            "notified_lead_hours": (
                sorted(notified_now)
            ),
            "last_seen_utc": (
                now.isoformat()
            ),
        }

        public_feed.append(
            {
                **launch.to_dict(),
                "notified_lead_hours": (
                    sorted(notified_now)
                ),
            }
        )

    cutoff = (
        now
        - dt.timedelta(days=3)
    )

    still_present = {
        launch.uid
        for launch in filtered
    }

    pruned = {}

    for (
        uid,
        record,
    ) in state["launches"].items():
        if uid in still_present:
            pruned[uid] = record
            continue

        launch_time = record.get(
            "launch_time_utc"
        )

        if (
            launch_time
            and dt.datetime.fromisoformat(
                launch_time
            )
            > cutoff
        ):
            pruned[uid] = record

    state["launches"] = pruned

    save_json(
        STATE_PATH,
        state,
    )

    public_feed.sort(
        key=lambda launch: (
            launch["launch_time_utc"]
            or launch["scheduled_date"]
            or "9999"
        )
    )

    save_json(
        PUBLIC_FEED_PATH,
        {
            "generated_at_utc": (
                now.isoformat()
            ),
            "rocket_keywords_filter": (
                rocket_filter
            ),
            "notification_locations": [
                "CA",
                "FL",
            ],
            "notification_lead_times_hours": (
                OFFERED_LEAD_TIMES_HOURS
            ),
            "launches": public_feed,
        },
    )

    log.info("Done.")


if __name__ == "__main__":
    main()
