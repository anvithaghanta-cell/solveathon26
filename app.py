import os
from typing import Any

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


@app.route("/quiz", methods=["GET", "POST"])
def quiz():
    if request.method == "GET":
        return render_template(
            "quiz.html",
            priorities=AGENCY_PRIORITIES,
            error=None,
        )

    focus = request.form.get("focus", "").strip().lower()
    raw_keywords = request.form.get("keywords", "").strip()

    if focus not in AGENCY_PRIORITIES:
        return render_template(
            "quiz.html",
            priorities=AGENCY_PRIORITIES,
            error="Please choose a project focus area.",
        )

    # Search for the selected focus and any extra project keywords.
    terms = [focus]
    terms.extend(
        term.strip()
        for term in raw_keywords.split(",")
        if term.strip()
    )

    results = find_opportunities_for_terms(terms)

    return render_template(
        "index.html",
        priorities=AGENCY_PRIORITIES,
        query=", ".join(terms),
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