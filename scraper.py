"""
scraper.py
----------
Fetches https://spaceflightnow.com/launch-schedule/ and parses it into a
list of structured launch records.

The page lists entries like this (as rendered text, one after another):

    August 18/19Falcon 9 • Starlink 17-50
    Launch time: Window opens at 7 p.m. PDT (10 p.m. EDT / 0200 UTC)  Launch site: SLC-4E, Vandenberg Space Force Base, California
    <description paragraph>
    Updated:
     August 15

We don't rely on exact CSS class names (Spaceflight Now has changed their
theme before, and probably will again). Instead we pull all the visible
text out of the main content area and parse it with regular expressions
that key off of consistent phrases ("Launch time:", "Launch site:",
"Updated:", the "•" separator, and the "(NNNN UTC)" time marker). This is
more resilient to minor markup/CSS changes than a selector-based scraper.

If Spaceflight Now does a bigger redesign and this stops matching
anything, `parse_schedule()` will simply return an empty list -- it will
never crash the whole pipeline, and notifier.py logs a warning so you'll
notice in the GitHub Actions run log.
"""

from __future__ import annotations

import re
import sys
import datetime as dt
from dataclasses import dataclass, asdict
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

# Matches the "date + title" header line, e.g.:
#   "August 18/19Falcon 9 • Starlink 17-50"
#   "NET August 27Ariane 6 • MTG-I2"
#   "TBDAtlas 5 • Boeing Starliner-1"
#   "NET Q4 2026Alpha • FLTA008"
#   "NET July 5, 2028Falcon Heavy • Dragonfly"
HEADER_RE = re.compile(
    r"""
    ^(?P<net>NET\s+)?
    (?P<date>
        TBD
        | Q[1-4]\s\d{4}
        | (?:%s)\s\d{1,2}(?:/\d{1,2})?(?:,\s\d{4})?
    )
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

# Pulls the authoritative UTC clock time out of a time description, e.g.
# "Window opens at 7 p.m. PDT (10 p.m. EDT / 0200 UTC)" -> "0200"
UTC_TIME_RE = re.compile(r"(\d{3,4})\s*UTC")

UPDATED_RE = re.compile(r"^Updated:\s*$")


@dataclass
class Launch:
    uid: str  # stable id used for de-duplication / state tracking
    rocket: str
    mission: str
    is_net: bool  # "No Earlier Than" -- date is not firm
    date_text: str  # the raw date text shown on the site, e.g. "August 18/19"
    site: str
    time_description: str  # human-readable, e.g. "Window opens at 7 p.m. PDT..."
    launch_time_utc: Optional[str]  # ISO 8601 UTC string, or None if unknown (TBD)
    updated_text: Optional[str]
    source_url: str = SCHEDULE_URL

    def to_dict(self):
        return asdict(self)


def _make_uid(rocket: str, mission: str) -> str:
    base = f"{rocket}::{mission}".lower()
    base = re.sub(r"\s+", " ", base).strip()
    return re.sub(r"[^a-z0-9]+", "-", base).strip("-")


def _resolve_utc_datetime(date_text: str, time_desc: str, now: dt.datetime) -> Optional[str]:
    """
    Combine the header date text (e.g. "August 18/19" or "August 20" or
    "NET July 5, 2028") with the UTC clock time pulled from the time
    description, and return an ISO 8601 UTC timestamp string.

    Returns None if we can't determine a precise time (TBD / Q-quarter
    entries, or entries missing a "(NNNN UTC)" marker).
    """
    if date_text.strip() == "TBD" or date_text.strip().startswith("Q"):
        return None

    m = re.match(
        r"(?P<month>%s)\s(?P<day1>\d{1,2})(?:/(?P<day2>\d{1,2}))?(?:,\s(?P<year>\d{4}))?"
        % MONTH_PATTERN,
        date_text.strip(),
    )
    if not m:
        return None

    month = MONTHS[m.group("month")]
    day1 = int(m.group("day1"))
    day2 = int(m.group("day2")) if m.group("day2") else None
    year = int(m.group("year")) if m.group("year") else None

    utc_match = UTC_TIME_RE.search(time_desc)
    if not utc_match:
        # No firm clock time (site sometimes just says "Launch time: TBD")
        return None
    hhmm = utc_match.group(1).zfill(4)
    hour, minute = int(hhmm[:2]), int(hhmm[2:])
    if hour > 23 or minute > 59:
        return None

    # When the site shows a day range like "18/19", the SECOND day is the
    # UTC calendar date (the launch crosses midnight UTC relative to the
    # local time zone shown first). A single day means both local and UTC
    # date match.
    day = day2 if day2 is not None else day1

    if year is None:
        # Infer year: assume current year unless that would put the date
        # more than ~2 months in the past, in which case assume next year
        # (handles December -> January rollover near year boundaries).
        candidate = dt.date(now.year, month, min(day, 28))
        if (now.date() - candidate).days > 60:
            year = now.year + 1
        else:
            year = now.year

    try:
        launch_dt = dt.datetime(year, month, day, hour, minute, tzinfo=dt.timezone.utc)
    except ValueError:
        return None

    return launch_dt.isoformat()


def fetch_html(url: str = SCHEDULE_URL, timeout: int = 30) -> str:
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout)
    resp.raise_for_status()
    return resp.text


def parse_schedule(html: str, now: Optional[dt.datetime] = None) -> list[Launch]:
    if now is None:
        now = dt.datetime.now(dt.timezone.utc)

    soup = BeautifulSoup(html, "html.parser")

    # Narrow down to the main article body if we can find it, so we don't
    # pick up nav links / "Breaking News" headlines that also contain
    # dates. Falls back to the whole page if the container isn't found.
    main = (
        soup.find("main")
        or soup.find(attrs={"class": re.compile(r"entry-content", re.I)})
        or soup.find(attrs={"id": re.compile(r"content", re.I)})
        or soup.body
        or soup
    )

    text = main.get_text("\n")
    lines = [ln.strip() for ln in text.split("\n")]
    lines = [ln for ln in lines if ln]  # drop blank lines

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

        # Look ahead a few lines for the "Launch time: ... Launch site: ..." line
        time_desc, site = "", ""
        j = i + 1
        search_limit = min(n, i + 6)
        while j < search_limit:
            tm = TIME_LINE_RE.match(lines[j])
            if tm:
                time_desc = tm.group("time_desc").strip()
                site = tm.group("site").strip()
                break
            j += 1

        # Look ahead further for the "Updated:" marker and the date after it
        updated_text = None
        k = j
        search_limit2 = min(n, j + 15)
        while k < search_limit2 - 1:
            if UPDATED_RE.match(lines[k]):
                updated_text = lines[k + 1].strip()
                break
            k += 1

        launch_time_utc = _resolve_utc_datetime(date_text, time_desc, now)

        launches.append(
            Launch(
                uid=_make_uid(rocket, mission),
                rocket=rocket,
                mission=mission,
                is_net=is_net,
                date_text=date_text,
                site=site,
                time_description=time_desc,
                launch_time_utc=launch_time_utc,
                updated_text=updated_text,
            )
        )
        i += 1

    return launches


def get_upcoming_launches(now: Optional[dt.datetime] = None) -> list[Launch]:
    html = fetch_html()
    return parse_schedule(html, now=now)


if __name__ == "__main__":
    # Quick manual test: `python scraper.py` prints what it parsed.
    results = get_upcoming_launches()
    print(f"Parsed {len(results)} launch entries:\n", file=sys.stderr)
    for l in results:
        print(
            f"- [{'NET ' if l.is_net else ''}{l.date_text}] {l.rocket} • {l.mission} "
            f"-> {l.launch_time_utc or 'TBD'}  ({l.site})"
        )
