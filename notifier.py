"""
notifier.py
-----------
Runs the scraper once per scheduled GitHub Actions run, sends ntfy alerts,
and writes the public dashboard feed.

LOCATION TOPICS
---------------
The NTFY_TOPIC GitHub secret is the base topic. This app adds a state suffix:

    CoolRockets-CA  -> California launches
    CoolRockets-FL  -> Florida launches

Subscribe to one topic for one state, or both topics for both states. Each
location topic receives 24-hour reminders, 3-hour reminders, and schedule
change alerts for launches in that state.
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
    format=(
        "%(asctime)s "
        "%(levelname)s "
        "%(message)s"
    ),
)

log = logging.getLogger(
    "notifier"
)

ROOT = Path(
    __file__
).parent

CONFIG_PATH = (
    ROOT
    / "config.json"
)

STATE_PATH = (
    ROOT
    / "data"
    / "state.json"
)

PUBLIC_FEED_PATH = (
    ROOT
    / "docs"
    / "launches.json"
)

PACIFIC = ZoneInfo(
    "America/Los_Angeles"
)

EASTERN = ZoneInfo(
    "America/New_York"
)

OFFERED_LEAD_TIMES_HOURS = [
    24,
    3,
]


def load_json(
    path: Path,
    default,
):
    if not path.exists():
        return default

    with path.open(
        encoding="utf-8"
    ) as handle:
        return json.load(
            handle
        )


def save_json(
    path: Path,
    data,
):
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with path.open(
        "w",
        encoding="utf-8",
    ) as handle:
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

    rocket_lower = (
        rocket.lower()
    )

    return any(
        keyword.lower()
        in rocket_lower
        for keyword
        in keywords
    )


def topic_for_location(
    base_topic: str,
    location_code: str,
) -> str:

    return (
        f"{base_topic}-"
        f"{location_code}"
    )


def send_ntfy(
    topic: str,
    title: str,
    message: str,
    priority: str = "default",
) -> bool:

    try:
        response = (
            requests.post(
                f"https://ntfy.sh/{topic}",
                data=(
                    message
                    .encode(
                        "utf-8"
                    )
                ),
                headers={
                    "Title": title,
                    "Priority": (
                        priority
                    ),
                },
                timeout=15,
            )
        )

        response.raise_for_status()

        log.info(
            "Sent notification "
            "to %s: %s",
            topic,
            title,
        )

        return True

    except Exception as exc:
        log.error(
            "Failed to send "
            "notification to "
            "%s (%r): %s",
            topic,
            title,
            exc,
        )

        return False


def timezone_for_location(
    location_code: str | None,
) -> ZoneInfo:

    return (
        PACIFIC
        if location_code == "CA"
        else EASTERN
    )


def format_launch_time(
    launch_time_utc: str,
    location_code: str | None,
) -> str:

    launch_dt = (
        dt.datetime
        .fromisoformat(
            launch_time_utc
        )
    )

    local = (
        launch_dt
        .astimezone(
            timezone_for_location(
                location_code
            )
        )
    )

    return local.strftime(
        "%A, %B %-d "
        "at %-I:%M %p %Z"
    )


def describe_schedule(
    launch: scraper.Launch,
) -> str:

    if launch.launch_time_utc:
        return format_launch_time(
            launch.launch_time_utc,
            launch.location_code,
        )

    if launch.scheduled_date:
        date_value = (
            dt.date
            .fromisoformat(
                launch.scheduled_date
            )
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


def schedule_signature(
    launch: scraper.Launch,
) -> dict:

    return {
        "date_text":
            launch.date_text,

        "scheduled_date":
            launch.scheduled_date,

        "launch_time_utc":
            launch.launch_time_utc,

        "time_description":
            launch.time_description,

        "site":
            launch.site,

        "location_code":
            launch.location_code,
    }


def main():
    config = load_json(
        CONFIG_PATH,
        {},
    )

    base_topic = (
        os.environ.get(
            "NTFY_TOPIC"
        )
        or config.get(
            "ntfy_topic"
        )
    )

    if (
        not base_topic
        or base_topic
        == "CHANGE-ME-TO-SOMETHING-UNIQUE"
    ):
        log.error(
            "No ntfy topic configured. "
            "Add a repo Secret named "
            "NTFY_TOPIC under "
            "Settings -> Secrets and "
            "variables -> Actions."
        )

        sys.exit(1)

    rocket_filter = (
        config.get(
            "rocket_keywords",
            [],
        )
    )

    notify_on_new = (
        config.get(
            "notify_on_new_launch_added",
            True,
        )
    )

    notify_on_time_change = (
        config.get(
            "notify_on_time_change",
            True,
        )
    )

    state = load_json(
        STATE_PATH,
        {
            "launches": {}
        },
    )

    state.setdefault(
        "launches",
        {},
    )

    now = dt.datetime.now(
        dt.timezone.utc
    )

    try:
        launches = (
            scraper
            .parse_schedule(
                scraper.fetch_html(),
                now=now,
            )
        )

    except Exception as exc:
        log.error(
            "Could not fetch/parse "
            "launch schedule: %s",
            exc,
        )

        sys.exit(1)

    if not launches:
        log.warning(
            "Parsed 0 launches -- "
            "Spaceflight Now's layout "
            "may have changed. "
            "No notifications will "
            "be sent this run."
        )

    filtered = [
        launch
        for launch
        in launches
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

        previous = (
            state[
                "launches"
            ].get(
                uid,
                {},
            )
        )

        is_new = (
            uid
            not in
            state["launches"]
        )

        previous_notified = set(
            previous.get(
                "notified_lead_hours",
                [],
            )
        )

        current_signature = (
            schedule_signature(
                launch
            )
        )

        previous_signature = (
            previous.get(
                "schedule_signature"
            )
        )

        # Backward compatibility
        # with your old state.json.
        if (
            previous_signature
            is None
            and previous
        ):
            previous_signature = {
                "date_text":
                    previous.get(
                        "date_text"
                    ),

                "scheduled_date":
                    previous.get(
                        "scheduled_date"
                    ),

                "launch_time_utc":
                    previous.get(
                        "launch_time_utc"
                    ),

                "time_description":
                    previous.get(
                        "time_description"
                    ),

                "site":
                    previous.get(
                        "site"
                    ),

                "location_code":
                    previous.get(
                        "location_code"
                    ),
            }

        schedule_changed = (
            not is_new
            and previous_signature
            is not None
            and previous_signature
            != current_signature
        )

        location_code = (
            launch.location_code
        )

        topic = (
            topic_for_location(
                base_topic,
                location_code,
            )
            if location_code
            in {
                "CA",
                "FL",
            }
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
                    "New launch "
                    "scheduled: "
                    f"{launch.mission} "
                    f"({location_code})"
                ),
                message=(
                    f"{launch.rocket} • "
                    f"{launch.mission}\n"
                    f"Launch: "
                    f"{describe_schedule(launch)}\n"
                    f"Site: "
                    f"{launch.site}"
                ),
            )

        if (
            schedule_changed
            and notify_on_time_change
            and topic
        ):
            old_time = (
                previous.get(
                    "launch_time_utc"
                )
            )

            old_date_text = (
                previous.get(
                    "date_text"
                )
            )

            if launch.launch_time_utc:
                new_when = (
                    format_launch_time(
                        launch.launch_time_utc,
                        location_code,
                    )
                )

                if old_time:
                    old_when = (
                        format_launch_time(
                            old_time,
                            location_code,
                        )
                    )
                else:
                    old_when = (
                        old_date_text
                        or
                        "previous schedule"
                    )

                message = (
                    f"{launch.rocket} • "
                    f"{launch.mission}\n"
                    f"Updated launch time: "
                    f"{new_when}\n"
                    f"Previously: "
                    f"{old_when}\n"
                    f"Site: "
                    f"{launch.site}"
                )

            else:
                message = (
                    f"{launch.rocket} • "
                    f"{launch.mission}\n"
                    f"The launch schedule "
                    f"changed.\n"
                    f"New schedule: "
                    f"{describe_schedule(launch)}\n"
                    f"Site: "
                    f"{launch.site}"
                )

            send_ntfy(
                topic,
                title=(
                    "Launch time changed: "
                    f"{launch.mission} "
                    f"({location_code})"
                ),
                message=message,
                priority="high",
            )

            # Re-arm both reminders
            # using the new launch time.
            previous_notified = set()

        notified_now = set(
            previous_notified
        )

        if (
            topic
            and
            launch.launch_time_utc
        ):
            launch_dt = (
                dt.datetime
                .fromisoformat(
                    launch.launch_time_utc
                )
            )

            if launch_dt > now:
                for lead_hours in (
                    OFFERED_LEAD_TIMES_HOURS
                ):
                    if (
                        lead_hours
                        in
                        previous_notified
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

                        lead_label = (
                            f"{lead_hours} hours"
                        )

                        send_ntfy(
                            topic,
                            title=(
                                f"Launch in "
                                f"{lead_label}: "
                                f"{launch.mission} "
                                f"({location_code})"
                            ),
                            message=(
                                f"{launch.rocket} • "
                                f"{launch.mission}\n"
                                f"Launch in "
                                f"{lead_label} "
                                f"at "
                                f"{exact_time}.\n"
                                f"Site: "
                                f"{launch.site}"
                            ),
                            priority=(
                                "high"
                                if lead_hours
                                == 3
                                else
                                "default"
                            ),
                        )

                        notified_now.add(
                            lead_hours
                        )

        state[
            "launches"
        ][uid] = {
            "rocket":
                launch.rocket,

            "mission":
                launch.mission,

            "launch_time_utc":
                launch.launch_time_utc,

            "scheduled_date":
                launch.scheduled_date,

            "date_text":
                launch.date_text,

            "time_description":
                launch.time_description,

            "site":
                launch.site,

            "location_code":
                launch.location_code,

            "schedule_signature":
                current_signature,

            "notified_lead_hours":
                sorted(
                    notified_now
                ),

            "last_seen_utc":
                now.isoformat(),
        }

        public_feed.append(
            {
                **launch.to_dict(),

                "notified_lead_hours":
                    sorted(
                        notified_now
                    ),
            }
        )

    cutoff = (
        now
        - dt.timedelta(
            days=3
        )
    )

    still_present = {
        launch.uid
        for launch
        in filtered
    }

    pruned = {}

    for (
        uid,
        record,
    ) in state[
        "launches"
    ].items():

        if (
            uid
            in still_present
        ):
            pruned[
                uid
            ] = record

            continue

        launch_time = (
            record.get(
                "launch_time_utc"
            )
        )

        if (
            launch_time
            and
            dt.datetime
            .fromisoformat(
                launch_time
            )
            > cutoff
        ):
            pruned[
                uid
            ] = record

    state[
        "launches"
    ] = pruned

    save_json(
        STATE_PATH,
        state,
    )

    public_feed.sort(
        key=lambda launch: (
            launch.get(
                "launch_time_utc"
            )
            or
            launch.get(
                "scheduled_date"
            )
            or
            "9999"
        )
    )

    save_json(
        PUBLIC_FEED_PATH,
        {
            "generated_at_utc":
                now.isoformat(),

            "rocket_keywords_filter":
                rocket_filter,

            "notification_locations":
                [
                    "CA",
                    "FL",
                ],

            "notification_lead_times_hours":
                OFFERED_LEAD_TIMES_HOURS,

            "launches":
                public_feed,
        },
    )

    log.info(
        "Done."
    )


if __name__ == "__main__":
    main()
