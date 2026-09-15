/**
 * browse.js
 * ---------
 * Handles the Browse Research page:
 *  - Fetches all approved documents from GET /api/documents
 *  - Renders research cards in the grid using safe DOM methods
 *  - Filters by: college, technology tag, year, and live search text
 *  - Clicking a card navigates to detail.html?id={source}
 *
 * BUG FIXES in this version:
 *  1. keywords crash — d.keywords was "Unknown" or null; .split() on it
 *     threw a silent JS error that stopped ALL cards from rendering.
 *     Fixed: safeStr() helper + keyword guard before every .split() call.
 *  2. College filter crash — assumed d.college was never null/undefined.
 *     Fixed: (d.college || "") defensive access + COLLEGE_MAP partial match.
 *  3. Search crash — d.authors and d.keywords accessed without null guard.
 *     Fixed: safeStr() wraps every field in filter logic.
 *  4. onclick injection — source stem interpolated directly into
 *     onclick="openDetail('${d.source}')" risked attribute breakout.
 *     Fixed: cards built with createElement + addEventListener.
 *  5. Nav now uses the shared applyNav(role) from api.js instead of
 *     manually rebuilding navbar HTML — keeps this page automatically
 *     in sync with NAV_LINKS (e.g. "My Submissions" for students)
 *     instead of needing a second edit here every time nav changes.
 */

/* ── State ───────────────────────────────────────────────────────────────── */
let allDocs = [];
let filters = { college: "", tag: "", year: "all", search: "" };

/* College abbreviation → partial keyword map.
   The LLM extracts full names like "College of Industrial Technology",
   not the sidebar abbreviation "CIT" — partial matching bridges the gap.
   Declared once at module scope (not rebuilt on every render() call). */
const COLLEGE_MAP = {
  CAS: ["arts", "sciences"],
  CCI: ["computing", "informatics"],
  CEA: ["engineering", "architecture"],
  CIT: ["industrial", "technology"],
  COE: ["education"],
};

/* ── Safe string helper ──────────────────────────────────────────────────── */

/**
 * Returns a trimmed string, or "" if the value is null, undefined, or
 * the literal string "Unknown".
 * Prevents crashes when the LLM returns "Unknown" for metadata fields
 * and stops "Unknown" from appearing as rendered text in the UI.
 *
 * @param {*} val - Any value from document metadata
 * @returns {string} Safe non-null string
 */
function safeStr(val) {
  if (val === null || val === undefined) return "";
  const s = String(val).trim();
  return s === "Unknown" ? "" : s;
}

/* ── Page init ───────────────────────────────────────────────────────────── */

async function initPage() {
  /* Verify real session — redirect to login if not logged in */
  let me;
  try {
    me = await apiMe();
  } catch {
    window.location.replace("index.html");
    return;
  }

  /* Nav — shared logic, keeps every page's nav in sync automatically */
  applyNav(me.role);

  const name     = me.full_name || me.username || "User";
  const avatarEl = document.getElementById("avatarEl");
  const rolePill = document.getElementById("rolePill");
  if (avatarEl) avatarEl.textContent = name.substring(0, 2).toUpperCase();
  if (rolePill) rolePill.textContent =
    me.role.charAt(0).toUpperCase() + me.role.slice(1);

  /* Show page now that auth confirmed and nav is correct */
  document.body.style.visibility = "visible";

  await loadDocuments();
}

/* ── Data loading ────────────────────────────────────────────────────────── */

/**
 * Fetches all approved documents from the backend.
 * Shows an error toast if unreachable — never falls back to mock data.
 */
async function loadDocuments() {
  try {
    const data = await apiDocuments();
    allDocs    = data.documents || [];
  } catch {
    toast("Could not load repository. Is the backend running?", "error");
    allDocs = [];
  } finally {
    /* Always hide spinner regardless of outcome */
    const loading = document.getElementById("loading");
    if (loading) loading.style.display = "none";
  }

  updateCounts();
  render();
}

/* ── Rendering ───────────────────────────────────────────────────────────── */

/**
 * Applies active filters and re-renders the research card grid.
 * All content written via textContent/createElement — XSS safe.
 */
