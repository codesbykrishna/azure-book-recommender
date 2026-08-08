/**
 * profile-menu.js — Profile dropdown (avatar, favorites, recent searches).
 *
 * Depends on auth.js (window.Auth) and favorites.js (window.Favorites)
 * being loaded first, and API_BASE being defined globally.
 *
 * Requires this markup to exist in the page (see index.html):
 *   #profile-chip-btn      — clickable chip that toggles the dropdown
 *   #profile-avatar        — initials avatar, filled in here
 *   #profile-dropdown      — the panel itself
 *   #profile-dropdown-email
 *   #profile-favorites-list
 *   #profile-history-list
 */

function _initials(nameOrEmail) {
  const namePart = (nameOrEmail || "").split("@")[0];
  const pieces = namePart.replace(/[._-]+/g, " ").trim().split(/\s+/);
  if (pieces.length === 0 || !pieces[0]) return "?";
  return pieces.length === 1
    ? pieces[0].slice(0, 2).toUpperCase()
    : (pieces[0][0] + pieces[1][0]).toUpperCase();
}

function _escape(s) {
  const div = document.createElement("div");
  div.textContent = s == null ? "" : String(s);
  return div.innerHTML;
}

function _bookLinkHref(title) {
  return `book.html?title=${encodeURIComponent(title)}`;
}

function _renderList(listEl, items, emptyText) {
  if (!items || items.length === 0) {
    listEl.innerHTML = `<li class="profile-dropdown-empty">${_escape(emptyText)}</li>`;
    return;
  }
  listEl.innerHTML = items.slice(0, 8).map(item => `
    <li>
      <a href="${_bookLinkHref(item.title)}" class="profile-dropdown-item">
        <span class="profile-dropdown-item-title">${_escape(item.title)}</span>
        ${item.genre ? `<span class="profile-dropdown-item-genre">${_escape(item.genre)}</span>` : ""}
      </a>
    </li>
  `).join("");
}

async function _fetchProfile() {
  const base = window.API_BASE || "";
  try {
    const resp = await fetch(`${base}/user_profile`, {
      headers: { ...window.Auth.authHeaders() },
    });
    if (!resp.ok) return null;
    return await resp.json();
  } catch (err) {
    console.warn("profile-menu.js: failed to fetch profile:", err);
    return null;
  }
}

async function _refreshDropdown() {
  const favList = document.getElementById("profile-favorites-list");
  const histList = document.getElementById("profile-history-list");
  if (!favList || !histList) return;

  const profile = await _fetchProfile();
  if (!profile) return;

  _renderList(favList, profile.favorites, "No favorites yet — tap a heart on any book");
  _renderList(histList, profile.history, "No searches yet");
}

function _setupProfileMenu(user) {
  const chipBtn   = document.getElementById("profile-chip-btn");
  const dropdown  = document.getElementById("profile-dropdown");
  const avatarEl  = document.getElementById("profile-avatar");
  const emailEl   = document.getElementById("profile-dropdown-email");
  if (!chipBtn || !dropdown) return;

  if (avatarEl) avatarEl.textContent = _initials(user.userDetails);
  if (emailEl) emailEl.textContent = user.userDetails || "";

  function closeDropdown() {
    dropdown.hidden = true;
    chipBtn.setAttribute("aria-expanded", "false");
  }
  function openDropdown() {
    dropdown.hidden = false;
    chipBtn.setAttribute("aria-expanded", "true");
    _refreshDropdown();
  }

  chipBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    if (dropdown.hidden) openDropdown();
    else closeDropdown();
  });

  document.addEventListener("click", (e) => {
    if (!dropdown.hidden && !dropdown.contains(e.target) && e.target !== chipBtn) {
      closeDropdown();
    }
  });

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !dropdown.hidden) closeDropdown();
  });

  // Keep the open dropdown in sync if a heart gets toggled elsewhere
  // on the page while it's open.
  window.addEventListener("favorites:changed", () => {
    if (!dropdown.hidden) _refreshDropdown();
  });
}

window.addEventListener("auth:ready", (e) => _setupProfileMenu(e.detail.user));
