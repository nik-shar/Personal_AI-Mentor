"""
integrations/job_search.py

Unified job-search utility for the job_hunter agent.

Three search strategies, each returning the same RawJobListing shape:
  1. SerpApi Google Jobs (primary) — aggregates Naukri, InstaHyre, LinkedIn India,
     Wellfound, Glassdoor India, Foundit. Best for India/Remote coverage.
  2. JSearch via RapidAPI (secondary) — LinkedIn, Indeed, ZipRecruiter.
     Good fallback and for broader international listings.
  3. ATS direct (supplementary) — Greenhouse/Lever JSON endpoints for a curated
     list of target AI companies. No API key needed; returns only roles from
     companies Nik explicitly wants to track.

All functions are fail-open: a failed source logs a warning and returns [],
never crashes the calling node.

Full-JD fetch:
  SerpApi descriptions are often truncated (~200-500 chars). fetch_full_jd()
  does a lightweight HTML fetch of the apply_url when the snippet is short.
  Used on-demand when tailoring — not during bulk search (keeps it fast).
"""

from __future__ import annotations

import os
import re
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Shared data shape
# ---------------------------------------------------------------------------

RawJobListing = dict  # typed via TypedDict alias below for clarity

# Keys guaranteed present on every returned dict (missing → None):
# title, company, location, is_remote, description, apply_url,
# source_platform, posted_date, salary_range, experience_required

EMPTY_LISTING: RawJobListing = {
    "title": "",
    "company": "",
    "location": "",
    "is_remote": False,
    "description": "",
    "apply_url": "",
    "source_platform": "",
    "posted_date": None,
    "salary_range": None,
    "experience_required": None,
    "description_truncated": False,
}


def _make_listing(**kwargs: Any) -> RawJobListing:
    """Build a RawJobListing with EMPTY_LISTING defaults."""
    out = dict(EMPTY_LISTING)
    out.update(kwargs)
    # Fallback: generate a Google search link when no direct apply URL exists.
    if not out.get("apply_url"):
        company = out.get("company", "")
        title = out.get("title", "")
        location = out.get("location", "")
        query_parts = [p for p in (company, title, location) if p]
        query = " ".join(query_parts) if query_parts else "job apply"
        from urllib.parse import quote
        out["apply_url"] = f"https://www.google.com/search?q={quote(query)}+apply"
    return out


# ---------------------------------------------------------------------------
# 1. SerpApi Google Jobs (primary)
# ---------------------------------------------------------------------------

SERPAPI_KEY = os.getenv("SERP_API_KEY", "").strip()
SERPAPI_ENDPOINT = "https://serpapi.com/search"

# Location strings that mean "no geographic constraint". Google Jobs rejects
# these as a `location` param (400 "Unsupported location"), so a remote pass
# drops the param and folds "remote" into the query text instead.
REMOTE_LOCATION_TOKENS = {"remote", "anywhere", "worldwide", "global", "work from home", "wfh"}


# Google Jobs Japan indexes very few English-language queries — "AI Engineer"
# returns 0 jobs while "AI エンジニア" returns a full page (verified 2026-08).
# When the Japan geo is active, localize well-known role terms.
_JAPAN_QUERY_MAP = {
    "ai engineer": "AI エンジニア",
    "ml engineer": "機械学習 エンジニア",
    "machine learning": "機械学習 エンジニア",
    "llm": "LLM エンジニア",
    "data scientist": "データサイエンティスト",
    "software engineer": "ソフトウェア エンジニア",
    "ai": "AI エンジニア",
}


def _serpapi_geo(location: str) -> tuple[str, str]:
    """Map a location string to the right (google_domain, gl) pair.

    The India defaults were hardcoded originally — searching "Japan" through
    google.co.in times out / returns nothing, so geo-route per location.
    """
    lowered = (location or "").strip().lower()
    if "japan" in lowered:
        return "google.co.jp", "jp"
    if lowered in REMOTE_LOCATION_TOKENS:
        # Worldwide remote pass: google.com indexes global "Anywhere" roles.
        # The India domain returns ZERO results when no location param is set,
        # so the remote pass must not use google.co.in.
        return "google.com", "us"
    if not lowered or "india" in lowered:
        return "google.co.in", "in"
    return "google.com", "us"


