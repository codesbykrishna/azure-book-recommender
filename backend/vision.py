"""
Azure AI Vision OCR helper for the Scan Book Cover feature.

Uses the Azure Computer Vision Read API (v3.2) to extract text from
an uploaded book cover image, then attempts to identify:
  1. An ISBN (13-digit or 10-digit number, with or without hyphens)
  2. A book title (the longest non-numeric text block found)

Required env vars:
  VISION_ENDPOINT   e.g. https://<resource>.cognitiveservices.azure.com/
  VISION_KEY        API key for the Azure AI Vision resource
"""

import os
import re
import time
import logging
import requests

VISION_ENDPOINT = os.environ.get("VISION_ENDPOINT", "")
VISION_KEY = os.environ.get("VISION_KEY", "")


def _read_api_url():
    return f"{VISION_ENDPOINT.rstrip('/')}/vision/v3.2/read/analyze"


def extract_text_from_image(image_bytes: bytes, content_type: str = "image/jpeg"):
    """
    Submit image bytes to Azure AI Vision Read API and return a list of
    all text lines detected (in the order they appear on the page).

    Returns [] on any error.
    """
    if not (VISION_ENDPOINT and VISION_KEY):
        logging.error("Azure Vision not configured (VISION_ENDPOINT / VISION_KEY missing)")
        return []

    headers = {
        "Ocp-Apim-Subscription-Key": VISION_KEY,
        "Content-Type": content_type,
    }

    # Step 1: Submit the image for async OCR
    try:
        submit = requests.post(
            _read_api_url(),
            headers=headers,
            data=image_bytes,
            timeout=20,
        )
        submit.raise_for_status()
    except Exception as exc:
        logging.error(f"Vision OCR submit failed: {exc}")
        return []

    # The operation URL is in the 'Operation-Location' response header
    operation_url = submit.headers.get("Operation-Location")
    if not operation_url:
        logging.error("Vision OCR: no Operation-Location header in response")
        return []

    # Step 2: Poll until the operation is complete (max ~10 seconds)
    poll_headers = {"Ocp-Apim-Subscription-Key": VISION_KEY}
    for attempt in range(10):
        time.sleep(1)
        try:
            poll = requests.get(operation_url, headers=poll_headers, timeout=15)
            poll.raise_for_status()
            result = poll.json()
        except Exception as exc:
            logging.error(f"Vision OCR poll failed: {exc}")
            return []

        status = result.get("status", "")
        if status == "succeeded":
            lines = []
            for page in result.get("analyzeResult", {}).get("readResults", []):
                for line in page.get("lines", []):
                    text = line.get("text", "").strip()
                    if text:
                        lines.append(text)
            return lines

        if status == "failed":
            logging.error("Vision OCR operation failed")
            return []

        # status is "running" or "notStarted" — keep polling

    logging.warning("Vision OCR timed out after 10 seconds")
    return []


# ── ISBN / title extraction ────────────────────────────────────────────────────

# Matches ISBN-13 (978/979 prefix) and ISBN-10 (legacy), with optional hyphens
_ISBN13_RE = re.compile(r'\b(97[89][- ]?(?:\d[- ]?){9}\d)\b')
_ISBN10_RE = re.compile(r'\b(\d[- ]?(?:\d[- ]?){8}[\dXx])\b')


def find_isbn(text_lines):
    """
    Search OCR text lines for an ISBN-13 or ISBN-10.
    Returns the first match found (digits only, no hyphens), or None.
    """
    full_text = " ".join(text_lines)

    # Prefer ISBN-13
    m13 = _ISBN13_RE.search(full_text)
    if m13:
        return re.sub(r'[- ]', '', m13.group(1))

    m10 = _ISBN10_RE.search(full_text)
    if m10:
        return re.sub(r'[- ]', '', m10.group(1))

    return None


def find_title(text_lines):
    """
    Heuristic: the book title is usually the longest text line that is
    NOT a pure number / barcode / ISBN / publisher boilerplate.
    Returns the best candidate line, or None.
    """
    candidates = []
    for line in text_lines:
        stripped = line.strip()
        if not stripped:
            continue
        # Skip lines that are purely numeric (barcodes, prices, page numbers)
        if re.fullmatch(r'[\d\s\-\.]+', stripped):
            continue
        # Skip very short lines (single letters, single words < 3 chars)
        if len(stripped) < 4:
            continue
        # Skip lines that look like ISBNs
        if _ISBN13_RE.search(stripped) or _ISBN10_RE.search(stripped):
            continue
        candidates.append(stripped)

    if not candidates:
        return None

    # The title is usually one of the first long lines on a cover
    # Score: earlier lines win ties; longer lines score higher
    scored = [(len(line), -i, line) for i, line in enumerate(candidates)]
    scored.sort(reverse=True)

    return scored[0][2]


def extract_book_info_from_cover(image_bytes: bytes, content_type: str = "image/jpeg"):
    """
    Full pipeline: OCR → find ISBN or title.

    Returns:
      {
        "isbn":  "<13 or 10 digit string>" | None,
        "title": "<detected title text>"   | None,
        "all_text": ["line1", "line2", ...]   (for debugging)
      }
    """
    lines = extract_text_from_image(image_bytes, content_type)
    isbn  = find_isbn(lines)
    title = find_title(lines) if not isbn else None  # title only needed if no ISBN

    return {
        "isbn":     isbn,
        "title":    title,
        "all_text": lines,
    }
