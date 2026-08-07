"""
Azure Functions (Python v2 programming model) backend for the
Personalized Book Recommendation Engine.
 
Endpoints:
  GET  /api/titles         — autocomplete list
  POST /api/recommend      — dataset recommendations + GPT explanations
  POST /api/chat           — GPT chatbot
  POST /api/book_details   — Google Books metadata + dataset check + recommendations
  POST /api/scan_cover     — image OCR → Google Books → dataset check
  GET  /api/user_profile   — get/create user profile in Cosmos DB
  POST /api/user_profile   — update user profile in Cosmos DB
 
Required app settings:
  STORAGE_CONNECTION_STR, BLOB_CONTAINER
  AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_KEY, AZURE_OPENAI_DEPLOYMENT, AZURE_OPENAI_API_VERSION
  TRANSLATOR_KEY, TRANSLATOR_ENDPOINT, TRANSLATOR_REGION
  GOOGLE_BOOKS_API_KEY
  VISION_ENDPOINT, VISION_KEY
  COSMOS_CONNECTION_STR, COSMOS_DATABASE, COSMOS_USERS_CONTAINER
"""
 
import os
import json
import logging
import base64
 
import azure.functions as func
from azure.storage.blob import BlobServiceClient
 
from similarity        import fuzzy_match_title, get_recommendations
from openai_helper     import generate_recommendation_explanation, chat_with_assistant
from translator_helper import translate_text
from google_books      import search_book, search_by_title, search_by_isbn
from vision            import extract_book_info_from_cover
from cosmos_helper     import get_or_create_user, update_user_profile, toggle_favorite, add_search_history
 
app = func.FunctionApp()
 
STORAGE_CONNECTION_STR = os.environ.get("STORAGE_CONNECTION_STR", "")
BLOB_CONTAINER         = os.environ.get("BLOB_CONTAINER", "books")
 
_data_cache = None
 
 
# ── Shared helpers ─────────────────────────────────────────────────────────────
 