def search_via_serpapi(
    query: str,
    location: str = "India",
    num_results: int = 10,
) -> list[RawJobListing]:
    """
    Query Google Jobs via SerpApi.

    Covers: Naukri, InstaHyre, LinkedIn India, Wellfound, Glassdoor India,
    Foundit (Monster India), TimesJobs — whichever Google Jobs indexes for
    the given location.

    Args:
        query:      Role search string e.g. "AI Engineer LLM"
        location:   Geographic location ("India", "Japan", "Bangalore").
                    Pass "Remote" for a worldwide remote-roles pass — the
                    keyword is folded into the query and no location
                    constraint is sent (Google 400s on location="Remote").
        num_results: Target number of results per call (Google returns ~10/page).

    Returns:
        list of RawJobListing dicts.
    """
    if not SERPAPI_KEY:
        print("[job_search] SerpApi key (SERP_API_KEY) not set — skipping.")
        return []

    try:
        import requests

        loc_clean = (location or "").strip()
        is_remote_pass = loc_clean.lower() in REMOTE_LOCATION_TOKENS
        if is_remote_pass and "remote" not in query.lower():
            query = f"{query} remote"

        google_domain, gl = _serpapi_geo(loc_clean)

        # Japan market: Google Jobs Japan barely indexes English queries —
        # localize well-known role terms ("AI Engineer" → "AI エンジニア").
        if gl == "jp":
            q_lower = query.lower()
            for en_term, jp_term in _JAPAN_QUERY_MAP.items():
                if en_term in q_lower:
                    query = jp_term
                    break

        params = {
            "engine": "google_jobs",
            "q": query,
            "google_domain": google_domain,
            "hl": "en",
            "gl": gl,
            "num": num_results,
            "api_key": SERPAPI_KEY,
        }
        if loc_clean and not is_remote_pass:
            params["location"] = loc_clean

        resp = requests.get(SERPAPI_ENDPOINT, params=params, timeout=20)
        resp.raise_for_status()
        data = resp.json()

        results = data.get("jobs_results") or []
        listings: list[RawJobListing] = []

        for job in results:
            # Detect remote
            loc_raw = job.get("location") or ""
            is_remote = "remote" in loc_raw.lower()

            # Platform attribution from detected_extensions or via_text
            via = job.get("via") or ""  # e.g. "via Naukri", "via LinkedIn"
            platform = via.replace("via ", "").strip().lower() if via else "google_jobs"

            # Salary (may be in detected_extensions)
            ext = job.get("detected_extensions") or {}
            salary = ext.get("salary") or None
            exp_raw = ext.get("work_from_home")  # boolean for remote

            # Experience string if present
            posted_at = ext.get("posted_at") or None
            schedule = ext.get("schedule_type") or None
            exp_years = ext.get("experience") or None

            # Description — Google Jobs gives a highlights block + description
            description = job.get("description") or ""
            if not description:
                highlights = job.get("job_highlights") or []
                description = "\n\n".join(
                    f"{h.get('title', '')}:\n" + "\n".join(h.get("items", []))
                    for h in highlights
                )

            # Apply link: prefer a direct job-apply link; fall back to the
            # Google-hosted posting page (job_google_link) then a related link —
            # so a working link is captured on as many listings as possible and
            # the user can open one directly to apply.
            apply_url = (
                job.get("job_apply_link")
                or job.get("job_google_link")
                or job.get("related_links", [{}])[0].get("link", "")
                or ""
            )

            desc_truncated = len(description) < 400

            listings.append(_make_listing(
                title=job.get("title") or "",
                company=job.get("company_name") or "",
                location=loc_raw,
                is_remote=is_remote or bool(exp_raw),
                description=description,
                apply_url=apply_url,
                source_platform=platform,
                posted_date=posted_at,
                salary_range=salary,
                experience_required=exp_years,
                description_truncated=desc_truncated,
            ))

        print(f"[job_search] SerpApi returned {len(listings)} listings for '{query}' / {location}.")
        return listings

    except Exception as exc:
        print(f"[job_search] SerpApi search failed: {exc}")
        return []


