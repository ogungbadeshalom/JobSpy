#!/usr/bin/env python3
"""find_jobs.py — scrape all JobSync fork boards in one deduped CSV/Excel.

Usage:
    python3 find_jobs.py "software engineer" --remote --days 1 --out jobs.xlsx

Scrapes all working boards (LinkedIn, Indeed, ZipRecruiter, Glassdoor,
Greenhouse, BuiltIn, RemoteOK), filters to recent jobs, drops cross-board
duplicates, and writes a clean table.
"""
import argparse
import os

import pandas as pd

from jobspy import scrape_jobs, Site

BOARDS = [
    "linkedin",
    "indeed",
    "zip_recruiter",
    "glassdoor",
    "greenhouse",
    "builtin",
    "remoteok",
]


def main():
    ap = argparse.ArgumentParser(description="Job-finder across JobSync boards")
    ap.add_argument("query", nargs="?", default="software engineer")
    ap.add_argument("--boards", nargs="+", default=BOARDS, help="boards to scrape")
    ap.add_argument("--per", type=int, default=25, help="jobs wanted per board")
    ap.add_argument("--remote", action="store_true", help="remote-only")
    ap.add_argument("--days", type=int, default=None, help="only jobs from last N days")
    ap.add_argument("--out", default=None, help="output file (.csv or .xlsx)")
    args = ap.parse_args()

    boards = [b for b in args.boards if b in [s.value for s in Site]]
    if not boards:
        print("No valid boards. Allowed:", [s.value for s in Site])
        return

    print(f"Scraping {len(boards)} boards: {', '.join(boards)}")
    jobs = scrape_jobs(
        site_name=boards,
        search_term=args.query,
        is_remote=args.remote,
        results_wanted=args.per,
        cross_site_dedup=True,
        hours_old=args.days * 24 if args.days else None,
    )

    print(f"\n{len(jobs)} unique jobs after cross-board dedup")
    if len(jobs) == 0:
        print("No jobs returned. If on a datacenter IP, some boards block — "
              "run on a normal machine or pass a residential proxy.")
        return

    cols = ["site", "title", "company", "location", "is_remote",
            "date_posted", "job_url"]
    cols = [c for c in cols if c in jobs.columns]
    show = jobs[cols].copy()
    if "date_posted" in show:
        show["date_posted"] = pd.to_datetime(show["date_posted"]).dt.date
    print("\n=== first 15 ===")
    print(show.head(15).to_string(index=False))

    if args.out:
        ext = os.path.splitext(args.out)[1].lower()
        if ext in (".xlsx", ".xls"):
            jobs.to_excel(args.out, index=False)
        else:
            jobs.to_csv(args.out, index=False)
        print(f"\nWrote {len(jobs)} jobs → {args.out}")


if __name__ == "__main__":
    main()