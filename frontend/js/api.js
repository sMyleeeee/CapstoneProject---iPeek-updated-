/**
 * api.js
 * ------
 * ADAPTER LAYER for the FastAPI/JWT backend.
 *
 * Every exported function keeps a stable name/shape so page scripts
 * never talk to the raw backend directly — only to this file.
 *
 * TOKEN STORAGE: JWT stored in sessionStorage under "ipeek_token".
 * Cleared on logout via sessionStorage.clear().
 *
 * ADDED: apiRequestOtp / apiVerifyOtp / apiRegister — the three calls
 * behind the new registration flow in login.js. Role is never sent by
 * the client in apiRegister(); the backend derives it from the email
 * domain. See services/otp_service.py::determine_role_from_email().
 */

const API_BASE = "http://localhost:8000";

/* Shared local outline SVG set; icons are decorative beside named controls. */
function createUiIcon(name) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  const attributes = {
    class: "ui-icon", viewBox: "0 0 24 24", fill: "none", stroke: "currentColor",
    "stroke-width": "2", "stroke-linecap": "round", "stroke-linejoin": "round",
    "aria-hidden": "true", focusable: "false",
  };
  Object.entries(attributes).forEach(([key, value]) => svg.setAttribute(key, value));
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", `icons.svg#${name}`);
  svg.appendChild(use);
  return svg;
}

function uiIconMarkup(name) {
  return createUiIcon(name).outerHTML;
}

function createActionIconButton(icon, tooltip, label = tooltip) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "action-icon-btn";
  button.title = tooltip;
  button.dataset.tooltip = tooltip;
  button.setAttribute("aria-label", label);
  button.appendChild(createUiIcon(icon));
  return button;
}

function createSignOutButton() {
  const button = createActionIconButton("logout", "Sign out");
  button.classList.add("sign-out-btn");
  button.addEventListener("click", async () => {
    try { await apiLogout(); } catch {}
    sessionStorage.clear();
    window.location.replace("index.html");
  });
  return button;
}

/* ── Nav link sets ──────────────────────────────────────────────────── */
const NAV_LINKS = {
  student: [
    { href: "ask.html", label: "Ask", id: "ask", icon: "search" },
    { href: "browse.html",  label: "Browse Research",  id: "browse",    icon: "browse" },
    { href: "upload.html",  label: "Submit Proposal",  id: "upload",    icon: "upload" },
    { href: "my-submissions.html", label: "My Submissions", id: "my-submissions", icon: "my-submissions" },
  ],
  librarian: [
    { href: "ask.html", label: "Ask", id: "ask", icon: "search" },
    { href: "dashboard.html", label: "Dashboard",     id: "dashboard", icon: "dashboard" },
    { href: "review.html",    label: "Review Queue",  id: "review",    icon: "review" },
    { href: "browse.html",    label: "Browse",        id: "browse",    icon: "browse" },
    { href: "upload.html",    label: "Upload",        id: "upload",    icon: "upload" },
    { href: "my-submissions.html", label: "My Submissions", id: "my-submissions", icon: "my-submissions" },
    { href: "users.html",      label: "Users",           id: "users",      icon: "users" },
  ],
};

let _navIdentity = null;
let _renderedNavState = null;

// Restore presentation only; every page still verifies the session with /auth/me.
function restoreNavPresentation() {
  if (!_getToken() || !document.querySelector(".navbar")) return;
  try {
    const saved = JSON.parse(sessionStorage.getItem("ipeek_nav") || "null");
    if (!saved || !Object.hasOwn(NAV_LINKS, saved.role)) return;
    const avatar = document.getElementById("avatarEl");
    const role = document.getElementById("rolePill");
    if (avatar) avatar.textContent = saved.initials;
    if (role) role.textContent = saved.label;
    _renderNav(saved.role);
  } catch { /* Storage unavailable: normal authenticated initialization follows. */ }
}

function applyNav(role) {
  _renderNav(role);
}

