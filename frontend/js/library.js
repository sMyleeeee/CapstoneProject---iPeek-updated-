/**
 * library.js
 * ----------
 * Library Viewing Station (kiosk/OPAC terminal page).
 *  - Requires a normal login (student/faculty/librarian).
 *  - On load, marks THIS login session as a kiosk session (POST
 *    /kiosk/activate) so the backend serves full PDFs to it.
 *  - Lists approved research with the same card pattern as browse.js;
 *    clicking a card opens the full PDF in the on-page viewer (viewer
 *    logic relocated here from detail.js — Part 3 moved remote full-PDF
 *    access to this page only).
 *  - Lock control (librarian): locks THIS session to this page only
 *    (session-scoped via JWT sid — never touches any other session).
 *
 * All DOM writes use textContent/createElement — XSS safe (matches
 * browse.js / detail.js / review.js).
 */

/* ── State ───────────────────────────────────────────────────────────────── */
let allDocs = [];
let filters = { search: "" };
let currentRole   = null;
let currentLocked = false;
let currentSource = null;

/* PDF viewer state — relocated from detail.js */
let pdfDoc     = null;
let pdfPage    = 1;
let pdfVisible = false;

/* ── Safe string helper (same as browse.js) ──────────────────────────────── */

function safeStr(val) {
  if (val === null || val === undefined) return "";
  const s = String(val).trim();
  return s === "Unknown" ? "" : s;
}

/* ── Page init ───────────────────────────────────────────────────────────── */

async function initPage() {
  let me;
  try {
    me = await apiMe();
  } catch {
    window.location.replace("index.html");
    return;
  }

  currentRole = me.role;
  applyNav(me.role);   // async kiosk guard runs inside (redirects if locked elsewhere)

  const avatarEl = document.getElementById("avatarEl");
  const rolePill = document.getElementById("rolePill");
  if (avatarEl) avatarEl.textContent = (me.full_name || me.username).substring(0, 2).toUpperCase();
  if (rolePill) rolePill.textContent = me.role.charAt(0).toUpperCase() + me.role.slice(1);

  document.body.style.visibility = "visible";

  /* Register this session as a Library Viewing Station session so the
     backend's full-PDF gate admits requests from it. */
  try { await apiKioskActivate(); } catch { /* lock features need a re-login if no sid */ }

  await refreshLockControl();
  await loadDocuments();
}

/* ── Data loading ────────────────────────────────────────────────────────── */

async function loadDocuments() {
  try {
    const data = await apiDocuments();
    allDocs    = data.documents || [];
  } catch {
    toast("Could not load repository. Is the backend running?", "error");
    allDocs = [];
  } finally {
    const loading = document.getElementById("loading");
    if (loading) loading.style.display = "none";
  }
  render();
}

/* ── Rendering — same card pattern as browse.js ──────────────────────────── */

