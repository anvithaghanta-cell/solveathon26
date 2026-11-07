import os
from typing import Any

import requests
from flask import Flask, jsonify, render_template, request

app = Flask(__name__)

GRANTS_SEARCH_URL = "https://api.grants.gov/v1/api/search2"

# Replace these with 3-5 priorities drawn from your selected NC agency's
# strategic plan. Keep a note of the plan section/page for your presentation.
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


def find_opportunities(keyword: str):
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


@app.get("/")
def index():
    return render_template("index.html", priorities=AGENCY_PRIORITIES, query="", results=[])


@app.post("/search")
def search():
    raw_query = request.form.get("keyword", "").strip()
    if not raw_query:
        raw_query = ", ".join(AGENCY_PRIORITIES)

    query_terms = [term.strip() for term in raw_query.split(",") if term.strip()]
    if not query_terms:
        query_terms = list(AGENCY_PRIORITIES)

    seen_ids = set()
    results = []
    for term in query_terms:
        for item in find_opportunities(term):
            if item["id"] not in seen_ids:
                seen_ids.add(item["id"])
                results.append(item)

    return render_template(
        "index.html",
        priorities=AGENCY_PRIORITIES,
        query=raw_query,
        results=results[:20],
    )


@app.get("/api/search")
def api_search():
    keyword = request.args.get("keyword", "").strip() or ", ".join(AGENCY_PRIORITIES)
    results = find_opportunities(keyword)
    return jsonify({"results": results[:20]})


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=int(os.getenv("PORT", "5000")))