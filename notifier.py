"""Watch-A-Launch hourly updater and notification scheduler."""

from __future__ import annotations

import calendar
import datetime as dt
import hashlib
import json
import logging
import os
import sys
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

import requests

import launch_source

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("Watch-A-Launch")

ROOT = Path(__file__).parent
CONFIG_PATH = ROOT / "config.json"
STATE_PATH = ROOT / "data" / "state.json"
PUBLIC_FEED_PATH = ROOT / "docs" / "launches.json"

PACIFIC = ZoneInfo("America/Los_Angeles")
EASTERN = ZoneInfo("America/New_York")
REMINDER_HOURS = [24, 3]
SCHEDULE_EVENT_ORDER = ("24h", "3h", "now")

# ntfy allows delayed delivery from 10 seconds to 3 days ahead.
# The five-minute cushion avoids edge cases at the exact server limit.
NTFY_MIN_SCHEDULE_DELAY = dt.timedelta(seconds=10)
NTFY_MAX_SCHEDULE_DELAY = (
    dt.timedelta(days=3)
    - dt.timedelta(minutes=5)
)


def load_json(path: Path, default):
    if not path.exists():
        return default

    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return default


def save_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8") as handle:
        json.dump(
            data,
            handle,
            indent=2,
            ensure_ascii=False,
        )


def rocket_matches_filter(
    rocket: str,
    keywords: list[str],
) -> bool:
    if not keywords:
        return True

    rocket_lower = rocket.lower()

    return any(
        keyword.lower() in rocket_lower
        for keyword in keywords
    )


def parse_utc_datetime(
    value: str | None,
) -> dt.datetime | None:
    if not value:
        return None

    try:
        parsed = dt.datetime.fromisoformat(
            value.replace("Z", "+00:00")
        )
    except (TypeError, ValueError):
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(
            tzinfo=dt.timezone.utc
        )

    return parsed.astimezone(
        dt.timezone.utc
    )


def utc_iso(value: dt.datetime) -> str:
    return value.astimezone(
        dt.timezone.utc
    ).isoformat()


def timezone_for_location(location_code: str):
    return (
        EASTERN
        if location_code == "FL"
        else PACIFIC
    )


def state_name(location_code: str) -> str:
    if location_code == "CA":
        return "California"

    if location_code == "FL":
        return "Florida"

    return location_code


def format_exact_time(
    launch_time_utc: str,
    location_code: str,
) -> str:
    launch_dt = parse_utc_datetime(
        launch_time_utc
    )

    if launch_dt is None:
        return "Exact launch time unavailable"

    local = launch_dt.astimezone(
        timezone_for_location(location_code)
    )

    hour = local.strftime("%I").lstrip("0") or "0"

    return (
        f"{local.strftime('%A, %B')} "
        f"{local.day}, "
        f"{local.year} "
        f"at {hour}:"
        f"{local.strftime('%M')} "
        f"{local.strftime('%p %Z')}"
    )


def describe_launch_time(launch) -> str:
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
        except ValueError:
            pass

    return launch.date_text or "Date/time TBD"


def one_calendar_month_ahead(
    value: dt.datetime,
) -> dt.datetime:
    """Return the same local/UTC instant fields one calendar month later."""
    if value.month == 12:
        year = value.year + 1
        month = 1
    else:
        year = value.year
        month = value.month + 1

    last_day = calendar.monthrange(
        year,
        month,
    )[1]

    day = min(
        value.day,
        last_day,
    )

    return value.replace(
        year=year,
        month=month,
        day=day,
    )


def launch_is_within_change_notification_window(
    launch,
    now: dt.datetime,
) -> bool:
    """
    Time-change alerts are intentionally limited to launches no more than
    one calendar month ahead. Far-future schedule changes still update the
    website and state, but do not create noisy push notifications.
    """
    limit = one_calendar_month_ahead(now)

    launch_dt = parse_utc_datetime(
        launch.launch_time_utc
    )

    if launch_dt is not None:
        return now <= launch_dt <= limit

    if launch.scheduled_date:
        try:
            launch_date = dt.date.fromisoformat(
                launch.scheduled_date
            )
        except ValueError:
            return False

        return (
            now.date()
            <= launch_date
            <= limit.date()
        )

    return False


def topic_for_location(
    base_topic: str,
    location_code: str,
) -> str:
    return f"{base_topic}-{location_code}"


