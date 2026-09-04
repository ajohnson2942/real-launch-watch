"""Solar-geometry predictor for rocket-launch 'space jellyfish' twilight plumes.

This module intentionally predicts lighting geometry, not weather. A favorable
prediction means the observer-level sky is dark enough for contrast while a
representative high-altitude Falcon plume can still be sunlit. Clouds, haze,
mission trajectory, staging, and observer location can still change what is
actually visible.
"""

from __future__ import annotations

import datetime as dt
import math
from zoneinfo import ZoneInfo

EARTH_RADIUS_KM = 6371.0
SUNSET_ALTITUDE_DEG = -0.833
JELLYFISH_BRIGHT_SKY_LIMIT_DEG = -5.0
JELLYFISH_DEEP_TWILIGHT_LIMIT_DEG = -12.5
MAX_USEFUL_PLUME_SHADOW_HEIGHT_KM = 160.0

SITE_REFERENCE = {
    "CA": {
        "name": "Vandenberg Space Force Base",
        "latitude": 34.742,
        "longitude": -120.572,
        "timezone": "America/Los_Angeles",
    },
    "FL": {
        "name": "Cape Canaveral / Kennedy Space Center",
        "latitude": 28.4889,
        "longitude": -80.5778,
        "timezone": "America/New_York",
    },
}


