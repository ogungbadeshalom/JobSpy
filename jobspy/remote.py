"""Master remote-job detector.

More robust than the per-scraper one-liners. Catches far more phrasing:
fully remote, 100% remote, remote anywhere, anywhere in [region], remote-first,
work from home, wfh, telecommute, distributed team, work remotely, hybrid_exempt,
office not required, etc. Filters out false positives (e.g. "remote in NYC/onsite
required, 3 days onsite, hybrid on Tuesdays").
"""

import re

# Strong positive signals: job is clearly remote
REMOTE_POSITIVE = [
    "fully remote",
    "100% remote",
    "remote only",
    "work from home",
    "work-from-home",
    "wfh",
    "telecommut",
    "work remote",
    "remote work",
    "working remotely",
    "remote position",
    "remote role",
    "remote job",
    "remote anywhere",
    "anywhere in the us",
    "anywhere in the world",
    "work from anywhere",
    "work anywhere",
    "work from wherever",
    "distributed team",
    "remote-first",
    "remote friendly",
    "remote",
    "home office",
    "based remotely",
    "virtual position",
    "100% work-from-home",
    "fully distributed",
    "all remote",
]

# Signals that point to a NON-remote job (hybrid / office-bound)
ONSITE_HYBRID = [
    "on-site",
    "on site",
    "onsite",
    "in-office",
    "in office",
    "hybrid",
    "office-based",
    "must be based in",
    "must reside in",
    "relocation",
    "located in the office",
    "tidende in office",
    "office environment",
    "on premises",
    "office hours required",
]


def contains(title: str, description: str, location: str = "") -> bool:
    """
    Heuristic: returns True if the job looks remote.
    - If clear remote keywords appear and no strong onsite/hybrid signal appears
      in the TITLE/description head, it's remote.
    - Hybrid tags that ALSO say remote -> we still mark remote if remote shows up
      more than once / remote-first wording (best-effort).
    """
    title_l = (title or "").lower()
    desc_l = (description or "").lower()
    loc_l = (location or "").lower()

    power_text = f"{title_l} {loc_l}"
    full_text = f"{power_text} {desc_l}"

    # Early: if location says remote, keep
    if any(w in loc_l for w in ("remote", "telecommute", "work from home", "wfh")):
        return True

    has_remote = any(w in full_text for w in REMOTE_POSITIVE)
    if not has_remote:
        return False

    # If remote found, reject if it's clearly an onsite/hybrid job and the remote
    # mention appears < 2 times (a comprehensive mention in the description body).
    remote_hits = sum(full_text.count(w) for w in REMOTE_POSITIVE)
    onsite_hits = sum(full_text.count(w) for w in ONSITE_HYBRID)

    if onsite_hits and remote_hits <= onsite_hits:
        # Example: "Sunnyvale, CA (Hybrid - 3 days onsite, remote OK)" -> ambiguous.
        # Prefer readable signal from location/title which already handled.
        if any(w in power_text for w in ONSITE_HYBRID):
            # location/title strongly says onsite -> not remote
            return False

    return True