# ---------------------------------------------------------------------------
# 2. JSearch via RapidAPI (secondary)
# ---------------------------------------------------------------------------

JSEARCH_KEY = os.getenv("JSEARCH_RAPIDAPI_KEY", "").strip()
JSEARCH_ENDPOINT = "https://jsearch.p.rapidapi.com/search"


def search_via_jsearch(
    query: str,
    location: str = "India",
    num_results: int = 10,
) -> list[RawJobListing]:
    """
    Query JSearch (RapidAPI) — LinkedIn, Indeed, ZipRecruiter, Glassdoor.

    JSearch always returns full job_description (no truncation issue).
    Used as secondary source when SerpApi doesn't cover a role well.
    Skipped silently if JSEARCH_RAPIDAPI_KEY is not set.
    """
    if not JSEARCH_KEY:
        # Secondary source is optional — log at debug level, not warning.
        return []

    try:
        import requests

        headers = {
            "X-RapidAPI-Key": JSEARCH_KEY,
            "X-RapidAPI-Host": "jsearch.p.rapidapi.com",
        }
        params = {
            "query": f"{query} {location}",
            "page": "1",
            "num_pages": "1",
            "date_posted": "month",  # last 30 days
        }

        resp = requests.get(JSEARCH_ENDPOINT, headers=headers, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        raw_jobs = data.get("data") or []
        listings: list[RawJobListing] = []

        for job in raw_jobs[:num_results]:
            loc_raw = job.get("job_city") or job.get("job_country") or ""
            if job.get("job_state"):
                loc_raw = f"{job['job_city']}, {job['job_state']}"

            listings.append(_make_listing(
                title=job.get("job_title") or "",
                company=job.get("employer_name") or "",
                location=loc_raw,
                is_remote=bool(job.get("job_is_remote")),
                description=job.get("job_description") or "",
                apply_url=job.get("job_apply_link") or job.get("job_google_link") or "",
                source_platform=job.get("job_publisher") or "jsearch",
                posted_date=job.get("job_posted_at_datetime_utc", "")[:10] or None,
                salary_range=_format_salary_jsearch(job),
                experience_required=job.get("job_required_experience", {}).get("required_experience_in_months"),
                description_truncated=False,  # JSearch always gives full description
            ))

        print(f"[job_search] JSearch returned {len(listings)} listings for '{query}' / {location}.")
        return listings

    except Exception as exc:
        print(f"[job_search] JSearch search failed: {exc}")
        return []


def _format_salary_jsearch(job: dict) -> Optional[str]:
    sal = job.get("job_salary_currency")
    min_s = job.get("job_min_salary")
    max_s = job.get("job_max_salary")
    period = job.get("job_salary_period") or ""
    if min_s and max_s and sal:
        return f"{sal} {int(min_s)}-{int(max_s)} {period}".strip()
    return None


# ---------------------------------------------------------------------------
# 3. ATS direct queries — Greenhouse / Lever
# ---------------------------------------------------------------------------

# Curated list of AI-first companies with known ATS board slugs.
# Every entry below was probed live against its public board API (2026-08):
# the old list was 100% dead (Greenhouse host was api.greenhouse.io — wrong;
# the public job-board host is boards-api.greenhouse.io — and several slugs
# had rotted as companies moved ATS platforms). Re-probe before adding more.
_ATS_COMPANIES: list[dict] = [
    {"name": "OpenAI",            "ats": "ashby",      "slug": "openai"},
    {"name": "Anthropic",         "ats": "greenhouse", "slug": "anthropic"},
    {"name": "Cohere",            "ats": "ashby",      "slug": "cohere"},
    {"name": "Mistral",           "ats": "lever",      "slug": "mistral"},
    {"name": "Perplexity",        "ats": "ashby",      "slug": "perplexity"},
    {"name": "ElevenLabs",        "ats": "ashby",      "slug": "elevenlabs"},
    {"name": "Cursor (Anysphere)", "ats": "ashby",     "slug": "cursor"},
    {"name": "LlamaIndex",        "ats": "ashby",      "slug": "llamaindex"},
    {"name": "Sarvam AI",         "ats": "ashby",      "slug": "sarvam"},
    {"name": "Databricks",        "ats": "greenhouse", "slug": "databricks"},
    {"name": "Scale AI",          "ats": "greenhouse", "slug": "scaleai"},
]

_GREENHOUSE_JOBS_URL = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
_LEVER_JOBS_URL = "https://api.lever.co/v0/postings/{slug}?mode=json"
_ASHBY_JOBS_URL = "https://api.ashbyhq.com/posting-api/job-board/{slug}"

_AI_KEYWORDS = {
    "ai", "ml", "machine learning", "data", "nlp", "llm", "language model",
    "deep learning", "research", "engineer", "scientist", "platform",
    "inference", "training", "rlhf", "generative", "foundation", "model",
    "backend", "software", "swe",
}


def search_via_ats_direct(
    filter_keywords: Optional[list[str]] = None,
) -> list[RawJobListing]:
    """
    Query Greenhouse/Lever public job boards for curated target AI companies.

    Only returns roles whose title matches AI/ML/engineering keywords.
    No API key required — these are public endpoints.
    Description is not available here; fetched on demand by fetch_full_jd().

    Args:
        filter_keywords: Extra keywords to match in job title (e.g. ["data scientist"]).
    """
    try:
        import requests
    except ImportError:
        return []

    extra_kw = {k.lower() for k in (filter_keywords or [])}
    match_kw = _AI_KEYWORDS | extra_kw
    listings: list[RawJobListing] = []

    for company in _ATS_COMPANIES:
        try:
            if company["ats"] == "greenhouse":
                url = _GREENHOUSE_JOBS_URL.format(slug=company["slug"])
                data = requests.get(url, timeout=10).json()
                jobs = data.get("jobs") or []
                for job in jobs:
                    title = (job.get("title") or "").lower()
                    if not any(kw in title for kw in match_kw):
                        continue
                    loc = ""
                    offices = job.get("offices") or []
                    if offices:
                        loc = ", ".join(o.get("name", "") for o in offices)
                    listings.append(_make_listing(
                        title=job.get("title") or "",
                        company=company["name"],
                        location=loc,
                        is_remote="remote" in loc.lower() or "remote" in title,
                        description="",  # not in listing response; fetch on demand
                        apply_url=job.get("absolute_url") or "",
                        source_platform="greenhouse",
                        posted_date=None,
                        description_truncated=True,
                    ))

            elif company["ats"] == "lever":
                url = _LEVER_JOBS_URL.format(slug=company["slug"])
                jobs = requests.get(url, timeout=10).json()
                if not isinstance(jobs, list):
                    continue
                for job in jobs:
                    title = (job.get("text") or "").lower()
                    if not any(kw in title for kw in match_kw):
                        continue
                    loc = (job.get("categories") or {}).get("location") or ""
                    team = (job.get("categories") or {}).get("team") or ""
                    desc_html = (job.get("description") or "")
                    desc_plain = re.sub(r"<[^>]+>", " ", desc_html).strip()
                    listings.append(_make_listing(
                        title=job.get("text") or "",
                        company=company["name"],
                        location=loc,
                        is_remote="remote" in loc.lower(),
                        description=desc_plain[:2000],
                        apply_url=job.get("hostedUrl") or "",
                        source_platform="lever",
                        posted_date=None,
                        description_truncated=len(desc_plain) < 400,
                    ))

            elif company["ats"] == "ashby":
                url = _ASHBY_JOBS_URL.format(slug=company["slug"])
                data = requests.get(url, timeout=10).json()
                for job in data.get("jobs") or []:
                    if job.get("isListed") is False:
                        continue
                    title = (job.get("title") or "").lower()
                    if not any(kw in title for kw in match_kw):
                        continue
                    loc = job.get("location") or ""
                    desc_plain = (job.get("descriptionPlain") or "").strip()
                    listings.append(_make_listing(
                        title=job.get("title") or "",
                        company=company["name"],
                        location=loc,
                        is_remote=bool(job.get("isRemote")) or "remote" in loc.lower(),
                        description=desc_plain[:2000],
                        apply_url=job.get("jobUrl") or job.get("applyUrl") or "",
                        source_platform="ashby",
                        posted_date=(job.get("publishedAt") or "")[:10] or None,
                        description_truncated=len(desc_plain) < 400,
                    ))

        except Exception as exc:
            print(f"[job_search] ATS direct failed for {company['name']}: {exc}")
            continue

    print(f"[job_search] ATS direct returned {len(listings)} listings.")
    return listings


# ---------------------------------------------------------------------------
# 4. Full JD fetcher (on-demand, not during bulk search)
# ---------------------------------------------------------------------------

_MIN_DESC_CHARS = 400  # below this, we try to fetch the full page


def fetch_full_jd(url: str) -> Optional[str]:
    """
    Fetch the full job description from a job posting URL.

    Called on-demand when:
      - SerpApi description is truncated (< 400 chars)
      - ATS direct returned no description

    Strips HTML tags and common boilerplate (nav, footer, script).
    Fail-open: returns None if anything goes wrong.
    """
    if not url:
        return None
    try:
        import requests
        from bs4 import BeautifulSoup

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
        }
        resp = requests.get(url, headers=headers, timeout=12, allow_redirects=True)
        if resp.status_code != 200:
            return None

        soup = BeautifulSoup(resp.text, "html.parser")

        # Remove noise elements
        for tag in soup(["script", "style", "nav", "header", "footer",
                          "noscript", "aside", "form", "iframe"]):
            tag.decompose()

        # Try known job-description selectors first
        for selector in [
            "[class*='job-description']",
            "[class*='jobDescription']",
            "[class*='description']",
            "[id*='job-description']",
            "[id*='jobDescription']",
            "article",
            "main",
        ]:
            elem = soup.select_one(selector)
            if elem:
                text = elem.get_text(separator="\n", strip=True)
                if len(text) > 200:
                    return _clean_text(text)[:6000]

        # Fallback: whole body text
        body = soup.find("body")
        if body:
            return _clean_text(body.get_text(separator="\n", strip=True))[:6000]

    except Exception as exc:
        print(f"[job_search] fetch_full_jd failed for {url}: {exc}")

    return None


