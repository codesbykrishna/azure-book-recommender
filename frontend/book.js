/**
 * book.js — Shelf Book Details Page
 *
 * Reads ?title= from the URL, calls POST /api/book_details,
 * then renders the full Book Details page + recommendations.
 *
 * Does NOT call Google Books directly — all data comes from the backend.
 */

/* ─── Config ────────────────────────────────────────────────── */
const API_BASE = "https://book-recommend-api-c3bzbjbgbydab7hw.centralindia-01.azurewebsites.net/api";
// Explicitly expose on window — see app.js for why this line is needed.
window.API_BASE = API_BASE;

/* ─── DOM refs ──────────────────────────────────────────────── */
const pageLoading   = document.getElementById("page-loading");
const pageError     = document.getElementById("page-error");
const pageContent   = document.getElementById("page-content");
const errorMsg      = document.getElementById("error-msg");

const bookCover       = document.getElementById("book-cover");
const bookCoverPH     = document.getElementById("book-cover-placeholder");
const bookTitle       = document.getElementById("book-title");
const bookAuthors     = document.getElementById("book-authors");
const bookCategories  = document.getElementById("book-categories");
const bookRatingWrap  = document.getElementById("book-rating-wrap");
const bookStars       = document.getElementById("book-stars");
const bookRatingCount = document.getElementById("book-rating-count");
const datasetWarning  = document.getElementById("dataset-warning");
const datasetWarningMsg = document.getElementById("dataset-warning-msg");
const googleBooksBtn  = document.getElementById("google-books-btn");

const statPublisher   = document.getElementById("stat-publisher");
const statPublisherV  = document.getElementById("stat-publisher-val");
const statDate        = document.getElementById("stat-date");
const statDateV       = document.getElementById("stat-date-val");
const statPages       = document.getElementById("stat-pages");
const statPagesV      = document.getElementById("stat-pages-val");
const statLanguage    = document.getElementById("stat-language");
const statLanguageV   = document.getElementById("stat-language-val");
const statIsbn10      = document.getElementById("stat-isbn10");
const statIsbn10V     = document.getElementById("stat-isbn10-val");
const statIsbn13      = document.getElementById("stat-isbn13");
const statIsbn13V     = document.getElementById("stat-isbn13-val");

const bookDescSection = document.getElementById("book-desc-section");
const bookDescription = document.getElementById("book-description");
const descToggle      = document.getElementById("desc-toggle");

const recSection  = document.getElementById("rec-section");
const recGrid     = document.getElementById("rec-grid");
const noRecMsg    = document.getElementById("no-rec-msg");
const noRecText   = document.getElementById("no-rec-text");

/* ─── Helpers ───────────────────────────────────────────────── */

