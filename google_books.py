"""
Google Books API helper.

Functions:
  search_book(title)      — original function, unchanged (used by /api/recommend)
  search_by_title(title)  — full metadata fetch by title (used by /api/book_details)
  search_by_isbn(isbn)    — fetch by ISBN-10 or ISBN-13  (used by /api/scan_cover)

Required env var:
  GOOGLE_BOOKS_API_KEY
"""

import os
import requests

GOOGLE_BOOKS_API_KEY = os.getenv("GOOGLE_BOOKS_API_KEY")


def _extract_full_metadata(item):
    """
    Shared helper: extract full book metadata from a Google Books item dict.
    Returns a rich dict used by the Book Details page.
    """
    info = item.get("volumeInfo", {})

    # Extract ISBN-10 and ISBN-13 separately
    isbn_10 = ""
    isbn_13 = ""
    for identifier in info.get("industryIdentifiers", []):
        if identifier.get("type") == "ISBN_10":
            isbn_10 = identifier.get("identifier", "")
        elif identifier.get("type") == "ISBN_13":
            isbn_13 = identifier.get("identifier", "")

    # Prefer highest resolution thumbnail available, force HTTPS
    image_links = info.get("imageLinks", {})
    thumbnail = (
        image_links.get("extraLarge")
        or image_links.get("large")
        or image_links.get("medium")
        or image_links.get("small")
        or image_links.get("thumbnail")
        or ""
    )
    if thumbnail.startswith("http://"):
        thumbnail = thumbnail.replace("http://", "https://", 1)

    return {
        "title":               info.get("title", ""),
        "authors":             info.get("authors", []),
        "publisher":           info.get("publisher", ""),
        "publishedDate":       info.get("publishedDate", ""),
        "description":         info.get("description", ""),
        "pageCount":           info.get("pageCount"),
        "categories":          info.get("categories", []),
        "averageRating":       info.get("averageRating"),
        "ratingsCount":        info.get("ratingsCount"),
        "language":            info.get("language", ""),
        "thumbnail":           thumbnail,
        "infoLink":            info.get("infoLink", ""),
        "isbn10":              isbn_10,
        "isbn13":              isbn_13,
        # Kept for backward compatibility
        "industryIdentifiers": info.get("industryIdentifiers", []),
    }


# ── Original function — DO NOT MODIFY (used by /api/recommend) ────────────────

def search_book(title):
    """
    Original search function used by the /api/recommend endpoint.
    Kept exactly as-is for backward compatibility.
    """
    url = (
        "https://www.googleapis.com/books/v1/volumes"
        f"?q=intitle:{title}&key={GOOGLE_BOOKS_API_KEY}"
    )

    response = requests.get(url)

    if response.status_code != 200:
        return None

    data = response.json()

    if "items" not in data or len(data["items"]) == 0:
        return None

    volume = data["items"][0]["volumeInfo"]

    return {
        "title":               volume.get("title"),
        "authors":             volume.get("authors", []),
        "publisher":           volume.get("publisher"),
        "publishedDate":       volume.get("publishedDate"),
        "description":         volume.get("description"),
        "pageCount":           volume.get("pageCount"),
        "categories":          volume.get("categories", []),
        "averageRating":       volume.get("averageRating"),
        "language":            volume.get("language"),
        "thumbnail":           volume.get("imageLinks", {}).get("thumbnail"),
        "infoLink":            volume.get("infoLink"),
        "industryIdentifiers": volume.get("industryIdentifiers", [])
    }


# ── New functions (used by /api/book_details and /api/scan_cover) ─────────────

def search_by_title(title):
    """
    Search Google Books by title and return FULL metadata for the best match.
    Used by /api/book_details (Option 1 — Search Book).
    Returns a full metadata dict or None.
    """
    if not title:
        return None

    params = {
        "q":          f"intitle:{title}",
        "key":        GOOGLE_BOOKS_API_KEY,
        "maxResults": 1,
    }

    try:
        resp = requests.get(
            "https://www.googleapis.com/books/v1/volumes",
            params=params,
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return None

    items = data.get("items", [])
    if not items:
        return None

    return _extract_full_metadata(items[0])


def search_by_isbn(isbn):
    """
    Search Google Books by ISBN-10 or ISBN-13.
    Used by /api/scan_cover when OCR detects an ISBN.
    Returns a full metadata dict or None.
    """
    if not isbn:
        return None

    # Strip hyphens/spaces that may appear in scanned ISBNs
    isbn_clean = isbn.replace("-", "").replace(" ", "")

    params = {
        "q":          f"isbn:{isbn_clean}",
        "key":        GOOGLE_BOOKS_API_KEY,
        "maxResults": 1,
    }

    try:
        resp = requests.get(
            "https://www.googleapis.com/books/v1/volumes",
            params=params,
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return None

    items = data.get("items", [])
    if not items:
        return None

    return _extract_full_metadata(items[0])
