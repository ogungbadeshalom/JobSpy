"""SmartRecruiters boards scraper for JobSpy.

Scrapes public job postings from SmartRecruiters' public API:
    GET https://api.smartrecruiters.com/v1/companies/{company}/postings

Like Greenhouse, a SmartRecruiters "company" maps to an employer. Many
companies do NOT public-post via the API (they must opt in), so the scraper
scans a curated list and filters by the caller's search term. The `location`
field can override which single company to scan (one word = company id).
"""

from __future__ import annotations

import re
from datetime import datetime

import requests

from jobspy.smartrecruiters.constant import API_BASE, DEFAULT_COMPANIES
from jobspy.model import (
    JobPost,
    JobResponse,
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

log = create_logger("SmartRecruiters")


class SmartRecruiters(Scraper):
    """Scraper for SmartRecruiters ATS job boards."""

    def __init__(
        self,
        proxies=None,
        ca_cert=None,
        user_agent=None,
    ):
        super().__init__(
            Site(Site.SMART_RECRUITERS), proxies=proxies, ca_cert=ca_cert, user_agent=user_agent
        )
        self.session = None
        self.scraper_input = None

    def scrape(self, scraper_input: ScraperInput) -> JobResponse:
        self.scraper_input = scraper_input
        self.session = create_session(
            proxies=self.proxies, ca_cert=self.ca_cert, has_retry=True, is_tls=False
        )

        companies = self._resolve_companies(scraper_input)
        all_jobs: list[JobPost] = []
        for company in companies:
            jobs = self._scrape_company(company, scraper_input)
            all_jobs.extend(jobs)
            if len(all_jobs) >= scraper_input.results_wanted:
                break

        # dedupe by URL
        seen = set()
        unique = []
        for j in all_jobs:
            if j.job_url not in seen:
                seen.add(j.job_url)
                unique.append(j)

        return JobResponse(jobs=unique[: scraper_input.results_wanted])

    def _resolve_companies(self, scraper_input: ScraperInput) -> list[str]:
        companies = DEFAULT_COMPANIES
        location = (scraper_input.location or "").strip()
        # Ignore generic/geo-ish locations (the JobBidder app sends 'Remote' or
        # 'United States' by default) — only a single bare word that isn't a
        # location keyword maps to a company slug override.
        _non_company = {"remote", "united states", "usa", "us", "any", ""}
        if (location and len(location.split()) == 1 and location.lower() not in _non_company):
            slug = re.sub(r"[^a-z0-9-]", "", location.lower())
            if slug:
                companies = [slug]
        return companies

    def _scrape_company(self, company: str, si: ScraperInput) -> list[JobPost]:
        url = API_BASE.format(company=company)
        params = {"limit": 100}
        try:
            resp = self.session.get(url, params=params, timeout=si.request_timeout)
        except requests.exceptions.RequestException as e:
            log.warning(f"SmartRecruiters '{company}': {e}")
            return []
        if resp.status_code != 200:
            log.warning(f"SmartRecruiters '{company}': HTTP {resp.status_code}")
            return []

        try:
            data = resp.json()
        except Exception:
            return []

        jobs = []
        for raw in data.get("content", []):
            job = self._process_job(raw, company)
            if job and self._matches_term(job, si.search_term):
                jobs.append(job)
        return jobs

    def _process_job(self, raw: dict, company: str) -> JobPost | None:
        title = raw.get("name")
        if not title:
            return None

        job_id = raw.get("id")
        loc_raw = raw.get("location") or {}
        city = loc_raw.get("city") or ""
        country = loc_raw.get("country") or ""
        loc_name = ", ".join(x for x in (city, country) if x)
        location = None
        if loc_name:
            location = Location(country=country, city=city)

        job_url = raw.get("ref") or raw.get("url")
        if not job_url or job_url.startswith("http://api") or "api.smartrecruiters" in (job_url or ""):
            job_url = f"https://jobs.smartrecruiters.com/{company}/{job_id}"

        content = raw.get("jobAd") or {}
        plain = re.sub(r"<[^>]+>", " ", content.get("sections", {}).get("jobDescription", {}).get("text", ""))
        plain = re.sub(r"\s+", " ", plain).strip()

        date_posted = None
        released = raw.get("releasedDate")
        if released:
            try:
                date_posted = datetime.fromisoformat(released.replace("Z", "+00:00"))
            except Exception:
                date_posted = None

        # SmartRecruiters list endpoint doesn't include the full job ad, so
        # job_type is derived from the department/function labels when the ad
        # body is empty — fall back to None (no job_type) otherwise.
        _jt = job_type_from_description(plain) or job_type_from_category(
            (raw.get("department") or {}).get("label", "") + " " +
            (raw.get("function") or {}).get("label", "")
        )
        return JobPost(
            id=str(job_id or ""),
            title=title,
            company_name=raw.get("company", {}).get("name") or company.capitalize(),
            location=location,
            job_url=job_url,
            date_posted=date_posted.date() if date_posted else None,
            description=plain,
            is_remote=remote_contains(title, plain, loc_name),
            listing_type="SmartRecruiters",
            emails=extract_emails_from_text(plain),
            job_type=[_jt] if _jt else None,
        )

    def _matches_term(self, job: JobPost, term: str | None) -> bool:
        if not term:
            return True
        hay = f"{job.title} {job.description or ''}".lower()
        return term.lower() in hay


def job_type_from_category(category: str):
    """Infer job type from department/function labels (no ad body available)."""
    from jobspy.model import JobType

    low = (category or "").lower()
    if any(k in low for k in ("intern", "trainee", "apprentice")):
        return JobType.INTERNSHIP
    if any(k in low for k in ("contract", "temporary", "temp", "part-time", "part time")):
        return JobType.CONTRACT
    # engineering/tech categories default to full-time unless otherwise marked
    if any(k in low for k in ("engineering", "software", "developer", "full-time", "full time")):
        return JobType.FULL_TIME
    return None


def job_type_from_description(description: str):
    from jobspy.model import JobType

    low = (description or "").lower()
    if any(k in low for k in ("full time", "full-time", "fulltime")):
        return JobType.FULL_TIME
    if any(k in low for k in ("part time", "part-time")):
        return JobType.PART_TIME
    if "contract" in low:
        return JobType.CONTRACT
    if any(k in low for k in ("intern", "internship", "trainee")):
        return JobType.INTERNSHIP
    if "temporary" in low:
        return JobType.TEMPORARY
    return None