function render() {
  const q     = filters.search.toLowerCase();
  const grid  = document.getElementById("grid");
  const empty = document.getElementById("empty");

  const docs = allDocs.filter(d => {

    /* College filter */
    if (filters.college) {
      const keywords = COLLEGE_MAP[filters.college] ||
                       [filters.college.toLowerCase()];
      const col      = safeStr(d.college).toLowerCase();
      /* Papers with no college info are NOT hidden — avoids silently
         excluding valid papers just because metadata was incomplete */
      if (col && !keywords.some(k => col.includes(k))) return false;
    }

    /* Year filter — exact match on normalized 4-digit year */
    if (filters.year !== "all") {
      const yr = safeStr(d.year);
      if (yr && yr !== filters.year) return false;
    }

    /* Tag filter */
    if (filters.tag) {
      const kw = safeStr(d.keywords).toLowerCase();
      if (kw && !kw.includes(filters.tag.toLowerCase())) return false;
    }

    /* Live search */
    if (q) {
      const title   = safeStr(d.title).toLowerCase();
      const authors = safeStr(d.authors).toLowerCase();
      const kw      = safeStr(d.keywords).toLowerCase();
      if (!title.includes(q) && !authors.includes(q) && !kw.includes(q)) {
        return false;
      }
    }

    return true;
  });

  /* Update result count */
  const countEl = document.getElementById("resultsCount");
  if (countEl) {
    countEl.textContent =
      `Showing ${docs.length} result${docs.length !== 1 ? "s" : ""} — ISAT-U Main Campus`;
  }

  grid.innerHTML = "";

  if (docs.length === 0) {
    if (empty) empty.style.display = "block";
    return;
  }

  if (empty) empty.style.display = "none";

  /* Build each card with createElement — never innerHTML with data values */
  docs.forEach(d => {
    const card = document.createElement("div");
    card.className = "r-card";

    /* Click handler via addEventListener — source never interpolated
       into an attribute string, preventing onclick injection */
    card.addEventListener("click", () => openDetail(d.source));

    /* College · Year */
    const idEl = document.createElement("div");
    idEl.className   = "r-card-id";
    idEl.textContent = `${safeStr(d.college) || "—"} · ${safeStr(d.year) || "—"}`;

    /* Title — falls back to source stem if LLM returned nothing useful */
    const titleEl = document.createElement("div");
    titleEl.className   = "r-card-title";
    titleEl.textContent = safeStr(d.title) || d.source || "Untitled";

    /* Authors */
    const metaEl = document.createElement("div");
    metaEl.className   = "r-card-meta";
    metaEl.textContent = safeStr(d.authors) || "Authors unknown";

    /* Tags — only rendered when keywords exist and aren't "Unknown" */
    const tagsEl = document.createElement("div");
    tagsEl.className = "r-card-tags";

    const kwStr = safeStr(d.keywords);
    if (kwStr) {
      kwStr.split(",")
        .map(t => t.trim())
        .filter(Boolean)
        .slice(0, 3)
        .forEach(t => {
          const tag = document.createElement("span");
          tag.className   = "tag";
          tag.textContent = t;
          tagsEl.appendChild(tag);
        });
    }

    card.appendChild(idEl);
    card.appendChild(titleEl);
    card.appendChild(metaEl);
    card.appendChild(tagsEl);
    grid.appendChild(card);
  });
}

/**
 * Updates the sidebar count badges after documents load.
 */
function updateCounts() {
  const total = allDocs.length;
  const set   = (id, val) => {
    const el = document.getElementById(id);
    if (el) el.textContent = val;
  };
  set("cntAll",      total);
  set("cntApproved", total);
  set("cntOngoing",  0);
  set("cntRejected", 0);
  /* Mobile filter sheet counts */
  set("cntAllM",      total);
  set("cntApprovedM", total);
  set("cntOngoingM",  0);
  set("cntRejectedM", 0);
}

/* ── Filter handlers ─────────────────────────────────────────────────────── */

function setFilter(type, value, el) {
  el.closest(".sb-section")
    .querySelectorAll(".sb-item")
    .forEach(b => b.classList.remove("active"));
  el.classList.add("active");

  /* Toggle off same filter, except for status */
  if (type !== "status") {
    filters[type] = filters[type] === value ? "" : value;
  } else {
    filters[type] = value;
  }

  render();
}

function setYear(year, el) {
  document.querySelectorAll(".year-pill")
    .forEach(p => p.classList.remove("active"));
  el.classList.add("active");
  filters.year = year;
  render();
}

function openDetail(source) {
  window.location.href = `detail.html?id=${encodeURIComponent(source)}`;
}

/* ── Live search ─────────────────────────────────────────────────────────── */

let searchTimer;
const searchInput = document.getElementById("searchInput");
if (searchInput) {
  searchInput.addEventListener("input", e => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      filters.search = e.target.value;
      render();
    }, 300);
  });
}

/* ── Initialize ──────────────────────────────────────────────────────────── */
initPage();