def load_data():
    """
    Load enriched_data.json from Blob Storage.
    Cached in memory for the lifetime of the Function App instance.
    Recommendations come ONLY from this dataset — never from Google Books.
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
    raw         = blob_client.download_blob().readall()
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
 
 
def _dataset_lookup(google_title, data):
    """
    Fuzzy-match a Google Books title against enriched_data.json.
    Uses score_cutoff=70 to avoid false positives.
    Returns (book | None, score).
    """
    if not google_title:
        return None, 0
    return fuzzy_match_title(google_title, data, score_cutoff=70)
 
 
def _build_recommendations(liked_book, data, language, top_n):
    """
    Run the existing recommendation engine and return the formatted list.
    The recommended BOOKS themselves come only from the dataset — never
    from Google Books — but we do call Google Books per recommendation
    just to fetch a cover thumbnail for display. If that lookup fails or
    Google has no cover for that title, thumbnail is simply None and the
    frontend falls back to its placeholder.
    """
    top_matches     = get_recommendations(liked_book, data, top_n=top_n)
    recommendations = []
 
    for score, book, shared_phrases in top_matches:
        explanation_en = generate_recommendation_explanation(liked_book, book, shared_phrases)
        explanation    = translate_text(explanation_en, language)
 
        thumbnail = None
        try:
            gb = search_book(book["title"])
            if gb and gb.get("thumbnail"):
                thumbnail = gb["thumbnail"]
                if thumbnail.startswith("http://"):
                    thumbnail = thumbnail.replace("http://", "https://", 1)
        except Exception as exc:
            logging.warning(f"_build_recommendations: cover lookup failed for '{book['title']}': {exc}")
 
        recommendations.append({
            "index":         book["index"],
            "title":         book["title"],
            "genre":         book["genre"],
            "score":         round(score, 3),
            "shared_themes": shared_phrases[:5],
            "explanation":   explanation,
            "thumbnail":     thumbnail,
        })
 
    return recommendations
 
 
def _get_swa_user(req):
    """
    Identify the logged-in user making this request.

    Primary path: the x-ms-client-principal header Azure Static Web Apps
    injects automatically — but ONLY when a request is proxied through
    the Static Web App's own domain to a linked backend. This Function
    App is called directly on its own azurewebsites.net domain (see
    API_BASE in app.js/book.js), which is a plain cross-origin request,
    so that header will normally be absent here.

    Fallback path: the frontend confirms sign-in itself via /.auth/me
    (same-origin, always reliable) and sends the user's id/email
    explicitly via X-User-Id / X-User-Email headers. This is a
    reasonable tradeoff for a project at this scale, but note it does
    mean the backend trusts what the client claims rather than a
    cryptographically-verified identity — fine for a personal book app,
    not something to reuse as-is for anything handling sensitive data.
    The proper long-term fix is linking this Function App as the
    Static Web App's official backend (Standard plan) and calling it
    via a same-origin relative path instead of the direct URL.
    """
    header = req.headers.get("x-ms-client-principal", "")
    if header:
        try:
            decoded   = base64.b64decode(header).decode("utf-8")
            principal = json.loads(decoded)
            if "authenticated" in principal.get("userRoles", []):
                return principal
        except Exception:
            pass

    user_id = req.headers.get("X-User-Id", "")
    email   = req.headers.get("X-User-Email", "")
    if user_id:
        return {"userId": user_id, "userDetails": email, "userRoles": ["authenticated"]}

    return None


def _record_history_if_signed_in(req, book):
    """
    Best-effort: if the caller is signed in, record this book in their
    search history. Never raises — history is a nice-to-have, and a
    Cosmos hiccup here should never break a recommend/book_details
    response the user is actually waiting on.
    """
    principal = _get_swa_user(req)
    if not principal:
        return
    try:
        add_search_history(principal["userId"], book)
    except Exception as exc:
        logging.warning(f"_record_history_if_signed_in failed: {exc}")
 
 
# ══════════════════════════════════════════════════════════════════════════════
# ENDPOINT 1 — GET /api/titles
# ══════════════════════════════════════════════════════════════════════════════
 
@app.route(route="titles", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def get_titles(req: func.HttpRequest) -> func.HttpResponse:
    """Return lightweight title list for frontend autocomplete."""
    try:
        data = load_data()
    except Exception as exc:
        logging.error(f"get_titles failed: {exc}")
        return _json_response({"error": str(exc)}, status_code=500)
 
    titles = [{"index": b["index"], "title": b["title"], "genre": b["genre"]} for b in data]
    return _json_response(titles)
 
 
# ══════════════════════════════════════════════════════════════════════════════
# ENDPOINT 2 — POST /api/recommend
# ══════════════════════════════════════════════════════════════════════════════
 
@app.route(route="recommend", methods=["POST"], auth_level=func.AuthLevel.ANONYMOUS)
def recommend(req: func.HttpRequest) -> func.HttpResponse:
    """Dataset-based recommendations with GPT explanations and translation."""
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
        logging.error(f"recommend: load_data failed: {exc}")
        return _json_response({"error": str(exc)}, status_code=500)
 
    liked_book, match_score = fuzzy_match_title(book_title, data)
    if liked_book is None:
        return _json_response(
            {"error": f"No book found matching '{book_title}'"}, status_code=404
        )
 
    google_book     = search_book(liked_book["title"])
    recommendations = _build_recommendations(liked_book, data, language, top_n)
    _record_history_if_signed_in(req, liked_book)
 
    return _json_response({
        "matched_book": {
            "index":       liked_book["index"],
            "title":       liked_book["title"],
            "genre":       liked_book["genre"],
            "match_score": match_score,
        },
        "google_book":     google_book,
        "recommendations": recommendations,
    })
 
 
# ══════════════════════════════════════════════════════════════════════════════
# ENDPOINT 3 — POST /api/chat
# ══════════════════════════════════════════════════════════════════════════════
 
@app.route(route="chat", methods=["POST"], auth_level=func.AuthLevel.ANONYMOUS)
def chat(req: func.HttpRequest) -> func.HttpResponse:
    """GPT chatbot with lightweight RAG and translation."""
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
        logging.error(f"chat: load_data failed: {exc}")
        return _json_response({"error": str(exc)}, status_code=500)
 
    candidate_books    = []
    seen_genres        = set()
    liked_book, _score = fuzzy_match_title(message, data, score_cutoff=60)
 
    if liked_book:
        candidate_books.append(liked_book)
        for _s, b, _shared in get_recommendations(liked_book, data, top_n=5):
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
# ENDPOINT 4 — POST /api/book_details
# ══════════════════════════════════════════════════════════════════════════════
 
@app.route(route="book_details", methods=["POST"], auth_level=func.AuthLevel.ANONYMOUS)
def book_details(req: func.HttpRequest) -> func.HttpResponse:
    """
    Search a book by title.
    1. Fetch full metadata from Google Books.
    2. Check if book exists in dataset.
    3. If yes  → generate recommendations using existing engine.
    4. If no   → return book details with unavailable message.
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
 
    # Step 1 — Fetch from Google Books
    try:
        google_book = search_by_title(title)
    except Exception as exc:
        logging.error(f"book_details: Google Books API failed: {exc}")
        return _json_response({"error": "Failed to fetch book details"}, status_code=500)
 
    if google_book is None:
        return _json_response(
            {"error": f"Book not found: '{title}'"},
            status_code=404
        )
 
    # Translate description if language is not English
    if google_book.get("description") and language != "en":
        google_book["description"] = translate_text(
            google_book["description"],
            language
        )
 
    # Step 2 — Dataset existence check
    try:
        data = load_data()
    except Exception as exc:
        logging.error(f"book_details: load_data failed: {exc}")
        return _json_response({"error": str(exc)}, status_code=500)
 
    dataset_book, match_score = _dataset_lookup(google_book["title"], data)
    in_dataset = dataset_book is not None
 
    # Step 3 — Recommendations only if book exists in dataset
    recommendations = None
    unavailable_msg = None
 
    if in_dataset:
        recommendations = _build_recommendations(dataset_book, data, language, top_n)
        _record_history_if_signed_in(req, dataset_book)
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
    })
 
 
