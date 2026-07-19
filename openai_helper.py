"""
Helper functions for calling an Azure OpenAI model deployed through
Azure AI Foundry.

This deployment (gpt-5.4) uses the newer Responses API
(POST {endpoint}/openai/v1/responses), confirmed from the Endpoint field
shown on the deployment's own page in Azure AI Foundry — NOT the legacy
Chat Completions API (POST {endpoint}/openai/deployments/{name}/chat/completions).
These are genuinely different request/response shapes, not just a
different URL path for the same thing.

Required environment variables (set as Function App settings):
  AZURE_OPENAI_ENDPOINT     e.g. https://<your-resource>.services.ai.azure.com
  AZURE_OPENAI_KEY          API key for the Azure OpenAI / Foundry resource
  AZURE_OPENAI_DEPLOYMENT   the deployment name, e.g. "gpt-5.4"

AZURE_OPENAI_API_VERSION is no longer used — the v1 Responses API is
GA and doesn't take an api-version query param.
"""

import os
import json
import logging
import requests

AZURE_OPENAI_ENDPOINT   = os.environ.get("AZURE_OPENAI_ENDPOINT", "")
AZURE_OPENAI_KEY        = os.environ.get("AZURE_OPENAI_KEY", "")
AZURE_OPENAI_DEPLOYMENT = os.environ.get("AZURE_OPENAI_DEPLOYMENT", "")


def _extract_output_text(data):
    """
    Pull the assistant's text out of a Responses API JSON body.
    Shape: {"output": [ {"type": "message", "content": [ {"type": "output_text", "text": "..."} ] } ]}
    There can be other item types (e.g. reasoning items) mixed into
    "output" — skip anything that isn't a message/output_text pair.
    """
    for item in data.get("output", []):
        if item.get("type") != "message":
            continue
        for content_item in item.get("content", []):
            if content_item.get("type") == "output_text" and content_item.get("text"):
                return content_item["text"].strip()
    return None


def _chat_completion(messages, max_tokens=300, temperature=0.7):
    """
    Low-level call to the Azure OpenAI Responses API.
    `messages` is the familiar [{"role": ..., "content": ...}, ...] list —
    we split the leading "system" message out into "instructions" and
    send the rest as "input", since that's what the Responses API expects.
    `temperature` is accepted for signature compatibility with existing
    callers but intentionally NOT sent — gpt-5-class models reject any
    value other than the default and it's not worth two request shapes.
    """
    if not (AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_KEY and AZURE_OPENAI_DEPLOYMENT):
        logging.warning("Azure OpenAI not configured - skipping LLM call")
        return None

    url = f"{AZURE_OPENAI_ENDPOINT.rstrip('/')}/openai/v1/responses"
    headers = {
        "Content-Type": "application/json",
        "api-key": AZURE_OPENAI_KEY,
    }

    instructions = None
    input_items  = []
    for m in messages:
        if m.get("role") == "system" and instructions is None:
            instructions = m.get("content", "")
        else:
            input_items.append({"role": m["role"], "content": m["content"]})

    body = {
        "model":            AZURE_OPENAI_DEPLOYMENT,
        "input":            input_items,
        "max_output_tokens": max_tokens,
    }
    if instructions:
        body["instructions"] = instructions

    try:
        resp = requests.post(url, headers=headers, json=body, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        text = _extract_output_text(data)
        if text is None:
            logging.error(f"Azure OpenAI response had no output_text: {json.dumps(data)[:500]}")
        return text
    except Exception as exc:
        body_text = getattr(exc, "response", None)
        body_text = body_text.text if body_text is not None else ""
        logging.error(f"Azure OpenAI call failed: {exc} | response body: {body_text}")
        return None


def generate_recommendation_explanation(liked_book, recommended_book, shared_phrases):
    """
    Generate a friendly natural-language explanation for why
    `recommended_book` is being suggested because the user liked
    `liked_book`. Falls back to a templated sentence if the LLM
    is not configured or the call fails.
    """
    shared = ", ".join(shared_phrases[:5]) if shared_phrases else "similar themes"

    fallback = (
        f"Because you enjoyed \"{liked_book['title']}\" with its themes of "
        f"{shared}, \"{recommended_book['title']}\" may appeal to you for "
        f"similar reasons."
    )

    system_prompt = (
        "You are a friendly book recommendation assistant. Given a book the "
        "user liked and a recommended book, write ONE short, warm sentence "
        "(max 40 words) explaining why the recommended book might appeal to "
        "them, referencing shared themes, mood, or genre. Do not mention "
        "key phrases or algorithms - speak naturally, like a knowledgeable "
        "friend recommending a book."
    )

    user_prompt = (
        f"Liked book: \"{liked_book['title']}\" (genre: {liked_book['genre']}).\n"
        f"Liked book summary (short): {liked_book['summary'][:400]}\n\n"
        f"Recommended book: \"{recommended_book['title']}\" "
        f"(genre: {recommended_book['genre']}).\n"
        f"Recommended book summary (short): {recommended_book['summary'][:400]}\n\n"
        f"Shared themes/key phrases: {shared}\n\n"
        f"Write the recommendation sentence now."
    )

    result = _chat_completion(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        max_tokens=120,
        temperature=0.8,
    )

    return result or fallback


def chat_with_assistant(message, history, candidate_books):
    """
    General-purpose chatbot endpoint. `candidate_books` is a small list of
    books (dicts with title/genre/summary) relevant to the conversation,
    used as lightweight RAG context.
    """
    context_lines = []
    for b in candidate_books[:8]:
        context_lines.append(
            f"- \"{b['title']}\" (genre: {b['genre']}): {b['summary'][:200]}"
        )
    context_block = "\n".join(context_lines) if context_lines else "No specific matches found."

    system_prompt = (
        "You are a helpful, friendly book recommendation chatbot for a "
        "personalized book recommendation app. Use the provided book "
        "context when relevant to suggest titles and explain why a user "
        "might like them. Keep responses conversational and concise "
        "(2-4 sentences unless the user asks for more detail).\n\n"
        f"Relevant books from the catalog:\n{context_block}"
    )

    messages = [{"role": "system", "content": system_prompt}]
    for turn in history[-6:]:  # keep last few turns for context
        role = turn.get("role")
        content = turn.get("content")
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": message})

    result = _chat_completion(messages, max_tokens=300, temperature=0.7)
    return result or (
        "Sorry, I couldn't reach the recommendation assistant right now. "
        "Please try again in a moment."
    )
