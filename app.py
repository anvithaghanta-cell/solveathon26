import os
import re
import logging
from typing import Any
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from flask import (
    Flask, jsonify, redirect, render_template,
    request, session, url_for
)
from dotenv import load_dotenv
load_dotenv()

from azure.ai.inference import ChatCompletionsClient
from azure.core.credentials import AzureKeyCredential

ai_client = ChatCompletionsClient(
    endpoint="https://aghanta-4230-resource.services.ai.azure.com/models",
    credential=AzureKeyCredential(os.environ.get("AZURE_OPENAI_KEY", "")),
)


def explain_grant_fit(grant: dict, user_context: dict) -> str:
    try:
        from azure.ai.inference.models import SystemMessage, UserMessage
        response = ai_client.complete(
            messages=[
                SystemMessage(content="You are a helpful grant advisor."),
                UserMessage(content=(
                    f"In 2-3 sentences, explain why this grant fits the user. Be specific.\n\n"
                    f"User: {user_context.get('org_type')} org, focus: {user_context.get('focus')}, "
                    f"description: {user_context.get('description')}, location: {user_context.get('location')}\n\n"
                    f"Grant: {grant.get('title')} | {grant.get('agency')} | {grant.get('summary','')[:400]}"
                )),
            ],
            model=os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-6.1-sol"),
        )
        return response.choices[0].message.content.strip()
    except Exception:
        logging.exception("Azure AI explanation failed")
        return ""

app = Flask(__name__)

# ── Secret key ────────────────────────────────────────────────────────────────
# For local dev the fallback string is fine.
# For production, create a file called .env in the same folder as app.py and
# add one line:   SECRET_KEY=paste-any-long-random-string-here
# Then load it with:  python-dotenv  (pip install python-dotenv)
# OR just set the environment variable before you run Flask:
#   Mac/Linux:  export SECRET_KEY="some-long-random-string"
#   Windows:    set SECRET_KEY=some-long-random-string
app.secret_key = os.environ.get("SECRET_KEY", "dev-only-change-before-deploying")

# Flask sessions store data in a cookie — 4 KB limit.
# We store grant pools server-side in a simple dict instead.
_server_sessions: dict = {}

GRANTS_SEARCH_URL  = "https://api.grants.gov/v1/api/search2"
GRANTS_DETAILS_URL = "https://api.grants.gov/v1/api/fetchOpportunity"

AGENCY_PRIORITIES = ["water", "flood", "resilience", "infrastructure"]
DATING_ROUNDS     = 5
DATING_POOL_SIZE  = 10