def _parse_utc(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def _julian_day(value: dt.datetime) -> float:
    value = value.astimezone(dt.timezone.utc)
    year = value.year
    month = value.month
    day = value.day + (
        value.hour
        + (value.minute + (value.second + value.microsecond / 1_000_000) / 60) / 60
    ) / 24

    if month <= 2:
        year -= 1
        month += 12

    a = year // 100
    b = 2 - a + a // 4

    return (
        math.floor(365.25 * (year + 4716))
        + math.floor(30.6001 * (month + 1))
        + day
        + b
        - 1524.5
    )


def solar_elevation_deg(
    when_utc: dt.datetime,
    latitude_deg: float,
    longitude_deg: float,
) -> float:
    """Approximate apparent solar-center elevation in degrees."""

    jd = _julian_day(when_utc)
    n = jd - 2451545.0

    mean_longitude = (280.460 + 0.9856474 * n) % 360.0
    mean_anomaly = math.radians((357.528 + 0.9856003 * n) % 360.0)
    ecliptic_longitude = math.radians(
        (
            mean_longitude
            + 1.915 * math.sin(mean_anomaly)
            + 0.020 * math.sin(2 * mean_anomaly)
        )
        % 360.0
    )
    obliquity = math.radians(23.439 - 0.0000004 * n)

    right_ascension_deg = math.degrees(
        math.atan2(
            math.cos(obliquity) * math.sin(ecliptic_longitude),
            math.cos(ecliptic_longitude),
        )
    ) % 360.0

    declination = math.asin(
        math.sin(obliquity) * math.sin(ecliptic_longitude)
    )

    gmst_deg = (
        280.46061837
        + 360.98564736629 * (jd - 2451545.0)
    ) % 360.0

    hour_angle_deg = (
        (gmst_deg + longitude_deg - right_ascension_deg + 180.0) % 360.0
    ) - 180.0

    latitude = math.radians(latitude_deg)
    hour_angle = math.radians(hour_angle_deg)

    elevation = math.asin(
        math.sin(latitude) * math.sin(declination)
        + math.cos(latitude) * math.cos(declination) * math.cos(hour_angle)
    )

    return math.degrees(elevation)


def _solar_crossing(
    local_date: dt.date,
    latitude_deg: float,
    longitude_deg: float,
    timezone: ZoneInfo,
    target_altitude_deg: float,
    *,
    rising: bool,
) -> dt.datetime | None:
    """Find a solar-altitude crossing on a local calendar date."""

    local_start = dt.datetime.combine(local_date, dt.time.min, tzinfo=timezone)
    local_end = dt.datetime.combine(
        local_date + dt.timedelta(days=1),
        dt.time.min,
        tzinfo=timezone,
    )

    start_utc = local_start.astimezone(dt.timezone.utc)
    end_utc = local_end.astimezone(dt.timezone.utc)

    step = dt.timedelta(minutes=4)
    previous_time = start_utc
    previous_delta = (
        solar_elevation_deg(previous_time, latitude_deg, longitude_deg)
        - target_altitude_deg
    )

    current_time = previous_time + step

    while current_time <= end_utc:
        current_delta = (
            solar_elevation_deg(current_time, latitude_deg, longitude_deg)
            - target_altitude_deg
        )

        crossed = (
            previous_delta < 0 <= current_delta
            if rising
            else previous_delta > 0 >= current_delta
        )

        if crossed:
            low = previous_time
            high = current_time

            for _ in range(32):
                middle = low + (high - low) / 2
                middle_delta = (
                    solar_elevation_deg(middle, latitude_deg, longitude_deg)
                    - target_altitude_deg
                )

                if rising:
                    if middle_delta < 0:
                        low = middle
                    else:
                        high = middle
                else:
                    if middle_delta > 0:
                        low = middle
                    else:
                        high = middle

            return high.astimezone(dt.timezone.utc)

        previous_time = current_time
        previous_delta = current_delta
        current_time += step

    return None


def _shadow_height_km(solar_elevation: float) -> float:
    """Approximate local vertical height needed to clear Earth's shadow."""

    if solar_elevation >= 0:
        return 0.0

    depression = math.radians(abs(solar_elevation))
    cosine = max(0.01, math.cos(depression))
    return EARTH_RADIUS_KM * ((1.0 / cosine) - 1.0)


def _iso(value: dt.datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(dt.timezone.utc).isoformat()


def _minutes_between(
    later: dt.datetime,
    earlier: dt.datetime | None,
) -> float | None:
    if earlier is None:
        return None
    return (later - earlier).total_seconds() / 60.0


def predict_launch(
    launch_time_utc: str | None,
    location_code: str | None,
) -> dict:
    """Return a JSON-serializable jellyfish lighting prediction."""

    base = {
        "likely": False,
        "confidence": "unavailable",
        "phase": None,
        "reference_location": None,
        "solar_elevation_deg": None,
        "required_sunlit_altitude_km": None,
        "sunset_utc": None,
        "sunrise_utc": None,
        "minutes_after_sunset": None,
        "minutes_before_sunrise": None,
        "window_start_utc": None,
        "window_end_utc": None,
        "reason": None,
        "model": "solar-geometry-v1",
    }

    site = SITE_REFERENCE.get(location_code or "")
    launch_dt = _parse_utc(launch_time_utc)

    if site is None:
        base["reason"] = (
            "Jellyfish prediction is currently limited to California and Florida launch sites."
        )
        return base

    base["reference_location"] = site["name"]

    if launch_dt is None:
        base["reason"] = (
            "Exact liftoff time is required before twilight lighting can be predicted."
        )
        return base

    timezone = ZoneInfo(site["timezone"])
    local_date = launch_dt.astimezone(timezone).date()
    latitude = float(site["latitude"])
    longitude = float(site["longitude"])

    sunset = _solar_crossing(
        local_date,
        latitude,
        longitude,
        timezone,
        SUNSET_ALTITUDE_DEG,
        rising=False,
    )
    sunrise = _solar_crossing(
        local_date,
        latitude,
        longitude,
        timezone,
        SUNSET_ALTITUDE_DEG,
        rising=True,
    )

    evening_start = _solar_crossing(
        local_date,
        latitude,
        longitude,
        timezone,
        JELLYFISH_BRIGHT_SKY_LIMIT_DEG,
        rising=False,
    )
    evening_end = _solar_crossing(
        local_date,
        latitude,
        longitude,
        timezone,
        JELLYFISH_DEEP_TWILIGHT_LIMIT_DEG,
        rising=False,
    )
    morning_start = _solar_crossing(
        local_date,
        latitude,
        longitude,
        timezone,
        JELLYFISH_DEEP_TWILIGHT_LIMIT_DEG,
        rising=True,
    )
    morning_end = _solar_crossing(
        local_date,
        latitude,
        longitude,
        timezone,
        JELLYFISH_BRIGHT_SKY_LIMIT_DEG,
        rising=True,
    )

    solar_elevation = solar_elevation_deg(
        launch_dt,
        latitude,
        longitude,
    )
    shadow_height = _shadow_height_km(solar_elevation)

    base.update(
        {
            "solar_elevation_deg": round(solar_elevation, 2),
            "required_sunlit_altitude_km": round(shadow_height, 1),
            "sunset_utc": _iso(sunset),
            "sunrise_utc": _iso(sunrise),
            "minutes_after_sunset": (
                round(_minutes_between(launch_dt, sunset), 1)
                if sunset and launch_dt >= sunset
                else None
            ),
            "minutes_before_sunrise": (
                round(_minutes_between(sunrise, launch_dt), 1)
                if sunrise and launch_dt <= sunrise
                else None
            ),
        }
    )

    evening_likely = bool(
        evening_start
        and evening_end
        and evening_start <= launch_dt <= evening_end
    )
    morning_likely = bool(
        morning_start
        and morning_end
        and morning_start <= launch_dt <= morning_end
    )

    if evening_likely:
        base.update(
            {
                "likely": True,
                "confidence": "strong",
                "phase": "evening",
                "window_start_utc": _iso(evening_start),
                "window_end_utc": _iso(evening_end),
                "reason": (
                    "Liftoff falls inside the strong evening twilight window: "
                    "the ground-level sky is dark enough for contrast while a "
                    "typical high-altitude ascent plume can remain sunlit."
                ),
            }
        )
        return base

    if morning_likely:
        base.update(
            {
                "likely": True,
                "confidence": "strong",
                "phase": "morning",
                "window_start_utc": _iso(morning_start),
                "window_end_utc": _iso(morning_end),
                "reason": (
                    "Liftoff falls inside the strong pre-sunrise twilight window: "
                    "the observer-level sky is still dark while a typical high-altitude "
                    "ascent plume can already be illuminated by the Sun."
                ),
            }
        )
        return base

    if solar_elevation > JELLYFISH_BRIGHT_SKY_LIMIT_DEG:
        base["reason"] = (
            "The sky is expected to be too bright for a strong twilight-plume contrast."
        )
    elif shadow_height > MAX_USEFUL_PLUME_SHADOW_HEIGHT_KM:
        base["reason"] = (
            "The Sun is too far below the horizon for the strongest ascent plume to remain sunlit."
        )
    else:
        base["reason"] = "The launch time is outside the model's strong twilight window."

    return base
