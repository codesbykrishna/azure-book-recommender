"""
Azure Functions (Python v2 programming model) backend.

EXISTING endpoints (unchanged):
  GET  /api/titles     — autocomplete list
  POST /api/recommend  — dataset-based recommendations + GPT explanations
  POST /api/chat       — GPT-5.4 chatbot

NEW endpoints:
  POST /api/book_details  — Option 1: search by title → Google Books metadata
                            + check if book exists in dataset
  POST /api/scan_cover    — Option 2: upload image → Vision OCR → ISBN/title
                            → Google Books metadata + dataset existence check

Required app settings:
  STORAGE_CONNECTION_STR, BLOB_CONTAINER
  AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_KEY, AZURE_OPENAI_DEPLOYMENT, AZURE_OPENAI_API_VERSION
  TRANSLATOR_KEY, TRANSLATOR_ENDPOINT, TRANSLATOR_REGION
  GOOGLE_BOOKS_API_KEY
  VISION_ENDPOINT, VISION_KEY                        ← new for scan_cover
"""

import os
import json
import logging
import base64

import azure.functions as func
from azure.storage.blob import BlobServiceClient

# ── Existing helpers (unchanged) ───────────────────────────────────────────────
from similarity import fuzzy_match_title, get_recommendations
from openai_helper import generate_recommendation_explanation, chat_with_assistant
from translator_helper import translate_text

# ── Existing Google Books helper (search_book kept as-is) ─────────────────────
from google_books import search_book, search_by_title, search_by_isbn

# ── New Vision helper ─────────────────────────────────────────────────────────
from vision_helper import extract_book_info_from_cover

app = func.FunctionApp()

STORAGE_CONNECTION_STR = os.environ.get("STORAGE_CONNECTION_STR", "")
BLOB_CONTAINER         = os.environ.get("BLOB_CONTAINER", "books")

_data_cache = None


# ── Shared utilities ───────────────────────────────────────────────────────────

def load_data():
    """
    Load enriched_data.json from Blob Storage.
    Cached in memory for the lifetime of the Function App instance.
    """
    global _data_cache
    if _data_cache is not None:
        return _data_cache

    if not STORAGE_CONNECTION_STR:
        raise RuntimeError("STORAGE_CONNECTION_STR app setting is not configured")

    blob_service = BlobServiceClient.from_connection_string(STORAGE_CONNECTION_STR)
    blob_client  = blob_service.get_blob_client(
        container=BLOB_CONTAINER, blob="enriched_data.json"
    )
    raw = blob_client.download_blob().readall()
    _data_cache = json.loads(raw)
    logging.info(f"Loaded {len(_data_cache)} books into cache")
    return _data_cache


def _json_response(payload, status_code=200):
    return func.HttpResponse(
        json.dumps(payload, ensure_ascii=False),
        status_code=status_code,
        mimetype="application/json",
        headers={"Access-Control-Allow-Origin": "*"},
    )


def _check_dataset_existence(google_title: str, data: list):
    """
    Check whether a book (identified by its Google Books title) exists
    in enriched_data.json using fuzzy matching.

    Returns (dataset_book | None, match_score).
    A score_cutoff of 70 is used — stricter than /recommend (40) to
    avoid false positives when deciding recommendation eligibility.
    """
    if not google_title:
        return None, 0
    return fuzzy_match_title(google_title, data, score_cutoff=70)


def _build_recommendations(liked_book, data, language, top_n=5):
    """
    Run the existing recommendation engine and return the formatted list.
    Reuses get_recommendations + generate_recommendation_explanation + translate_text.
    Recommendations come ONLY from the dataset — never from Google Books.
    """
    top_matches   = get_recommendations(liked_book, data, top_n=top_n)
    recommendations = []

    for score, book, shared_phrases in top_matches:
        explanation_en = generate_recommendation_explanation(liked_book, book, shared_phrases)
        explanation    = translate_text(explanation_en, language)
        recommendations.append({
            "index":         book["index"],
            "title":         book["title"],
            "genre":         book["genre"],
            "score":         round(score, 3),
            "shared_themes": shared_phrases[:5],
            "explanation":   explanation,
        })

    return recommendations


# ══════════════════════════════════════════════════════════════════════════════
# EXISTING ENDPOINTS — NOT MODIFIED
# ══════════════════════════════════════════════════════════════════════════════