function render() {
  const q     = filters.search.toLowerCase();
  const grid  = document.getElementById("grid");
  const empty = document.getElementById("empty");

  const docs = allDocs.filter(d => {
    if (!q) return true;
    const haystack = `${safeStr(d.title)} ${safeStr(d.authors)}`.toLowerCase();
    return haystack.includes(q);
  });

  const countEl = document.getElementById("resultsCount");
  if (countEl) {
    countEl.textContent = `Showing ${docs.length} paper${docs.length !== 1 ? "s" : ""} — full versions on this terminal`;
  }

  grid.innerHTML = "";

  if (docs.length === 0) {
    if (empty) empty.style.display = "block";
    return;
  }
  if (empty) empty.style.display = "none";

  docs.forEach(d => {
    const card = document.createElement("div");
    card.className = "r-card";
    card.addEventListener("click", () => openViewer(d.source, d.title));

    const idEl = document.createElement("div");
    idEl.className   = "r-card-id";
    idEl.textContent = `${safeStr(d.college) || "—"} · ${safeStr(d.year) || "—"}`;

    const titleEl = document.createElement("div");
    titleEl.className   = "r-card-title";
    titleEl.textContent = safeStr(d.title) || d.source || "Untitled";

    const metaEl = document.createElement("div");
    metaEl.className   = "r-card-meta";
    metaEl.textContent = safeStr(d.authors) || "Authors unknown";

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

  staggerIn(grid);
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

/* ── Lock control (session-scoped — own sid only) ────────────────────────── */

async function refreshLockControl() {
  const btn = document.getElementById("kioskLockBtn");
  if (!btn) return;
  try {
    currentLocked = !!(await apiKioskStatus()).locked;
  } catch {
    currentLocked = false;
  }
  if (currentRole === "librarian") {
    btn.style.display = "inline-block";
    btn.textContent   = currentLocked ? "Unlock Session" : "Lock to This Page";
  } else {
    btn.style.display = "none";
  }
}

async function toggleKioskLock() {
  const btn = document.getElementById("kioskLockBtn");
  if (btn) btn.disabled = true;
  try {
    await apiKioskSetLock(!currentLocked);
    currentLocked = !currentLocked;
    toast(currentLocked
      ? "Session locked — restricted to this page until unlocked."
      : "Session unlocked.", "success");
    applyNav(currentRole);        // re-render nav: hidden when locked, restored when unlocked
    await refreshLockControl();
  } catch (e) {
    toast(`Lock failed: ${e.message}`, "error");
  } finally {
    if (btn) btn.disabled = false;
  }
}

/* Remote lock/unlock from the dashboard — nav is re-rendered by the
   guard's poll; this only syncs the lock button. */
window.addEventListener("kiosk-lock-change", () => {
  refreshLockControl();
});

/* ── PDF viewer — relocated from detail.js ───────────────────────────────── */

function openViewer(source, title) {
  currentSource = source;

  const titleEl = document.getElementById("viewerTitle");
  if (titleEl) titleEl.textContent = title || source;
  const titleOv = document.getElementById("viewerTitleOv");
  if (titleOv) titleOv.textContent = title || source;

  const isMobile = window.innerWidth <= 768;
  if (isMobile) {
    const overlay = document.getElementById("pdfOverlay");
    if (overlay) overlay.classList.add("open");
  } else {
    const card = document.getElementById("pdfViewerCard");
    if (!card) return;
    card.style.display = "block";
    card.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  pdfVisible = true;
  pdfDoc = null;   // a different paper may have been viewed before
  pdfPage = 1;
  loadPdf();
}

async function loadPdf() {
  const errorEl = document.getElementById("pdfError");
  const canvas  = document.getElementById("pdfCanvas");
  if (errorEl) errorEl.style.display = "none";
  if (canvas)  canvas.style.display  = "block";

  try {
    const url = apiPdfUrl(currentSource);

    if (typeof pdfjsLib === "undefined") {
      throw new Error("PDF.js not loaded. Check the CDN script tag in library.html.");
    }

    pdfjsLib.GlobalWorkerOptions.workerSrc =
      "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js";

    // httpHeaders carries the JWT bearer token
    pdfDoc = await pdfjsLib.getDocument({ url, httpHeaders: apiAuthHeaderForPdf() }).promise;
    updatePageInfo();
    await renderPdfPage(1);

  } catch (e) {
    if (canvas)  canvas.style.display  = "none";
    if (errorEl) {
      errorEl.style.display = "block";
      errorEl.textContent   = `Could not load PDF: ${e.message}`;
    }
    toast("PDF failed to load.", "error");
  }
}

async function renderPdfPage(num) {
  if (!pdfDoc) return;
  pdfPage = num;

  const isMobile = window.innerWidth <= 768;
  const canvasId = isMobile ? "pdfCanvasOverlay" : "pdfCanvas";
  const canvas = document.getElementById(canvasId);
  if (!canvas) return;

  try {
    const page     = await pdfDoc.getPage(num);
    const viewport = page.getViewport({ scale: 1.4 });
    canvas.width  = viewport.width;
    canvas.height = viewport.height;

    const ctx = canvas.getContext("2d");
    await page.render({ canvasContext: ctx, viewport }).promise;
  } catch (e) {
    toast(`Could not render page ${num}: ${e.message}`, "error");
  }

  updatePageInfo();
}

function updatePageInfo() {
  const isMobile = window.innerWidth <= 768;
  const infoId   = isMobile ? "pdfPageInfoOv" : "pdfPageInfo";
  const prevId   = isMobile ? "pdfPrevBtnOv"  : "pdfPrevBtn";
  const nextId   = isMobile ? "pdfNextBtnOv"  : "pdfNextBtn";

  const info    = document.getElementById(infoId);
  const prevBtn = document.getElementById(prevId);
  const nextBtn = document.getElementById(nextId);

  if (info) {
    info.textContent = pdfDoc ? `Page ${pdfPage} / ${pdfDoc.numPages}` : "Page — / —";
  }
  if (prevBtn) prevBtn.disabled = pdfPage <= 1;
  if (nextBtn) nextBtn.disabled = !pdfDoc || pdfPage >= pdfDoc.numPages;
}

function pdfPrevPage() {
  if (pdfPage > 1) renderPdfPage(pdfPage - 1);
}

function pdfNextPage() {
  if (pdfDoc && pdfPage < pdfDoc.numPages) renderPdfPage(pdfPage + 1);
}

function closePdfOverlay() {
  const overlay = document.getElementById("pdfOverlay");
  if (overlay) overlay.classList.remove("open");
}

function hidePdfViewer() {
  const isMobile = window.innerWidth <= 768;

  if (isMobile) {
    closePdfOverlay();
  } else {
    const card = document.getElementById("pdfViewerCard");
    if (card) card.style.display = "none";
  }
  pdfVisible = false;
}

/* ── Initialize ──────────────────────────────────────────────────────────── */
initPage();
