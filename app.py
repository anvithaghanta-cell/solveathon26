import os
from typing import Any
import re
import requests
from flask import Flask, jsonify, render_template, request


app = Flask(__name__)

GRANTS_SEARCH_URL = "https://api.grants.gov/v1/api/search2"

AGENCY_PRIORITIES = [
    "water",
    "flood",
    "resilience",
    "infrastructure",
]


def normalize_opportunity(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": raw.get("id") or raw.get("number") or "unknown",
        "title": raw.get("title") or "Untitled opportunity",
        "agency": raw.get("agency") or raw.get("agencyName") or "Unknown agency",
        "category": raw.get("category") or raw.get("oppCategory") or "General",
        "close_date": raw.get("closeDate") or raw.get("deadline") or "Not specified",
        "url": raw.get("link") or raw.get("url") or "",
        "summary": raw.get("summary") or raw.get("description") or "",
        "keywords": raw.get("keywords") or [],
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
                return results

    return results


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

    # Focus area is the primary term
    if focus:
        terms.append(focus)

    # Map org type to relevant grant terms
    org_map = {
        "nonprofit": ["nonprofit", "community"],
        "government": ["municipality", "local government"],
        "university": ["research", "university"],
        "business": ["small business"],
        "tribal": ["tribal", "indigenous"],
        "individual": [],
    }
    terms.extend(org_map.get(org_type, []))

    # Pull meaningful words from the description (4+ chars, not stopwords)
    stopwords = {"that", "this", "with", "from", "will", "have", "been",
                 "they", "them", "their", "also", "into", "more", "some"}
    if description:
        words = re.findall(r"[a-zA-Z]{4,}", description.lower())
        terms.extend(w for w in words if w not in stopwords)

    # Add location if provided
    if location:
        terms.append(location.strip())

    # Deduplicate while preserving order
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

    return sum(1 for term in terms if term.lower() in text)


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
    #funding_size = request.form.get("funding_size", "").strip()

    # Validate required fields
    if not org_type or not focus:
        return render_template("quiz.html", priorities=AGENCY_PRIORITIES,
                               error="Please select your organization type and project focus.",
                               form=request.form, current_step=1)

    # Build search terms and fetch results
    terms = extract_terms(org_type, focus, description, location)
    results = find_opportunities_for_terms(terms[:5])  # top 5 terms to avoid too many API calls

    # Score and sort results by relevance
    for result in results:
        result["score"] = score_result(result, terms)
    results.sort(key=lambda r: r["score"], reverse=True)

    return render_template(
        "quiz_results.html",
        terms=terms[:5],
        results=results,
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