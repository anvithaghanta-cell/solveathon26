import os
from typing import Any
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
from flask import Flask, jsonify, render_template, request


app = Flask(__name__)

GRANTS_SEARCH_URL = "https://api.grants.gov/v1/api/search2"
GRANTS_DETAILS_URL = "https://api.grants.gov/v1/api/fetchOpportunity"

AGENCY_PRIORITIES = [
    "water",
    "flood",
    "resilience",
    "infrastructure",
]

FUNDING_RANGES = {
    "small":  (0, 50_000),
    "medium": (50_000, 250_000),
    "large":  (250_000, 1_000_000),
    "xlarge": (1_000_000, float("inf")),
    "any":    (0, float("inf")),
}


def normalize_opportunity(raw: dict[str, Any]) -> dict[str, Any]:
    opportunity_id = raw.get("id")
    return {
        "id": opportunity_id or raw.get("number") or "unknown",
        "title": raw.get("title") or "Untitled opportunity",
        "agency": raw.get("agency") or raw.get("agencyName") or "Unknown agency",
        "category": raw.get("category") or raw.get("oppCategory") or "General",
        "close_date": raw.get("closeDate") or raw.get("deadline") or "Not specified",
        "url": raw.get("link")
        or raw.get("url")
        or (
            f"https://www.grants.gov/search-results-detail/{opportunity_id}"
            if opportunity_id
            else ""
        ),
        "summary": raw.get("summary") or raw.get("description") or "",
        "keywords": raw.get("keywords") or [],
        "opportunity_id": opportunity_id,
    }


