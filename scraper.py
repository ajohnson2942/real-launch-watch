"""
scraper.py
----------
Reads Spaceflight Now's public launch schedule and turns the human-readable
schedule into structured launch records used by the notifier and dashboard.

The parser intentionally keys off stable phrases such as "Launch time:",
"Launch site:", "Updated:", the rocket/mission bullet separator, and UTC
clock times instead of fragile CSS class names.
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
    m: i
    for i, m in enumerate(
        [
            "January", "February", "March", "April", "May", "June",
            "July", "August", "September", "October", "November", "December",
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
        | Q[1-4]\s\d{4}
        | (?:%s)\s\d{1,2}(?:/\d{1,2})?(?:,\s\d{4})?
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

TIME_LINE_RE = re.compile(
    r"Launch time:\s*(?P<time_desc>.*?)\s*Launch site:\s*(?P<site>.*?)\s*$"
)
TIME_ONLY_RE = re.compile(r"Launch time:\s*(?P<time_desc>.*?)\s*$")
SITE_ONLY_RE = re.compile(r"Launch site:\s*(?P<site>.*?)\s*$")
UTC_TIME_RE = re.compile(r"(\d{3,4})\s*UTC")
UPDATED_RE = re.compile(r"^Updated:\s*$")
DATE_RE = re.compile(
    r"(?P<month>%s)\s(?P<day1>\d{1,2})(?:/(?P<day2>\d{1,2}))?(?:,\s(?P<year>\d{4}))?"
    % MONTH_PATTERN
)


@dataclass
class Launch:
    uid: str
    rocket: str
    mission: str
    is_net: bool
    date_text: str
    scheduled_date: Optional[str]  # YYYY-MM-DD using the schedule's first/local day
    site: str
    location_code: Optional[str]  # CA / FL when applicable
    time_description: str
    launch_time_utc: Optional[str]
    updated_text: Optional[str]
    source_url: str = SCHEDULE_URL

    def to_dict(self):
        return asdict(self)


def _make_uid(rocket: str, mission: str) -> str:
    base = f"{rocket}::{mission}".lower()
    base = re.sub(r"\s+", " ", base).strip()
    return re.sub(r"[^a-z0-9]+", "-", base).strip("-")


def _location_code(site: str) -> Optional[str]:
    site_l = (site or "").lower()
    if "california" in site_l or "vandenberg" in site_l:
        return "CA"
    if "florida" in site_l or "cape canaveral" in site_l or "kennedy space center" in site_l:
        return "FL"
    return None


def _parse_schedule_date(
    date_text: str,
    now: dt.datetime,
) -> Optional[tuple[int, int, int, Optional[int]]]:
    """Return (year, month, first_day, optional_second_day) for calendar dates."""
    if date_text.strip() == "TBD" or date_text.strip().startswith("Q"):
        return None

    match = DATE_RE.fullmatch(date_text.strip())
    if not match:
        return None

    month = MONTHS[match.group("month")]
    day1 = int(match.group("day1"))
    day2 = int(match.group("day2")) if match.group("day2") else None
    year = int(match.group("year")) if match.group("year") else None

    if year is None:
        # Assume the current year unless that date is already far enough behind us
        # that the schedule is clearly referring to the following year.
        try:
            candidate = dt.date(now.year, month, day1)
        except ValueError:
            return None

        if (now.date() - candidate).days > 60:
            year = now.year + 1
        else:
            year = now.year

    try:
        dt.date(year, month, day1)
    except ValueError:
        return None

    return year, month, day1, day2


def _resolve_scheduled_date(date_text: str, now: dt.datetime) -> Optional[str]:
    parsed = _parse_schedule_date(date_text, now)
    if not parsed:
        return None

    year, month, day1, _ = parsed
    return dt.date(year, month, day1).isoformat()


def _resolve_utc_datetime(
    date_text: str,
    time_desc: str,
    now: dt.datetime,
) -> Optional[str]:
    """Resolve an exact launch timestamp when the schedule publishes a UTC time."""
    parsed = _parse_schedule_date(date_text, now)
    if not parsed:
        return None

    utc_match = UTC_TIME_RE.search(time_desc)
    if not utc_match:
        return None

    hhmm = utc_match.group(1).zfill(4)
    hour, minute = int(hhmm[:2]), int(hhmm[2:])

    if hour > 23 or minute > 59:
        return None

    year, month, day1, day2 = parsed

    # For a range such as "August 18/19", Spaceflight Now uses the second
    # day for UTC when the local launch time crosses midnight UTC.
    utc_day = day2 if day2 is not None else day1

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


def fetch_html(url: str = SCHEDULE_URL, timeout: int = 30) -> str:
    response = requests.get(
        url,
        headers={"User-Agent": USER_AGENT},
        timeout=timeout,
    )
    response.raise_for_status()
    return response.text


def parse_schedule(
    html: str,
    now: Optional[dt.datetime] = None,
) -> list[Launch]:
    if now is None:
        now = dt.datetime.now(dt.timezone.utc)

    soup = BeautifulSoup(html, "html.parser")

    main = (
        soup.find("main")
        or soup.find(attrs={"class": re.compile(r"entry-content", re.I)})
        or soup.find(attrs={"id": re.compile(r"content", re.I)})
        or soup.body
        or soup
    )

    text = main.get_text("\n")
    lines = [line.strip() for line in text.split("\n") if line.strip()]

    launches: list[Launch] = []

    i = 0
    n = len(lines)

    while i < n:
        header_match = HEADER_RE.match(lines[i])

        if not header_match:
            i += 1
            continue

        is_net = bool(header_match.group("net"))
        date_text = header_match.group("date").strip()
        rocket = header_match.group("rocket").strip()
        mission = header_match.group("mission").strip()

        time_desc, site = "", ""

        j = i + 1
        search_limit = min(n, i + 8)

        while j < search_limit:
            combined = TIME_LINE_RE.match(lines[j])

            if combined:
                time_desc = combined.group("time_desc").strip()
                site = combined.group("site").strip()
                break

            time_only = TIME_ONLY_RE.match(lines[j])

            if time_only:
                time_desc = time_only.group("time_desc").strip()

                if j + 1 < n:
                    site_only = SITE_ONLY_RE.match(lines[j + 1])

                    if site_only:
                        site = site_only.group("site").strip()
                        j += 1
                        break

            j += 1

        updated_text = None

        k = j
        search_limit2 = min(n, j + 15)

        while k < search_limit2 - 1:
            if UPDATED_RE.match(lines[k]):
                updated_text = lines[k + 1].strip()
                break

            k += 1

        launches.append(
            Launch(
                uid=_make_uid(rocket, mission),
                rocket=rocket,
                mission=mission,
                is_net=is_net,
                date_text=date_text,
                scheduled_date=_resolve_scheduled_date(date_text, now),
                site=site,
                location_code=_location_code(site),
                time_description=time_desc,
                launch_time_utc=_resolve_utc_datetime(
                    date_text,
                    time_desc,
                    now,
                ),
                updated_text=updated_text,
            )
        )

        i += 1

    return launches


def get_upcoming_launches(
    now: Optional[dt.datetime] = None,
) -> list[Launch]:
    return parse_schedule(fetch_html(), now=now)


if __name__ == "__main__":
    results = get_upcoming_launches()

    print(
        f"Parsed {len(results)} launch entries:\n",
        file=sys.stderr,
    )

    for launch in results:
        print(
            f"- [{'NET ' if launch.is_net else ''}{launch.date_text}] "
            f"{launch.rocket} • {launch.mission} -> "
            f"{launch.launch_time_utc or 'TBD'} "
            f"[{launch.location_code or 'other'}] ({launch.site})"
        )
