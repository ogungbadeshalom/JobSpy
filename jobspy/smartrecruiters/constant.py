"""SmartRecruiters board scraper constants."""

# Public SmartRecruiters posting API per company.
API_BASE = "https://api.smartrecruiters.com/v1/companies/{company}/postings"

# Curated list of companies that expose public postings via the API (many
# SmartRecruiters customers opt out). Only opted-in companies return jobs.
DEFAULT_COMPANIES = [
    "visa",
    "captain-360",
    "bluebot-digital",
]