def fetch_opportunity_details(opportunity_id: Any) -> dict[str, Any]:
    response = requests.post(
        GRANTS_DETAILS_URL,
        json={"opportunityId": opportunity_id},
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()

    if payload.get("errorcode") not in (0, None):
        raise RuntimeError(f"Grants.gov details API error: {payload}")

    details = payload.get("data") or {}
    synopsis = details.get("synopsis") or {}
    return {
        "summary": synopsis.get("synopsisDesc"),
        "award_floor": synopsis.get("awardFloorFormatted"),
        "award_ceiling": synopsis.get("awardCeilingFormatted"),
        "estimated_funding": synopsis.get("estimatedFundingFormatted"),
    }


def find_opportunities(keyword: str) -> list[dict[str, Any]]:
    response = requests.post(
        GRANTS_SEARCH_URL,
        json={
            "rows": 20,
            "keyword": keyword,
            "oppStatuses": "forecasted|posted",
            "startRecordNum": 0,
        },
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()

    if payload.get("errorcode") not in (0, None):
        raise RuntimeError(f"Grants.gov API error: {payload}")

    hits = payload.get("data", {}).get("oppHits", [])
    return [normalize_opportunity(item) for item in hits if item]


def find_opportunities_for_terms(terms: list[str]) -> list[dict[str, Any]]:
    """Search each term and combine results without duplicate opportunities."""
    seen_ids = set()
    results = []

    for term in terms:
        for item in find_opportunities(term):
            if item["id"] not in seen_ids:
                seen_ids.add(item["id"])
                results.append(item)

            if len(results) >= 20:
                break
        if len(results) >= 20:
            break

    detail_items = [item for item in results if item.get("opportunity_id")]
    if detail_items:
        with ThreadPoolExecutor(max_workers=min(5, len(detail_items))) as executor:
            requests_by_future = {
                executor.submit(fetch_opportunity_details, item["opportunity_id"]): item
                for item in detail_items
            }
            for future in as_completed(requests_by_future):
                item = requests_by_future[future]
                try:
                    details = future.result()
                    item.update(
                        {
                            key: value
                            for key, value in details.items()
                            if value is not None and value != ""
                        }
                    )
                except (requests.RequestException, RuntimeError):
                    logging.exception(
                        "Could not load Grants.gov details for opportunity %s",
                        item["id"],
                    )

    return results


def parse_dollar(value: str) -> Any:
    """Convert a formatted dollar string like '$1,000,000' to a float."""
    if not value:
        return None
    cleaned = re.sub(r"[^\d.]", "", value)
    try:
        return float(cleaned)
    except ValueError:
        return None


def within_funding_range(result: dict, funding_size: str) -> bool:
    """Return True if the grant's award range overlaps with the user's desired range."""
    if not funding_size or funding_size not in FUNDING_RANGES or funding_size == "any":
        return True

    user_min, user_max = FUNDING_RANGES[funding_size]
    ceiling = parse_dollar(result.get("award_ceiling", ""))
    floor = parse_dollar(result.get("award_floor", ""))

    # If no funding data available, don't filter it out
    if ceiling is None and floor is None:
        return True

    grant_max = ceiling if ceiling is not None else floor
    grant_min = floor if floor is not None else 0

    return grant_min <= user_max and grant_max >= user_min


@app.get("/")
def index():
    return render_template(
        "index.html",
        priorities=AGENCY_PRIORITIES,
        query="",
        results=[],
    )


@app.post("/search")
def search():
    raw_query = request.form.get("keyword", "").strip()
    if not raw_query:
        raw_query = ", ".join(AGENCY_PRIORITIES)

    query_terms = [term.strip() for term in raw_query.split(",") if term.strip()]
    if not query_terms:
        query_terms = list(AGENCY_PRIORITIES)

    results = find_opportunities_for_terms(query_terms)

    return render_template(
        "index.html",
        priorities=AGENCY_PRIORITIES,
        query=raw_query,
        results=results,
    )


def extract_terms(org_type: str, focus: str, description: str, location: str) -> list[str]:
    """Build a ranked list of search terms from quiz answers."""
    terms = []

    focus_map = {
        "education": ["education", "school", "community college", "workforce training"],
        "health": ["health", "human services", "mental health", "public health"],
        "justice": ["public safety", "justice", "corrections", "emergency management"],
        "environment": ["environment", "natural resources", "agriculture", "wildlife", "conservation"],
        "economic": ["economic development", "commerce", "workforce", "small business"],
        "transportation": ["transportation", "infrastructure", "roads", "transit"],
        "housing": ["housing", "affordable housing", "community development"],
        "technology": ["technology", "digital", "broadband", "cybersecurity"],
    }

    if focus:
        terms.extend(focus_map.get(focus, [focus]))

    org_map = {
        "nonprofit": ["nonprofit", "community"],
        "government": ["municipality", "local government"],
        "university": ["research", "university"],
        "business": ["small business"],
        "tribal": ["tribal", "indigenous"],
        "individual": [],
    }
    terms.extend(org_map.get(org_type, []))

    stopwords = {"that", "this", "with", "from", "will", "have", "been",
                 "they", "them", "their", "also", "into", "more", "some"}
    if description:
        words = re.findall(r"[a-zA-Z]{4,}", description.lower())
        desc_words = [w for w in words if w not in stopwords]
        terms.extend(desc_words[:3])

    if location:
        terms.append(location.strip())

    seen = set()
    unique = []
    for t in terms:
        if t not in seen:
            seen.add(t)
            unique.append(t)

    return unique


def score_result(result: dict, terms: list[str]) -> int:
    """Score a grant result by how many search terms appear in its text."""
    text = " ".join([
        result.get("title", ""),
        result.get("summary", ""),
        result.get("agency", ""),
        result.get("category", ""),
    ]).lower()

    score = 0
    for term in terms:
        term_lower = term.lower()
        if term_lower in text or any(
            word.startswith(term_lower) or term_lower.startswith(word)
            for word in text.split()
            if len(word) >= 4
        ):
            score += 1
    return score


@app.route("/quiz", methods=["GET", "POST"])
def quiz():
    empty_form = {
        "org_type": "", "focus": "", "description": "", "location": "", "funding_size": ""
    }

    if request.method == "GET":
        return render_template("quiz.html", priorities=AGENCY_PRIORITIES,
                               error=None, form=empty_form, current_step=1)

    # Collect form values
    org_type = request.form.get("org_type", "").strip()
    focus = request.form.get("focus", "").strip()
    description = request.form.get("description", "").strip()
    location = request.form.get("location", "").strip()
    funding_size = request.form.get("funding_size", "").strip()

    # Validate required fields
    if not org_type or not focus:
        return render_template("quiz.html", priorities=AGENCY_PRIORITIES,
                               error="Please select your organization type and project focus.",
                               form=request.form, current_step=1)

    # Build search terms and fetch results
    terms = extract_terms(org_type, focus, description, location)
    results = find_opportunities_for_terms(terms[:5])

    # Filter by funding size
    if funding_size and funding_size != "any":
        results = [r for r in results if within_funding_range(r, funding_size)]

    # Score and sort results by relevance
    for result in results:
        result["score"] = score_result(result, terms)
    results.sort(key=lambda r: r["score"], reverse=True)

    return render_template(
        "quiz_results.html",
        terms=terms[:5],
        results=results,
        funding_size=funding_size,
    )


@app.get("/api/search")
def api_search():
    keyword = request.args.get("keyword", "").strip()
    terms = [term.strip() for term in keyword.split(",") if term.strip()]

    if not terms:
        terms = list(AGENCY_PRIORITIES)

    results = find_opportunities_for_terms(terms)
    return jsonify({"results": results})


if __name__ == "__main__":
    app.run(
        debug=True,
        host="0.0.0.0",
        port=int(os.getenv("PORT", "5000")),
    )