def ntfy_url(
    topic: str,
    sequence_id: str | None = None,
) -> str:
    url = (
        "https://ntfy.sh/"
        f"{quote(topic, safe='')}"
    )

    if sequence_id:
        url += (
            "/"
            f"{quote(sequence_id, safe='')}"
        )

    return url


def send_ntfy(
    topic: str,
    title: str,
    message: str,
    priority: str = "default",
    tags: str = "rocket",
) -> bool:
    try:
        response = requests.post(
            ntfy_url(topic),
            data=message.encode("utf-8"),
            headers={
                "Title": title,
                "Priority": priority,
                "Tags": tags,
            },
            timeout=20,
        )

        response.raise_for_status()

        log.info(
            "Notification sent to %s: %s",
            topic,
            title,
        )

        return True

    except Exception as exc:
        log.error(
            "Could not send notification to %s: %s",
            topic,
            exc,
        )

        return False


def schedule_ntfy(
    topic: str,
    sequence_id: str,
    deliver_at: dt.datetime,
    title: str,
    message: str,
    priority: str,
    tags: str,
) -> bool:
    deliver_at = deliver_at.astimezone(
        dt.timezone.utc
    )

    try:
        response = requests.post(
            ntfy_url(
                topic,
                sequence_id,
            ),
            data=message.encode("utf-8"),
            headers={
                "Title": title,
                "Priority": priority,
                "Tags": tags,
                "At": str(
                    int(deliver_at.timestamp())
                ),
            },
            timeout=20,
        )

        response.raise_for_status()

        log.info(
            "Scheduled %s for %s at %s",
            title,
            topic,
            utc_iso(deliver_at),
        )

        return True

    except Exception as exc:
        log.error(
            "Could not schedule %s for %s at %s: %s",
            title,
            topic,
            utc_iso(deliver_at),
            exc,
        )

        return False


def cancel_ntfy_sequence(
    topic: str,
    sequence_id: str,
) -> bool:
    try:
        response = requests.delete(
            ntfy_url(
                topic,
                sequence_id,
            ),
            timeout=20,
        )

        if response.status_code == 404:
            return True

        response.raise_for_status()

        log.info(
            "Canceled scheduled notification: %s/%s",
            topic,
            sequence_id,
        )

        return True

    except Exception as exc:
        log.warning(
            "Could not cancel scheduled notification %s/%s: %s",
            topic,
            sequence_id,
            exc,
        )

        return False


def schedule_signature(launch) -> dict:
    return {
        "scheduled_date": launch.scheduled_date,
        "launch_time_utc": launch.launch_time_utc,
        "site": launch.site,
        "location_code": launch.location_code,
    }


def previous_signature(previous: dict):
    if previous.get("schedule_signature"):
        return previous["schedule_signature"]

    if previous:
        return {
            "scheduled_date": previous.get(
                "scheduled_date"
            ),
            "launch_time_utc": previous.get(
                "launch_time_utc"
            ),
            "site": previous.get("site"),
            "location_code": previous.get(
                "location_code"
            ),
        }

    return None


def make_sequence_id(
    launch_uid: str,
    event_kind: str,
    topic: str,
    deliver_at: dt.datetime,
) -> str:
    raw = (
        f"{launch_uid}|"
        f"{event_kind}|"
        f"{topic}|"
        f"{utc_iso(deliver_at)}"
    )

    digest = hashlib.sha256(
        raw.encode("utf-8")
    ).hexdigest()[:24]

    return f"wal-{event_kind}-{digest}"


def make_payload_hash(
    title: str,
    message: str,
    priority: str,
    tags: str,
) -> str:
    raw = "\n".join(
        [title, message, priority, tags]
    )

    return hashlib.sha256(
        raw.encode("utf-8")
    ).hexdigest()


