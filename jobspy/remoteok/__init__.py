"""RemoteOK board scraper for JobSpy.

RemoteOK is a remote-only job board with a free public JSON API:
    GET https://remoteok.com/api?tags=<topic>
Returns real structured jobs (position, company, salary, location, url,
apply_url, company_logo, date, tags). No browser / no CAPTCHA.
"""

from __future__ import annotations

from datetime import datetime, date
from urllib.parse import urlencode

from jobspy.model import (
    JobPost,
    JobResponse,
    Location,
    Scraper,
    ScraperInput,
    Site,
)
from jobspy.remote import contains as remote_contains
from jobspy.remoteok.constant import HEADERS
from jobspy.util import create_session, create_logger, extract_emails_from_text

log = create_logger("RemoteOK")

API_URL = "https://remoteok.com/api"


class RemoteOK(Scraper):
    def __init__(
        self,
        proxies: list[str] | str | None = None,
        ca_cert: str | None = None,
        user_agent: str | None = None,
    ):
        super().__init__(
            Site(Site.REMOTEOK), proxies=proxies, ca_cert=ca_cert, user_agent=user_agent
        )
        self.session = None

    def scrape(self, scraper_input: ScraperInput) -> JobResponse:
        self.session = create_session(
            proxies=self.proxies, ca_cert=self.ca_cert, is_tls=False, has_retry=True
        )
        url = self._build_url(scraper_input)
        try:
            resp = self.session.get(url, headers=HEADERS, timeout=scraper_input.request_timeout)
        except Exception as e:
            log.warning(f"RemoteOK request failed: {e}")
            return JobResponse(jobs=[])

        raw = resp.text
        # RemoteOK returns valid JSON but prepends repositories[] literals only
        # for /api?name=... They use a JS-invalid prefix. Trim to the array.
        start = raw.find("[")
        end = raw.rfind("]")
        if start == -1 or end == -1 or end <= start:
            log.warning("RemoteOK: no JSON array in response")
            return JobResponse(jobs=[])

        import json

        try:
            data = json.loads(raw[start : end + 1])
        except json.JSONDecodeError as e:
            log.warning(f"RemoteOK JSON parse failed: {e}")
            return JobResponse(jobs=[])

        # The first element is RemoteOK's "legal"/meta block; skip it.
        if data and isinstance(data[0], dict) and "position" not in data[0]:
            data = data[1:]

        jobs: list[JobPost] = []
        for item in data:
            job = self._process_job(item)
            if job and self._matches_term(job, scraper_input.search_term):
                jobs.append(job)
            if len(jobs) >= scraper_input.results_wanted:
                break

        return JobResponse(jobs=jobs)

    @staticmethod
    def _build_url(si: ScraperInput) -> str:
        # RemoteOK's API `tags` param only accepts a SINGLE recognized tag
        # (e.g. "python", "backend", "devops"). A multi-word search term like
        # "software engineer" isn't a valid tag and would return 0 results.
        # So: only use tags when the search term looks like a single token;
        # otherwise fetch unfiltered and let _matches_term filter client-side.
        if si.search_term and " " not in si.search_term.strip():
            return f"{API_URL}?{urlencode({'tags': si.search_term.strip()})}"
        return API_URL

    @staticmethod
    def _parse_date(value) -> date | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
        except ValueError:
            return None

    @staticmethod
    def _process_job(item: dict) -> JobPost | None:
        position = item.get("position") or item.get("title")
        if not position:
            return None
        # Reject junk / non-job entries RemoteOK sometimes returns (they have
        # no meaningful company + a boilerplate title). These aren't real jobs.
        title_l = position.lower()
        if not item.get("company") or any(
            k in title_l
            for k in ("no open roles", "why do you want", "open positions", "send us your cv", "we're always", "if you think you've got")
        ):
            return None
        company = item.get("company") or ""
        url = item.get("url") or item.get("apply_url") or item.get("slug") or ""
        url = str(url)
        if url.startswith("/"):
            url = "https://remoteok.com" + url

        loc_raw = str(item.get("location") or "Remote")
        parts = [p.strip() for p in loc_raw.split(",") if p.strip()]
        location = Location(city=parts[0] if parts else "Remote", country="Remote")
        if len(parts) > 1:
            location.state = parts[1]

        tags = item.get("tags") or []
        tags_str = ", ".join(tags) if isinstance(tags, list) else str(tags or "")

        return JobPost(
            id=str(item.get("id") or item.get("slug") or url),
            title=position,
            company_name=company,
            location=location,
            job_url=url,
            date_posted=RemoteOK._parse_date(item.get("date")),
            is_remote=True,  # RemoteOK is remote-only
            description=(item.get("description") or "")[:500] or tags_str,
            emails=extract_emails_from_text(f"{company} {position}"),
            listing_type="RemoteOK",
        )

    @staticmethod
    def _matches_term(job: JobPost, term: str | None) -> bool:
        if not term:
            return True
        hay = f"{job.title} {job.description or ''}".lower()
        return term.lower() in hay