"""
launch_source.py
----------------

Reliable launch-data layer for Watch-A-Launch.

PRIMARY SOURCE:
    Launch Library 2 (The Space Devs)
    https://ll.thespacedevs.com/

Launch Library 2 is a structured API, so it is much more reliable for
calendar data than trying to scrape the visual HTML of a website.

FALLBACK SOURCE:
    Spaceflight Now
    https://spaceflightnow.com/launch-schedule/

The GitHub Action only runs once per hour, so the primary API is only
queried once per hour.

The rest of the Watch-A-Launch app receives the same normalized Launch
objects regardless of which source succeeded.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import asdict, dataclass
from typing import Optional
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


# ---------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------

LL2_URL = "https://ll.thespacedevs.com/2.3.0/launches/upcoming/"
SPACEFLIGHT_NOW_URL = "https://spaceflightnow.com/launch-schedule/"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36 "
    "Watch-A-Launch/1.0"
)


# ---------------------------------------------------------------------
# Normalized launch format used by the rest of the app
# ---------------------------------------------------------------------

@dataclass
class Launch:
    uid: str
    rocket: str
    mission: str

    # True if date/time is still considered approximate / NET.
    is_net: bool

    # Human-readable schedule date.
    date_text: str

    # Local calendar date at the launch site.
    # Example:
    #     2026-08-18
    scheduled_date: Optional[str]

    site: str

    # "CA", "FL", or None
    location_code: Optional[str]

    # Human-readable source description.
    time_description: str

    # ISO-8601 UTC timestamp ONLY when we actually have
    # minute-level or better precision.
    launch_time_utc: Optional[str]

    updated_text: Optional[str]

    source_url: str

    def to_dict(self):
        return asdict(self)


# ---------------------------------------------------------------------
# General helpers
# ---------------------------------------------------------------------

def clean_text(value) -> str:
    if value is None:
        return ""

    return re.sub(
        r"\s+",
        " ",
        str(value),
    ).strip()


def make_uid(
    rocket: str,
    mission: str,
) -> str:
    base = (
        f"{rocket}::{mission}"
        .lower()
    )

    base = re.sub(
        r"\s+",
        " ",
        base,
    ).strip()

    return re.sub(
        r"[^a-z0-9]+",
        "-",
        base,
    ).strip("-")


def identify_location(
    text: str,
) -> Optional[str]:
    """
    Determine whether a launch is in California or Florida.
    """

    value = (
        text
        or ""
    ).lower()

    california_words = (
        "california",
        "vandenberg",
        "vafb",
        "vsfb",
    )

    florida_words = (
        "florida",
        "cape canaveral",
        "kennedy space center",
        "ksc",
        "ccsfs",
    )

    if any(
        word in value
        for word
        in california_words
    ):
        return "CA"

    if any(
        word in value
        for word
        in florida_words
    ):
        return "FL"

    return None


def timezone_for_location(
    location_code: Optional[str],
) -> ZoneInfo:
    if location_code == "FL":
        return ZoneInfo(
            "America/New_York"
        )

    return ZoneInfo(
        "America/Los_Angeles"
    )


def iso_to_datetime(
    value: str,
) -> Optional[dt.datetime]:
    if not value:
        return None

    try:
        return dt.datetime.fromisoformat(
            value.replace(
                "Z",
                "+00:00",
            )
        )
    except Exception:
        return None


# =====================================================================
# PRIMARY SOURCE — Launch Library 2
# =====================================================================

def fetch_launch_library() -> list[Launch]:
    """
    Fetch upcoming SpaceX launches from Launch Library 2.

    This is our preferred source because it is structured JSON rather
    than HTML intended for humans.
    """

    params = {
        "format": "json",

        # SpaceX only
        "lsp__name": "SpaceX",

        # Plenty for the calendar while remaining a single API request.
        "limit": 100,

        # Earliest launches first.
        "ordering": "net",

        # Don't intentionally include recently completed launches.
        "hide_recent_previous": "true",
    }

    response = requests.get(
        LL2_URL,
        params=params,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        },
        timeout=30,
    )

    response.raise_for_status()

    payload = response.json()

    results = payload.get(
        "results",
        [],
    )

    if not isinstance(
        results,
        list,
    ):
        raise RuntimeError(
            "Launch Library returned an unexpected response."
        )

    launches: list[Launch] = []

    for raw in results:
        launch = normalize_ll2_launch(
            raw
        )

        if launch is not None:
            launches.append(
                launch
            )

    launches.sort(
        key=lambda item: (
            item.launch_time_utc
            or item.scheduled_date
            or "9999"
        )
    )

    return launches


def normalize_ll2_launch(
    raw: dict,
) -> Optional[Launch]:

    # -------------------------------------------------------------
    # Provider
    # -------------------------------------------------------------

    provider = (
        raw.get(
            "launch_service_provider"
        )
        or {}
    )

    provider_name = clean_text(
        provider.get(
            "name"
        )
    )

    # Extra safety even though the API request already asks for SpaceX.
    if (
        provider_name
        and provider_name.lower()
        != "spacex"
    ):
        return None

    # -------------------------------------------------------------
    # Rocket
    # -------------------------------------------------------------

    rocket_obj = (
        raw.get("rocket")
        or {}
    )

    configuration = (
        rocket_obj.get(
            "configuration"
        )
        or {}
    )

    rocket = (
        clean_text(
            configuration.get(
                "name"
            )
        )
        or clean_text(
            configuration.get(
                "full_name"
            )
        )
        or "SpaceX"
    )

    # -------------------------------------------------------------
    # Mission
    # -------------------------------------------------------------

    mission_obj = (
        raw.get("mission")
        or {}
    )

    mission = clean_text(
        mission_obj.get(
            "name"
        )
    )

    full_name = clean_text(
        raw.get(
            "name"
        )
    )

    if not mission:
        if "|" in full_name:
            mission = clean_text(
                full_name.split(
                    "|",
                    1,
                )[1]
            )
        else:
            mission = (
                full_name
                or "Upcoming launch"
            )

    # -------------------------------------------------------------
    # Pad / location
    # -------------------------------------------------------------

    pad = (
        raw.get("pad")
        or {}
    )

    pad_name = clean_text(
        pad.get(
            "name"
        )
    )

    location = (
        pad.get(
            "location"
        )
        or {}
    )

    location_name = clean_text(
        location.get(
            "name"
        )
    )

    location_description = clean_text(
        location.get(
            "description"
        )
    )

    pad_description = clean_text(
        pad.get(
            "description"
        )
    )

    site_parts = []

    if pad_name:
        site_parts.append(
            pad_name
        )

    if (
        location_name
        and location_name
        not in site_parts
    ):
        site_parts.append(
            location_name
        )

    site = ", ".join(
        site_parts
    )

    location_search_text = " ".join(
        [
            site,
            location_description,
            pad_description,
        ]
    )

    location_code = identify_location(
        location_search_text
    )

    # Ignore Texas/Starbase/etc for the notification states,
    # but still allow the data object to exist if needed elsewhere.

    # -------------------------------------------------------------
    # Launch time
    # -------------------------------------------------------------

    net_raw = clean_text(
        raw.get(
            "net"
        )
    )

    net_dt = iso_to_datetime(
        net_raw
    )

    if net_dt is None:
        return None

    if net_dt.tzinfo is None:
        net_dt = net_dt.replace(
            tzinfo=dt.timezone.utc
        )

    net_dt = net_dt.astimezone(
        dt.timezone.utc
    )

    # -------------------------------------------------------------
    # Precision
    #
    # Launch Library sometimes knows only a day/month/etc.
    # In that situation, "net" can still contain a timestamp, but
    # that timestamp should NOT be treated as an exact liftoff time.
    # -------------------------------------------------------------

    precision = (
        raw.get(
            "net_precision"
        )
        or {}
    )

    precision_name = clean_text(
        precision.get(
            "name"
        )
    )

    precision_abbrev = clean_text(
        precision.get(
            "abbrev"
        )
    ).upper()

    exact_precision = (
        precision_abbrev
        in {
            "SEC",
            "MIN",
        }
        or precision_name.lower()
        in {
            "second",
            "minute",
        }
    )

    # -------------------------------------------------------------
    # Site timezone
    # -------------------------------------------------------------

    timezone_name = clean_text(
        location.get(
            "timezone_name"
        )
    )

    try:
        if timezone_name:
            local_zone = ZoneInfo(
                timezone_name
            )
        else:
            local_zone = timezone_for_location(
                location_code
            )
    except Exception:
        local_zone = timezone_for_location(
            location_code
        )

    local_dt = net_dt.astimezone(
        local_zone
    )

    scheduled_date = (
        local_dt
        .date()
        .isoformat()
    )

    # -------------------------------------------------------------
    # Human-readable date text
    # -------------------------------------------------------------

    date_text = (
        f"{local_dt.strftime('%B')} "
        f"{local_dt.day}"
    )

    if (
        local_dt.year
        != dt.datetime.now(
            dt.timezone.utc
        ).year
    ):
        date_text += (
            f", {local_dt.year}"
        )

    # -------------------------------------------------------------
    # Time description
    # -------------------------------------------------------------

    if exact_precision:
        time_description = (
            local_dt.strftime(
                "%A, %B %-d at %-I:%M %p %Z"
            )
        )

        launch_time_utc = (
            net_dt.isoformat()
        )

    else:
        launch_time_utc = None

        if precision_name:
            time_description = (
                f"Currently scheduled for "
                f"{date_text}; exact launch time TBD "
                f"({precision_name.lower()}-level estimate)"
            )
        else:
            time_description = (
                f"Currently scheduled for "
                f"{date_text}; exact launch time TBD"
            )

    # -------------------------------------------------------------
    # NET status
    # -------------------------------------------------------------

    status = (
        raw.get(
            "status"
        )
        or {}
    )

    status_name = clean_text(
        status.get(
            "name"
        )
    ).lower()

    is_net = (
        not exact_precision
        or "to be confirmed"
        in status_name
        or "to be determined"
        in status_name
    )

    # -------------------------------------------------------------
    # Last updated
    # -------------------------------------------------------------

    updated_text = clean_text(
        raw.get(
            "last_updated"
        )
    ) or None

    uid = (
        clean_text(
            raw.get(
                "id"
            )
        )
        or make_uid(
            rocket,
            mission,
        )
    )

    return Launch(
        uid=uid,
        rocket=rocket,
        mission=mission,
        is_net=is_net,
        date_text=date_text,
        scheduled_date=scheduled_date,
        site=site,
        location_code=location_code,
        time_description=time_description,
        launch_time_utc=launch_time_utc,
        updated_text=updated_text,
        source_url=LL2_URL,
    )


# =====================================================================
# FALLBACK — Spaceflight Now
# =====================================================================

MONTHS = {
    name: number
    for number, name
    in enumerate(
        [
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December",
        ],
        start=1,
    )
}

MONTH_PATTERN = "|".join(
    MONTHS.keys()
)

HEADER_RE = re.compile(
    rf"""
    ^(?P<net>NET\s+)?
    (?P<date>
        TBD
        |
        Q[1-4]\s+\d{{4}}
        |
        (?:{MONTH_PATTERN})
        \s+
        \d{{1,2}}
        (?:/\d{{1,2}})?
        (?:,\s*\d{{4}})?
    )
    \s+
    (?P<rocket>[^•]+?)
    \s*•\s*
    (?P<mission>.+?)
    $
    """,
    re.VERBOSE,
)

TIME_RE = re.compile(
    r"^Launch time:\s*(.+)$",
    re.IGNORECASE,
)

SITE_RE = re.compile(
    r"^Launch site:\s*(.+)$",
    re.IGNORECASE,
)

UTC_TIME_RE = re.compile(
    r"(\d{3,4})\s*UTC",
    re.IGNORECASE,
)

DATE_RE = re.compile(
    rf"""
    ^(?P<month>{MONTH_PATTERN})
    \s+
    (?P<day1>\d{{1,2}})
    (?:/(?P<day2>\d{{1,2}}))?
    (?:,\s*(?P<year>\d{{4}}))?
    $
    """,
    re.VERBOSE,
)


def fetch_spaceflight_now() -> list[Launch]:
    response = requests.get(
        SPACEFLIGHT_NOW_URL,
        headers={
            "User-Agent": USER_AGENT
        },
        timeout=30,
    )

    response.raise_for_status()

    return parse_spaceflight_now(
        response.text
    )


def parse_spaceflight_now(
    html: str,
) -> list[Launch]:
    now = dt.datetime.now(
        dt.timezone.utc
    )

    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    main = (
        soup.find("main")
        or soup.body
        or soup
    )

    lines = [
        clean_text(line)
        for line
        in main
        .get_text("\n")
        .splitlines()
        if clean_text(line)
    ]

    launches: list[Launch] = []

    index = 0

    while index < len(lines):
        header = HEADER_RE.match(
            lines[index]
        )

        if not header:
            index += 1
            continue

        date_text = clean_text(
            header.group(
                "date"
            )
        )

        rocket = clean_text(
            header.group(
                "rocket"
            )
        )

        mission = clean_text(
            header.group(
                "mission"
            )
        )

        is_net = bool(
            header.group(
                "net"
            )
        )

        time_description = ""
        site = ""
        updated_text = None

        cursor = index + 1

        while (
            cursor < len(lines)
            and cursor < index + 14
        ):
            # Stop when the next launch starts.
            if HEADER_RE.match(
                lines[cursor]
            ):
                break

            time_match = TIME_RE.match(
                lines[cursor]
            )

            if time_match:
                time_description = clean_text(
                    time_match.group(1)
                )

            site_match = SITE_RE.match(
                lines[cursor]
            )

            if site_match:
                site = clean_text(
                    site_match.group(1)
                )

            if lines[
                cursor
            ].lower().startswith(
                "updated:"
            ):
                updated_text = clean_text(
                    lines[cursor][
                        len("Updated:"):
                    ]
                )

                if (
                    not updated_text
                    and cursor + 1
                    < len(lines)
                ):
                    updated_text = clean_text(
                        lines[
                            cursor + 1
                        ]
                    )

            cursor += 1

        location_code = identify_location(
            site
        )

        (
            scheduled_date,
            exact_time,
        ) = resolve_spaceflight_date(
            date_text,
            time_description,
            location_code,
            now,
        )

        launches.append(
            Launch(
                uid=make_uid(
                    rocket,
                    mission,
                ),
                rocket=rocket,
                mission=mission,
                is_net=is_net,
                date_text=date_text,
                scheduled_date=scheduled_date,
                site=site,
                location_code=location_code,
                time_description=time_description,
                launch_time_utc=exact_time,
                updated_text=updated_text,
                source_url=SPACEFLIGHT_NOW_URL,
            )
        )

        index = max(
            index + 1,
            cursor,
        )

    return launches


def resolve_spaceflight_date(
    date_text: str,
    time_description: str,
    location_code: Optional[str],
    now: dt.datetime,
) -> tuple[
    Optional[str],
    Optional[str],
]:
    if (
        date_text == "TBD"
        or date_text.startswith(
            "Q"
        )
    ):
        return (
            None,
            None,
        )

    match = DATE_RE.match(
        date_text
    )

    if not match:
        return (
            None,
            None,
        )

    month = MONTHS[
        match.group(
            "month"
        )
    ]

    day1 = int(
        match.group(
            "day1"
        )
    )

    day2 = (
        int(
            match.group(
                "day2"
            )
        )
        if match.group(
            "day2"
        )
        else None
    )

    year = (
        int(
            match.group(
                "year"
            )
        )
        if match.group(
            "year"
        )
        else now.year
    )

    try:
        local_date = dt.date(
            year,
            month,
            day1,
        )
    except ValueError:
        return (
            None,
            None,
        )

    if (
        now.date()
        - local_date
    ).days > 60:
        year += 1

        try:
            local_date = dt.date(
                year,
                month,
                day1,
            )
        except ValueError:
            return (
                None,
                None,
            )

    scheduled_date = (
        local_date.isoformat()
    )

    utc_match = UTC_TIME_RE.search(
        time_description
        or ""
    )

    if not utc_match:
        return (
            scheduled_date,
            None,
        )

    hhmm = (
        utc_match
        .group(1)
        .zfill(4)
    )

    hour = int(
        hhmm[:2]
    )

    minute = int(
        hhmm[2:]
    )

    if (
        hour > 23
        or minute > 59
    ):
        return (
            scheduled_date,
            None,
        )

    # In entries like "August 18/19", Spaceflight Now
    # uses the second day for the UTC calendar day.
    utc_day = (
        day2
        if day2 is not None
        else day1
    )

    try:
        utc_dt = dt.datetime(
            year,
            month,
            utc_day,
            hour,
            minute,
            tzinfo=dt.timezone.utc,
        )

    except ValueError:
        return (
            scheduled_date,
            None,
        )

    return (
        scheduled_date,
        utc_dt.isoformat(),
    )


# =====================================================================
# PUBLIC FUNCTION
# =====================================================================

def get_launches() -> tuple[
    list[Launch],
    str,
]:
    """
    Return:
        (launches, source_name)

    Try Launch Library 2 first.
    If that fails or somehow returns nothing, automatically fall back
    to Spaceflight Now.
    """

    primary_error = None

    try:
        launches = (
            fetch_launch_library()
        )

        if launches:
            return (
                launches,
                "Launch Library 2",
            )

        primary_error = (
            "Launch Library returned "
            "zero launches."
        )

    except Exception as exc:
        primary_error = str(
            exc
        )

    try:
        launches = (
            fetch_spaceflight_now()
        )

        if launches:
            return (
                launches,
                "Spaceflight Now fallback",
            )

    except Exception as exc:
        raise RuntimeError(
            "Both launch sources failed. "
            f"Launch Library error: "
            f"{primary_error}. "
            f"Spaceflight Now error: "
            f"{exc}"
        ) from exc

    raise RuntimeError(
        "Both launch sources returned "
        "zero launches. "
        f"Launch Library error: "
        f"{primary_error}"
    )


if __name__ == "__main__":
    launches, source = (
        get_launches()
    )

    print(
        f"Source: {source}"
    )

    print(
        f"Launches found: "
        f"{len(launches)}"
    )

    for launch in launches:
        print(
            f"{launch.scheduled_date or 'TBD'} | "
            f"{launch.location_code or '--'} | "
            f"{launch.rocket} | "
            f"{launch.mission} | "
            f"{launch.launch_time_utc or 'time TBD'}"
        )
