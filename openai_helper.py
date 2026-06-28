"""
Helper functions for calling an Azure OpenAI model deployed through
Azure AI Foundry.

Required environment variables (set as Function App settings):
  AZURE_OPENAI_ENDPOINT     e.g. https://<your-resource>.openai.azure.com/
  AZURE_OPENAI_KEY          API key for the Azure OpenAI / Foundry resource
  AZURE_OPENAI_DEPLOYMENT   the deployment name, e.g. "gpt-4o-mini"
  AZURE_OPENAI_API_VERSION  e.g. "2024-08-01-preview" (optional, has default)
"""

import os
import json
import logging
import requests
from openai import OpenAI

AZURE_OPENAI_ENDPOINT = os.environ.get("AZURE_OPENAI_ENDPOINT", "").rstrip("/")
AZURE_OPENAI_KEY = os.environ.get("AZURE_OPENAI_KEY", "")
AZURE_OPENAI_DEPLOYMENT = os.environ.get("AZURE_OPENAI_DEPLOYMENT", "")

client = None

if (
    AZURE_OPENAI_ENDPOINT
    and AZURE_OPENAI_KEY
    and AZURE_OPENAI_DEPLOYMENT
):
    client = OpenAI(
        base_url=f"{AZURE_OPENAI_ENDPOINT}/openai/v1",
        api_key=AZURE_OPENAI_KEY,
    )


def _chat_completion(prompt, max_tokens=300):
    if client is None:
        logging.warning("Azure OpenAI is not configured.")
        return None

    try:
        response = client.responses.create(
            model=AZURE_OPENAI_DEPLOYMENT,
            input=prompt,
            max_output_tokens=max_tokens,
        )

        return response.output_text.strip()

    except Exception as e:
        logging.error(f"Azure OpenAI call failed: {e}")
        return None


def generate_recommendation_explanation(liked_book, recommended_book, shared_phrases):
    shared = ", ".join(shared_phrases[:5]) if shared_phrases else "similar themes"

    fallback = (
        f'Because you enjoyed "{liked_book["title"]}", '
        f'"{recommended_book["title"]}" offers similar themes and may be a great next read.'
    )

    prompt = f"""
You are a friendly book recommendation assistant.

The user liked:

Title: {liked_book['title']}
Genre: {liked_book['genre']}
Summary:
{liked_book['summary']}

Recommend:

Title: {recommended_book['title']}
Genre: {recommended_book['genre']}
Summary:
{recommended_book['summary']}

Shared themes:
{shared}

Write ONE short natural recommendation sentence (maximum 40 words).
"""

    result = _chat_completion(prompt, 120)

    return result or fallback


def chat_with_assistant(message, history, candidate_books):
    context = ""

    for book in candidate_books[:8]:
        context += (
            f"Title: {book['title']}\n"
            f"Genre: {book['genre']}\n"
            f"Summary: {book['summary'][:250]}\n\n"
        )

    prompt = f"""
You are Shelf, a friendly AI book assistant.

Relevant books:

{context}

Conversation:

"""

    for turn in history[-6:]:
        prompt += f"{turn['role']}: {turn['content']}\n"

    prompt += f"\nUser: {message}\nAssistant:"

    result = _chat_completion(prompt, 300)

    return result or (
        "Sorry, I couldn't reach the recommendation assistant right now. Please try again."
    )
