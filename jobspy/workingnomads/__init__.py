"""WorkingNomads board scraper for JobSpy.

WorkingNomads is a curated remote-first job board. The frontend loads its jobs
from a public JSON endpoint (/api/exposed_jobs) — no key, no anti-bot, clean
structured data. Returns a flat JSON array:
  [ { url, title, description, company_name, category_name, tags, location,
      pub_date } ]
"""

from __future__ import annotations

from datetime import datetime

from jobspy.workingnomads.constant import API_URL
from jobspy.model import (
    JobPost,
    JobResponse,
    Location,
    Scraper,
    ScraperInput,
    Site,
)
from jobspy.remote import contains as remote_contains
from jobspy.util import create_session, create_logger, extract_emails_from_text

log = create_logger("WorkingNomads")


class WorkingNomads(Scraper):
    def __init__(self, proxies=None, ca_cert=None, user_agent=None):
        super().__init__(
            Site(Site.WORKINGNOMADS), proxies=proxies, ca_cert=ca_cert, user_agent=user_agent
        )
        self.session = None

    def scrape(self, scraper_input: ScraperInput) -> JobResponse:
        self.session = create_session(
            proxies=self.proxies, ca_cert=self.ca_cert, is_tls=False, has_retry=True
        )
        try:
            resp = self.session.get(API_URL, timeout=scraper_input.request_timeout)
        except Exception as e:
            log.warning(f"WorkingNomads request failed: {e}")
            return JobResponse(jobs=[])
        if resp.status_code != 200:
            log.warning(f"WorkingNomads HTTP {resp.status_code}")
            return JobResponse(jobs=[])

        try:
            data = resp.json()
        except Exception:
            return JobResponse(jobs=[])

        if not isinstance(data, list):
            return JobResponse(jobs=[])

        jobs: list[JobPost] = []
        for raw in data:
            job = self._process_job(raw)
            if job and self._matches_term(job, scraper_input.search_term):
                jobs.append(job)
            if len(jobs) >= scraper_input.results_wanted:
                break

        return JobResponse(jobs=jobs[: scraper_input.results_wanted])

    def _process_job(self, raw: dict) -> JobPost | None:
        title = raw.get("title")
        if not title:
            return None

        loc_text = str(raw.get("location") or "")
        city = "Remote" if (not loc_text or "remote" in loc_text.lower()) else loc_text.split(",")[0].strip()
        location = Location(city=city, state="", country="")

        job_url = raw.get("url")
        if not job_url:
            job_url = ""

        date_posted = None
        pd = raw.get("pub_date")
        if pd:
            try:
                date_posted = datetime.fromisoformat(str(pd).replace("Z", "+00:00"))
            except Exception:
                date_posted = None

        import re
        description = re.sub(r"<[^>]+>", " ", str(raw.get("description") or ""))
        description = re.sub(r"\s+", " ", description).strip()[:2000]

        tags = raw.get("tags")
        if isinstance(tags, list):
            tags_text = ", ".join(str(t) for t in tags)
        else:
            tags_text = str(tags or "")

        return JobPost(
            id=None,
            title=title,
            company_name=raw.get("company_name") or None,
            location=location,
            job_url=job_url,
            date_posted=date_posted.date() if date_posted else None,
            description=f"{description} {tags_text}".strip(),
            is_remote=True,  # workingnomads is a remote-only board
            listing_type="WorkingNomads",
            emails=extract_emails_from_text(description),
        )

    def _matches_term(self, job: JobPost, term: str | None) -> bool:
        if not term:
            return True
        return term.lower() in f"{job.title} {job.description or ''}".lower()