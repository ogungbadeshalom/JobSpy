"""WeWorkRemotely board scraper constants."""

# Minimal headers: WeWorkRemotely sits behind Cloudflare and 403s on
# "browser-like" sec-fetch/accept-language combos. A plain User-Agent (like
# curl) passes cleanly (verified: minimal UA -> 200, full browser headers -> 403).
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
}

BASE_URL = "https://weworkremotely.com"
# Remote job directory (all categories).
SEARCH_URL = "https://weworkremotely.com/remote-jobs"