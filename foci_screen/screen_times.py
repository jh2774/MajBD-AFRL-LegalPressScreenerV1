"""When scheduled screens run: a few set times a day, in the site's own time.

Watchlists re-screen, and a watch on a whole office catches up, only in the
hour after each time in FOCI_SCREEN_TIMES (midnight and noon by default),
in FOCI_TIMEZONE (US Eastern by default). The daily workflow asks every hour;
outside those hours the site says so and does nothing. A screen a person asks
for — "Screen", "Run now" — still runs when they ask.

A whole office's daily runs (FOCI_SCOPE_RUNS_PER_DAY) are shared out across
the day's times, so the morning does not use them all.

Time zones: the standard library's database where the host has one, and the
US zones' own rules — daylight time from the second Sunday of March to the
first Sunday of November — where it does not, which is the case on some
hosts and on Windows. No package to install either way.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

DEFAULT_TIMES = "00:00,12:00"
DEFAULT_ZONE = "America/New_York"
# Screens run from each time until this many minutes after it.
WINDOW_MINUTES = 59

# Standard offset from UTC in hours, and whether daylight time applies.
_US_ZONES = {
    "america/new_york": (-5, True, "Eastern"), "us/eastern": (-5, True, "Eastern"),
    "eastern": (-5, True, "Eastern"), "et": (-5, True, "Eastern"),
    "america/chicago": (-6, True, "Central"), "us/central": (-6, True, "Central"),
    "central": (-6, True, "Central"), "ct": (-6, True, "Central"),
    "america/denver": (-7, True, "Mountain"), "us/mountain": (-7, True, "Mountain"),
    "mountain": (-7, True, "Mountain"), "mt": (-7, True, "Mountain"),
    "america/phoenix": (-7, False, "Arizona"),
    "america/los_angeles": (-8, True, "Pacific"), "us/pacific": (-8, True, "Pacific"),
    "pacific": (-8, True, "Pacific"), "pt": (-8, True, "Pacific"),
    "utc": (0, False, "UTC"), "gmt": (0, False, "UTC"),
}
ALWAYS = ("", "any", "always", "anytime", "off")


class ScreenTimesError(ValueError):
    """A setting that cannot be read, with a reason a person can act on."""


def parse_times(raw: str | None) -> list[tuple[int, int]] | None:
    """[(0, 0), (12, 0)] from "00:00,12:00" — or "12am, 12pm", or "9am, 1pm, 5pm".
    None when screens may run at any time ("any")."""
    text = (DEFAULT_TIMES if raw is None else raw).strip().lower()
    if text in ALWAYS:
        return None
    out: list[tuple[int, int]] = []
    for part in text.replace(";", ",").split(","):
        part = part.strip().replace(" ", "")
        if not part:
            continue
        suffix = ""
        if part.endswith(("am", "pm")):
            part, suffix = part[:-2], part[-2:]
        hour_text, _, minute_text = part.partition(":")
        try:
            hour, minute = int(hour_text), int(minute_text or 0)
        except ValueError:
            raise ScreenTimesError(
                f"Not a time: {part!r}. Write times like 09:00,13:00,17:00.") from None
        if suffix:
            if not 1 <= hour <= 12:
                raise ScreenTimesError(f"Not a time: {part}{suffix}.")
            hour = hour % 12 + (12 if suffix == "pm" else 0)
        if not (0 <= hour < 24 and 0 <= minute < 60):
            raise ScreenTimesError(f"Not a time: {part}{suffix}.")
        out.append((hour, minute))
    if not out:
        return None
    return sorted(set(out))


def _nth_sunday(year: int, month: int, n: int) -> int:
    first = datetime(year, month, 1)
    return 1 + (6 - first.weekday()) % 7 + 7 * (n - 1)


def _us_offset(now_utc: datetime, standard: int, dst: bool) -> timedelta:
    std = timedelta(hours=standard)
    if not dst:
        return std
    year = now_utc.year
    # 2:00 local standard time on the second Sunday of March, to 2:00 local
    # daylight time on the first Sunday of November.
    starts = datetime(year, 3, _nth_sunday(year, 3, 2), 2, tzinfo=timezone.utc) - std
    ends = (datetime(year, 11, _nth_sunday(year, 11, 1), 2, tzinfo=timezone.utc)
            - std - timedelta(hours=1))
    return std + timedelta(hours=1) if starts <= now_utc < ends else std


def local_now(zone: str | None, now_utc: datetime | None = None) -> datetime:
    """The time now in `zone`, without tzinfo."""
    now_utc = now_utc or datetime.now(timezone.utc)
    name = (zone or DEFAULT_ZONE).strip()
    try:
        from zoneinfo import ZoneInfo
        return now_utc.astimezone(ZoneInfo(name)).replace(tzinfo=None)
    except Exception:              # noqa: BLE001 - no database here: the rules below
        pass
    rule = _US_ZONES.get(name.lower())
    if rule is None:
        raise ScreenTimesError(
            f"Not a time zone this site knows: {name!r}. Use one such as "
            f"America/New_York, America/Chicago, America/Denver or America/Los_Angeles.")
    return (now_utc + _us_offset(now_utc, rule[0], rule[1])).replace(tzinfo=None)


def zone_label(zone: str | None) -> str:
    name = (zone or DEFAULT_ZONE).strip()
    rule = _US_ZONES.get(name.lower())
    return f"{rule[2]} time" if rule else name


def slot(now: datetime, times: list[tuple[int, int]]) -> int | None:
    """Which of the day's times `now` falls in the hour after, or None."""
    minutes = now.hour * 60 + now.minute
    for i, (hour, minute) in enumerate(times):
        start = hour * 60 + minute
        if start <= minutes < start + WINDOW_MINUTES:
            return i
    return None


def runs_allowed(now: datetime, times: list[tuple[int, int]], per_day: int) -> int:
    """How many runs a whole office's watch may have made by now today: its
    daily runs, shared out across the times that have begun."""
    begun = sum(1 for hour, minute in times if (hour, minute) <= (now.hour, now.minute))
    return math.ceil(per_day * begun / len(times))


def clock(hour: int, minute: int) -> str:
    suffix = "am" if hour < 12 else "pm"
    return f"{(hour % 12) or 12}:{minute:02d} {suffix}"


def describe(times: list[tuple[int, int]] | None, zone: str | None) -> str:
    """"12:00 am and 12:00 pm Eastern time"."""
    if times is None:
        return "any time"
    words = [clock(h, m) for h, m in times]
    joined = words[0] if len(words) == 1 else ", ".join(words[:-1]) + " and " + words[-1]
    return f"{joined} {zone_label(zone)}"


def next_time(now: datetime, times: list[tuple[int, int]]) -> str:
    """When the next screening hour begins: "1:00 pm", or "9:00 am tomorrow"."""
    for hour, minute in times:
        if (hour, minute) > (now.hour, now.minute):
            return clock(hour, minute)
    return f"{clock(*times[0])} tomorrow"
