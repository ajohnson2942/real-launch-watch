"""
notifier.py
-----------

Watch-A-Launch hourly updater.

This file:

1. Gets upcoming SpaceX launches.
2. Filters them using config.json.
3. Writes docs/launches.json for the website/calendar.
4. Sends California notifications to:
       <NTFY_TOPIC>-CA
5. Sends Florida notifications to:
       <NTFY_TOPIC>-FL
6. Sends 24-hour and 3-hour reminders.
7. Detects launch-time/date changes and sends an updated alert.

The GitHub Action runs this once per hour.
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

import launch_source


# ---------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format=(
        "%(asctime)s "
        "%(levelname)s "
        "%(message)s"
    ),
)

log = logging.getLogger(
    "Watch-A-Launch"
)


# ---------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------

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


# ---------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------

PACIFIC = ZoneInfo(
    "America/Los_Angeles"
)

EASTERN = ZoneInfo(
    "America/New_York"
)

REMINDER_HOURS = [
    24,
    3,
]


# ---------------------------------------------------------------------
# Basic JSON helpers
# ---------------------------------------------------------------------

def load_json(
    path: Path,
    default,
):
    if not path.exists():
        return default

    try:
        with path.open(
            "r",
            encoding="utf-8",
        ) as handle:
            return json.load(
                handle
            )

    except Exception:
        return default


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
            ensure_ascii=False,
        )


# ---------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------

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


# ---------------------------------------------------------------------
# Location / time formatting
# ---------------------------------------------------------------------

def timezone_for_location(
    location_code: str,
):
    if location_code == "FL":
        return EASTERN

    return PACIFIC


def state_name(
    location_code: str,
) -> str:
    if location_code == "CA":
        return "California"

    if location_code == "FL":
        return "Florida"

    return location_code


def format_exact_time(
    launch_time_utc: str,
    location_code: str,
) -> str:
    launch_dt = (
        dt.datetime
        .fromisoformat(
            launch_time_utc
            .replace(
                "Z",
                "+00:00",
            )
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


def describe_launch_time(
    launch,
) -> str:
    if (
        launch.launch_time_utc
        and launch.location_code
    ):
        return format_exact_time(
            launch.launch_time_utc,
            launch.location_code,
        )

    if launch.scheduled_date:
        try:
            value = dt.date.fromisoformat(
                launch.scheduled_date
            )

            return (
                f"{value.strftime('%A, %B')} "
                f"{value.day}, "
                f"{value.year} "
                f"(exact time TBD)"
            )

        except Exception:
            pass

    return (
        launch.date_text
        or "Date/time TBD"
    )


# ---------------------------------------------------------------------
# ntfy
# ---------------------------------------------------------------------

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
        response = requests.post(
            f"https://ntfy.sh/{topic}",
            data=message.encode(
                "utf-8"
            ),
            headers={
                "Title": title,
                "Priority": priority,
                "Tags": "rocket",
            },
            timeout=20,
        )

        response.raise_for_status()

        log.info(
            "Notification sent "
            "to %s: %s",
            topic,
            title,
        )

        return True

    except Exception as exc:
        log.error(
            "Could not send "
            "notification to %s: %s",
            topic,
            exc,
        )

        return False


# ---------------------------------------------------------------------
# Schedule-change detection
# ---------------------------------------------------------------------

def schedule_signature(
    launch,
):
    """
    Only include things that actually represent the launch schedule.

    This prevents a harmless API description update from creating a
    fake "launch changed!" push notification.
    """

    return {
        "scheduled_date":
            launch.scheduled_date,

        "launch_time_utc":
            launch.launch_time_utc,

        "site":
            launch.site,

        "location_code":
            launch.location_code,
    }


def previous_signature(
    previous: dict,
):
    if (
        previous.get(
            "schedule_signature"
        )
    ):
        return previous[
            "schedule_signature"
        ]

    # Compatibility with your older state.json format.
    if previous:
        return {
            "scheduled_date":
                previous.get(
                    "scheduled_date"
                ),

            "launch_time_utc":
                previous.get(
                    "launch_time_utc"
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

    return None


# ---------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------

def main():

    # -------------------------------------------------------------
    # Configuration
    # -------------------------------------------------------------

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

    if not base_topic:
        log.error(
            "NTFY_TOPIC is missing. "
            "Add it under GitHub "
            "Settings -> Secrets and "
            "variables -> Actions."
        )

        sys.exit(1)

    rocket_keywords = (
        config.get(
            "rocket_keywords",
            [
                "Falcon 9",
                "Falcon Heavy",
                "Starship",
            ],
        )
    )

    notify_on_new = (
        config.get(
            "notify_on_new_launch_added",
            True,
        )
    )

    notify_on_change = (
        config.get(
            "notify_on_time_change",
            True,
        )
    )

    # -------------------------------------------------------------
    # Existing state
    # -------------------------------------------------------------

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

    # Important:
    # If state is completely empty because the old scraper was broken,
    # DON'T suddenly blast users with a "new launch" alert for every
    # launch already on the schedule.
    first_successful_bootstrap = (
        len(
            state["launches"]
        )
        == 0
    )

    # -------------------------------------------------------------
    # Get launch data
    # -------------------------------------------------------------

    try:
        (
            all_launches,
            data_source,
        ) = launch_source.get_launches()

    except Exception as exc:
        log.error(
            "Launch-data refresh failed: %s",
            exc,
        )

        # Very important:
        # Do NOT overwrite launches.json with [] when the source fails.
        # Leave the last successful calendar data in place.
        sys.exit(1)

    log.info(
        "Launch source: %s",
        data_source,
    )

    log.info(
        "Received %d upcoming launches",
        len(all_launches),
    )

    # -------------------------------------------------------------
    # Rocket filter
    # -------------------------------------------------------------

    launches = [
        launch
        for launch
        in all_launches
        if rocket_matches_filter(
            launch.rocket,
            rocket_keywords,
        )
    ]

    log.info(
        "%d launches remain "
        "after rocket filtering",
        len(launches),
    )

    # -------------------------------------------------------------
    # Current time
    # -------------------------------------------------------------

    now = dt.datetime.now(
        dt.timezone.utc
    )

    public_feed = []

    currently_present_uids = set()

    # -------------------------------------------------------------
    # Process launches
    # -------------------------------------------------------------

    for launch in launches:

        uid = launch.uid

        currently_present_uids.add(
            uid
        )

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
            state[
                "launches"
            ]
        )

        old_signature = (
            previous_signature(
                previous
            )
        )

        new_signature = (
            schedule_signature(
                launch
            )
        )

        schedule_changed = (
            not is_new
            and old_signature
            is not None
            and old_signature
            != new_signature
        )

        old_launch_time = (
            previous.get(
                "launch_time_utc"
            )
        )

        notified_leads = set(
            previous.get(
                "notified_lead_hours",
                [],
            )
        )

        location_code = (
            launch.location_code
        )

        notification_topic = None

        if (
            location_code
            in {
                "CA",
                "FL",
            }
        ):
            notification_topic = (
                topic_for_location(
                    base_topic,
                    location_code,
                )
            )

        # ---------------------------------------------------------
        # Newly added launch
        # ---------------------------------------------------------

        if (
            is_new
            and not first_successful_bootstrap
            and notify_on_new
            and notification_topic
        ):
            send_ntfy(
                notification_topic,

                title=(
                    f"New "
                    f"{state_name(location_code)} "
                    f"launch scheduled"
                ),

                message=(
                    f"{launch.rocket} • "
                    f"{launch.mission}\n"
                    f"Launch: "
                    f"{describe_launch_time(launch)}\n"
                    f"Site: "
                    f"{launch.site}"
                ),
            )

        # ---------------------------------------------------------
        # Launch schedule changed
        # ---------------------------------------------------------

        if (
            schedule_changed
            and notify_on_change
            and notification_topic
        ):

            new_when = (
                describe_launch_time(
                    launch
                )
            )

            if old_launch_time:
                try:
                    old_when = (
                        format_exact_time(
                            old_launch_time,
                            location_code,
                        )
                    )

                except Exception:
                    old_when = (
                        previous.get(
                            "date_text"
                        )
                        or
                        "the previous time"
                    )

            else:
                old_when = (
                    previous.get(
                        "date_text"
                    )
                    or
                    "the previous schedule"
                )

            send_ntfy(
                notification_topic,

                title=(
                    f"Launch time changed: "
                    f"{launch.mission}"
                ),

                message=(
                    f"{launch.rocket} • "
                    f"{launch.mission}\n"
                    f"NEW: {new_when}\n"
                    f"Previously: {old_when}\n"
                    f"Site: {launch.site}"
                ),

                priority="high",
            )

            # Launch moved, so old 24h/3h reminders should no longer
            # prevent reminders based on the new schedule.
            notified_leads = set()

        # ---------------------------------------------------------
        # 24-hour / 3-hour reminders
        # ---------------------------------------------------------

        if (
            notification_topic
            and launch.launch_time_utc
        ):

            try:
                launch_dt = (
                    dt.datetime
                    .fromisoformat(
                        launch
                        .launch_time_utc
                        .replace(
                            "Z",
                            "+00:00",
                        )
                    )
                    .astimezone(
                        dt.timezone.utc
                    )
                )

            except Exception:
                launch_dt = None

            if (
                launch_dt
                and launch_dt > now
            ):

                for lead_hours in (
                    REMINDER_HOURS
                ):

                    if (
                        lead_hours
                        in notified_leads
                    ):
                        continue

                    reminder_time = (
                        launch_dt
                        - dt.timedelta(
                            hours=lead_hours
                        )
                    )

                    # Since the checker runs hourly, send the reminder on
                    # the first hourly check at or after the target time.
                    if reminder_time <= now:

                        exact_time = (
                            format_exact_time(
                                launch.launch_time_utc,
                                location_code,
                            )
                        )

                        if lead_hours == 3:
                            title = (
                                f"Launch in 3 hours: "
                                f"{launch.mission}"
                            )

                            message = (
                                f"{launch.rocket} • "
                                f"{launch.mission}\n"
                                f"Launch in 3 hours "
                                f"at {exact_time}.\n"
                                f"Site: "
                                f"{launch.site}"
                            )

                        else:
                            title = (
                                f"Launch in 24 hours: "
                                f"{launch.mission}"
                            )

                            message = (
                                f"{launch.rocket} • "
                                f"{launch.mission}\n"
                                f"Launch in 24 hours "
                                f"at {exact_time}.\n"
                                f"Site: "
                                f"{launch.site}"
                            )

                        sent = send_ntfy(
                            notification_topic,
                            title=title,
                            message=message,
                            priority=(
                                "high"
                                if lead_hours == 3
                                else "default"
                            ),
                        )

                        if sent:
                            notified_leads.add(
                                lead_hours
                            )

        # ---------------------------------------------------------
        # Save state
        # ---------------------------------------------------------

        state[
            "launches"
        ][uid] = {

            "rocket":
                launch.rocket,

            "mission":
                launch.mission,

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

            "source_url":
                launch.source_url,

            "schedule_signature":
                new_signature,

            "notified_lead_hours":
                sorted(
                    notified_leads
                ),

            "last_seen_utc":
                now.isoformat(),
        }

        # ---------------------------------------------------------
        # Public website data
        # ---------------------------------------------------------

        public_feed.append(
            {
                **launch.to_dict(),

                "notified_lead_hours":
                    sorted(
                        notified_leads
                    ),
            }
        )

    # -------------------------------------------------------------
    # Clean old state
    # -------------------------------------------------------------

    cleaned_state = {}

    stale_cutoff = (
        now
        - dt.timedelta(
            days=7
        )
    )

    for (
        uid,
        record,
    ) in state[
        "launches"
    ].items():

        if (
            uid
            in currently_present_uids
        ):
            cleaned_state[
                uid
            ] = record

            continue

        old_time = (
            record.get(
                "launch_time_utc"
            )
        )

        if old_time:
            try:
                old_dt = (
                    dt.datetime
                    .fromisoformat(
                        old_time
                        .replace(
                            "Z",
                            "+00:00",
                        )
                    )
                )

                if old_dt > stale_cutoff:
                    cleaned_state[
                        uid
                    ] = record

            except Exception:
                pass

    state[
        "launches"
    ] = cleaned_state

    # -------------------------------------------------------------
    # Sort website data
    # -------------------------------------------------------------

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

    # -------------------------------------------------------------
    # CRITICAL SAFETY CHECK
    #
    # The old version happily replaced good data with [] if scraping
    # broke. This version does not.
    # -------------------------------------------------------------

    if not public_feed:
        log.error(
            "The launch source succeeded "
            "but zero launches survived the "
            "rocket filter. Refusing to overwrite "
            "the existing public feed."
        )

        sys.exit(1)

    # -------------------------------------------------------------
    # Save
    # -------------------------------------------------------------

    save_json(
        STATE_PATH,
        state,
    )

    save_json(
        PUBLIC_FEED_PATH,
        {
            "generated_at_utc":
                now.isoformat(),

            "source":
                data_source,

            "rocket_keywords_filter":
                rocket_keywords,

            "notification_locations":
                [
                    "CA",
                    "FL",
                ],

            "notification_lead_times_hours":
                REMINDER_HOURS,

            "launches":
                public_feed,
        },
    )

    # -------------------------------------------------------------
    # Useful action-log diagnostics
    # -------------------------------------------------------------

    california_count = sum(
        1
        for launch
        in public_feed
        if launch.get(
            "location_code"
        )
        == "CA"
    )

    florida_count = sum(
        1
        for launch
        in public_feed
        if launch.get(
            "location_code"
        )
        == "FL"
    )

    log.info(
        "Calendar feed written successfully."
    )

    log.info(
        "California launches: %d",
        california_count,
    )

    log.info(
        "Florida launches: %d",
        florida_count,
    )

    log.info(
        "Total website launches: %d",
        len(public_feed),
    )


if __name__ == "__main__":
    main()
