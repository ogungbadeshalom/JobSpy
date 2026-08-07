"""Remotive board scraper for JobSpy.

Remotive (remotive.com) is a remote-first job board. We scrape its
server-rendered category/tag pages which embed the job tiles directly in the
HTML (e.g. https://remotive.com/remote-net-jobs for .NET roles), parsing the
embedded JSON job objects. This surfaces the specific .NET (or other tag)
remote jobs the caller targets — more reliable than the broad text-search API.
"""

from __future__ import annotations

import json
import re
from datetime import datetime

from jobspy.remotive.constant import BASE_URL
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

log = create_logger("Remotive")


class Remotive(Scraper):
    def __init__(self, proxies=None, ca_cert=None, user_agent=None):
        super().__init__(
            Site(Site.REMOTIVE), proxies=proxies, ca_cert=ca_cert, user_agent=user_agent
        )
        self.session = None

    def scrape(self, scraper_input: ScraperInput) -> JobResponse:
        self.session = create_session(
            proxies=self.proxies, ca_cert=self.ca_cert, is_tls=False, has_retry=True
        )
        url = self._build_url(scraper_input)
        jobs: list[JobPost] = []
        try:
            resp = self.session.get(url, timeout=scraper_input.request_timeout)
        except Exception as e:
            log.warning(f"Remotive request failed: {e}")
            return JobResponse(jobs=[])
        if resp.status_code != 200:
            log.warning(f"Remotive HTTP {resp.status_code}")
            return JobResponse(jobs=[])

        for raw in self._extract_jobs(resp.text):
            job = self._process_job(raw)
            if job and self._matches_term(job, scraper_input.search_term):
                jobs.append(job)
            if len(jobs) >= scraper_input.results_wanted:
                break

        return JobResponse(jobs=jobs[: scraper_input.results_wanted])

    def _build_url(self, si: ScraperInput) -> str:
        # Map the caller's search to a Remotive tag page when it's a well-known
        # tag/category (e.g. ".net" -> remote-net-jobs). Otherwise fall back to
        # the full remote directory and let the include-filter narrow.
        term = (si.search_term or "").strip().lower()
        tag = re.sub(r"[^a-z0-9]+", "-", term).strip("-")  # ".net" -> "net"
        tag = tag.replace("--", "-")
        if tag in {"net", "net-jobs"}:
            return f"{BASE_URL}/remote-net-jobs"
        if tag and tag not in {"", "remote", "jobs"}:
            return f"{BASE_URL}/remote-{tag}-jobs"
        return f"{BASE_URL}/remote-jobs"

    def _extract_jobs(self, html: str):
        # Remotive embeds the job list as a JS var:
        #   window.__INITIAL_SEARCH_RESULTS__ = {"results":[{"hits":[{...job...}]}]}
        start = html.find("window.__INITIAL_SEARCH_RESULTS__")
        if start == -1:
            log.warning("Remotive: no __INITIAL_SEARCH_RESULTS__ blob found")
            return []
        brace = html.find("{", start)
        if brace == -1:
            return []
        # balance braces to capture the full JSON object
        depth = 0
        i = brace
        in_str, esc = False, False
        n = len(html)
        while i < n:
            c = html[i]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
            else:
                if c == '"':
                    in_str = True
                elif c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                    if depth == 0:
                        break
            i += 1
        blob = html[brace : i + 1]
        try:
            data = json.loads(blob)
        except Exception as e:
            log.warning(f"Remotive: could not parse blob: {e}")
            return []
        # Collect all dicts that look like jobs (title + company_name)
        jobs = []
        stack = [data]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                if isinstance(node.get("title"), str) and ("company_name" in node or "url" in node):
                    jobs.append(node)
                stack.extend(node.values())
            elif isinstance(node, list):
                stack.extend(node)
        return jobs

    def _process_job(self, raw: dict) -> JobPost | None:
        title = raw.get("title")
        if not title:
            return None
        # strip <em> tags Remotive wraps matched keywords in
        title = re.sub(r"<[^>]+>", "", title).strip()

        # locations can be a list e.g. ["USA", "Remote"] or a string
        locs = raw.get("locations") or raw.get("location") or raw.get("candidate_required_location") or ""
        if isinstance(locs, list):
            loc_text = ", ".join(str(x) for x in locs)
        else:
            loc_text = str(locs)
        city = "Remote" if ("remote" in loc_text.lower() or not loc_text.strip()) else loc_text.split(",")[0].strip()
        location = Location(city=city, state="", country="")

        job_url = raw.get("url")
        if not job_url:
            job_url = f"{BASE_URL}/remote-jobs/{raw.get('id','')}"

        date_posted = None
        pd = raw.get("pub_date") or raw.get("publication_date") or raw.get("published")
        if pd:
            try:
                date_posted = datetime.fromisoformat(str(pd).replace("Z", "+00:00"))
            except Exception:
                date_posted = None

        # skills / description
        skills = raw.get("skills") or []
        skills_text = ", ".join(str(s) for s in skills) if isinstance(skills, list) else str(skills)
        description = f"{skills_text} {str(raw.get('description') or '')}".strip()

        return JobPost(
            id=str(raw.get("id") or ""),
            title=title,
            company_name=raw.get("company_name") or None,
            location=location,
            job_url=job_url,
            date_posted=date_posted.date() if date_posted else None,
            description=description,
            is_remote=True,  # remotive is a remote-only board
            listing_type="Remotive",
            emails=extract_emails_from_text(description or f"{title} {raw.get('company_name','') or ''}"),
        )

    def _matches_term(self, job: JobPost, term: str | None) -> bool:
        if not term:
            return True
        return term.lower() in f"{job.title} {job.description or ''}".lower()