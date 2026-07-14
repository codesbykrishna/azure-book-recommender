"""
Fuzzy title matching and similarity scoring between books.
"""

from rapidfuzz import fuzz, process


def fuzzy_match_title(query, books, score_cutoff=40):
    """
    Find the book in `books` whose title best matches `query`.
    Returns (book, score) or (None, 0) if nothing scores above the cutoff.
    """
    if not query:
        return None, 0

    titles = [b["title"] for b in books]
    result = process.extractOne(
        query, titles, scorer=fuzz.WRatio, score_cutoff=score_cutoff
    )
    if result is None:
        return None, 0

    _, score, idx = result
    return books[idx], score


def score_similarity(liked_book, candidate_book):
    """
    Returns (score, shared_keyphrases) where score is a float combining:
      - genre match (weight 1.0)
      - key-phrase overlap (Jaccard similarity, weight 2.0)
    """
    score = 0.0

    if liked_book.get("genre") and liked_book.get("genre") == candidate_book.get("genre"):
        score += 1.0

    liked_phrases = set(p.lower() for p in liked_book.get("keyphrases", []))
    cand_phrases = set(p.lower() for p in candidate_book.get("keyphrases", []))

    shared = liked_phrases & cand_phrases
    union = liked_phrases | cand_phrases
    jaccard = (len(shared) / len(union)) if union else 0.0
    score += 2.0 * jaccard

    # Preserve original-case shared phrases for nicer display
    shared_display = [
        p for p in candidate_book.get("keyphrases", []) if p.lower() in shared
    ]

    return score, shared_display


def get_recommendations(liked_book, all_books, top_n=5):
    """
    Returns a list of (score, book, shared_keyphrases) tuples for the
    top_n most similar books to `liked_book`, excluding itself.
    """
    scored = []
    for book in all_books:
        if book["index"] == liked_book["index"]:
            continue
        score, shared = score_similarity(liked_book, book)
        if score > 0:
            scored.append((score, book, shared))

    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[:top_n]
