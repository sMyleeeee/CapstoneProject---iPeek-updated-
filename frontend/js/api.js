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

/* ── Nav link sets ──────────────────────────────────────────────────── */
const NAV_LINKS = {
  student: [
    { href: "browse.html",  label: "Browse Research",  id: "browse",    icon: "M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253" },
    { href: "upload.html",  label: "Submit Proposal",  id: "upload",    icon: "M12 4v16m8-8H4" },
    { href: "my-submissions.html", label: "My Submissions", id: "my-submissions", icon: "M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2" },
  ],
  faculty: [
    { href: "browse.html",  label: "Browse Research",  id: "browse",    icon: "M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253" },
    { href: "upload.html",  label: "Submit Proposal",  id: "upload",    icon: "M12 4v16m8-8H4" },
    { href: "my-submissions.html", label: "My Submissions", id: "my-submissions", icon: "M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2" },
  ],
  librarian: [
    { href: "dashboard.html", label: "Dashboard",     id: "dashboard", icon: "M3 12l2-2m0 0l7-7 7 7M5 10v10a1 1 0 001 1h3m10-11l2 2m-2-2v10a1 1 0 01-1 1h-3m-6 0a1 1 0 001-1v-4a1 1 0 011-1h2a1 1 0 011 1v4a1 1 0 001 1m-6 0h6" },
    { href: "review.html",    label: "Review Queue",  id: "review",    icon: "M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" },
    { href: "browse.html",    label: "Browse",        id: "browse",    icon: "M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253" },
    { href: "upload.html",    label: "Upload",        id: "upload",    icon: "M12 4v16m8-8H4" },
    { href: "my-submissions.html", label: "My Submissions", id: "my-submissions", icon: "M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2" },
  ],
};