def notification_spec(
    launch,
    event_kind: str,
) -> dict | None:
    launch_dt = parse_utc_datetime(
        launch.launch_time_utc
    )

    if (
        launch_dt is None
        or launch.location_code
        not in {"CA", "FL"}
    ):
        return None

    exact_time = format_exact_time(
        launch.launch_time_utc,
        launch.location_code,
    )

    location = (
        launch.site
        or state_name(launch.location_code)
    )

    if event_kind == "24h":
        return {
            "deliver_at": (
                launch_dt
                - dt.timedelta(hours=24)
            ),
            "title": (
                f"Launch in 24 hours: "
                f"{launch.mission}"
            ),
            "message": (
                f"{launch.rocket} • "
                f"{launch.mission}\n"
                f"Launch in 24 hours "
                f"at {exact_time}.\n"
                f"Location: {location}"
            ),
            "priority": "default",
            "tags": "rocket",
        }

    if event_kind == "3h":
        return {
            "deliver_at": (
                launch_dt
                - dt.timedelta(hours=3)
            ),
            "title": (
                f"Launch in 3 hours: "
                f"{launch.mission}"
            ),
            "message": (
                f"{launch.rocket} • "
                f"{launch.mission}\n"
                f"Launch in 3 hours "
                f"at {exact_time}.\n"
                f"Location: {location}"
            ),
            "priority": "high",
            "tags": "rocket,alarm_clock",
        }

    if event_kind == "now":
        return {
            "deliver_at": launch_dt,
            "title": "LAUNCHING NOW!",
            "message": (
                f"{launch.rocket} • "
                f"{launch.mission}\n"
                f"Launch time: {exact_time}.\n"
                f"Location: {location}"
            ),
            "priority": "urgent",
            "tags": "rocket,rotating_light",
        }

    return None


def record_is_same(
    existing: dict,
    expected: dict,
) -> bool:
    keys = (
        "sequence_id",
        "topic",
        "deliver_at_utc",
        "payload_hash",
    )

    return all(
        existing.get(key) == expected.get(key)
        for key in keys
    )


def record_is_still_future(
    record: dict,
    now: dt.datetime,
) -> bool:
    deliver_at = parse_utc_datetime(
        record.get("deliver_at_utc")
    )

    return bool(
        deliver_at
        and deliver_at
        > now + dt.timedelta(seconds=5)
    )


def cancel_future_records(
    records: dict,
    now: dt.datetime,
):
    if not isinstance(records, dict):
        return

    for record in records.values():
        if (
            not isinstance(record, dict)
            or not record_is_still_future(
                record,
                now,
            )
        ):
            continue

        topic = record.get("topic")
        sequence_id = record.get("sequence_id")

        if topic and sequence_id:
            cancel_ntfy_sequence(
                topic,
                sequence_id,
            )


