"""BuiltIn board scraper for JobSpy.

BuiltIn is a real job board at builtin.com (the "Built In" network:
Chicago, Austin, Denver, Colorado, etc.). The /jobs search page is
server-rendered HTML and parses cleanly with BeautifulSoup — no browser,
no CAPTCHA on a normal IP (unlike LinkedIn/Glassdoor).

Card DOM (verified Aug 2026):
  .left-side-tile-item-2           -> company name
  .left-side-tile-item-3 > h2      -> job title
  a[href^="/job/"]                 -> job URL
  span <Remote|Hybrid|On-site>     -> remote status
  span "City, Region, Code"        -> location
"""

from __future__ import annotations

from urllib.parse import urlencode, urljoin

from bs4 import BeautifulSoup

from jobspy.builtin.constant import HEADERS, SEARCH_URL, BASE_URL
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

log = create_logger("BuiltIn")

_REMOTE_LABELS = ("remote", "hybrid", "on-site", "onsite", "remote or hybrid")


class BuiltIn(Scraper):
    def __init__(
        self,
        proxies: list[str] | str | None = None,
        ca_cert: str | None = None,
        user_agent: str | None = None,
    ):
        super().__init__(
            Site(Site.BUILTIN), proxies=proxies, ca_cert=ca_cert, user_agent=user_agent
        )
        self.session = None
        self.seen_urls = set()

    def scrape(self, scraper_input: ScraperInput) -> JobResponse:
        self.session = create_session(
            proxies=self.proxies, ca_cert=self.ca_cert, is_tls=False, has_retry=True
        )
        first_url = self._build_search_url(scraper_input)
        jobs: list[JobPost] = []

        url = first_url
        page = 1
        while len(self.seen_urls) < scraper_input.results_wanted:
            page_jobs, has_next = self._scrape_page(url, scraper_input)
            jobs.extend(page_jobs)
            if not has_next or len(page_jobs) == 0 or page >= 5:
                break
            page += 1
            sep = "&" if "?" in first_url else "?"
            url = f"{first_url}{sep}page={page}"

        return JobResponse(jobs=jobs[: scraper_input.results_wanted])

    @staticmethod
    def _build_search_url(si: ScraperInput) -> str:
        params = {}
        if si.search_term:
            params["search"] = si.search_term
        if si.is_remote:
            params["remote"] = "true"
        if si.location and not si.is_remote:
            params["location"] = si.location
        qs = urlencode(params)
        return f"{SEARCH_URL}?{qs}" if qs else SEARCH_URL

    def _scrape_page(self, url: str, si: ScraperInput) -> tuple[list[JobPost], bool]:
        try:
            resp = self.session.get(url, headers=HEADERS, timeout=si.request_timeout)
        except Exception as e:
            log.warning(f"BuiltIn request failed: {e}")
            return [], False
        if resp.status_code != 200:
            log.warning(f"BuiltIn HTTP {resp.status_code}")
            return [], False

        soup = BeautifulSoup(resp.text, "html.parser")
        links = soup.select('a[href^="/job/"]')
        jobs: list[JobPost] = []
        has_next = bool(soup.select_one('a[href*="page=2"]'))

        for a in links:
            href = a.get("href")
            if not href:
                continue
            abs_url = urljoin(BASE_URL, href)
            if abs_url in self.seen_urls:
                continue
            self.seen_urls.add(abs_url)

            card = a.find_parent("div", class_="row") or a.parent
            if not card:
                continue
            job = self._parse_card(card, abs_url)
            if job:
                jobs.append(job)

        return jobs, has_next

    @staticmethod
    def _parse_card(card, url: str) -> JobPost | None:
        def inner(sel):
            el = card.select_one(sel)
            return " ".join(el.get_text(" ", strip=True).split()) if el else ""

        company = inner(".left-side-tile-item-2")
        title = inner(".left-side-tile-item-3 > h2") or inner(".left-side-tile-item-3")
        if not title:
            return None

        # collect any text that carries a remote/hybrid/onsite label
        labels = []
        for s in card.find_all("span"):
            t = " ".join(s.get_text(" ", strip=True).split())
            if t and any(k in t.lower() for k in _REMOTE_LABELS):
                labels.append(t)
        remote_text = " ".join(labels)
        is_remote = remote_contains(title, remote_text, "")

        # location: the span that looks like a place ("City, ST" / "4 Locations")
        location = Location(country="US")
        for s in card.find_all("span"):
            t = " ".join(s.get_text(" ", strip=True).split())
            if "," in t and not any(k in t.lower() for k in _REMOTE_LABELS):
                parts = [p.strip() for p in t.split(",") if p.strip()]
                if parts:
                    location.city = parts[0]
                    if len(parts) > 1:
                        location.state = parts[1]
                break
        if is_remote and not location.city:
            location.city = "Remote"
            location.state = ""

        return JobPost(
            title=title,
            company_name=company or None,
            location=location,
            job_url=url,
            is_remote=is_remote,
            description=remote_text or f"{title} - {company}",
            listing_type="BuiltIn",
            emails=extract_emails_from_text(f"{title} {company}"),
        )