function applyNav(role) {
  const container = document.querySelector(".nav-links");
  if (!container) return;
  const links   = NAV_LINKS[role] || NAV_LINKS.student;
  const current = window.location.pathname.split("/").pop();

  /* ── SVG icon helper ────────────────────────────────────── */
  function navIcon(d) {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("width", "16");
    svg.setAttribute("height", "16");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("fill", "none");
    svg.setAttribute("stroke", "currentColor");
    svg.setAttribute("stroke-width", "2");
    svg.setAttribute("stroke-linecap", "round");
    svg.setAttribute("stroke-linejoin", "round");
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("d", d);
    svg.appendChild(path);
    return svg;
  }

  /* ── Desktop pill nav ──────────────────────────────────── */
  const track = document.createElement("div");
  track.className = "nav-track";

  const ul = document.createElement("ul");
  ul.className = "pill-list";
  links.forEach(({ href, label, icon }) => {
    const li = document.createElement("li");
    const a  = document.createElement("a");
    a.className   = `pill${href === current ? " is-active" : ""}`;
    a.href        = href;
    if (icon) a.appendChild(navIcon(icon));
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

  /* ── Nav right: bell + profile + logout ─────────────────── */
  const navRight = document.querySelector(".nav-right");
  if (navRight) {
    /* Save references BEFORE clearing — innerHTML destroys these */
    const existingAvatar = document.getElementById("avatarEl");
    const existingRole   = document.getElementById("rolePill");

    navRight.innerHTML = "";

    /* Bell notification button */
    const notifWrapper = document.createElement("div");
    notifWrapper.className = "nav-notif-wrapper";

    const bellBtn = document.createElement("button");
    bellBtn.className = "nav-icon-btn";
    bellBtn.title = "Notifications";
    bellBtn.setAttribute("aria-label", "Notifications");
    const bellSvg = navIcon("M18 8A6 6 0 006 8c0 7-3 9-3 9h18s-3-2-3-9M13.73 21a2 2 0 01-3.46 0");
    bellBtn.appendChild(bellSvg);

    const dropdown = document.createElement("div");
    dropdown.className = "nav-dropdown";
    dropdown.innerHTML = '<div class="notif-header">Notifications</div><div class="notif-empty">No new notifications</div>';

    bellBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      dropdown.classList.toggle("open");
    });

    notifWrapper.appendChild(bellBtn);
    notifWrapper.appendChild(dropdown);
    navRight.appendChild(notifWrapper);

    /* Close dropdown on outside click */
    document.addEventListener("click", () => dropdown.classList.remove("open"));

    /* Profile pill */
    if (existingAvatar && existingRole) {
      const wrapper = document.createElement("a");
      wrapper.className = "profile-pill";
      wrapper.href = "profile.html";
      wrapper.appendChild(existingAvatar);
      wrapper.appendChild(existingRole);
      navRight.appendChild(wrapper);
    }

    /* Icon logout button */
    const logoutBtn = document.createElement("button");
    logoutBtn.className = "nav-icon-btn";
    logoutBtn.title = "Sign Out";
    logoutBtn.setAttribute("aria-label", "Sign Out");
    const logoutSvg = navIcon("M9 21H5a2 2 0 01-2-2V5a2 2 0 012-2h4M16 17l5-5-5-5M21 12H9");
    logoutBtn.appendChild(logoutSvg);
    logoutBtn.addEventListener("click", async () => {
      try { await apiLogout(); } catch {}
      sessionStorage.clear();
      window.location.replace("index.html");
    });
    navRight.appendChild(logoutBtn);
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
  mBrand.innerHTML = '<div class="nav-logo">i</div> Research Repository';
  mobileHeader.appendChild(mBrand);

  const avatarEl = document.getElementById("avatarEl");
  const rolePill = document.getElementById("rolePill");
  const initials = avatarEl ? avatarEl.textContent : "?";
  const roleText = rolePill ? rolePill.textContent : role;
  const mProfile = document.createElement("a");
  mProfile.className = "mobile-profile-icon";
  mProfile.href = "profile.html";
  mProfile.textContent = initials;
  mProfile.title = `${roleText} — View Profile`;
  mobileHeader.appendChild(mProfile);

  /* ── Bottom tab bar ────────────────────────────────────── */
  let bottomBar = document.querySelector(".bottom-tab-bar");
  if (!bottomBar) {
    bottomBar = document.createElement("div");
    bottomBar.className = "bottom-tab-bar";
    document.body.appendChild(bottomBar);
  }
  bottomBar.innerHTML = "";

  const bUl = document.createElement("ul");
  bUl.className = "pill-list";
  links.forEach(({ href, label, icon }) => {
    const li = document.createElement("li");
    const a  = document.createElement("a");
    a.className   = `pill${href === current ? " is-active" : ""}`;
    a.href        = href;
    if (icon) a.appendChild(navIcon(icon));
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

function apiAuthHeaderForPdf() {
  return _authHeaders();
}

/* ── Auth ────────────────────────────────────────────────────────────── */

async function apiLogin(username, password) {
  const body = new URLSearchParams();
  body.append("username", username);
  body.append("password", password);

  const r = await fetch(`${API_BASE}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body,
  });
  const d = await r.json();
  if (!r.ok) throw new Error(d.detail || "Login failed.");

  sessionStorage.setItem("ipeek_token", d.access_token);
  return { success: true, role: d.role, full_name: d.fullname };
}

async function apiLogout() {
  return { success: true };
}

async function apiMe() {
  const r = await fetch(`${API_BASE}/auth/me`, { headers: _authHeaders() });
  const d = await r.json();
  if (!r.ok) throw new Error(d.detail || "Not logged in.");
  return {
    user_id:   d.id,
    username:  d.username,
    role:      d.role,
    full_name: d.fullname,
    email:     d.email,
    contact:   d.contact,
    department: d.department,
  };
}

async function apiUpdateProfile(fullName, email, contact) {
  const r = await fetch(`${API_BASE}/auth/me`, {
    method: "PUT",
    headers: { "Content-Type": "application/json", ..._authHeaders() },
    body: JSON.stringify({ fullname: fullName, email, contact }),
  });
  const d = await r.json();
  if (!r.ok) throw new Error(d.detail || "Failed to update profile.");
  return {
    user_id: d.id, username: d.username, role: d.role,
    full_name: d.fullname, email: d.email, contact: d.contact, department: d.department,
  };
}

async function apiUpdatePassword(currentPassword, newPassword) {
  const r = await fetch(`${API_BASE}/auth/password`, {
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
  const r = await fetch(`${API_BASE}/auth/register/request-otp`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email }),
  });
  const d = await r.json();
  if (!r.ok) throw new Error(d.detail || "Could not send verification code.");
  return d;
}

async function apiVerifyOtp(email, code) {
  const r = await fetch(`${API_BASE}/auth/register/verify-otp`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, code }),
  });
  const d = await r.json();
  if (!r.ok) throw new Error(d.detail || "Verification failed.");
  return d;
}

async function apiRegister({ email, password, fullname, department }) {
  const r = await fetch(`${API_BASE}/auth/register`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password, fullname, department: department || null }),
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

  const r = await fetch(`${API_BASE}/repository/upload/preview`, {
    method: "POST",
    headers: _authHeaders(),
    body: fd,
  });
  const d = await r.json();
  if (!r.ok) throw new Error(d.detail || "Preview failed.");
  return d;
}

async function apiIngestConfirm(previewId, finalValues) {
  const r = await fetch(`${API_BASE}/repository/upload/confirm`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ..._authHeaders() },
    body: JSON.stringify({ preview_id: previewId, final_values: finalValues }),
  });
  const d = await r.json();
  if (!r.ok) throw new Error(d.detail || "Submission failed.");
  return d;
}

/* ── RAG Analysis ────────────────────────────────────────────────────── */

async function apiSimilarity(researchId) {
  const r = await fetch(`${API_BASE}/ai/${researchId}/similarity`, {
    method: "POST", headers: _authHeaders(),
  });
  const d = await r.json();
  if (!r.ok) throw new Error(d.detail || "Similarity failed.");
  return d;
}

async function apiSummary(researchId) {
  const r = await fetch(`${API_BASE}/ai/${researchId}/summary`, {
    method: "POST", headers: _authHeaders(),
  });
  const d = await r.json();
  if (!r.ok) throw new Error(d.detail || "Summary failed.");
  return d;
}

async function apiGaps(researchId) {
  const r = await fetch(`${API_BASE}/ai/${researchId}/gaps`, {
    method: "POST", headers: _authHeaders(),
  });
  const d = await r.json();
  if (!r.ok) throw new Error(d.detail || "Gap analysis failed.");
  return d;
}

async function apiRefineAnalysis(researchId, query, target) {
  const r = await fetch(`${API_BASE}/ai/${researchId}/refine`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ..._authHeaders() },
    body: JSON.stringify({ query, target }),
  });
  const d = await r.json();
  if (!r.ok) throw new Error(d.detail || "Refine failed.");
  return d;
}

async function apiChat(question, history = []) {
  const r = await fetch(`${API_BASE}/ai/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ..._authHeaders() },
    body: JSON.stringify({ question, history }),
  });
  const d = await r.json();
  if (!r.ok) throw new Error(d.detail || "Chat failed.");
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
    college:          item.department,
    department:       item.department,
    keywords:         "",
    abstract:         item.abstract || "",
    source:           item.source_stem,
    source_stem:      item.source_stem,
    status:           item.status,
    feedback:         item.feedback_note,
    submitted_at:     item.created_at,
  };
}

async function apiDocuments() {
  const r = await fetch(`${API_BASE}/repository/browse`, { headers: _authHeaders() });
  const d = await r.json();
  if (!r.ok) throw new Error(d.detail || "Failed to load documents.");
  return { documents: d.map(_mapResearch) };
}

async function apiDocumentDetail(source) {
  const r = await fetch(`${API_BASE}/repository/${encodeURIComponent(source)}/detail`, {
    headers: _authHeaders(),
  });
  const d = await r.json();
  if (!r.ok) throw new Error(d.detail || "Document not found.");
  return _mapResearch(d);
}

async function apiStatus() {
  return { status: "ok", chunks_indexed: 0, message: "" };
}

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
    const r = await fetch(url, { headers: _authHeaders() });
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
  const r = await fetch(`${API_BASE}/admin/repository/${researchId}/validate`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ..._authHeaders() },
    body: JSON.stringify({ approve: action === "validated", feedback_message: comments || null }),
  });
  const d = await r.json();
  if (!r.ok) throw new Error(d.detail || "Failed to record decision.");
  return d;
}

async function apiDeletePaper(id) {
  const r = await fetch(`${API_BASE}/admin/repository/${id}`, {
    method: "DELETE", headers: _authHeaders(),
  });
  const d = await r.json();
  if (!r.ok) throw new Error(d.detail || "Delete failed.");
  return { success: true, message: d.detail };
}

async function apiSubmissions() {
  const r = await fetch(`${API_BASE}/admin/repository`, { headers: _authHeaders() });
  const d = await r.json();
  if (!r.ok) throw new Error(d.detail || "Failed to fetch submissions.");
  return { submissions: d.map(_mapResearch) };
}

async function apiMySubmissions() {
  const r = await fetch(`${API_BASE}/repository/my-uploads`, { headers: _authHeaders() });
  const d = await r.json();
  if (!r.ok) throw new Error(d.detail || "Failed to fetch your submissions.");
  return { submissions: d.map(_mapResearch) };
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