FUNDING_RANGES = {
    "small":  (0, 50_000),
    "medium": (50_000, 250_000),
    "large":  (250_000, 1_000_000),
    "xlarge": (1_000_000, float("inf")),
    "any":    (0, float("inf")),
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _safe_int(val: Any) -> int:
    try:
        return int(val) if val not in (None, "", "N/A") else 0
    except (TypeError, ValueError):
        return 0


def _in_range(val: Any, lo: int, hi: int) -> bool:
    try:
        v = int(val)
    except (TypeError, ValueError):
        return True
    if v == 0:
        return True
    return lo <= v <= hi


def normalize_opportunity(raw: dict) -> dict:
    oid = raw.get("id")
    return {
        "id":               oid or raw.get("number") or "unknown",
        "title":            raw.get("title") or "Untitled opportunity",
        "agency":           raw.get("agency") or raw.get("agencyName") or "Unknown agency",
        "category":         raw.get("category") or raw.get("oppCategory") or "General",
        "close_date":       raw.get("closeDate") or raw.get("deadline") or "Not specified",
        "url":              raw.get("link") or raw.get("url") or (
                                f"https://www.grants.gov/search-results-detail/{oid}" if oid else ""
                            ),
        "summary":          raw.get("summary") or raw.get("description") or "",
        "keywords":         raw.get("keywords") or [],
        "opportunity_id":   oid,
        "award_floor_num":  _safe_int(raw.get("awardFloor")),
        "award_ceiling_num":_safe_int(raw.get("awardCeiling")),
    }


def fetch_opportunity_details(opportunity_id: Any) -> dict:
    resp = requests.post(GRANTS_DETAILS_URL, json={"opportunityId": opportunity_id}, timeout=30)
    resp.raise_for_status()
    payload = resp.json()
    if payload.get("errorcode") not in (0, None):
        raise RuntimeError(f"Grants.gov details error: {payload}")
    synopsis = (payload.get("data") or {}).get("synopsis") or {}
    return {
        "summary":           synopsis.get("synopsisDesc"),
        "award_floor":       synopsis.get("awardFloorFormatted"),
        "award_ceiling":     synopsis.get("awardCeilingFormatted"),
        "award_floor_num":   _safe_int(synopsis.get("awardFloor")),
        "award_ceiling_num": _safe_int(synopsis.get("awardCeiling")),
        "estimated_funding": synopsis.get("estimatedFundingFormatted"),
        "close_date":        synopsis.get("responseDate") or synopsis.get("closeDate"),
    }


def find_opportunities(keyword: str, rows: int = 20) -> list[dict]:
    resp = requests.post(
        GRANTS_SEARCH_URL,
        json={"rows": rows, "keyword": keyword,
              "oppStatuses": "forecasted|posted", "startRecordNum": 0},
        timeout=30,
    )
    resp.raise_for_status()
    payload = resp.json()
    if payload.get("errorcode") not in (0, None):
        raise RuntimeError(f"Grants.gov API error: {payload}")
    return [normalize_opportunity(h) for h in payload.get("data", {}).get("oppHits", []) if h]


def find_opportunities_for_terms(terms: list[str], limit: int = 20) -> list[dict]:
    seen_ids: set = set()
    results: list = []
    for term in terms:
        for item in find_opportunities(term):
            if item["id"] not in seen_ids:
                seen_ids.add(item["id"])
                results.append(item)
            if len(results) >= limit:
                break
        if len(results) >= limit:
            break

    detail_items = [r for r in results if r.get("opportunity_id")]
    if detail_items:
        with ThreadPoolExecutor(max_workers=min(5, len(detail_items))) as ex:
            futures = {ex.submit(fetch_opportunity_details, r["opportunity_id"]): r
                       for r in detail_items}
            for fut in as_completed(futures):
                item = futures[fut]
                try:
                    item.update({k: v for k, v in fut.result().items()
                                 if v is not None and v != ""})
                except (requests.RequestException, RuntimeError):
                    logging.exception("Detail fetch failed for %s", item["id"])
    return results


# ── Main routes ───────────────────────────────────────────────────────────────


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
    return render_template("index.html", priorities=AGENCY_PRIORITIES,
                           query="", results=[], min_award="", max_award="")


@app.post("/search")
def search():
    raw_query = request.form.get("keyword", "").strip() or ", ".join(AGENCY_PRIORITIES)
    min_award = request.form.get("min_award", "").strip()
    max_award = request.form.get("max_award", "").strip()
    terms     = [t.strip() for t in raw_query.split(",") if t.strip()] or list(AGENCY_PRIORITIES)
    results   = find_opportunities_for_terms(terms)

    try:
        lo = int(min_award) if min_award else 0
        hi = int(max_award) if max_award else 999_999_999
        if min_award or max_award:
            results = [r for r in results
                       if _in_range(r.get("award_ceiling_num") or r.get("award_floor_num") or 0, lo, hi)]
    except ValueError:
        pass

    return render_template("index.html", priorities=AGENCY_PRIORITIES,
                           query=raw_query, results=results,
                           min_award=min_award, max_award=max_award)


def extract_terms(org_type: str, focus: str, description: str, location: str) -> list[str]:
    """Build a ranked list of search terms from quiz answers."""
    terms = []

    focus_map = {
        "education":     ["education", "school", "community college", "workforce training"],
        "health":        ["health", "human services", "mental health", "public health"],
        "justice":       ["public safety", "justice", "corrections", "emergency management"],
        "environment":   ["environment", "natural resources", "agriculture", "wildlife", "conservation"],
        "economic":      ["economic development", "commerce", "workforce", "small business"],
        "transportation":["transportation", "infrastructure", "roads", "transit"],
        "housing":       ["housing", "affordable housing", "community development"],
        "technology":    ["technology", "digital", "broadband", "cybersecurity"],
    }
    if focus:
        terms.extend(focus_map.get(focus, [focus]))

    org_map = {
        "nonprofit": ["nonprofit", "community"], "government": ["municipality", "local government"],
        "university": ["research", "university"], "business": ["small business"],
        "tribal": ["tribal", "indigenous"], "individual": [],
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
            seen.add(t); unique.append(t)
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
    empty = {"org_type":"","focus":"","description":"","location":"","funding_size":"","min_award":"","max_award":""}
    if request.method == "GET":
        return render_template("quiz.html", priorities=AGENCY_PRIORITIES, error=None, form=empty, current_step=1)

    # Collect form values
    org_type = request.form.get("org_type", "").strip()
    focus = request.form.get("focus", "").strip()
    description = request.form.get("description", "").strip()
    location = request.form.get("location", "").strip()
    funding_size = request.form.get("funding_size", "").strip()
    min_award = request.form.get("min_award", "").strip()
    max_award = request.form.get("max_award", "").strip()

    if not org_type or not focus:
        return render_template("quiz.html", priorities=AGENCY_PRIORITIES,
                               error="Please select your organization type and project focus.",
                               form=request.form, current_step=1)

    terms   = extract_terms(org_type, focus, description, location)
    results = find_opportunities_for_terms(terms[:5])
    for r in results:
        r["score"] = score_result(r, terms)
    results.sort(key=lambda r: -r["score"])

    try:
        lo = int(min_award) if min_award else 0
        hi = int(max_award) if max_award else 999_999_999
        if min_award or max_award:
            results = [r for r in results
                       if _in_range(r.get("award_ceiling_num") or r.get("award_floor_num") or 0, lo, hi)]
    except ValueError:
        pass

        # Filter by funding size
    if funding_size and funding_size != "any":
        results = [r for r in results if within_funding_range(r, funding_size)]

    # Generate AI explanations for top 5 results
    user_context = {
        "org_type": org_type,
        "focus": focus,
        "description": description,
        "location": location,
    }
    for result in results[:5]:
        result["ai_explanation"] = explain_grant_fit(result, user_context)

    return render_template(
        "quiz_results.html",
        terms=terms[:5],
        results=results,
        funding_size=funding_size,
    )


@app.get("/api/search")
def api_search():
    keyword = request.args.get("keyword","").strip()
    terms   = [t.strip() for t in keyword.split(",") if t.strip()] or list(AGENCY_PRIORITIES)
    return jsonify({"results": find_opportunities_for_terms(terms)})


# ── Speed Dating routes ───────────────────────────────────────────────────────

def _sid() -> str:
    """Get or create a server-side session id stored in the cookie."""
    import uuid
    if "sid" not in session:
        session["sid"] = str(uuid.uuid4())
    return session["sid"]


def _sdata() -> dict:
    return _server_sessions.setdefault(_sid(), {})


def _size_bucket(ceiling: int) -> str:
    if ceiling <= 0:         return "unknown"
    if ceiling <= 50_000:    return "small"
    if ceiling <= 500_000:   return "mid"
    if ceiling <= 5_000_000: return "large"
    return "xlarge"


def _learn_preferences(liked: list[dict], disliked: list[dict]) -> dict:
    profile: dict = {"agencies":{}, "categories":{}, "size_buckets":{}, "keywords":{}}

    def _apply(grant: dict, weight: float):
        ag  = grant.get("agency","")
        cat = grant.get("category","")
        bkt = _size_bucket(_safe_int(grant.get("award_ceiling_num",0)))
        if ag:  profile["agencies"][ag]    = profile["agencies"].get(ag,0)    + weight
        if cat: profile["categories"][cat] = profile["categories"].get(cat,0) + weight
        if bkt != "unknown":
            profile["size_buckets"][bkt] = profile["size_buckets"].get(bkt,0) + weight
        if weight > 0:
            text = (grant.get("title","") + " " + grant.get("summary","")).lower()
            skip = {"that","this","with","from","will","have","been","they","them","their",
                    "also","into","more","some","grant","fund","program","project","federal","state"}
            for word in re.findall(r"[a-z]{4,}", text):
                if word not in skip:
                    profile["keywords"][word] = profile["keywords"].get(word,0) + 1

    for g in liked:    _apply(g,  1.0)
    for g in disliked: _apply(g, -0.5)
    return profile


def _score_by_profile(grant: dict, profile: dict) -> float:
    ag  = grant.get("agency","")
    cat = grant.get("category","")
    bkt = _size_bucket(_safe_int(grant.get("award_ceiling_num",0)))
    score  = profile["agencies"].get(ag,0)   * 2.0
    score += profile["categories"].get(cat,0) * 1.5
    score += profile["size_buckets"].get(bkt,0) * 1.0
    text   = (grant.get("title","") + " " + grant.get("summary","")).lower()
    for word, freq in profile["keywords"].items():
        if word in text:
            score += freq * 0.3
    return round(score, 3)


@app.get("/date")
def date_start():
    _sid()  # ensure sid exists, but don't clear data
    return render_template("date_start.html")


@app.post("/date/begin")
def date_begin():
    sid = _sid()
    _server_sessions.pop(sid, None)  # clear here instead
    pool = find_opportunities_for_terms(
        AGENCY_PRIORITIES + ["health","education","housing","broadband","workforce"],
        limit=DATING_POOL_SIZE,
    )
    data = _sdata()
    data["pool"]     = pool
    data["index"]    = 0
    data["liked"]    = []
    data["disliked"] = []
    return redirect(url_for("date_card"))


@app.get("/date/card")
def date_card():
    data = _sdata()
    pool = data.get("pool", [])
    idx  = data.get("index", 0)

    if not pool:
        return redirect(url_for("date_start"))
    if idx >= DATING_ROUNDS or idx >= len(pool):
        return redirect(url_for("date_results"))

    return render_template(
        "date_card.html",
        grant       = pool[idx],
        round_num   = idx + 1,
        total_rounds= DATING_ROUNDS,
        liked_count = len(data.get("liked", [])),
    )


@app.post("/date/swipe")
def date_swipe():
    data     = _sdata()
    pool     = data.get("pool", [])
    idx      = data.get("index", 0)
    liked    = data.get("liked", [])
    disliked = data.get("disliked", [])
    decision = request.form.get("decision")
    grant_id = request.form.get("grant_id")

    current = next((g for g in pool if str(g.get("id")) == grant_id), None)
    if current:
        (liked if decision == "like" else disliked).append(current)

    idx += 1
    data["index"]    = idx
    data["liked"]    = liked
    data["disliked"] = disliked

    if idx >= DATING_ROUNDS:
        return redirect(url_for("date_results"))
    return redirect(url_for("date_card"))


@app.get("/date/results")
def date_results():
    data     = _sdata()
    pool     = data.get("pool", [])
    liked    = data.get("liked", [])
    disliked = data.get("disliked", [])

    if not pool:
        return redirect(url_for("date_start"))

    seen_ids  = {g["id"] for g in liked} | {g["id"] for g in disliked}
    profile   = _learn_preferences(liked, disliked)
    unseen    = [g for g in pool if g["id"] not in seen_ids]

    top_kws = sorted(profile["keywords"], key=lambda k: -profile["keywords"][k])[:4]
    if top_kws:
        extras    = find_opportunities_for_terms(top_kws)
        extra_ids = {g["id"] for g in unseen} | seen_ids
        for g in extras:
            if g["id"] not in extra_ids:
                unseen.append(g); extra_ids.add(g["id"])

    for g in unseen:
        g["match_score"] = _score_by_profile(g, profile)

    return render_template(
        "date_results.html",
        liked          = liked,
        disliked       = disliked,
        recommendations= sorted(unseen, key=lambda g: -g["match_score"])[:15],
        profile        = profile,
    )


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=int(os.getenv("PORT", "5000")))