# ══════════════════════════════════════════════════════════════════════════════
# ENDPOINT 5 — POST /api/scan_cover
# ══════════════════════════════════════════════════════════════════════════════
 
@app.route(route="scan_cover", methods=["POST"], auth_level=func.AuthLevel.ANONYMOUS)
def scan_cover(req: func.HttpRequest) -> func.HttpResponse:
    """
    Upload a book cover image.
    Pipeline: base64 image → Vision OCR → ISBN or title
              → Google Books → dataset check → recommendations if found.
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
 
    # Step 1 — Decode base64
    try:
        image_bytes = base64.b64decode(image_b64)
    except Exception:
        return _json_response({"error": "image must be valid base64"}, status_code=400)
 
    # Step 2 — Azure AI Vision OCR
    ocr_result     = extract_book_info_from_cover(image_bytes, content_type)
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
 
    # Step 3 — Google Books lookup (ISBN takes priority over title)
    google_book = None
    if detected_isbn:
        google_book = search_by_isbn(detected_isbn)
    if not google_book and detected_title:
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
 
    # Step 4 — Dataset existence check
    try:
        data = load_data()
    except Exception as exc:
        logging.error(f"scan_cover: load_data failed: {exc}")
        return _json_response({"error": str(exc)}, status_code=500)
 
    dataset_book, match_score = _dataset_lookup(google_book["title"], data)
    in_dataset = dataset_book is not None
 
    # Step 5 — Recommendations only from dataset
    recommendations = None
    unavailable_msg = None
 
    if in_dataset:
        recommendations = _build_recommendations(dataset_book, data, language, top_n)
        _record_history_if_signed_in(req, dataset_book)
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
 
 
# ══════════════════════════════════════════════════════════════════════════════
# ENDPOINT 6 — GET /api/user_profile
# ══════════════════════════════════════════════════════════════════════════════
 
@app.route(route="user_profile", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def get_user_profile(req: func.HttpRequest) -> func.HttpResponse:
    """
    Returns the current user's profile from Cosmos DB.
    Creates the profile automatically on first login.
    Authentication: Azure SWA built-in (x-ms-client-principal header).
    """
    principal = _get_swa_user(req)
    if not principal:
        return _json_response({"error": "Not authenticated"}, status_code=401)
 
    user_id      = principal["userId"]
    email        = principal.get("userDetails", "")
    display_name = email.split("@")[0] if email else "Reader"
 
    try:
        profile = get_or_create_user(user_id, email, display_name)
    except Exception as exc:
        logging.error(f"get_user_profile failed: {exc}")
        return _json_response({"error": "Failed to load user profile"}, status_code=500)
 
    safe = {k: v for k, v in profile.items() if not k.startswith("_")}
    return _json_response(safe)
 
 
# ══════════════════════════════════════════════════════════════════════════════
# ENDPOINT 7 — POST /api/user_profile
# ══════════════════════════════════════════════════════════════════════════════
 
@app.route(route="user_profile", methods=["POST"], auth_level=func.AuthLevel.ANONYMOUS)
def update_user_profile_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    """
    Update displayName or preferences for the logged-in user.
    Request body (all fields optional):
    {
      "displayName": "Jane",
      "preferences": { "language": "es" }
    }
    """
    principal = _get_swa_user(req)
    if not principal:
        return _json_response({"error": "Not authenticated"}, status_code=401)
 
    try:
        body = req.get_json()
    except ValueError:
        return _json_response({"error": "Request body must be JSON"}, status_code=400)
 
    user_id = principal["userId"]
 
    try:
        updated = update_user_profile(user_id, body)
    except Exception as exc:
        logging.error(f"update_user_profile failed: {exc}")
        return _json_response({"error": "Failed to update profile"}, status_code=500)
 
    safe = {k: v for k, v in updated.items() if not k.startswith("_")}
    return _json_response(safe)


# ══════════════════════════════════════════════════════════════════════════════
# ENDPOINT 8 — POST /api/favorites
# ══════════════════════════════════════════════════════════════════════════════

@app.route(route="favorites", methods=["POST"], auth_level=func.AuthLevel.ANONYMOUS)
def toggle_favorite_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    """
    Add or remove a book from the logged-in user's favorites (toggle).
    Requires sign-in — returns 401 if not authenticated.

    Request body:
    {
      "index": 4281,                  (required — dataset book index)
      "title": "Book title",          (optional, stored for display)
      "genre": "fantasy"               (optional, stored for display)
    }

    Response: {"favorites": [...], "isFavorite": true|false}
    """
    principal = _get_swa_user(req)
    if not principal:
        return _json_response({"error": "Not authenticated"}, status_code=401)

    try:
        body = req.get_json()
    except ValueError:
        return _json_response({"error": "Request body must be JSON"}, status_code=400)

    if body.get("index") is None:
        return _json_response({"error": "index is required"}, status_code=400)

    user_id = principal["userId"]

    try:
        result = toggle_favorite(user_id, body)
    except Exception as exc:
        logging.error(f"toggle_favorite_endpoint failed: {exc}")
        return _json_response({"error": "Failed to update favorites"}, status_code=500)

    return _json_response(result)
