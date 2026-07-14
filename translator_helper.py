"""
Helper for calling Azure Translator to localize recommendation explanations.

Required environment variables:
  TRANSLATOR_KEY       API key for the "translator-book" resource
  TRANSLATOR_ENDPOINT  e.g. https://api.cognitive.microsofttranslator.com
  TRANSLATOR_REGION    the resource's region, e.g. "eastus"
"""

import os
import logging
import requests

TRANSLATOR_KEY = os.environ.get("TRANSLATOR_KEY", "")
TRANSLATOR_ENDPOINT = os.environ.get(
    "TRANSLATOR_ENDPOINT", "https://api.cognitive.microsofttranslator.com"
)
TRANSLATOR_REGION = os.environ.get("TRANSLATOR_REGION", "")


def translate_text(text, target_language):
    """
    Translate `text` into `target_language` (e.g. 'es', 'fr', 'hi', 'th').
    Returns the original text unchanged if translation isn't configured,
    fails, or the target language is English/unspecified.
    """
    if not text or not target_language or target_language.lower() in ("en", "en-us"):
        return text

    if not (TRANSLATOR_KEY and TRANSLATOR_REGION):
        logging.warning("Azure Translator not configured - skipping translation")
        return text

    url = f"{TRANSLATOR_ENDPOINT.rstrip('/')}/translate"
    params = {"api-version": "3.0", "to": target_language}
    headers = {
        "Ocp-Apim-Subscription-Key": TRANSLATOR_KEY,
        "Ocp-Apim-Subscription-Region": TRANSLATOR_REGION,
        "Content-Type": "application/json",
    }
    body = [{"text": text}]

    try:
        resp = requests.post(url, params=params, headers=headers, json=body, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        return data[0]["translations"][0]["text"]
    except Exception as exc:
        logging.error(f"Translation failed: {exc}")
        return text