def sync_scheduled_notifications(
    launch,
    notification_topic: str | None,
    previous_records: dict,
    now: dt.datetime,
) -> dict:
    """Register, replace, or cancel exact future ntfy notifications."""
    if not isinstance(previous_records, dict):
        previous_records = {}

    expected_records: dict[str, dict] = {}
    expected_specs: dict[str, dict] = {}

    if notification_topic:
        for event_kind in SCHEDULE_EVENT_ORDER:
            spec = notification_spec(
                launch,
                event_kind,
            )

            if spec is None:
                continue

            deliver_at = spec[
                "deliver_at"
            ].astimezone(dt.timezone.utc)

            delay = deliver_at - now

            if delay <= dt.timedelta(0):
                continue

            if delay > NTFY_MAX_SCHEDULE_DELAY:
                continue

            old_record = previous_records.get(
                event_kind,
                {},
            )

            if (
                isinstance(old_record, dict)
                and record_is_still_future(
                    old_record,
                    now,
                )
                and old_record.get("topic")
                == notification_topic
                and old_record.get("sequence_id")
            ):
                sequence_id = old_record[
                    "sequence_id"
                ]
            else:
                sequence_id = make_sequence_id(
                    launch.uid,
                    event_kind,
                    notification_topic,
                    deliver_at,
                )

            expected_records[event_kind] = {
                "sequence_id": sequence_id,
                "topic": notification_topic,
                "deliver_at_utc": utc_iso(
                    deliver_at
                ),
                "payload_hash": make_payload_hash(
                    spec["title"],
                    spec["message"],
                    spec["priority"],
                    spec["tags"],
                ),
            }

            expected_specs[event_kind] = spec

    for event_kind, old_record in previous_records.items():
        if not isinstance(old_record, dict):
            continue

        expected = expected_records.get(
            event_kind
        )

        if (
            expected
            and record_is_same(
                old_record,
                expected,
            )
        ):
            continue

        old_topic = old_record.get("topic")
        old_sequence_id = old_record.get(
            "sequence_id"
        )

        if (
            expected
            and old_topic == expected.get("topic")
            and old_sequence_id
            == expected.get("sequence_id")
        ):
            continue

        if (
            record_is_still_future(
                old_record,
                now,
            )
            and old_topic
            and old_sequence_id
        ):
            cancel_ntfy_sequence(
                old_topic,
                old_sequence_id,
            )

    active_records: dict[str, dict] = {}

    for event_kind in SCHEDULE_EVENT_ORDER:
        expected = expected_records.get(
            event_kind
        )
        spec = expected_specs.get(event_kind)

        if not expected or not spec:
            continue

        old_record = previous_records.get(
            event_kind,
            {},
        )

        if (
            isinstance(old_record, dict)
            and record_is_same(
                old_record,
                expected,
            )
        ):
            active_records[event_kind] = expected
            continue

        deliver_at = spec[
            "deliver_at"
        ].astimezone(dt.timezone.utc)

        delay = deliver_at - now

        if delay < NTFY_MIN_SCHEDULE_DELAY:
            send_ntfy(
                expected["topic"],
                title=spec["title"],
                message=spec["message"],
                priority=spec["priority"],
                tags=spec["tags"],
            )
            continue

        scheduled = schedule_ntfy(
            expected["topic"],
            expected["sequence_id"],
            deliver_at,
            title=spec["title"],
            message=spec["message"],
            priority=spec["priority"],
            tags=spec["tags"],
        )

        if scheduled:
            active_records[event_kind] = expected

    return active_records


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
            "NTFY_TOPIC is missing. "
            "Add it under GitHub "
            "Settings -> Secrets and "
            "variables -> Actions."
        )
        sys.exit(1)

    rocket_keywords = config.get(
        "rocket_keywords",
        [
            "Falcon 9",
            "Falcon Heavy",
            "Starship",
        ],
    )

    notify_on_new = config.get(
        "notify_on_new_launch_added",
        True,
    )

    notify_on_change = config.get(
        "notify_on_time_change",
        True,
    )

    state = load_json(
        STATE_PATH,
        {"launches": {}},
    )
    state.setdefault("launches", {})

    first_successful_bootstrap = (
        len(state["launches"]) == 0
    )

    try:
        all_launches, data_source = (
            launch_source.get_launches()
        )
    except Exception as exc:
        log.error(
            "Launch-data refresh failed: %s",
            exc,
        )
        sys.exit(1)

    log.info(
        "Launch source: %s",
        data_source,
    )
    log.info(
        "Received %d upcoming launches",
        len(all_launches),
    )

    launches = [
        launch
        for launch in all_launches
        if rocket_matches_filter(
            launch.rocket,
            rocket_keywords,
        )
    ]

    log.info(
        "%d launches remain after rocket filtering",
        len(launches),
    )

    now = dt.datetime.now(dt.timezone.utc)
    public_feed = []
    currently_present_uids = set()

    for launch in launches:
        uid = launch.uid
        currently_present_uids.add(uid)

        previous = state["launches"].get(
            uid,
            {},
        )

        is_new = uid not in state["launches"]

        old_signature = previous_signature(
            previous
        )
        new_signature = schedule_signature(
            launch
        )

        schedule_changed = (
            not is_new
            and old_signature is not None
            and old_signature != new_signature
        )

        location_code = launch.location_code

        notification_topic = (
            topic_for_location(
                base_topic,
                location_code,
            )
            if location_code in {"CA", "FL"}
            else None
        )

        notified_leads = set(
            previous.get(
                "notified_lead_hours",
                [],
            )
        )

        if schedule_changed:
            notified_leads = set()

        if (
            is_new
            and not first_successful_bootstrap
            and notify_on_new
            and notification_topic
        ):
            send_ntfy(
                notification_topic,
                title=(
                    f"New {state_name(location_code)} "
                    f"launch scheduled"
                ),
                message=(
                    f"{launch.rocket} • "
                    f"{launch.mission}\n"
                    f"Launch: "
                    f"{describe_launch_time(launch)}\n"
                    f"Location: {launch.site}"
                ),
            )

        change_alert_allowed = (
            schedule_changed
            and notify_on_change
            and notification_topic
            and launch_is_within_change_notification_window(
                launch,
                now,
            )
        )

        if change_alert_allowed:
            old_launch_time = previous.get(
                "launch_time_utc"
            )

            if old_launch_time:
                old_location = (
                    previous.get("location_code")
                    or location_code
                )

                old_when = format_exact_time(
                    old_launch_time,
                    old_location,
                )
            else:
                old_when = (
                    previous.get("date_text")
                    or "the previous schedule"
                )

            send_ntfy(
                notification_topic,
                title=(
                    "Launch time changed: "
                    f"{launch.mission}"
                ),
                message=(
                    f"{launch.rocket} • "
                    f"{launch.mission}\n"
                    f"NEW: "
                    f"{describe_launch_time(launch)}\n"
                    f"Previously: {old_when}\n"
                    f"Location: {launch.site}"
                ),
                priority="high",
                tags=(
                    "rocket,"
                    "arrows_counterclockwise"
                ),
            )

        elif (
            schedule_changed
            and notify_on_change
            and notification_topic
        ):
            log.info(
                "Suppressed far-future launch-time change alert: %s",
                launch.mission,
            )

        scheduled_notifications = (
            sync_scheduled_notifications(
                launch,
                notification_topic,
                previous.get(
                    "scheduled_notifications",
                    {},
                ),
                now,
            )
        )

        launch_dt = parse_utc_datetime(
            launch.launch_time_utc
        )

        if launch_dt:
            for lead_hours in REMINDER_HOURS:
                target = (
                    launch_dt
                    - dt.timedelta(
                        hours=lead_hours
                    )
                )

                if target <= now:
                    notified_leads.add(
                        lead_hours
                    )

        state["launches"][uid] = {
            "rocket": launch.rocket,
            "mission": launch.mission,
            "date_text": launch.date_text,
            "scheduled_date": launch.scheduled_date,
            "launch_time_utc": launch.launch_time_utc,
            "time_description": launch.time_description,
            "site": launch.site,
            "location_code": launch.location_code,
            "source_url": launch.source_url,
            "schedule_signature": new_signature,
            "scheduled_notifications": (
                scheduled_notifications
            ),
            "notified_lead_hours": sorted(
                notified_leads
            ),
            "last_seen_utc": now.isoformat(),
        }

        public_feed.append(
            {
                **launch.to_dict(),
                "notified_lead_hours": sorted(
                    notified_leads
                ),
            }
        )

    cleaned_state = {}
    stale_cutoff = now - dt.timedelta(days=7)

    for uid, record in state["launches"].items():
        if uid in currently_present_uids:
            cleaned_state[uid] = record
            continue

        cancel_future_records(
            record.get(
                "scheduled_notifications",
                {},
            ),
            now,
        )

        old_dt = parse_utc_datetime(
            record.get("launch_time_utc")
        )

        if old_dt and old_dt > stale_cutoff:
            cleaned_state[uid] = {
                **record,
                "scheduled_notifications": {},
            }

    state["launches"] = cleaned_state

    public_feed.sort(
        key=lambda launch: (
            launch.get("launch_time_utc")
            or launch.get("scheduled_date")
            or "9999"
        )
    )

    if not public_feed:
        log.error(
            "The launch source succeeded but zero launches "
            "survived the rocket filter. Refusing to "
            "overwrite the existing public feed."
        )
        sys.exit(1)

    save_json(
        STATE_PATH,
        state,
    )

    save_json(
        PUBLIC_FEED_PATH,
        {
            "generated_at_utc": now.isoformat(),
            "source": data_source,
            "rocket_keywords_filter": rocket_keywords,
            "notification_locations": [
                "CA",
                "FL",
            ],
            "notification_lead_times_hours": (
                REMINDER_HOURS
            ),
            "launching_now_notifications": True,
            "time_change_notification_window": (
                "one_calendar_month"
            ),
            "launches": public_feed,
        },
    )

    california_count = sum(
        launch.get("location_code") == "CA"
        for launch in public_feed
    )

    florida_count = sum(
        launch.get("location_code") == "FL"
        for launch in public_feed
    )

    scheduled_count = sum(
        len(
            record.get(
                "scheduled_notifications",
                {},
            )
        )
        for record in state["launches"].values()
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
        "Active exact ntfy schedules: %d",
        scheduled_count,
    )
    log.info(
        "Total website launches: %d",
        len(public_feed),
    )


if __name__ == "__main__":
    main()
