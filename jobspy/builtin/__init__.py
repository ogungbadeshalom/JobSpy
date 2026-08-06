"""BuiltIn board scraper for JobSpy.

BuildIn is a real job board at builtin.com (the "Built In" network:
Chicago, Austin, Denver, Colorado, etc.). The /jobs search page is
server-rendered HTML and parses cleanly with BeautifulSoup — no browser,
no CAPTCHA on a normal IP (unlike LinkedIn/Glassdoor).
"""

from __future__ import annotations

import re
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
            text = " ".join(card.get_text(" ", strip=True).split())
            job = self._parse_card(text, abs_url)
            if job:
                jobs.append(job)

        return jobs, has_next

    @staticmethod
    def _parse_card(text: str, url: str) -> JobPost | None:
        """Parse a BuiltIn card whose flattened text looks like:
        'CompanyName JobTitle Reposted 3 Days Ago Saved Hybrid Berlin, DEU Mid level'
        We extract: company (first token), title (rest up to a recency marker),
        remote flag (Hybrid/Remote/On-site), location, and emails.
        """
        if len(text) < 5 or not any(k in text for k in ("Posted", "Reposted", "By")):
            # some cards have a job title with no company prefix; keep loose
            pass

        remote = remote_contains("", text, "")
        remote_label = ""
        for kw in ("Remote", "Hybrid", "On-Site", "Onsite"):
            if kw.lower() in text.lower():
                remote_label = kw
                break

        # Company is the first token; title follows. Recency marker splits title from meta.
        tokens = text.split()
        company = tokens[0] if tokens else None

        # title = text after company, up to recency phrase (e.g. 'Reposted', 'Posted', 'ago')
        title = ""
        m = re.match(r"^\S+\s+(.+?)(?:\s+(?:Reposted|Posted|\d+ (?:Day|Hour|Week)s? ago).*)$", text, re.IGNORECASE)
        if m:
            title = m.group(1)
        elif len(tokens) > 1:
            # Take tokens[1:5] as best guess until we hit a meta word
            meta_words = {"reposted", "posted", "ago", "saved", "mid", "junior", "senior", "lead"}
            title = " ".join(t for t in tokens[1:] if t.lower() not in meta_words and not t.isdigit())[:200]
            # heuristic cutoff: stop at a location word like 'in\n' - keep simple
        if not title:
            return None

        # Location: last chunk that looks like a place (CITY, REGION CODE)
        loc_match = re.search(r"\b([A-Za-z][A-Za-z .-]+?,?\s?[A-Za-z]{2,3})\s*$", text)
        location = Location(country="US")
        if loc_match:
            loc_str = loc_match.group(1).strip()
            parts = [p.strip() for p in loc_str.split(",") if p.strip()]
            if parts:
                location.city = parts[0]
                if len(parts) > 1:
                    location.state = parts[1]
        if remote_label in ("Remote",):
            location.city = "Remote"
            location.state = ""

        return JobPost(
            title=title,
            company_name=company,
            location=location,
            job_url=url,
            is_remote=remote_contains(title, text, ""),
            description=text,
            listing_type="BuiltIn",
            emails=extract_emails_from_text(text),
        )