import os
import requests

GOOGLE_BOOKS_API_KEY = os.getenv("GOOGLE_BOOKS_API_KEY")


def search_book(title):
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
        "title": volume.get("title"),
        "authors": volume.get("authors", []),
        "publisher": volume.get("publisher"),
        "publishedDate": volume.get("publishedDate"),
        "description": volume.get("description"),
        "pageCount": volume.get("pageCount"),
        "categories": volume.get("categories", []),
        "averageRating": volume.get("averageRating"),
        "language": volume.get("language"),
        "thumbnail": volume.get("imageLinks", {}).get("thumbnail"),
        "infoLink": volume.get("infoLink"),
        "industryIdentifiers": volume.get("industryIdentifiers", [])
    }