function _renderNav(role) {
  const container = document.querySelector(".nav-links");
  if (!container) return;
  const state = role;
  // Auth confirmation must not replace the restored pill or replay its motion.
  if (_renderedNavState === state) return;
  _renderedNavState = state;
  const links   = NAV_LINKS[role] || NAV_LINKS.student;
  const current = window.location.pathname.split("/").pop();

  /* ── Desktop pill nav ──────────────────────────────────── */
  {
    const track = document.createElement("div");
    track.className = "nav-track";

    const ul = document.createElement("ul");
    ul.className = "pill-list";
    links.forEach(({ href, label, icon, target }) => {
      const li = document.createElement("li");
      const a  = document.createElement("a");
      a.className   = `pill${href === current ? " is-active" : ""}`;
      a.href        = href;
      if (target) a.target = target;
      if (icon) a.appendChild(createUiIcon(icon));
      const span = document.createElement("span");
      span.textContent = label;
      a.appendChild(span);
      li.appendChild(a);
      ul.appendChild(li);
    });
    track.appendChild(ul);
    container.innerHTML = "";
    container.appendChild(track);

    if (typeof initPillNav === "function") initPillNav(track);
  }

  /* ── Nav right: bell + profile + logout ─────────────────── */
  const navRight = document.querySelector(".nav-right");
  if (navRight) {
    // Preserve identity elements while rebuilding navigation.
    const existingAvatar = document.getElementById("avatarEl") || (_navIdentity && _navIdentity.avatarEl);
    const existingRole   = document.getElementById("rolePill")  || (_navIdentity && _navIdentity.rolePill);
    if (existingAvatar && existingRole) _navIdentity = { avatarEl: existingAvatar, rolePill: existingRole };

    navRight.innerHTML = "";

    {
      /* Bell notification button */
      const notifWrapper = document.createElement("div");
      notifWrapper.className = "nav-notif-wrapper";

      const bellBtn = document.createElement("button");
      bellBtn.className = "nav-icon-btn";
      bellBtn.title = "Notifications";
      bellBtn.setAttribute("aria-label", "Notifications");
      const bellSvg = createUiIcon("bell");
      bellBtn.appendChild(bellSvg);

      const dropdown = document.createElement("div");
      dropdown.className = "nav-dropdown";
      wireNotifications(bellBtn, dropdown);

      wireNotificationDisclosure(bellBtn, dropdown);

      notifWrapper.appendChild(bellBtn);
      notifWrapper.appendChild(dropdown);
      navRight.appendChild(notifWrapper);

      /* Close dropdown on outside click */


      /* Profile pill */
      if (existingAvatar && existingRole) {
        const wrapper = document.createElement("a");
        wrapper.className = "profile-pill";
        wrapper.href = "profile.html";
        existingAvatar.removeAttribute("href");
        existingAvatar.removeAttribute("tabindex");
        wrapper.appendChild(existingAvatar);
        wrapper.appendChild(existingRole);
        navRight.appendChild(wrapper);
      }
    }

    /* Keep sign out accessible on desktop and mobile. */
    navRight.appendChild(createSignOutButton());
  }

  /* ── Mobile header ─────────────────────────────────────── */
  let mobileHeader = document.querySelector(".mobile-header");
  if (!mobileHeader) {
    mobileHeader = document.createElement("div");
    mobileHeader.className = "mobile-header";
    document.body.insertBefore(mobileHeader, document.body.firstChild);
  }
  mobileHeader.innerHTML = "";
  const mBrand = document.createElement("div");
  mBrand.className = "nav-brand";
  mBrand.innerHTML = '<div class="nav-logo">i</div><span>Research Repository</span>';
  mobileHeader.appendChild(mBrand);
  const mobileActions = document.createElement("div");
  mobileActions.className = "mobile-header-actions";
  mobileHeader.appendChild(mobileActions);

  {
    const avatarEl = document.getElementById("avatarEl") || (_navIdentity && _navIdentity.avatarEl);
    const rolePill = document.getElementById("rolePill") || (_navIdentity && _navIdentity.rolePill);
    const initials = avatarEl ? avatarEl.textContent : "?";
    const roleText = rolePill ? rolePill.textContent : role;
    const mProfile = document.createElement("a");
    mProfile.className = "mobile-profile-icon";
    mProfile.href = "profile.html";
    mProfile.textContent = initials;
    mProfile.title = `${roleText} — View Profile`;
    mobileActions.appendChild(mProfile);
  }

  /* ── Bottom tab bar ────────────────────────────────────── */
  {
    const wrapper = document.createElement("div"); wrapper.className = "nav-notif-wrapper";
    const bell = document.createElement("button"); bell.className = "nav-icon-btn";
    bell.setAttribute("aria-label", "Notifications");
    bell.appendChild(createUiIcon("bell"));
    const dropdown = document.createElement("div"); dropdown.className = "nav-dropdown";
    wireNotifications(bell, dropdown);
    wireNotificationDisclosure(bell, dropdown);
    wrapper.append(bell, dropdown); mobileActions.appendChild(wrapper);
  }
  mobileActions.appendChild(createSignOutButton());
  let bottomBar = document.querySelector(".bottom-tab-bar");
  if (!bottomBar) {
    bottomBar = document.createElement("div");
    bottomBar.className = "bottom-tab-bar";
    document.body.appendChild(bottomBar);
  }
  bottomBar.innerHTML = "";

  document.body.classList.add("nav-ready");

  const bUl = document.createElement("ul");
  bUl.className = "pill-list";
  links.forEach(({ href, label, icon, target }) => {
    const li = document.createElement("li");
    const a  = document.createElement("a");
    a.className   = `pill${href === current ? " is-active" : ""}`;
    a.href        = href;
    if (target) a.target = target;
    if (icon) a.appendChild(createUiIcon(icon));
    const span = document.createElement("span");
    const shortLabels = {
      "Browse Research": "Browse",
      "Submit Proposal": "Submit",
      "My Submissions": "My Subs",
      "Dashboard": "Home",
      "Review Queue": "Review",
      "Upload": "Upload",
      "Browse": "Browse"
    };
    span.textContent = shortLabels[label] || label;
    a.appendChild(span);
    li.appendChild(a);
    bUl.appendChild(li);
  });
  bottomBar.appendChild(bUl);

  if (typeof initPillNav === "function") initPillNav(bottomBar);
}

