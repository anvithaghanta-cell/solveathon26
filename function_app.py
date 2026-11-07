import datetime
import json
import logging
import os

import azure.functions as func  # type: ignore[reportMissingImports]
import requests
from azure.storage.blob import BlobServiceClient  # type: ignore[reportMissingImports]

app = func.FunctionApp()

SEARCH_URL = "https://api.grants.gov/v1/api/search2"

# Replace these with relevant terms from your selected agency plan.
KEYWORDS = ["flood", "public health", "emergency preparedness"]

def fetch_matches():
    results_by_id = {}

    for keyword in KEYWORDS:
        start = 0

        # Keep this small for the prototype. Each query gets at most 10 pages.
        for _ in range(10):
            body = {
                "rows": 100,
                "keyword": keyword,
                "oppStatuses": "forecasted|posted",
                "startRecordNum": start,
            }

            response = requests.post(SEARCH_URL, json=body, timeout=30)
            response.raise_for_status()
            payload = response.json()

            if payload.get("errorcode") != 0:
                raise RuntimeError(f"Grants.gov API error: {payload}")

            data = payload.get("data", {})
            hits = data.get("oppHits", [])

            if not hits:
                break

            for hit in hits:
                # Deduplicate results found by more than one keyword.
                key = str(hit.get("id") or hit.get("number"))
                if key and key != "None":
                    results_by_id[key] = hit

            if len(hits) < 100:
                break

            start += len(hits)

    return list(results_by_id.values())


def save_to_blob(records):
    # Configure AzureWebJobsStorage in your Function App settings.
    connection_string = os.environ["AzureWebJobsStorage"]
    blob_service = BlobServiceClient.from_connection_string(connection_string)

    container = blob_service.get_container_client("grants-data")
    try:
        container.create_container()
    except Exception:
        # The container may already exist.
        pass

    document = {
        "source": "Grants.gov search2 API",
        "refreshed_at_utc": datetime.datetime.now(
            datetime.timezone.utc
        ).isoformat(),
        "keywords": KEYWORDS,
        "opportunities": records,
    }

    blob = container.get_blob_client("opportunities-latest.json")
    blob.upload_blob(json.dumps(document), overwrite=True)


@app.timer_trigger(
    schedule="0 0 */6 * * *",
    arg_name="timer",
    run_on_startup=False,
    use_monitor=True,
)
def refresh_grants(timer: func.TimerRequest) -> None:
    logging.info("Starting Grants.gov refresh.")

    # If the API call fails, the new file is not uploaded, so the previous
    # successful snapshot remains available.
    records = fetch_matches()
    save_to_blob(records)

    logging.info("Saved %s opportunity records.", len(records))