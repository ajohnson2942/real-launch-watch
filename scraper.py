"""
scraper.py
----------
Fetches Spaceflight Now's public launch schedule and turns it into structured
launch records for the notifier and dashboard.

The parser intentionally keys off stable text labels instead of CSS classes.
Spaceflight Now currently renders launch headers like:

    August 18/19 Falcon 9 • Starlink 17-50
    Launch time: Window opens at 7 p.m. PDT (10 p.m. EDT / 0200 UTC)
    Launch site: SLC-4E, Vandenberg Space Force Base, California

It also handles older formatting where the date and rocket name were joined
without a space, and where Launch time / Launch site appeared on one line.
"""

from __future__ import annotations

import datetime as dt
import re
import sys
from dataclasses import asdict, dataclass
from typing import Optional

import requests
from bs4 import BeautifulSoup

SCHEDULE_URL = "https://spaceflightnow.com/launch-schedule/"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 "
    "SpaceLaunchNotifier/1.0 (personal, non-commercial notifier bot)"
)

MONTHS = {
    month: number
    for number, month in enumerate(
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
MONTH_PATTERN = "|".join(MONTHS.keys())

HEADER_RE = re.compile(
    r"""
    ^(?P<net>NET\s+)?
    (?P<date>
        TBD
        | Q[1-4]\s+\d{4}
        | (?:%s)\s+\d{1,2}(?:/\d{1,2})?(?:,\s*\d{4})?
    )
    \s*
    (?P<rocket>[A-Z][^\n•]*?)
    \s*•\s*
    (?P<mission>[^\n]+?)
    $
    """
    % MONTH_PATTERN,
    re.VERBOSE,
)

TIME_AND_SITE_RE = re.compile(
    r"^Launch time:\s*(?P<time_desc>.*?)\s+Launch site:\s*(?P<site>.*?)\s*$",
    re.IGNORECASE,
)
TIME_ONLY_RE = re.compile(
    r"^Launch time:\s*(?P<time_desc>.*?)\s*$",
    re.IGNORECASE,
)
SITE_ONLY_RE = re.compile(
    r"^Launch site:\s*(?P<site>.*?)\s*$",
    re.IGNORECASE,
)
UTC_TIME_RE = re.compile(
    r"(\d{3,4})\s*UTC",
    re.IGNORECASE,
)
UPDATED_RE = re.compile(
    r"^Updated:\s*(?P<same_line>.*)$",
    re.IGNORECASE,
)
DATE_RE = re.compile(
    r"^(?P<month>%s)\s+(?P<day1>\d{1,2})(?:/(?P<day2>\d{1,2}))?(?:,\s*(?P<year>\d{4}))?$"
    % MONTH_PATTERN
)


@dataclass
class Launch:
    uid: str
    rocket: str
    mission: str
    is_net: bool
    date_text: str
    scheduled_date: Optional[str]
    site: str
    location_code: Optional[str]
    time_description: str
    launch_time_utc: Optional[str]
    updated_text: Optional[str]
    source_url: str = SCHEDULE_URL

    def to_dict(self):
        return asdict(self)


def _make_uid(
    rocket: str,
    mission: str,
) -> str:
    base = f"{rocket}::{mission}".lower()
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


def _location_code(
    site: str,
) -> Optional[str]:
    site_lower = (
        site or ""
    ).lower()

    if (
        "california" in site_lower
        or "vandenberg" in site_lower
    ):
        return "CA"

    if (
        "florida" in site_lower
        or "cape canaveral" in site_lower
        or "kennedy space center" in site_lower
    ):
        return "FL"

    return None


def _parse_date_parts(
    date_text: str,
    now: dt.datetime,
) -> Optional[
    tuple[
        int,
        int,
        int,
        Optional[int],
    ]
]:
    raw = date_text.strip()

    if (
        raw == "TBD"
        or raw.startswith("Q")
    ):
        return None

    match = DATE_RE.match(
        raw
    )

    if not match:
        return None

    month = MONTHS[
        match.group("month")
    ]

    day1 = int(
        match.group("day1")
    )

    day2 = (
        int(
            match.group("day2")
        )
        if match.group("day2")
        else None
    )

    year = (
        int(
            match.group("year")
        )
        if match.group("year")
        else None
    )

    if year is None:
        try:
            candidate = dt.date(
                now.year,
                month,
                day1,
            )
        except ValueError:
            return None

        year = (
            now.year + 1
            if (
                now.date()
                - candidate
            ).days > 60
            else now.year
        )

    try:
        dt.date(
            year,
            month,
            day1,
        )
    except ValueError:
        return None

    return (
        year,
        month,
        day1,
        day2,
    )


def _scheduled_date(
    date_text: str,
    now: dt.datetime,
) -> Optional[str]:
    """
    Return the first/local calendar day
    printed in the launch header.
    """

    parts = _parse_date_parts(
        date_text,
        now,
    )

    if not parts:
        return None

    year, month, day1, _ = parts

    return dt.date(
        year,
        month,
        day1,
    ).isoformat()


def _resolve_utc_datetime(
    date_text: str,
    time_desc: str,
    now: dt.datetime,
) -> Optional[str]:
    """
    Combine the schedule date with its
    published UTC clock time.
    """

    parts = _parse_date_parts(
        date_text,
        now,
    )

    if not parts:
        return None

    utc_match = UTC_TIME_RE.search(
        time_desc or ""
    )

    if not utc_match:
        return None

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
        return None

    (
        year,
        month,
        day1,
        day2,
    ) = parts

    # For "August 18/19", the first
    # number is the local date and the
    # second is the UTC calendar date.
    utc_day = (
        day2
        if day2 is not None
        else day1
    )

    try:
        launch_dt = dt.datetime(
            year,
            month,
            utc_day,
            hour,
            minute,
            tzinfo=dt.timezone.utc,
        )
    except ValueError:
        return None

    return launch_dt.isoformat()


def fetch_html(
    url: str = SCHEDULE_URL,
    timeout: int = 30,
) -> str:
    response = requests.get(
        url,
        headers={
            "User-Agent": USER_AGENT
        },
        timeout=timeout,
    )

    response.raise_for_status()

    return response.text


def parse_schedule(
    html: str,
    now: Optional[
        dt.datetime
    ] = None,
) -> list[Launch]:

    if now is None:
        now = dt.datetime.now(
            dt.timezone.utc
        )

    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    main = (
        soup.find("main")
        or soup.find(
            attrs={
                "class": re.compile(
                    r"entry-content",
                    re.I,
                )
            }
        )
        or soup.find(
            attrs={
                "id": re.compile(
                    r"content",
                    re.I,
                )
            }
        )
        or soup.body
        or soup
    )

    lines = [
        line.strip()
        for line
        in main
        .get_text("\n")
        .splitlines()
        if line.strip()
    ]

    launches: list[
        Launch
    ] = []

    index = 0

    while index < len(
        lines
    ):
        header = HEADER_RE.match(
            lines[index]
        )

        if not header:
            index += 1
            continue

        is_net = bool(
            header.group(
                "net"
            )
        )

        date_text = (
            header.group(
                "date"
            ).strip()
        )

        rocket = (
            header.group(
                "rocket"
            ).strip()
        )

        mission = (
            header.group(
                "mission"
            ).strip()
        )

        time_desc = ""
        site = ""
        updated_text = None

        lookahead_end = min(
            len(lines),
            index + 18,
        )

        cursor = (
            index + 1
        )

        while (
            cursor
            < lookahead_end
        ):
            if (
                cursor
                > index + 1
                and HEADER_RE.match(
                    lines[cursor]
                )
            ):
                break

            combined = (
                TIME_AND_SITE_RE.match(
                    lines[cursor]
                )
            )

            if combined:
                time_desc = (
                    combined
                    .group(
                        "time_desc"
                    )
                    .strip()
                )

                site = (
                    combined
                    .group(
                        "site"
                    )
                    .strip()
                )

                cursor += 1
                continue

            time_only = (
                TIME_ONLY_RE.match(
                    lines[cursor]
                )
            )

            if time_only:
                time_desc = (
                    time_only
                    .group(
                        "time_desc"
                    )
                    .strip()
                )

                cursor += 1
                continue

            site_only = (
                SITE_ONLY_RE.match(
                    lines[cursor]
                )
            )

            if site_only:
                site = (
                    site_only
                    .group(
                        "site"
                    )
                    .strip()
                )

                cursor += 1
                continue

            updated = (
                UPDATED_RE.match(
                    lines[cursor]
                )
            )

            if updated:
                same_line = (
                    updated
                    .group(
                        "same_line"
                    )
                    .strip()
                )

                if same_line:
                    updated_text = (
                        same_line
                    )

                elif (
                    cursor + 1
                    < len(lines)
                    and not
                    HEADER_RE.match(
                        lines[
                            cursor
                            + 1
                        ]
                    )
                ):
                    updated_text = (
                        lines[
                            cursor
                            + 1
                        ].strip()
                    )

                cursor += 1
                continue

            cursor += 1

        launches.append(
            Launch(
                uid=_make_uid(
                    rocket,
                    mission,
                ),
                rocket=rocket,
                mission=mission,
                is_net=is_net,
                date_text=date_text,
                scheduled_date=(
                    _scheduled_date(
                        date_text,
                        now,
                    )
                ),
                site=site,
                location_code=(
                    _location_code(
                        site
                    )
                ),
                time_description=(
                    time_desc
                ),
                launch_time_utc=(
                    _resolve_utc_datetime(
                        date_text,
                        time_desc,
                        now,
                    )
                ),
                updated_text=(
                    updated_text
                ),
            )
        )

        index += 1

    return launches


def get_upcoming_launches(
    now: Optional[
        dt.datetime
    ] = None,
) -> list[Launch]:

    return parse_schedule(
        fetch_html(),
        now=now,
    )


if __name__ == "__main__":
    results = (
        get_upcoming_launches()
    )

    print(
        f"Parsed {len(results)} launch entries:\n",
        file=sys.stderr,
    )

    for launch in results:
        print(
            f"- {launch.date_text} | "
            f"{launch.rocket} • "
            f"{launch.mission} | "
            f"{launch.location_code or 'OTHER'} | "
            f"{launch.launch_time_utc or 'TBD'}"
        )