function saveNavPresentation() {
  if (!_renderedNavState || !_getToken()) return;
  const role = _renderedNavState;
  try {
    sessionStorage.setItem("ipeek_nav", JSON.stringify({
      role,
      initials: document.getElementById("avatarEl")?.textContent || "",
      label: document.getElementById("rolePill")?.textContent || "",
    }));
  } catch { /* Navigation also works when session storage is unavailable. */ }
}

function applyProfilePill() {
  const navRight = document.querySelector(".nav-right");
  if (!navRight) return;
  const existingAvatar = document.getElementById("avatarEl");
  const existingRole   = document.getElementById("rolePill");
  if (!existingAvatar || !existingRole) return;
  if (existingAvatar.closest(".profile-pill")) return;

  const wrapper = document.createElement("a");
  wrapper.className = "profile-pill";
  wrapper.href = "profile.html";
  navRight.insertBefore(wrapper, existingAvatar);
  wrapper.appendChild(existingAvatar);
  wrapper.appendChild(existingRole);
}

/* ── Token helpers ───────────────────────────────────────────────────── */

function _getToken() {
  return sessionStorage.getItem("ipeek_token");
}

function _authHeaders() {
  const token = _getToken();
  return token ? { "Authorization": `Bearer ${token}` } : {};
}

/* ── Auth ────────────────────────────────────────────────────────────── */

