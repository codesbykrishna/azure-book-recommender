/**
 * app.js — Shelf Book Recommendation Engine
 *
 * HOW TO CONFIGURE:
 *   Set API_BASE to the base URL of your deployed Azure Function App.
 *   e.g. "https://book-recommend-api.azurewebsites.net/api"
 *
 *   If running locally with `func start`, use:
 *   "http://localhost:7071/api"
 *
 *   titles.json is loaded from the same folder as index.html.
 *   After running enrich_data.py, copy titles.json here OR let the
 *   /api/titles endpoint serve it (see fallback below).
 */

const API_BASE = "https://book-recommend-api.azurewebsites.net/api";
// ← Replace with your actual Azure Function App URL after deployment

/* ═══════════════════════════════════════════════════════════════
   SHARED STATE
═══════════════════════════════════════════════════════════════ */
let allTitles = [];       // [{index, title, genre}]
let selectedTitle = "";   // the currently chosen title (from autocomplete)

/* ═══════════════════════════════════════════════════════════════
   NAVIGATION
═══════════════════════════════════════════════════════════════ */
document.querySelectorAll(".nav-tab").forEach(tab => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".nav-tab").forEach(t => t.classList.remove("active"));
    tab.classList.add("active");

    const target = tab.dataset.view;
    document.querySelectorAll(".view").forEach(v => v.classList.remove("active"));
    document.getElementById(`view-${target}`).classList.add("active");
  });
});

/* ═══════════════════════════════════════════════════════════════
   LOAD TITLES (for autocomplete)
═══════════════════════════════════════════════════════════════ */
async function loadTitles() {
  // Try local titles.json first (fastest), fall back to /api/titles
  try {
    const resp = await fetch("titles.json");
    if (resp.ok) {
      allTitles = await resp.json();
      return;
    }
  } catch (_) {}

  try {
    const resp = await fetch(`${API_BASE}/titles`);
    if (resp.ok) {
      allTitles = await resp.json();
    }
  } catch (err) {
    console.warn("Could not load titles:", err);
  }
}

loadTitles();

/* ═══════════════════════════════════════════════════════════════
   AUTOCOMPLETE
═══════════════════════════════════════════════════════════════ */
const bookInput     = document.getElementById("book-input");
const suggestionBox = document.getElementById("suggestions");
const recommendBtn  = document.getElementById("recommend-btn");

let activeIdx = -1;

// Simple fuzzy match: every word in the query appears in the title
function matchScore(query, title) {
  const q = query.toLowerCase();
  const t = title.toLowerCase();
  if (t.startsWith(q)) return 2;
  if (t.includes(q)) return 1;
  // word-level: every word of query is somewhere in title
  const words = q.split(" ").filter(Boolean);
  if (words.length > 1 && words.every(w => t.includes(w))) return 0.8;
  return 0;
}

function highlightMatch(title, query) {
  if (!query) return escHtml(title);
  const idx = title.toLowerCase().indexOf(query.toLowerCase());
  if (idx === -1) return escHtml(title);
  return (
    escHtml(title.slice(0, idx)) +
    "<mark>" + escHtml(title.slice(idx, idx + query.length)) + "</mark>" +
    escHtml(title.slice(idx + query.length))
  );
}

function escHtml(s) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function showSuggestions(query) {
  if (!query || query.length < 2) { closeSuggestions(); return; }

  const scored = allTitles
    .map(b => ({ ...b, score: matchScore(query, b.title) }))
    .filter(b => b.score > 0)
    .sort((a, b) => b.score - a.score)
    .slice(0, 8);

  if (scored.length === 0) { closeSuggestions(); return; }

  suggestionBox.innerHTML = scored.map((b, i) =>
    `<li role="option" data-title="${escHtml(b.title)}" data-idx="${i}">
       <span class="sug-title">${highlightMatch(b.title, query)}</span>
       <span class="sug-genre">${escHtml(b.genre)}</span>
     </li>`
  ).join("");

  suggestionBox.classList.add("open");
  activeIdx = -1;

  suggestionBox.querySelectorAll("li").forEach(li => {
    li.addEventListener("mousedown", e => {
      e.preventDefault();
      selectTitle(li.dataset.title);
    });
  });
}

function closeSuggestions() {
  suggestionBox.innerHTML = "";
  suggestionBox.classList.remove("open");
  activeIdx = -1;
}

function selectTitle(title) {
  selectedTitle = title;
  bookInput.value = title;
  closeSuggestions();
  recommendBtn.disabled = false;
}

bookInput.addEventListener("input", () => {
  selectedTitle = "";
  recommendBtn.disabled = true;
  showSuggestions(bookInput.value.trim());
});

bookInput.addEventListener("keydown", e => {
  const items = suggestionBox.querySelectorAll("li");
  if (!items.length) return;

  if (e.key === "ArrowDown") {
    e.preventDefault();
    activeIdx = Math.min(activeIdx + 1, items.length - 1);
  } else if (e.key === "ArrowUp") {
    e.preventDefault();
    activeIdx = Math.max(activeIdx - 1, -1);
  } else if (e.key === "Enter" && activeIdx >= 0) {
    e.preventDefault();
    selectTitle(items[activeIdx].dataset.title);
    return;
  } else if (e.key === "Escape") {
    closeSuggestions(); return;
  } else { return; }

  items.forEach((li, i) => li.classList.toggle("active", i === activeIdx));
  if (activeIdx >= 0) bookInput.value = items[activeIdx].dataset.title;
});

