/**
 * auth.js — Authentication using Azure Static Web Apps Built-in Auth
 *
 * NO MSAL.js needed.
 * NO App Registration needed.
 * NO Client ID or Secret needed.
 *
 * How it works:
 *   - Login  → redirect to /.auth/login/aad  (Azure handles everything)
 *   - Logout → redirect to /.auth/logout
 *   - User   → fetch /.auth/me               (returns current user info)
 *
 * The user logs in with any Microsoft / Outlook / Hotmail account.
 *
 * EXPORTED GLOBALS (used by index.html UI):
 *   window.Auth.isLoggedIn()      → boolean
 *   window.Auth.getUser()         → { userId, userDetails, userRoles } | null
 *   window.Auth.getUserId()       → string | null
 *   window.Auth.login()           → void (redirects to Microsoft login)
 *   window.Auth.logout()          → void (redirects to logout)
 *   window.Auth.getAccessToken()  → null (not needed for SWA auth)
 */

/* ─── State ──────────────────────────────────────────────────── */
let _currentUser = null;   // set by initAuth()

// Resolves once the /.auth/me sign-in check (and profile sync) has
// finished, so other scripts can await window.Auth.ready before
// firing requests that depend on authHeaders() being correct.
let _resolveAuthReady;
const _authReadyPromise = new Promise((resolve) => { _resolveAuthReady = resolve; });

/* ─── Core: fetch current user from SWA ─────────────────────── */
/**
 * Calls /.auth/me which Azure Static Web Apps provides automatically.
 * Returns the clientPrincipal object if logged in, or null if not.
 *
 * clientPrincipal shape:
 * {
 *   "identityProvider": "aad",
 *   "userId":           "abc123...",   ← stable unique ID, use this for Cosmos DB
 *   "userDetails":      "user@email.com",
 *   "userRoles":        ["anonymous", "authenticated"]
 * }
 */
async function fetchCurrentUser() {
  try {
    const resp = await fetch("/.auth/me");
    if (!resp.ok) return null;
    const data = await resp.json();
    return data.clientPrincipal || null;
  } catch {
    return null;
  }
}

/* ─── UI helpers ─────────────────────────────────────────────── */
function _updateAuthUI(user) {
  const loginBtn    = document.getElementById("auth-login-btn");
  const logoutBtn   = document.getElementById("auth-logout-btn");
  const userNameEl  = document.getElementById("auth-user-name");
  const userArea    = document.getElementById("auth-user-area");

  if (!loginBtn) return;   // auth UI not present on this page

  if (user) {
    // Logged in
    loginBtn.hidden  = true;
    if (userArea)   userArea.hidden   = false;
    if (logoutBtn)  logoutBtn.hidden  = false;
    if (userNameEl) {
      // Show email address, trim to keep header clean
      const email = user.userDetails || "My Account";
      userNameEl.textContent = email.length > 20
        ? email.split("@")[0]   // show only username part if too long
        : email;
    }
  } else {
    // Not logged in
    loginBtn.hidden  = false;
    if (userArea)   userArea.hidden   = true;
    if (logoutBtn)  logoutBtn.hidden  = true;
    if (userNameEl) userNameEl.textContent = "";
  }
}

/* ─── Modal helpers ──────────────────────────────────────────── */
function _showAuthModal(show) {
  const modal = document.getElementById("auth-modal");
  if (!modal) return;
  modal.hidden = !show;
  document.body.classList.toggle("modal-open", show);
}

/* ─── Login / Logout ─────────────────────────────────────────── */
function login() {
  // SWA redirects to Microsoft login, then back to current page
  const returnUrl = encodeURIComponent(window.location.href);
  window.location.href = `/.auth/login/aad?post_login_redirect_uri=${returnUrl}`;
}

function logout() {
  const returnUrl = encodeURIComponent(window.location.origin);
  window.location.href = `/.auth/logout?post_logout_redirect_uri=${returnUrl}`;
}

/* ─── Backend profile sync ───────────────────────────────────── */
/**
 * After login, tell the backend to create/update the user's profile
 * in Cosmos DB. Uses the SWA userId as the stable unique identifier.
 */
async function _syncUserProfile(user) {
  if (!user) return;

  // API_BASE is defined in app.js / book.js — available globally
  const base = window.API_BASE || "";
  if (!base) return;

  try {
    await fetch(`${base}/user_profile`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...window.Auth.authHeaders(),
      },
      body: JSON.stringify({
        userId:      user.userId,
        email:       user.userDetails,
        displayName: user.userDetails.split("@")[0],
      }),
    });
  } catch (err) {
    console.warn("Profile sync failed:", err);
  }
}

/* ─── Init (called on every page load) ──────────────────────── */
async function initAuth() {
  _currentUser = await fetchCurrentUser();
  _updateAuthUI(_currentUser);

  if (_currentUser) {
    // Sync profile with Cosmos DB backend
    await _syncUserProfile(_currentUser);
    // Store userId globally for use by app.js / book.js features
    window._userId = _currentUser.userId;
    // Dispatch event so other scripts know auth is ready
    window.dispatchEvent(
      new CustomEvent("auth:ready", { detail: { user: _currentUser } })
    );
  }
  // Resolve regardless of whether the user is signed in — callers just
  // need to know the check is DONE, not that it succeeded.
  _resolveAuthReady();
}

/* ─── Public API ─────────────────────────────────────────────── */
window.Auth = {
  isLoggedIn:      () => _currentUser !== null,
  getUser:         () => _currentUser,
  getUserId:       () => _currentUser?.userId ?? null,
  login,
  logout,
  // SWA built-in auth doesn't use Bearer tokens for the frontend
  // Backend endpoints that need auth read the x-ms-client-principal header
  // automatically injected by SWA — no token passing needed
  getAccessToken:  () => Promise.resolve(null),
  // Explicit identity headers for calls made directly to book-recommend-api's
  // own domain (cross-origin from the SWA), which never receives Azure's
  // auto-injected x-ms-client-principal header. See _get_swa_user()'s
  // docstring in function_app.py for the full explanation and caveats.
  authHeaders: () => _currentUser
    ? { "X-User-Id": _currentUser.userId, "X-User-Email": _currentUser.userDetails || "" }
    : {},
  // await window.Auth.ready before sending any request that needs
  // authHeaders() to be accurate (recommend/book_details/scan_cover).
  ready: _authReadyPromise,
};

/* ─── Wire up DOM once ready ─────────────────────────────────── */
document.addEventListener("DOMContentLoaded", () => {
  // Run auth init
  initAuth();

  // Header login button → open modal
  const loginBtn = document.getElementById("auth-login-btn");
  if (loginBtn) {
    loginBtn.addEventListener("click", () => _showAuthModal(true));
  }

  // Header logout button
  const logoutBtn = document.getElementById("auth-logout-btn");
  if (logoutBtn) {
    logoutBtn.addEventListener("click", logout);
  }

  // Modal close button (×)
  const closeBtn = document.getElementById("auth-modal-close");
  if (closeBtn) {
    closeBtn.addEventListener("click", () => _showAuthModal(false));
  }

  // Click outside modal card to close
  const modal = document.getElementById("auth-modal");
  if (modal) {
    modal.addEventListener("click", (e) => {
      if (e.target === modal) _showAuthModal(false);
    });
  }

  // Modal "Sign in with Microsoft" button
  const msBtn = document.getElementById("auth-ms-btn");
  if (msBtn) {
    msBtn.addEventListener("click", (e) => {
      e.preventDefault();
      login();
    });
  }
});