async function apiLogin(username, password) {
  const body = new URLSearchParams();
  body.append("username", username);
  body.append("password", password);

  const r = await _apiFetch(`${API_BASE}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body,
  });
  const d = await r.json();
  if (!r.ok) throw new Error(d.detail || "Login failed.");

  sessionStorage.removeItem("ipeek_nav");
  sessionStorage.setItem("ipeek_token", d.access_token);
  return { success: true, role: d.role, full_name: d.fullname };
}

function apiLogout() {
  sessionStorage.clear();
  return { success: true };
}

async function apiMe() {
  const r = await _apiFetch(`${API_BASE}/auth/me`, { headers: _authHeaders() });
  const d = await r.json();
  if (!r.ok) throw new Error(d.detail || "Not logged in.");
  return {
    user_id:   d.id,
    username:  d.username,
    role:      d.role,
    full_name: d.fullname,
    email:     d.email,
    contact:   d.contact,
    department: d.department, college_id: d.college_id, college_code: d.college_code, landing: d.landing,
  };
}

async function apiUpdateProfile(fullName, email, contact, department) {
  const r = await _apiFetch(`${API_BASE}/auth/me`, {
    method: "PUT",
    headers: { "Content-Type": "application/json", ..._authHeaders() },
    body: JSON.stringify({ fullname: fullName, contact, ...(department !== undefined ? {department} : {}) }),
  });
  const d = await r.json();
  if (!r.ok) throw new Error(d.detail || "Failed to update profile.");
  return {
    user_id: d.id, username: d.username, role: d.role,
    full_name: d.fullname, email: d.email, contact: d.contact, department: d.department, college_id: d.college_id, college_code: d.college_code, landing: d.landing,
  };
}

async function apiUpdatePassword(currentPassword, newPassword) {
  const r = await _apiFetch(`${API_BASE}/auth/password`, {
    method: "PUT",
    headers: { "Content-Type": "application/json", ..._authHeaders() },
    body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
  });
  const d = await r.json();
  if (!r.ok) throw new Error(d.detail || "Failed to update password.");
  return d;
}

/* ── Registration (OTP flow) ────────────────────────────────────────── */
/* No auth headers needed on any of these three — the user isn't logged
   in yet during registration. Note apiRegister() never sends a "role"
   field: the server derives it from the email domain, and would ignore
   a client-supplied role even if one were sent. */

async function apiRequestOtp(email) {
  const r = await _apiFetch(`${API_BASE}/auth/register/request-otp`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email }),
  });
  const d = await r.json();
  if (!r.ok) throw new Error(d.detail || "Could not send verification code.");
  return d;
}

async function apiVerifyOtp(email, code) {
  const r = await _apiFetch(`${API_BASE}/auth/register/verify-otp`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, code }),
  });
  const d = await r.json();
  if (!r.ok) throw new Error(d.detail || "Verification failed.");
  return d;
}

async function apiRegister({ email, fullname, department }) {
  // No "password" field is ever sent — the server generates one after
  // OTP verification and emails it to the verified address.
  const r = await _apiFetch(`${API_BASE}/auth/register`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, fullname, department: department || null }),
  });
  const d = await r.json();
  if (!r.ok) throw new Error(d.detail || "Registration failed.");
  return d;
}

/* ── Ingest (upload preview/confirm) ────────────────────────────────── */

async function apiIngestPreview(file, formData) {
  const fd = new FormData();
  fd.append("file", file);
  fd.append("title", formData.title || "");
  fd.append("department", formData.department || "");
  fd.append("year", formData.year || "");
  fd.append("members", formData.members || "");
  fd.append("abstract", formData.abstract || "");

  const r = await _apiFetch(`${API_BASE}/repository/upload/preview`, {
    method: "POST",
    headers: _authHeaders(),
    body: fd,
  });
  const d = await r.json();
  if (!r.ok) throw new Error(d.detail || "Preview failed.");
  return d;
}

async function apiIngestConfirm(previewId, finalValues, publicPdfConsent) {
  const r = await _apiFetch(`${API_BASE}/repository/upload/confirm`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ..._authHeaders() },
    body: JSON.stringify({
      preview_id: previewId,
      final_values: finalValues,
      public_pdf_consent: !!publicPdfConsent,
    }),
  });
  const d = await r.json();
  if (!r.ok) throw new Error(d.detail || "Submission failed.");
  return d;
}

/* ── RAG Analysis ────────────────────────────────────────────────────── */

async function apiSimilarity(researchId) {
  const r = await _apiFetch(`${API_BASE}/ai/${researchId}/similarity`, {
    method: "POST", headers: _authHeaders(),
  });
  const d = await r.json();
  if (!r.ok) throw new Error(d.detail || "Similarity failed.");
  return d;
}

async function apiSummary(researchId) {
  const r = await _apiFetch(`${API_BASE}/ai/${researchId}/summary`, {
    method: "POST", headers: _authHeaders(),
  });
  const d = await r.json();
  if (!r.ok) throw new Error(d.detail || "Summary failed.");
  return d;
}

async function apiGaps(researchId) {
  const r = await _apiFetch(`${API_BASE}/ai/${researchId}/gaps`, {
    method: "POST", headers: _authHeaders(),
  });
  const d = await r.json();
  if (!r.ok) throw new Error(d.detail || "Gap analysis failed.");
  return d;
}

async function apiImrad(researchId) {
  const r = await _apiFetch(`${API_BASE}/ai/${researchId}/imrad`, {
    method: "POST", headers: _authHeaders(),
  });
  const d = await r.json();
  if (!r.ok) throw new Error(d.detail || "IMRaD analysis failed.");
  return d;
}

async function apiRefineAnalysis(researchId, query, target) {
  const r = await _apiFetch(`${API_BASE}/ai/${researchId}/refine`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ..._authHeaders() },
    body: JSON.stringify({ query, target }),
  });
  const d = await r.json();
  if (!r.ok) throw new Error(d.detail || "Refine failed.");
  return d;
}


/* ── Repository Data ─────────────────────────────────────────────────── */

function _mapResearch(item) {
  return {
    id:               item.id,
    title:            item.title,
    authors:          item.authors,
    lead_researcher:  item.authors,
    year:             item.year ? String(item.year) : "Unknown",
    school_year:      item.year ? String(item.year) : "Unknown",
    college:          item.college?.name || item.department,
    college_ref:      item.college || null,
    can_view_pdf:     item.can_view_pdf,
    pdf_block_reason: item.pdf_block_reason,
    page_count:       item.page_count,
    department:       item.department,
    college_id:       item.college_id,
    abstract:         item.abstract || "",
    source:           item.source_stem,
    source_stem:      item.source_stem,
    status:           item.status,
    feedback:         item.feedback_note,
    public_pdf_consent: !!item.public_pdf_consent,
    submitted_at:     item.created_at,
  };
}

async function apiDocuments(filters = {}) {
  const params = new URLSearchParams();
  if (filters.college) params.set("college_id", filters.college);
  if (filters.year && filters.year !== "all") params.set("year", filters.year);
  const r = await _apiFetch(`${API_BASE}/repository/browse?${params}`, { headers: _authHeaders() });
  const d = await r.json();
  if (!r.ok) throw new Error(d.detail || "Failed to load documents.");
  return { documents: d.map(_mapResearch) };
}

async function apiStatus() {
  return _apiJson("/admin/repository/status");
}

const apiDashboardStats = () => _apiJson("/admin/dashboard");

function apiPdfUrl(source) {
  return `${API_BASE}/repository/${encodeURIComponent(source)}/pdf`;
}

function apiPreviewPdfUrl(previewId) {
  return `${API_BASE}/repository/upload/preview/${encodeURIComponent(previewId)}/pdf`;
}

/**
 * Opens a protected PDF in a new tab. A plain <a href> can't carry the
 * JWT Authorization header, so this fetches the file as a blob with
 * the header attached, then opens that blob locally.
 */
async function viewPdfInNewTab(url) {
  try {
    const r = await _apiFetch(url, { headers: _authHeaders() });
    if (!r.ok) {
      const d = await r.json().catch(() => ({}));
      throw new Error(d.detail || "Could not load PDF.");
    }
    const blob = await r.blob();
    const blobUrl = URL.createObjectURL(blob);
    window.open(blobUrl, "_blank");
  } catch (e) {
    toast(`Could not open PDF: ${e.message}`, "error");
  }
}

async function apiValidateResearch(researchId, action, comments) {
  const r = await _apiFetch(`${API_BASE}/admin/repository/${researchId}/validate`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ..._authHeaders() },
    body: JSON.stringify({ approve: action === "validated", feedback_message: comments || null }),
  });
  const d = await r.json();
  if (!r.ok) throw new Error(d.detail || "Failed to record decision.");
  return d;
}

async function apiDeletePaper(id) {
  const r = await _apiFetch(`${API_BASE}/admin/repository/${id}`, {
    method: "DELETE", headers: _authHeaders(),
  });
  const d = await r.json();
  if (!r.ok) throw new Error(d.detail || "Delete failed.");
  return { success: true, message: d.detail };
}

async function apiSubmissions() {
  const r = await _apiFetch(`${API_BASE}/admin/repository`, { headers: _authHeaders() });
  const d = await r.json();
  if (!r.ok) throw new Error(d.detail || "Failed to fetch submissions.");
  return { submissions: d.map(_mapResearch) };
}

async function apiMySubmissions() {
  const r = await _apiFetch(`${API_BASE}/repository/my-uploads`, { headers: _authHeaders() });
  const d = await r.json();
  if (!r.ok) throw new Error(d.detail || "Failed to fetch your submissions.");
  return { submissions: d.map(_mapResearch) };
}

/* ── User management (librarian, users.html) ────────────────────────── */
/* Soft-delete only: deactivate flips a flag server-side — no user row or
   submission history is ever removed. Librarian accounts never appear. */

async function apiListUsers(includeInactive = false) {
  const r = await _apiFetch(
    `${API_BASE}/admin/users?include_inactive=${includeInactive ? "true" : "false"}`,
    { headers: _authHeaders() }
  );
  const d = await r.json();
  if (!r.ok) throw new Error(d.detail || "Failed to fetch users.");
  return d;
}

async function apiDeactivateUser(userId) {
  const r = await _apiFetch(`${API_BASE}/admin/users/${userId}/deactivate`, {
    method: "PATCH", headers: _authHeaders(),
  });
  const d = await r.json();
  if (!r.ok) throw new Error(d.detail || "Failed to deactivate user.");
  return d;
}

async function apiReactivateUser(userId) {
  const r = await _apiFetch(`${API_BASE}/admin/users/${userId}/reactivate`, {
    method: "PATCH", headers: _authHeaders(),
  });
  const d = await r.json();
  if (!r.ok) throw new Error(d.detail || "Failed to reactivate user.");
  return d;
}

async function apiAdminCreateUser({ fullname, email, department }) {
  const r = await _apiFetch(`${API_BASE}/admin/users`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ..._authHeaders() },
    body: JSON.stringify({ fullname, email, department: department || null }),
  });
  const d = await r.json();
  if (!r.ok) throw new Error(d.detail || "Failed to create user.");
  return d; // { user, generated_password } — password returned ONCE
}

/* ── Toast helper ────────────────────────────────────────────────────── */

function toast(msg, type = "info", duration = 3500) {
  const wrap = document.getElementById("toasts");
  if (!wrap) return;
  const el       = document.createElement("div");
  el.className   = `toast ${type}`;
  el.textContent = msg;
  wrap.appendChild(el);
  setTimeout(() => el.remove(), duration);
}
async function _apiFetch(url, options = {}) {
  const response = await fetch(url, options);
  if (response.ok) return response;
  const data = await response.json().catch(() => ({}));
  if (response.status === 401 && new Headers(options.headers || {}).has("Authorization")) {
    sessionStorage.clear(); window.location.replace("index.html?expired=1");
  }
  let message = typeof data.detail === "string" ? data.detail : "Request failed. Please try again.";
  if (response.status === 422) message = "Please check the form values.";
  if (response.status === 429 && typeof data.detail !== "string") message = "Too many requests. Please wait a minute and try again.";
  const error = new Error(message);
  error.status = response.status;
  error.reason = data.reason;
  throw error;
}
async function _apiJson(path, method = "GET", body, authenticated = true) {
  const response = await _apiFetch(`${API_BASE}${path}`, {
    method, headers: { ...(authenticated ? _authHeaders() : {}), ...(body !== undefined ? {"Content-Type": "application/json"} : {}) },
    ...(body !== undefined ? {body: JSON.stringify(body)} : {}),
  });
  return response.json();
}
const apiColleges = () => _apiJson("/auth/colleges", "GET", undefined, false);
const apiRequestPasswordReset = email => _apiJson("/auth/password/reset/request-otp", "POST", {email}, false);
const apiConfirmPasswordReset = (email, code) => _apiJson("/auth/password/reset/confirm", "POST", {email, code}, false);
const apiRepositoryFilters = () => _apiJson("/repository/filters");
const apiNotifications = () => _apiJson("/notifications?limit=20");
const apiMarkNotificationRead = id => _apiJson(`/notifications/${id}/read`, "PATCH");
const apiMarkAllNotificationsRead = () => _apiJson("/notifications/read-all", "POST");
const apiBulkDeletePapers = ids => _apiJson("/admin/repository/bulk-delete", "POST", ids);

function wireNotifications(bell, dropdown) {
  dropdown.setAttribute("aria-live", "polite");
  dropdown.addEventListener("click", event => event.stopPropagation());
  const refresh = async () => {
    if (dropdown.contains?.(document.activeElement)) dropdown.focus?.();
    dropdown.textContent = "Loading notifications...";
    try {
      const data = await apiNotifications();
      bell.classList.toggle("has-unread", data.unread_count > 0);
      bell.setAttribute("aria-label", `Notifications: ${data.unread_count} unread`);
      dropdown.replaceChildren();
      const heading = document.createElement("div"); heading.className = "notif-header";
      heading.textContent = `Notifications (${data.unread_count} unread)`; dropdown.appendChild(heading);
      const action = (label, run) => {
        const button = document.createElement("button"); button.className = "btn btn-ghost btn-sm"; button.textContent = label;
        button.onclick = async () => {
          button.disabled = true;
          try { await run(); await refresh(); toast("Notifications marked read.", "success"); }
          catch (error) { toast(error.message, "error"); button.disabled = false; }
        }; return button;
      };
      if (data.unread_count) dropdown.appendChild(action("Mark all read", apiMarkAllNotificationsRead));
      if (!data.items.length) { const empty = document.createElement("div"); empty.className = "notif-empty"; empty.textContent = "No notifications yet."; dropdown.appendChild(empty); }
      data.items.forEach(item => {
        const row = document.createElement("div"); row.className = "notification-item";
        const message = document.createElement(item.research_id == null ? "p" : "a"); message.textContent = item.message;
        if (item.research_id != null) message.href = "my-submissions.html";
        row.appendChild(message);
        if (item.status !== "read") row.appendChild(action("Mark read", () => apiMarkNotificationRead(item.id)));
        dropdown.appendChild(row);
      });
    } catch (error) {
      dropdown.textContent = error.message;
      const retry = document.createElement("button"); retry.textContent = "Retry"; retry.className = "btn btn-ghost btn-sm"; retry.onclick = refresh; dropdown.appendChild(retry);
    }
  }; dropdown.refreshNotifications = refresh; refresh();
}

function wireNotificationDisclosure(bell, dropdown) {
  dropdown.tabIndex = -1;
  dropdown.setAttribute('role', 'region');
  dropdown.setAttribute('aria-label', 'Notifications');
  bell.setAttribute('aria-expanded', 'false');
  const close = (restore = false) => {
    dropdown.classList.remove('open');
    bell.setAttribute('aria-expanded', 'false');
    if (restore && bell.isConnected) bell.focus();
  };
  bell.addEventListener('click', event => {
    event.stopPropagation();
    if (dropdown.classList.contains('open')) { close(true); return; }
    dropdown.classList.add('open');
    bell.setAttribute('aria-expanded', 'true');
    dropdown.focus();
    dropdown.refreshNotifications();
  });
  const escape = event => {
    if (event.key === 'Escape' && !event.defaultPrevented && dropdown.classList.contains('open')) {
      event.preventDefault(); event.stopPropagation(); close(true);
    }
  };
  bell.addEventListener('keydown', escape);
  dropdown.addEventListener('keydown', escape);
  document.addEventListener('click', event => {
    if (!dropdown.contains(event.target) && !bell.contains(event.target)) close();
  });
  document.addEventListener('focusin', event => {
    if (!dropdown.contains(event.target) && !bell.contains(event.target)) close();
  });
}

// Landing is a navigation hint, never an authorization check.
function landingPage(me) {
  if (me.landing === "dashboard") return "dashboard.html";
  if (me.landing === "ask") return "ask.html";
  return me.role === "librarian" ? "dashboard.html" : "ask.html";
}
const apiAskRepository = ({ query, college_id, year, context = [] }) =>
  _apiJson("/search/ask", "POST", { query, college_id, year, context });
const apiDocumentById = async id => _mapResearch(await _apiJson(`/repository/${encodeURIComponent(id)}`));
const apiReaderInfo = id => _apiJson(`/repository/${encodeURIComponent(id)}/reader`);
async function apiReaderPage(id, page, signal) {
  const response = await _apiFetch(`${API_BASE}/repository/${encodeURIComponent(id)}/reader/pages/${page}`, {
    headers: _authHeaders(), signal,
  });
  if (!response.headers.get("content-type")?.includes("image/jpeg")) {
    throw new Error("The reader returned an unsupported page format.");
  }
  return response.blob();
}
