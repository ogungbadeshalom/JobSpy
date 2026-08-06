"""Greenhouse boards scraper for JobSpy.

Scrapes job postings from Greenhouse's public Boards API. This is a
first-class ATS integration — no browser, no CAPTCHAs, just clean JSON.

The Greenhouse API:
    GET https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true

Because a Greenhouse "board" maps to a company, the `location` field is
re-interpreted as an override for WHICH boards to scan when the caller is
looking for a keyword. A dedicated list of boards is scanned and jobs
filtered by the caller's search term.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from typing import Tuple

import requests

from jobspy.greenhouse.constant import API_BASE, DEFAULT_BOARDS
from jobspy.greenhouse.util import parse_location, parse_compensation
from jobspy.model import (
    JobPost,
    JobResponse,
    JobType,
    Location,
    Scraper,
    ScraperInput,
    Site,
)
from jobspy.util import (
    create_session,
    create_logger,
    extract_emails_from_text,
)
from jobspy.remote import contains as remote_contains

log = create_logger("Greenhouse")


class Greenhouse(Scraper):
    """Scraper for Greenhouse ATS job boards."""

    def __init__(
        self,
        proxies: list[str] | str | None = None,
        ca_cert: str | None = None,
        user_agent: str | None = None,
    ):
        super().__init__(
            Site(Site.GREENHOUSE), proxies=proxies, ca_cert=ca_cert, user_agent=user_agent
        )
        self.session = None
        self.scraper_input = None
        self.seen_urls = set()

    def scrape(self, scraper_input: ScraperInput) -> JobResponse:
        self.scraper_input = scraper_input
        self.session = create_session(
            proxies=self.proxies, ca_cert=self.ca_cert, has_retry=True, is_tls=False
        )

        # Board selection: user-supplied search term filters inside the boards.
        all_jobs: list[JobPost] = []
        boards = self._resolve_boards(scraper_input)

        for board in boards:
            jobs = self._scrape_board(board, scraper_input)
            all_jobs.extend(jobs)
            if len(all_jobs) >= scraper_input.results_wanted:
                break

        # Deduplicate by URL
        seen = set()
        unique = []
        for j in all_jobs:
            if j.job_url not in seen:
                seen.add(j.job_url)
                unique.append(j)

        return JobResponse(jobs=unique[: scraper_input.results_wanted])

    def _resolve_boards(self, scraper_input: ScraperInput) -> list[str]:
        """
        If the caller's 'location' is a known company name, use that board.
        Otherwise use the default scan set.
        """
        boards = DEFAULT_BOARDS
        location = (scraper_input.location or "").strip()
        if location:
            slug = re.sub(r"[^a-z0-9]", "", location.lower())
            # if it's a single word it's likely a company board name
            if slug and len(location.split()) == 1:
                boards = [slug]
        return boards

    def _scrape_board(self, board: str, si: ScraperInput) -> list[JobPost]:
        url = API_BASE.format(board=board)
        params = {"content": "true"}
        try:
            resp = self.session.get(url, params=params, timeout=si.request_timeout)
        except requests.exceptions.RequestException as e:
            log.warning(f"Greenhouse board '{board}': {e}")
            return []
        if resp.status_code != 200:
            log.warning(f"Greenhouse board '{board}': HTTP {resp.status_code}")
            return []

        try:
            data = resp.json()
        except Exception:
            return []

        jobs = []
        for raw in data.get("jobs", []):
            job = self._process_job(raw, board)
            if job and self._matches_term(job, si.search_term):
                jobs.append(job)
        return jobs

    def _process_job(self, raw: dict, board: str) -> JobPost | None:
        job_id = raw.get("id")
        title = raw.get("title")
        if not title:
            return None

        loc_raw = raw.get("location") or {}
        loc_name = loc_raw.get("name") or ""
        # greenhouse 'location' sometimes under 'offices'
        offices = raw.get("offices") or []
        if offices and not loc_name:
            loc_name = "; ".join(o.get("location") or o.get("name") or "" for o in offices)

        location = parse_location(loc_name)

        job_url = raw.get("absolute_url") or f"https://boards.greenhouse.io/{board}/jobs/{job_id}"
        if job_url.startswith("/"):
            job_url = f"https://boards.greenhouse.io{job_url}"

        content = raw.get("content") or ""
        # strip HTML tags to plain text for description
        plain = re.sub(r"<[^>]+>", " ", content)
        plain = re.sub(r"\s+", " ", plain).strip()

        date_posted = None
        import re as _re
        updated = raw.get("updated_at")
        if updated:
            try:
                date_posted = datetime.fromisoformat(updated.replace("Z", "+00:00"))
            except Exception:
                try:
                    date_posted = datetime.fromisoformat(updated)
                except Exception:
                    date_posted = None
        else:
            # fall back: use board first_seen as a rough estimate? omit
            pass

        job_type = extract_job_type(plain)
        comp_raw = raw.get("compensation") or raw.get("salary")
        compensation = parse_compensation(str(comp_raw)) if comp_raw else None

        return JobPost(
            id=str(job_id),
            title=title,
            company_name=self._company_name(raw, board),
            location=location,
            job_url=job_url,
            date_posted=self._as_date(date_posted),
            description=plain,
            job_type=job_type,
            compensation=compensation,
            is_remote=remote_contains(title, plain, loc_name),
            listing_type="Greenhouse",
            emails=extract_emails_from_text(plain),
        )

    def _matches_term(self, job: JobPost, term: str | None) -> bool:
        if not term:
            return True
        hay = f"{job.title} {job.description or ''}".lower()
        return term.lower() in hay

    def _company_name(self, raw: dict, board: str) -> str:
        return raw.get("company_name") or board.capitalize() or board

    @staticmethod
    def _as_date(d):
        from datetime import date

        if not d:
            return None
        return d.date() if isinstance(d, datetime) else d


def extract_job_type(description: str) -> list[JobType] | None:
    """
    Best-effort job type detection from the description text.
    """
    low = (description or "").lower()
    mapping = {
        JobType.FULL_TIME: ["full time", "full-time", "fulltime"],
        JobType.PART_TIME: ["part time", "part-time", "parttime"],
        JobType.CONTRACT: ["contract", "contractor"],
        JobType.INTERNSHIP: ["intern", "internship", "trainee", "graduate"],
        JobType.TEMPORARY: ["temporary"],
    }
    found = []
    for jt, kws in mapping.items():
        if any(kw in low for kw in kws):
            found.append(jt)
    return found or None