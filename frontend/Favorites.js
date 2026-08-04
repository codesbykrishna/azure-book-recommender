/**
 * favorites.js — Favorite books (heart icon) feature.
 *
 * Depends on auth.js being loaded first (uses window.Auth) and on
 * API_BASE being defined globally by app.js / book.js.
 *
 * Public API (window.Favorites):
 *   isFavorite(index)   -> boolean, current known state for a book
 *   toggle(book)         -> Promise<boolean|null>
 *                           book = {index, title, genre}
 *                           returns the new isFavorite state, or null
 *                           if the user isn't signed in (also triggers
 *                           the sign-in modal in that case) or the
 *                           request failed.
 *   isLoaded()           -> boolean, whether the initial list has been
 *                           fetched yet (heart icons can wait on this
 *                           to avoid a flash of the wrong state)
 *
 * Fires a "favorites:ready" window event once the initial list has
 * loaded (or immediately, empty, if the user isn't signed in).
 */

let _favoriteIndexes = new Set();
let _favoritesLoaded = false;

async function _loadFavorites() {
  if (!window.Auth || !window.Auth.isLoggedIn()) {
    _favoritesLoaded = true;
    window.dispatchEvent(new CustomEvent("favorites:ready"));
    return;
  }

  const base = window.API_BASE || "";
  try {
    const resp = await fetch(`${base}/user_profile`, { credentials: "include" });
    if (resp.ok) {
      const profile = await resp.json();
      _favoriteIndexes = new Set((profile.favorites || []).map(f => f.index));
    }
  } catch (err) {
    console.warn("favorites.js: failed to load favorites list:", err);
  } finally {
    _favoritesLoaded = true;
    window.dispatchEvent(new CustomEvent("favorites:ready"));
  }
}

async function toggleFavorite(book) {
  if (!window.Auth || !window.Auth.isLoggedIn()) {
    // Not signed in — prompt sign-in instead of silently failing.
    if (typeof _showAuthModal === "function") {
      _showAuthModal(true);
    } else if (window.Auth && window.Auth.login) {
      window.Auth.login();
    }
    return null;
  }

  const base = window.API_BASE || "";
  try {
    const resp = await fetch(`${base}/favorites`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        index: book.index,
        title: book.title,
        genre: book.genre,
      }),
    });

    if (resp.status === 401) {
      // Session expired / not actually authenticated server-side
      if (typeof _showAuthModal === "function") _showAuthModal(true);
      return null;
    }
    if (!resp.ok) return null;

    const data = await resp.json();
    if (data.isFavorite) _favoriteIndexes.add(book.index);
    else _favoriteIndexes.delete(book.index);
    return data.isFavorite;
  } catch (err) {
    console.warn("favorites.js: toggle failed:", err);
    return null;
  }
}

window.Favorites = {
  isFavorite: (index) => _favoriteIndexes.has(index),
  toggle: toggleFavorite,
  isLoaded: () => _favoritesLoaded,

  /**
   * Returns the HTML for a heart toggle button for a given book.
   * Caller is responsible for inserting this into a card's innerHTML,
   * then calling wireButton() below once the element exists in the DOM.
   */
  heartButtonHtml(book) {
    const filled = _favoriteIndexes.has(book.index);
    return `<button type="button" class="fav-heart-btn${filled ? " is-favorite" : ""}"
              data-fav-index="${book.index}" aria-label="${filled ? "Remove from favorites" : "Add to favorites"}" title="${filled ? "Remove from favorites" : "Add to favorites"}">
              <svg viewBox="0 0 24 24" width="18" height="18" fill="${filled ? "currentColor" : "none"}" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
                <path d="M12 21s-7.5-4.6-10-9.3C.3 8.2 2 4.5 5.6 4c2-.3 3.9.7 5 2.4C11.6 4.7 13.5 3.7 15.5 4c3.6.5 5.3 4.2 3.6 7.7C19.5 16.4 12 21 12 21z"/>
              </svg>
            </button>`;
  },

  /**
   * Wires the click handler for a heart button already inserted into
   * the DOM (via the HTML from heartButtonHtml above). `container` is
   * any ancestor element to query within (usually the card itself).
   */
  wireHeartButton(container, book) {
    const btn = container.querySelector(`.fav-heart-btn[data-fav-index="${book.index}"]`);
    if (!btn) return;
    btn.addEventListener("click", async (e) => {
      e.stopPropagation();
      btn.disabled = true;
      const result = await toggleFavorite(book);
      btn.disabled = false;
      if (result === null) return; // not signed in, or request failed
      btn.classList.toggle("is-favorite", result);
      btn.setAttribute("aria-label", result ? "Remove from favorites" : "Add to favorites");
      btn.title = result ? "Remove from favorites" : "Add to favorites";
      const svg = btn.querySelector("svg");
      if (svg) svg.setAttribute("fill", result ? "currentColor" : "none");
    });
  },
};

// auth.js dispatches "auth:ready" only when a user IS logged in (see auth.js
// initAuth()), so we also load (as "signed out / empty") on plain page load
// as a fallback in case that event never fires this session.
window.addEventListener("auth:ready", _loadFavorites);
document.addEventListener("DOMContentLoaded", () => {
  setTimeout(() => { if (!_favoritesLoaded) _loadFavorites(); }, 1000);
});
