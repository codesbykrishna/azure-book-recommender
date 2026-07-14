import os
import json
import csv
import time

from azure.ai.textanalytics import TextAnalyticsClient
from azure.core.credentials import AzureKeyCredential
from azure.storage.blob import BlobServiceClient

# ---------------------------------------------------------------------------
# Config (from environment variables)
# ---------------------------------------------------------------------------
LANGUAGE_ENDPOINT = os.environ["LANGUAGE_ENDPOINT"]
LANGUAGE_KEY = os.environ["LANGUAGE_KEY"]
STORAGE_CONNECTION_STR = os.environ["STORAGE_CONNECTION_STR"]
BLOB_CONTAINER = os.environ.get("BLOB_CONTAINER", "books")
DATA_CSV_PATH = os.environ.get("DATA_CSV_PATH", "data.csv")

# Azure AI Language key phrase extraction limits
MAX_DOC_CHARS = 5000      # API limit is 5120 chars per document, leave margin
BATCH_SIZE = 10           # API limit: max 10 documents per request


def read_books(csv_path):
    books = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            summary = (row.get("summary") or "").strip()
            books.append(
                {
                    "index": int(row["index"]),
                    "title": (row.get("title") or "").strip(),
                    "genre": (row.get("genre") or "").strip(),
                    "summary": summary,
                }
            )
    return books


def chunked(seq, size):
    for i in range(0, len(seq), size):
        yield seq[i : i + size]


def extract_key_phrases_for_all(books, client):
    """Mutates each book dict in-place, adding a 'keyphrases' list."""
    total = len(books)
    for batch_num, batch in enumerate(chunked(books, BATCH_SIZE)):
        documents = [b["summary"][:MAX_DOC_CHARS] or " " for b in batch]

        # Simple retry loop in case of transient throttling errors
        for attempt in range(5):
            try:
                results = client.extract_key_phrases(documents=documents)
                break
            except Exception as exc:
                wait = 2 ** attempt
                print(f"  Batch {batch_num}: error ({exc}); retrying in {wait}s")
                time.sleep(wait)
        else:
            results = [None] * len(batch)

        for book, result in zip(batch, results):
            if result is not None and not result.is_error:
                book["keyphrases"] = list(result.key_phrases)
            else:
                book["keyphrases"] = []

        done = min((batch_num + 1) * BATCH_SIZE, total)
        if batch_num % 20 == 0 or done == total:
            print(f"  Processed {done}/{total} books")


def main():
    print(f"Reading {DATA_CSV_PATH} ...")
    books = read_books(DATA_CSV_PATH)
    print(f"Loaded {len(books)} books")

    print("Connecting to Azure AI Language ...")
    ta_client = TextAnalyticsClient(
        endpoint=LANGUAGE_ENDPOINT, credential=AzureKeyCredential(LANGUAGE_KEY)
    )

    print("Extracting key phrases (this may take a while for ~12k books) ...")
    extract_key_phrases_for_all(books, ta_client)

    # --- Write enriched_data.json (full dataset, used by the backend) -----
    enriched_path = "enriched_data.json"
    with open(enriched_path, "w", encoding="utf-8") as f:
        json.dump(books, f, ensure_ascii=False)
    print(f"Wrote {enriched_path}")

    # --- Write titles.json (lightweight, used by frontend autocomplete) ---
    titles = [{"index": b["index"], "title": b["title"], "genre": b["genre"]} for b in books]
    titles_path = "titles.json"
    with open(titles_path, "w", encoding="utf-8") as f:
        json.dump(titles, f, ensure_ascii=False)
    print(f"Wrote {titles_path}")

    # --- Upload both to Blob Storage --------------------------------------
    print(f"Uploading to Blob Storage container '{BLOB_CONTAINER}' ...")
    blob_service = BlobServiceClient.from_connection_string(STORAGE_CONNECTION_STR)
    container_client = blob_service.get_container_client(BLOB_CONTAINER)
    try:
        container_client.create_container()
    except Exception:
        pass  # already exists

    for path, blob_name, content_type in [
        (enriched_path, "enriched_data.json", "application/json"),
        (titles_path, "titles.json", "application/json"),
    ]:
        with open(path, "rb") as f:
            container_client.upload_blob(
                name=blob_name,
                data=f,
                overwrite=True,
                content_settings=None,
            )
        print(f"  Uploaded {blob_name}")

    print("Done!")


if __name__ == "__main__":
    main()
