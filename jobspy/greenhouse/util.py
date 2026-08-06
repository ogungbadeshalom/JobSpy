"""Greenhouse board scraper utilities."""

from __future__ import annotations

import re
from typing import Optional

from jobspy.model import Location, Compensation
from jobspy.util import create_logger

log = create_logger("Greenhouse")


def parse_location(location_name: Optional[str]) -> Location:
    """
    Converts a Greenhouse location string like 'San Francisco, CA' or
    'Remote - US' or 'New York, New York' into a Location model.
    Greenhouse often returns 'Remote', 'Hybrid - City' or 'City, State'.
    """
    if not location_name:
        return Location(country="US")

    text = location_name.strip()
    is_remote = any(
        k in text.lower() for k in ("remote", "wfh", "work from home", "anywhere")
    )
    city = None
    state = None
    country = "US"

    # Handle "Remote - X" : strip remote prefix
    if is_remote:
        country = "Remote"
        # e.g. 'Remote - US' -> country US
        m = re.search(r"-\s*([A-Za-z ]+)$", text)
        if m:
            country = m.group(1).strip()
        return Location(city="Remote", state="", country=country)

    # Handle "City, State" or "City, State, Country"
    parts = [p.strip() for p in text.split(",") if p.strip()]
    if len(parts) >= 2:
        city = parts[0]
        state = parts[1]
    elif len(parts) == 1:
        city = parts[0]

    return Location(city=city, state=state or None, country=country)


def parse_compensation(raw: str) -> Optional[Compensation]:
    """
    Greenhouse compensation comes in text form, e.g.
    '190,000 - 240,000 USD' or '$100k - $150k'.
    This is a best-effort parser; returns None when not parseable.
    """
    if not raw:
        return None
    text = raw.strip()
    currency = "USD"
    m = re.search(r"([A-Z]{3})", text)
    if m:
        currency = m.group(1)

    # Extract numeric ranges, handling commas, k or 'k, and currency symbols
    nums = re.findall(r"[\d.,]\d*", text.replace(",", ""))
    if not nums:
        return None
    if len(nums) >= 2:
        # Could be two numbers => range
        lo, hi = float(nums[0]), float(nums[1])
    else:
        lo = hi = float(nums[0])

    # 'k' suffix detection
    interval = "YEARLY"
    if "000" in text.lower():
        # already yearly
        pass
    elif "k" in text.lower() or "K" in text:
        lo, hi = lo * 1000, hi * 1000
    elif "hour" in text.lower() or "ph" in text.lower():
        interval = "HOURLY"

    from jobspy.model import CompensationInterval

    try:
        interval_enum = CompensationInterval(interval.lower())
    except ValueError:
        interval_enum = None
    return Compensation(
        min_amount=int(lo),
        max_amount=int(hi),
        currency=currency,
        interval=(interval_enum if interval_enum else CompensationInterval.YEARLY),
    )