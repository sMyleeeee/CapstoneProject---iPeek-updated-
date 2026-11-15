let allDocs = [];
let filters = { college: "", year: "all", search: "" };



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

  await loadBrowseFilters();
}

/* ── Data loading ────────────────────────────────────────────────────────── */

/**
 * Fetches all approved documents from the backend.
 * Shows an error toast if unreachable — never falls back to mock data.
 */
let browseRequest = 0;
async function loadDocuments() {
  const request = ++browseRequest;
  document.getElementById("loading").style.display = "block";
  document.getElementById("grid").replaceChildren();
  document.getElementById("empty").style.display = "none";
  try {
    const data = await apiDocuments(filters);
    if (request !== browseRequest) return;
    allDocs    = data.documents || [];
  } catch (error) {
    if (request !== browseRequest) return;
    toast(error.message, "error");
    document.getElementById("resultsCount").textContent = error.message;
    allDocs = [];
    const retry = document.createElement("button"); retry.textContent = "Retry"; retry.className = "btn btn-ghost"; retry.onclick = loadDocuments; document.getElementById("grid").appendChild(retry);
    return;
  } finally {
    /* Always hide spinner regardless of outcome */
    const loading = document.getElementById("loading");
    if (loading && request === browseRequest) loading.style.display = "none";
  }

  if (request !== browseRequest) return;
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

    /* Live search */
    if (q) {
      const title   = safeStr(d.title).toLowerCase();
      const authors = safeStr(d.authors).toLowerCase();
      if (!title.includes(q) && !authors.includes(q)) {
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
    const card = document.createElement("a");
    card.className = "r-card";

    /* Click handler via addEventListener — source never interpolated
       into an attribute string, preventing onclick injection */
    card.href = `detail.html?id=${encodeURIComponent(d.id)}`;

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

    card.appendChild(idEl);
    card.appendChild(titleEl);
    card.appendChild(metaEl);
    grid.appendChild(card);
  });

  staggerIn(grid); // subtle staggered fade-in, from motion.js
}

/**
 * Updates the sidebar count badges after documents load.
 */
async function loadBrowseFilters() {
  try {
    const data = await apiRepositoryFilters();
    filters.college = data.user_college?.id || "";
    filters.year = data.latest_year == null ? "all" : String(data.latest_year);
    document.querySelectorAll(".college-filters").forEach(container => {
      container.replaceChildren();
      const label = document.createElement("span"); label.className = "sb-label"; label.textContent = "Colleges"; container.appendChild(label);
      [{id: "", name: "All colleges", code: ""}, ...data.colleges].forEach(c => {
        const button = document.createElement("button"); button.className = "sb-item"; button.dataset.college = String(c.id);
        button.textContent = c.id ? `${c.code} - ${c.name} (${c.count})` : c.name;
        button.onclick = () => { filters.college = c.id; syncCollegeButtons(); loadDocuments(); };
        container.appendChild(button);
      });
    });
    syncCollegeButtons();
    const year = document.getElementById("yearFilter"); year.replaceChildren(new Option("All years", "all"));
    data.years.forEach(y => year.add(new Option(String(y), String(y)))); year.value = filters.year; year.disabled = false;
    year.onchange = () => { filters.year = year.value; loadDocuments(); };
    await loadDocuments();
  } catch (error) {
    document.getElementById("loading").style.display = "none";
    document.querySelectorAll(".college-filters").forEach(container => {
      container.textContent = error.message;
      const retry = document.createElement("button"); retry.className = "btn btn-ghost"; retry.textContent = "Retry"; retry.onclick = loadBrowseFilters; container.appendChild(retry);
    });
  }
}
function syncCollegeButtons() {
  document.querySelectorAll("[data-college]").forEach(button => button.classList.toggle("active", button.dataset.college === String(filters.college)));
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