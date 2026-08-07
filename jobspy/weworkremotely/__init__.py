"""WeWorkRemotely board scraper for JobSpy.

WeWorkRemotely (weworkremotely.com) is a remote-first job board. The /remote-jobs
directory is server-rendered HTML that parses cleanly with BeautifulSoup.

Card DOM (verified Aug 2026):
  a.listing-link--unlocked[href^="/remote-jobs/"] -> job URL
  .new-listing__header__title__text                -> job title
  .new-listing__company-name                       -> company name
  .new-listing__categories__category              -> tag / category
"""

from __future__ import annotations

from urllib.parse import urljoin

from bs4 import BeautifulSoup

from jobspy.weworkremotely.constant import HEADERS, SEARCH_URL, BASE_URL
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

log = create_logger("WeWorkRemotely")


class WeWorkRemotely(Scraper):
    def __init__(
        self,
        proxies: list[str] | str | None = None,
        ca_cert: str | None = None,
        user_agent: str | None = None,
    ):
        super().__init__(
            Site(Site.WEWORKREMOTELY), proxies=proxies, ca_cert=ca_cert, user_agent=user_agent
        )
        self.session = None
        self.seen_urls = set()

    def scrape(self, scraper_input: ScraperInput) -> JobResponse:
        self.session = create_session(
            proxies=self.proxies, ca_cert=self.ca_cert, is_tls=False, has_retry=True
        )

        # WWR has category filters but the directory is remote-only; we scan the
        # full listing and (optionally) filter by the caller's search term.
        url = SEARCH_URL
        jobs: list[JobPost] = []
        page = 1
        while len(jobs) < scraper_input.results_wanted:
            page_jobs = self._scrape_page(url, scraper_input)
            jobs.extend(page_jobs)
            if len(page_jobs) == 0 or page >= 5:
                break
            page += 1
            sep = "?" if "?" not in url else "&"
            url = f"{SEARCH_URL}{sep}page={page}"

        return JobResponse(jobs=jobs[: scraper_input.results_wanted])

    def _scrape_page(self, url: str, si: ScraperInput) -> list[JobPost]:
        try:
            resp = self.session.get(url, headers=HEADERS, timeout=si.request_timeout)
        except Exception as e:
            log.warning(f"WeWorkRemotely request failed: {e}")
            return []
        if resp.status_code != 200:
            log.warning(f"WeWorkRemotely HTTP {resp.status_code}")
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        jobs: list[JobPost] = []
        for a in soup.select('a[href^="/remote-jobs/"]'):
            href = a.get("href")
            if not href:
                continue
            abs_url = urljoin(BASE_URL, href)
            if abs_url in self.seen_urls:
                continue
            self.seen_urls.add(abs_url)

            # find the listing card that owns this link
            card = a.find_parent(attrs={"class": "new-listing"}) or a
            job = self._parse_card(card, abs_url)
            if job and self._matches_term(job, si.search_term):
                jobs.append(job)

        return jobs

    def _parse_card(self, card, url: str) -> JobPost | None:
        def inner(sel):
            el = card.select_one(sel)
            return " ".join(el.get_text(" ", strip=True).split()) if el else ""

        title = inner(".new-listing__header__title__text") or inner(".new-listing__header__title")
        if not title:
            return None
        company = inner(".new-listing__company-name") or None
        category = inner(".new-listing__categories__category") or ""

        # WeWorkRemotely is remote-only; mark remote.
        is_remote = True
        location = Location(country="", city="Remote", state="")

        return JobPost(
            title=title,
            company_name=company,
            location=location,
            job_url=url,
            is_remote=is_remote,
            description=f"{title} - {company}" if company else title,
            listing_type="WeWorkRemotely",
            emails=extract_emails_from_text(f"{title} {company or ''}"),
        )

    def _matches_term(self, job: JobPost, term: str | None) -> bool:
        if not term:
            return True
        return term.lower() in f"{job.title} {job.description or ''}".lower()