document.addEventListener("click", e => {
  if (!e.target.closest(".autocomplete-wrap")) closeSuggestions();
});

/* ═══════════════════════════════════════════════════════════════
   RECOMMEND
═══════════════════════════════════════════════════════════════ */
const resultsGrid  = document.getElementById("results");
const matchedBook  = document.getElementById("matched-book");
const matchedTitle = document.getElementById("matched-title");
const matchedGenre = document.getElementById("matched-genre");
const emptyState   = document.getElementById("empty-state");
const emptyMsg     = document.getElementById("empty-msg");
const btnText      = recommendBtn.querySelector(".btn-text");
const btnSpinner   = recommendBtn.querySelector(".btn-spinner");

function setLoading(on) {
  btnText.hidden = on;
  btnSpinner.hidden = !on;
  recommendBtn.disabled = on;
}

recommendBtn.addEventListener("click", async () => {
  const title  = selectedTitle || bookInput.value.trim();
  const lang   = document.getElementById("lang-select").value;
  const topN   = parseInt(document.getElementById("count-select").value);

  if (!title) return;

  setLoading(true);
  resultsGrid.innerHTML = "";
  matchedBook.hidden = true;
  emptyState.hidden = true;

  try {
    const resp = await fetch(`${API_BASE}/recommend`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ book_title: title, language: lang, top_n: topN }),
    });

    const data = await resp.json();

    if (!resp.ok) {
      showEmpty(data.error || "Something went wrong. Please try a different title.");
      return;
    }

    // Show matched book badge
    matchedTitle.textContent = data.matched_book.title;
    matchedGenre.textContent = data.matched_book.genre;
    matchedBook.hidden = false;

    // Render recommendation cards
    if (!data.recommendations || data.recommendations.length === 0) {
      showEmpty("No close matches found. Try another title.");
      return;
    }

    data.recommendations.forEach((rec, i) => {
      resultsGrid.appendChild(buildCard(rec, i + 1));
    });

  } catch (err) {
    showEmpty("Could not reach the recommendation service. Please try again.");
    console.error(err);
  } finally {
    setLoading(false);
    recommendBtn.disabled = false;
  }
});

function buildCard(rec, rank) {
  const card = document.createElement("div");
  card.className = "book-card";
  card.style.animationDelay = `${(rank - 1) * 60}ms`;

  const themes = (rec.shared_themes || []).slice(0, 4)
    .map(t => `<span class="theme-tag">${escHtml(t)}</span>`)
    .join("");

  card.innerHTML = `
    <span class="card-rank">PICK ${rank}</span>
    <p class="card-title">${escHtml(rec.title)}</p>
    <span class="card-genre">${escHtml(rec.genre)}</span>
    <p class="card-explanation">${escHtml(rec.explanation)}</p>
    ${themes ? `<div class="card-themes">${themes}</div>` : ""}
  `;
  return card;
}

function showEmpty(msg) {
  emptyMsg.textContent = msg;
  emptyState.hidden = false;
}

// Show empty state on load
showEmpty("Search for a book above to get started.");

/* ═══════════════════════════════════════════════════════════════
   CHATBOT
═══════════════════════════════════════════════════════════════ */
const chatMessages = document.getElementById("chat-messages");
const chatInput    = document.getElementById("chat-input");
const chatSend     = document.getElementById("chat-send");
let chatHistory = [];

function appendBubble(text, role) {
  const div = document.createElement("div");
  div.className = `chat-bubble ${role}`;
  div.innerHTML = `<p>${escHtml(text)}</p>`;
  chatMessages.appendChild(div);
  chatMessages.scrollTop = chatMessages.scrollHeight;
  return div;
}

function showTyping() {
  const div = document.createElement("div");
  div.className = "chat-bubble typing";
  div.id = "typing-indicator";
  div.innerHTML = `<div class="typing-dots"><span></span><span></span><span></span></div>`;
  chatMessages.appendChild(div);
  chatMessages.scrollTop = chatMessages.scrollHeight;
}

function removeTyping() {
  document.getElementById("typing-indicator")?.remove();
}

async function sendChatMessage(text) {
  if (!text.trim()) return;

  chatInput.value = "";
  chatSend.disabled = true;

  appendBubble(text, "user");
  chatHistory.push({ role: "user", content: text });

  showTyping();

  const lang = document.getElementById("chat-lang-select").value;

  try {
    const resp = await fetch(`${API_BASE}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text, history: chatHistory, language: lang }),
    });

    const data = await resp.json();
    removeTyping();

    const reply = resp.ok
      ? data.reply
      : (data.error || "I had trouble with that. Please try again.");

    appendBubble(reply, "assistant");
    chatHistory.push({ role: "assistant", content: reply });

    // Keep history bounded (last 10 turns)
    if (chatHistory.length > 20) chatHistory = chatHistory.slice(-20);

  } catch (err) {
    removeTyping();
    appendBubble("Sorry, I couldn't reach the server. Please check your connection.", "assistant");
    console.error(err);
  } finally {
    chatSend.disabled = false;
    chatInput.focus();
  }
}

chatSend.addEventListener("click", () => sendChatMessage(chatInput.value));
chatInput.addEventListener("keydown", e => {
  if (e.key === "Enter") sendChatMessage(chatInput.value);
});

document.querySelectorAll(".chat-pill").forEach(pill => {
  pill.addEventListener("click", () => sendChatMessage(pill.dataset.msg));
});