@app.route(route="titles", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def get_titles(req: func.HttpRequest) -> func.HttpResponse:
    """Return lightweight title list for autocomplete."""
    try:
        data = load_data()
    except Exception as exc:
        logging.error(f"get_titles failed: {exc}")
        return _json_response({"error": str(exc)}, status_code=500)

    titles = [{"index": b["index"], "title": b["title"], "genre": b["genre"]} for b in data]
    return _json_response(titles)


@app.route(route="recommend", methods=["POST"], auth_level=func.AuthLevel.ANONYMOUS)
def recommend(req: func.HttpRequest) -> func.HttpResponse:
    """
    Existing recommendation endpoint.
    FIX: google_book is now fetched AFTER the dataset existence check
         so we don't waste a Google API call when no book is found.
    """
    try:
        body = req.get_json()
    except ValueError:
        return _json_response({"error": "Request body must be JSON"}, status_code=400)

    book_title = (body.get("book_title") or "").strip()
    language   = (body.get("language")   or "en").strip()
    top_n      = int(body.get("top_n")   or 5)

    if not book_title:
        return _json_response({"error": "book_title is required"}, status_code=400)

    try:
        data = load_data()
    except Exception as exc:
        logging.error(f"recommend failed loading data: {exc}")
        return _json_response({"error": str(exc)}, status_code=500)

    # Dataset check first — avoids a wasted Google API call on no-match
    liked_book, match_score = fuzzy_match_title(book_title, data)
    if liked_book is None:
        return _json_response(
            {"error": f"No book found matching '{book_title}'"}, status_code=404
        )

    # Google Books call only after confirming dataset match
    google_book = search_book(liked_book["title"])

    recommendations = _build_recommendations(liked_book, data, language, top_n)

    return _json_response({
        "matched_book": {
            "index":       liked_book["index"],
            "title":       liked_book["title"],
            "genre":       liked_book["genre"],
            "match_score": match_score,
        },
        "google_book":    google_book,
        "recommendations": recommendations,
    })


@app.route(route="chat", methods=["POST"], auth_level=func.AuthLevel.ANONYMOUS)
def chat(req: func.HttpRequest) -> func.HttpResponse:
    """Existing GPT-5.4 chatbot endpoint — unchanged."""
    try:
        body = req.get_json()
    except ValueError:
        return _json_response({"error": "Request body must be JSON"}, status_code=400)

    message  = (body.get("message")  or "").strip()
    history  =  body.get("history")  or []
    language = (body.get("language") or "en").strip()

    if not message:
        return _json_response({"error": "message is required"}, status_code=400)

    try:
        data = load_data()
    except Exception as exc:
        logging.error(f"chat failed loading data: {exc}")
        return _json_response({"error": str(exc)}, status_code=500)

    candidate_books = []
    seen_genres     = set()
    liked_book, score = fuzzy_match_title(message, data, score_cutoff=60)
    if liked_book:
        candidate_books.append(liked_book)
        for s, b, _shared in get_recommendations(liked_book, data, top_n=5):
            candidate_books.append(b)
    else:
        for b in data:
            genre = (b.get("genre") or "").lower()
            if genre and genre in message.lower() and genre not in seen_genres:
                candidate_books.append(b)
                seen_genres.add(genre)
            if len(candidate_books) >= 5:
                break

    reply_en = chat_with_assistant(message, history, candidate_books)
    reply    = translate_text(reply_en, language)
    return _json_response({"reply": reply})


# ══════════════════════════════════════════════════════════════════════════════
# NEW ENDPOINT 1 — /api/book_details  (Option 1: Search Book)
# ══════════════════════════════════════════════════════════════════════════════

@app.route(route="book_details", methods=["POST"], auth_level=func.AuthLevel.ANONYMOUS)
def book_details(req: func.HttpRequest) -> func.HttpResponse:
    """
    Option 1 — Search Book by title.

    Request body:
      {
        "title":    "Harry Potter",
        "language": "en",   (optional, default "en")
        "top_n":    5        (optional, default 5)
      }

    Response:
      {
        "google_book":       { ...full metadata... },
        "in_dataset":        true | false,
        "dataset_book":      { index, title, genre } | null,
        "recommendations":   [ ...from dataset only... ] | null,
        "unavailable_msg":   null | "This book is not available..."
      }
    """
    try:
        body = req.get_json()
    except ValueError:
        return _json_response({"error": "Request body must be JSON"}, status_code=400)

    title    = (body.get("title")    or "").strip()
    language = (body.get("language") or "en").strip()
    top_n    = int(body.get("top_n") or 5)

    if not title:
        return _json_response({"error": "title is required"}, status_code=400)

    # 1. Fetch from Google Books
    google_book = search_by_title(title)
    if not google_book:
        return _json_response(
            {"error": f"No Google Books result found for '{title}'"},
            status_code=404,
        )

    # 2. Load dataset and check existence
    try:
        data = load_data()
    except Exception as exc:
        logging.error(f"book_details failed loading data: {exc}")
        return _json_response({"error": str(exc)}, status_code=500)

    # Use the clean Google Books title for dataset matching (more reliable
    # than the raw user query which may be a partial / typo'd title)
    dataset_book, match_score = _check_dataset_existence(google_book["title"], data)
    in_dataset = dataset_book is not None

    # 3. Generate recommendations only if book is in dataset
    recommendations  = None
    unavailable_msg  = None

    if in_dataset:
        recommendations = _build_recommendations(dataset_book, data, language, top_n)
    else:
        unavailable_msg = (
            "This book is not available in our recommendation dataset, "
            "so recommendations cannot be generated."
        )

    return _json_response({
        "google_book":      google_book,
        "in_dataset":       in_dataset,
        "dataset_book":     {
            "index": dataset_book["index"],
            "title": dataset_book["title"],
            "genre": dataset_book["genre"],
            "match_score": match_score,
        } if in_dataset else None,
        "recommendations":  recommendations,
        "unavailable_msg":  unavailable_msg,
    })


# ══════════════════════════════════════════════════════════════════════════════
# NEW ENDPOINT 2 — /api/scan_cover  (Option 2: Scan Book Cover)
# ══════════════════════════════════════════════════════════════════════════════

@app.route(route="scan_cover", methods=["POST"], auth_level=func.AuthLevel.ANONYMOUS)
def scan_cover(req: func.HttpRequest) -> func.HttpResponse:
    """
    Option 2 — Scan Book Cover image.

    Request body (JSON):
      {
        "image":        "<base64-encoded image bytes>",
        "content_type": "image/jpeg",   (optional, default image/jpeg)
        "language":     "en",           (optional)
        "top_n":        5               (optional)
      }

    Pipeline:
      image → Azure AI Vision OCR → detect ISBN or title
            → Google Books (by ISBN if found, else by title)
            → check dataset → recommendations if in dataset

    Response: same shape as /api/book_details, plus:
      {
        ...book_details fields...,
        "ocr": {
          "isbn":     "<detected ISBN>" | null,
          "title":    "<detected title>" | null,
          "all_text": ["line1", ...]
        }
      }
    """
    try:
        body = req.get_json()
    except ValueError:
        return _json_response({"error": "Request body must be JSON"}, status_code=400)

    image_b64    = (body.get("image")        or "").strip()
    content_type = (body.get("content_type") or "image/jpeg").strip()
    language     = (body.get("language")     or "en").strip()
    top_n        = int(body.get("top_n")     or 5)

    if not image_b64:
        return _json_response({"error": "image (base64) is required"}, status_code=400)

    # 1. Decode base64 image
    try:
        image_bytes = base64.b64decode(image_b64)
    except Exception:
        return _json_response({"error": "image must be valid base64"}, status_code=400)

    # 2. Azure AI Vision OCR
    ocr_result = extract_book_info_from_cover(image_bytes, content_type)
    detected_isbn  = ocr_result.get("isbn")
    detected_title = ocr_result.get("title")

    if not detected_isbn and not detected_title:
        return _json_response(
            {
                "error": "Could not detect a book title or ISBN from the image. "
                         "Please try a clearer photo.",
                "ocr":   ocr_result,
            },
            status_code=422,
        )

    # 3. Google Books lookup — ISBN takes priority over title
    if detected_isbn:
        google_book = search_by_isbn(detected_isbn)
        # Fall back to title search if ISBN lookup returns nothing
        if not google_book and detected_title:
            google_book = search_by_title(detected_title)
    else:
        google_book = search_by_title(detected_title)

    if not google_book:
        return _json_response(
            {
                "error": "Could not find this book in Google Books. "
                         "Please try searching by title instead.",
                "ocr":   ocr_result,
            },
            status_code=404,
        )

    # 4. Dataset existence check
    try:
        data = load_data()
    except Exception as exc:
        logging.error(f"scan_cover failed loading data: {exc}")
        return _json_response({"error": str(exc)}, status_code=500)

    dataset_book, match_score = _check_dataset_existence(google_book["title"], data)
    in_dataset = dataset_book is not None

    # 5. Recommendations only from dataset
    recommendations = None
    unavailable_msg = None

    if in_dataset:
        recommendations = _build_recommendations(dataset_book, data, language, top_n)
    else:
        unavailable_msg = (
            "This book is not available in our recommendation dataset, "
            "so recommendations cannot be generated."
        )

    return _json_response({
        "google_book":     google_book,
        "in_dataset":      in_dataset,
        "dataset_book":    {
            "index":       dataset_book["index"],
            "title":       dataset_book["title"],
            "genre":       dataset_book["genre"],
            "match_score": match_score,
        } if in_dataset else None,
        "recommendations": recommendations,
        "unavailable_msg": unavailable_msg,
        "ocr":             ocr_result,
    })
