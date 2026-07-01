"""
Azure Functions (Python v2 programming model) backend for the
Personalized Book Recommendation Engine.

Endpoints:
  GET  /api/titles     -> list of {index, title, genre} for autocomplete
  POST /api/recommend  -> fuzzy match + similarity + GPT explanation + translation
  POST /api/chat       -> conversational chatbot with translation

Required app settings:
  STORAGE_CONNECTION_STR, BLOB_CONTAINER
  AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_KEY, AZURE_OPENAI_DEPLOYMENT
  TRANSLATOR_KEY, TRANSLATOR_ENDPOINT, TRANSLATOR_REGION
  GOOGLE_BOOKS_API_KEY
"""

import os
import json
import logging

import azure.functions as func
from azure.storage.blob import BlobServiceClient

from similarity import fuzzy_match_title, get_recommendations
from openai_helper import generate_recommendation_explanation, chat_with_assistant
from translator_helper import translate_text
from google_books import search_book

app = func.FunctionApp()

STORAGE_CONNECTION_STR = os.environ.get("STORAGE_CONNECTION_STR", "")
BLOB_CONTAINER = os.environ.get("BLOB_CONTAINER", "books")

_data_cache = None


def load_data():
    """Load enriched_data.json from Blob Storage, cached for the lifetime
    of the Function App instance."""
    global _data_cache
    if _data_cache is not None:
        return _data_cache

    if not STORAGE_CONNECTION_STR:
        raise RuntimeError("STORAGE_CONNECTION_STR app setting is not configured")

    blob_service = BlobServiceClient.from_connection_string(STORAGE_CONNECTION_STR)
    blob_client = blob_service.get_blob_client(
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


@app.route(route="titles", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def get_titles(req: func.HttpRequest) -> func.HttpResponse:
    try:
        data = load_data()
    except Exception as exc:
        logging.error(f"get_titles failed: {exc}")
        return _json_response({"error": str(exc)}, status_code=500)

    titles = [{"index": b["index"], "title": b["title"], "genre": b["genre"]} for b in data]
    return _json_response(titles)


@app.route(route="recommend", methods=["POST"], auth_level=func.AuthLevel.ANONYMOUS)
def recommend(req: func.HttpRequest) -> func.HttpResponse:
    try:
        body = req.get_json()
    except ValueError:
        return _json_response({"error": "Request body must be JSON"}, status_code=400)

    book_title = (body.get("book_title") or "").strip()
    language = (body.get("language") or "en").strip()
    top_n = int(body.get("top_n") or 5)

    if not book_title:
        return _json_response({"error": "book_title is required"}, status_code=400)

    try:
        data = load_data()
    except Exception as exc:
        logging.error(f"recommend failed loading data: {exc}")
        return _json_response({"error": str(exc)}, status_code=500)

    # ── FIX: fuzzy match first, only call Google Books if a match is found ──
    liked_book, match_score = fuzzy_match_title(book_title, data)

    if liked_book is None:
        return _json_response(
            {"error": f"No book found matching '{book_title}'"}, status_code=404
        )

    # Google Books call happens only when we have a valid matched book
    google_book = search_book(liked_book["title"])

    top_matches = get_recommendations(liked_book, data, top_n=top_n)

    recommendations = []
    for score, book, shared_phrases in top_matches:
        explanation_en = generate_recommendation_explanation(liked_book, book, shared_phrases)
        explanation = translate_text(explanation_en, language)
        recommendations.append(
            {
                "index": book["index"],
                "title": book["title"],
                "genre": book["genre"],
                "score": round(score, 3),
                "shared_themes": shared_phrases[:5],
                "explanation": explanation,
            }
        )

    return _json_response(
        {
            "matched_book": {
                "index": liked_book["index"],
                "title": liked_book["title"],
                "genre": liked_book["genre"],
                "match_score": match_score,
            },
            "google_book": google_book,
            "recommendations": recommendations,
        }
    )


@app.route(route="chat", methods=["POST"], auth_level=func.AuthLevel.ANONYMOUS)
def chat(req: func.HttpRequest) -> func.HttpResponse:
    try:
        body = req.get_json()
    except ValueError:
        return _json_response({"error": "Request body must be JSON"}, status_code=400)

    message = (body.get("message") or "").strip()
    history = body.get("history") or []
    language = (body.get("language") or "en").strip()

    if not message:
        return _json_response({"error": "message is required"}, status_code=400)

    try:
        data = load_data()
    except Exception as exc:
        logging.error(f"chat failed loading data: {exc}")
        return _json_response({"error": str(exc)}, status_code=500)

    # Lightweight RAG: fuzzy-match the message against titles for context
    candidate_books = []
    seen_genres = set()
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
    reply = translate_text(reply_en, language)

    return _json_response({"reply": reply})