def _clean_text(raw: str) -> str:
    """Collapse whitespace, remove unicode junk, return clean plain text."""
    lines = []
    for line in raw.splitlines():
        line = line.strip()
        if line and len(line) > 2:
            lines.append(line)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 5. Convenience: multi-source search
# ---------------------------------------------------------------------------

def search_jobs(
    role_queries: list[str],
    locations: list[str],
    include_ats_direct: bool = True,
    num_per_source: int = 10,
) -> list[RawJobListing]:
    """
    Run all enabled sources and return a combined, deduplicated raw list.

    This is the single entry point called by job_searcher_node.

    Args:
        role_queries:   e.g. ["AI Engineer LLM", "Data Scientist NLP"]
        locations:      e.g. ["India", "Remote"]
        include_ats_direct: whether to query ATS company boards
        num_per_source: results per source per query

    Returns:
        Combined list of RawJobListing dicts, NOT yet filtered or scored.
    """
    all_listings: list[RawJobListing] = []

    for query in role_queries:
        for loc in locations:
            serpapi_results = search_via_serpapi(query, loc, num_per_source)
            all_listings.extend(serpapi_results)

            jsearch_results = search_via_jsearch(query, loc, num_per_source)
            all_listings.extend(jsearch_results)

    if include_ats_direct:
        ats_results = search_via_ats_direct(filter_keywords=role_queries)
        all_listings.extend(ats_results)

    print(f"[job_search] Total raw listings before dedup: {len(all_listings)}")
    return all_listings