/** Escape HTML to prevent XSS */
function esc(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/** Show/hide an element using the hidden attribute */
function show(el) { el.hidden = false; }
function hide(el) { el.hidden = true; }

/** Set text + show element; hide if value is empty */
function setField(wrapEl, valueEl, value) {
  if (value) {
    valueEl.textContent = value;
    show(wrapEl);
  } else {
    hide(wrapEl);
  }
}

/** Build a star rating string: ★★★★☆ */
function buildStars(rating) {
  const full  = Math.round(rating);
  const empty = 5 - full;
  return "★".repeat(Math.max(0, full)) + "☆".repeat(Math.max(0, empty));
}

/** Convert language code to readable name */
function langName(code) {
  try {
    return new Intl.DisplayNames(["en"], { type: "language" }).of(code) || code;
  } catch {
    return code;
  }
}

/** Navigate to book.html for a different title */
function goToBook(title) {
  window.location.href = `book.html?title=${encodeURIComponent(title)}`;
}

/* ─── State ─────────────────────────────────────────────────── */
let fullDescription = "";
let descExpanded    = false;
const DESC_LIMIT    = 400; // chars before "Show more"

/* ─── Show/hide page states ─────────────────────────────────── */
function showLoading() {
  show(pageLoading);
  hide(pageError);
  hide(pageContent);
}

function showError(msg) {
  hide(pageLoading);
  errorMsg.textContent = msg || "Something went wrong. Please try again.";
  show(pageError);
  hide(pageContent);
}

function showContent() {
  hide(pageLoading);
  hide(pageError);
  show(pageContent);
}

/* ─── Render book cover ─────────────────────────────────────── */
function renderCover(thumbnail, title) {
  if (thumbnail) {
    bookCover.src   = thumbnail;
    bookCover.alt   = title;
    bookCover.onerror = () => {
      hide(bookCover);
      show(bookCoverPH);
    };
    show(bookCover);
    hide(bookCoverPH);
  } else {
    hide(bookCover);
    show(bookCoverPH);
  }
}

/* ─── Render category chips ─────────────────────────────────── */
function renderCategories(categories) {
  bookCategories.innerHTML = "";
  const cats = Array.isArray(categories) ? categories : [];
  if (!cats.length) return;

  cats.slice(0, 4).forEach(cat => {
    const chip = document.createElement("span");
    chip.className = "category-chip";
    chip.textContent = cat;
    bookCategories.appendChild(chip);
  });
}

/* ─── Render star rating ────────────────────────────────────── */
function renderRating(rating, ratingsCount) {
  if (!rating) { hide(bookRatingWrap); return; }
  bookStars.textContent       = buildStars(rating);
  bookStars.title             = `${rating} out of 5`;
  bookRatingCount.textContent = ratingsCount
    ? `${rating} · ${ratingsCount.toLocaleString()} ratings`
    : String(rating);
  show(bookRatingWrap);
}

/* ─── Render description with expand/collapse ───────────────── */
function renderDescription(description) {
  if (!description) { hide(bookDescSection); return; }
  show(bookDescSection);
  fullDescription = description;

  if (description.length <= DESC_LIMIT) {
    bookDescription.textContent = description;
    hide(descToggle);
  } else {
    bookDescription.textContent = description.slice(0, DESC_LIMIT) + "…";
    show(descToggle);
    descToggle.textContent = "Show more";
    descExpanded = false;

    descToggle.onclick = () => {
      descExpanded = !descExpanded;
      bookDescription.textContent = descExpanded
        ? fullDescription
        : fullDescription.slice(0, DESC_LIMIT) + "…";
      descToggle.textContent = descExpanded ? "Show less" : "Show more";
    };
  }
}

/* ─── Render the full Book Details section ──────────────────── */
function renderBookDetails(gb) {
  // Page title
  document.title = gb.title ? `${gb.title} — Shelf` : "Book Details — Shelf";

  // Cover
  renderCover(gb.thumbnail, gb.title);

  // Categories / genres
  renderCategories(gb.categories);

  // Title
  bookTitle.textContent = gb.title || "Unknown Title";

  // Authors
  const authors = Array.isArray(gb.authors) && gb.authors.length
    ? gb.authors.join(", ")
    : null;
  bookAuthors.textContent = authors || "";
  bookAuthors.hidden = !authors;

  // Rating
  renderRating(gb.averageRating, gb.ratingsCount);

  // Stats
  setField(statPublisher, statPublisherV, gb.publisher);
  setField(statDate,      statDateV,      gb.publishedDate);
  setField(statPages,     statPagesV,     gb.pageCount ? `${gb.pageCount} pages` : null);
  setField(statLanguage,  statLanguageV,  gb.language ? langName(gb.language) : null);
  setField(statIsbn10,    statIsbn10V,    gb.isbn10);
  setField(statIsbn13,    statIsbn13V,    gb.isbn13);

  // Google Books button
  if (gb.infoLink) {
    googleBooksBtn.href = gb.infoLink;
    show(googleBooksBtn);
  } else {
    hide(googleBooksBtn);
  }

  // Description
  renderDescription(gb.description);
}

/* ─── Build a recommendation card ──────────────────────────── */
function buildRecCard(rec, index) {
  const card = document.createElement("div");
  card.className = "rec-card glass-card";
  card.style.animationDelay = `${index * 80}ms`;

  // Cover: the backend now looks up each recommendation's cover via
  // Google Books and sends it as rec.thumbnail. Fall back to the
  // genre-coloured placeholder if there's no thumbnail, or if the
  // image URL fails to load.
  const coverHtml = rec.thumbnail
    ? `<div class="rec-cover-wrap"><img class="rec-cover-img" src="${esc(rec.thumbnail)}" alt="${esc(rec.title)}"></div>`
    : `
    <div class="rec-cover-wrap">
      <div class="rec-cover-placeholder">
        <svg viewBox="0 0 60 80" fill="none">
          <rect width="60" height="80" rx="4" fill="var(--accent-lt)"/>
          <path d="M12 28h36M12 40h24M12 52h30" stroke="var(--accent-md)" stroke-width="3" stroke-linecap="round"/>
        </svg>
      </div>
    </div>`;

  const themes = (rec.shared_themes || []).slice(0, 3)
    .map(t => `<span class="theme-tag">${esc(t)}</span>`)
    .join("");

  card.innerHTML = `
    ${coverHtml}
    <div class="rec-card-body">
      <span class="card-genre">${esc(rec.genre || "")}</span>
      <p class="rec-card-title">${esc(rec.title)}</p>
      <p class="rec-explanation">${esc(rec.explanation)}</p>
      ${themes ? `<div class="card-themes">${themes}</div>` : ""}
      <button class="btn-view-details" data-title="${esc(rec.title)}">
        View Details
        <svg viewBox="0 0 16 16" fill="none" width="14" height="14">
          <path d="M3 8h10M9 4l4 4-4 4" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
      </button>
    </div>
  `;

  card.querySelector(".btn-view-details").addEventListener("click", () => {
    goToBook(rec.title);
  });

  const coverImg = card.querySelector(".rec-cover-img");
  if (coverImg) {
    coverImg.onerror = () => {
      coverImg.closest(".rec-cover-wrap").innerHTML = `
        <div class="rec-cover-placeholder">
          <svg viewBox="0 0 60 80" fill="none">
            <rect width="60" height="80" rx="4" fill="var(--accent-lt)"/>
            <path d="M12 28h36M12 40h24M12 52h30" stroke="var(--accent-md)" stroke-width="3" stroke-linecap="round"/>
          </svg>
        </div>`;
    };
  }

  return card;
}

/* ─── Render recommendations ────────────────────────────────── */
function renderRecommendations(recommendations, unavailableMsg) {
  recGrid.innerHTML = "";

  // Book not in dataset
  if (unavailableMsg) {
    noRecText.textContent = unavailableMsg;
    show(noRecMsg);
    hide(recSection);
    return;
  }

  // In dataset but no results returned
  if (!recommendations || recommendations.length === 0) {
    noRecText.textContent = "No similar books found in the dataset.";
    show(noRecMsg);
    hide(recSection);
    return;
  }

  hide(noRecMsg);
  show(recSection);

  recommendations.forEach((rec, i) => {
    recGrid.appendChild(buildRecCard(rec, i));
  });
}

/* ─── Render dataset warning banner ────────────────────────── */
function renderDatasetWarning(inDataset, unavailableMsg) {
  if (!inDataset && unavailableMsg) {
    datasetWarningMsg.textContent = unavailableMsg;
    show(datasetWarning);
  } else {
    hide(datasetWarning);
  }
}

/* ─── Main: fetch and render ────────────────────────────────── */
async function init() {
  const params = new URLSearchParams(window.location.search);
  const titleFromUrl = (params.get("title") || "").trim();
  const language = params.get("language") || "en";
  const topN = parseInt(params.get("top_n")) || 5;
  /* ── PATH A: Scan Book Cover ─────────────────────────────────
     When the user scanned a cover, app.js stored the full backend
     response in sessionStorage under "bookDetails" and redirected
     here without a ?title= query param.
     We consume the stored data once, then clear it so a page
     refresh doesn't re-render stale content.
  ────────────────────────────────────────────────────────────── */
  const cachedRaw = sessionStorage.getItem("bookDetails");

  if (!titleFromUrl && cachedRaw) {
    // Remove immediately so a hard refresh starts clean
    sessionStorage.removeItem("bookDetails");

    let data;
    try {
      data = JSON.parse(cachedRaw);
    } catch {
      showError("The scan result was corrupted. Please try scanning again.");
      return;
    }

    const gb = data.google_book;
    if (!gb) {
      showError("No book information was returned from the scan. Please try again.");
      return;
    }

    showContent();
    renderBookDetails(gb);
    renderDatasetWarning(data.in_dataset, data.unavailable_msg);
    renderRecommendations(data.recommendations, data.unavailable_msg);
    return;   // ← done; skip the fetch below
  }

  /* ── PATH B: Search by Title ─────────────────────────────────
     Normal flow: ?title=Harry+Potter → call /api/book_details.
     This path is completely unchanged from the original code.
  ────────────────────────────────────────────────────────────── */
  if (!titleFromUrl) {
    showError("No book title was provided. Please go back and search for a book.");
    return;
  }

  // Update page heading while loading
  document.title = `${titleFromUrl} — Shelf`;
  showLoading();

  // Call /api/book_details
  let data;
  try {
    const resp = await fetch(`${API_BASE}/book_details`, {
      method:  "POST",
      headers: {
        "Content-Type": "application/json",
        ...(window.Auth ? window.Auth.authHeaders() : {}),
      },
      body: JSON.stringify({
        title:    titleFromUrl,
        language: language,
        top_n:    topN,
      }),
    });

    data = await resp.json();

    if (!resp.ok) {
      // 404 = book not found in Google Books
      if (resp.status === 404) {
        showError(
          `We couldn't find "${titleFromUrl}" in Google Books. ` +
          "Please try a different title or check the spelling."
        );
      } else {
        showError(data.error || "Failed to load book details. Please try again.");
      }
      return;
    }
  } catch (err) {
    console.error("book_details fetch failed:", err);
    showError("Could not reach the server. Please check your connection and try again.");
    return;
  }

  // Handle missing google_book gracefully
  const gb = data.google_book;
  if (!gb) {
    showError("No additional book information available for this title.");
    return;
  }

  // Render everything
  showContent();
  renderBookDetails(gb);
  renderDatasetWarning(data.in_dataset, data.unavailable_msg);
  renderRecommendations(data.recommendations, data.unavailable_msg);
}

// Kick off on DOM ready
document.addEventListener("DOMContentLoaded", init);
