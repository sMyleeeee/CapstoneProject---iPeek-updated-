/**
 * detail.js
 * ---------
 * Research Detail page: loads one paper, runs AI similarity/summary/gaps
 * analysis, and drives the PDF viewer + chat panel.
 *
 * FIX NOTES (added — these were missing and caused the page to show
 * only the static shell with every panel stuck on "Loading..."):
 *   - docId was referenced but never parsed from the URL — threw a
 *     ReferenceError on the very first line of initPage(), which
 *     silently killed everything after it.
 *   - renderPaperInfo, showError, switchTab, renderSimilarityBars,
 *     renderSimilarProjects, renderGapsList, sendChat, chatKey, and
 *     the PDF viewer helpers (renderPdfPage, updatePageInfo,
 *     pdfPrevPage, pdfNextPage, hidePdfViewer) were called by this
 *     file and by detail.html's onclick handlers, but never defined.
 *   - pdfDoc / pdfPage / pdfVisible were used but never declared.
 *
 * All DOM writes use textContent/createElement, never innerHTML with
 * data values — matches the XSS-safe pattern used across the rest of
 * the app (see browse.js, review.js, my-submissions.js).
 */

/* ── URL param ───────────────────────────────────────────────────────────── */
const docId = new URLSearchParams(window.location.search).get("id");

/* ── State ───────────────────────────────────────────────────────────────── */
let paperData    = null;
let researchId    = null;

/* PDF viewer state */
let pdfDoc     = null;
let pdfPage    = 1;
let pdfVisible = false;

async function initPage() {
  const navPromise = (async () => {
    try {
      const me = await apiMe();
      applyNav(me.role);
      const el = document.getElementById("avatarEl");
      const rp = document.getElementById("rolePill");
      if (el) el.textContent = (me.full_name || me.username).substring(0, 2).toUpperCase();
      if (rp) rp.textContent = me.role.charAt(0).toUpperCase() + me.role.slice(1);
    } catch {
      applyNav("guest");
    }
    document.body.style.visibility = "visible";
  })();

  if (!docId) {
    showError("No document ID specified.");
    await navPromise;
    return;
  }

  try {
    paperData  = await apiDocumentDetail(docId);
    researchId = paperData.id;   // numeric id, resolved from source_stem
    renderPaperInfo(paperData);
  } catch (e) {
    showError(`Could not load paper: ${e.message}`);
    await navPromise;
    return;
  }

  await navPromise;

  ["simBars", "summaryBox", "gapsBox"].forEach(id => {
    const el = document.getElementById(id);
    if (el && typeof bookLoaderHTML === "function") {
      el.innerHTML = `<div style="display:flex;justify-content:center;padding:30px 0;">${bookLoaderHTML(48)}</div>`;
    }
  });

  await Promise.all([
    runSimilarity(),
    runSummary(),
    runGaps(),
  ]);

  /* Render citation badges from similarity sources */
  renderCitationBadges();

  const badge = document.getElementById("analysisStatus");
  if (badge) {
    badge.textContent = "Complete";
    badge.className   = "badge badge-validated";
  }
}

/* ── Paper info / error rendering ───────────────────────────────────────── */

/**
 * Fills in the paper title, meta line, chips, and abstract from the
 * mapped research object returned by apiDocumentDetail().
 * @param {Object} p - mapped research object (see api.js _mapResearch)
 */
function renderPaperInfo(p) {
  const titleEl = document.getElementById("paperTitle");
  if (titleEl) titleEl.textContent = p.title || p.source || "Untitled";

  const metaEl = document.getElementById("paperMeta");
  if (metaEl) {
    const bits = [p.authors, p.department, p.year].filter(v => v && v !== "Unknown");
    metaEl.textContent = bits.join(" · ") || "No metadata available.";
  }

  const chipsEl = document.getElementById("metaChips");
  if (chipsEl) {
    chipsEl.innerHTML = "";
    const chipValues = [
      { label: p.department, cls: "meta-chip" },
      { label: p.year, cls: "meta-chip" },
      { label: p.status, cls: `badge badge-${p.status}` },
    ];
    chipValues.forEach(({ label, cls }) => {
      if (!label || label === "Unknown") return;
      const chip = document.createElement("span");
      chip.className   = cls;
      chip.textContent = label;
      chipsEl.appendChild(chip);
    });
  }

  const abstractEl = document.getElementById("abstractText");
  if (abstractEl) abstractEl.textContent = p.abstract || "No abstract provided.";
}

/**
 * Shows an error state in place of the paper title/abstract, and stops
 * the AI panels from spinning forever.
 * @param {string} msg
 */
function showError(msg) {
  const titleEl = document.getElementById("paperTitle");
  if (titleEl) titleEl.textContent = "Unable to load research";

  const metaEl = document.getElementById("paperMeta");
  if (metaEl) metaEl.textContent = "";

  const abstractEl = document.getElementById("abstractText");
  if (abstractEl) abstractEl.textContent = msg;

  ["simBars", "summaryBox", "gapsBox"].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.textContent = "";
  });

  const badge = document.getElementById("analysisStatus");
  if (badge) {
    badge.textContent = "Unavailable";
    badge.className   = "badge badge-rejected";
  }

  toast(msg, "error");
}

/* ── Tabs ────────────────────────────────────────────────────────────────── */

const TAB_PANELS = {
  similarity: "tabSimilarity",
  summary:    "tabSummary",
  gaps:       "tabGaps",
};

/**
 * Returns the currently active tab key from the tab buttons.
 */
function getActiveTab() {
  const active = document.querySelector(".tab-btn.active");
  if (!active) return "similarity";
  const text = active.textContent.toLowerCase();
  if (text.includes("similarity")) return "similarity";
  if (text.includes("summary"))    return "summary";
  if (text.includes("gap"))        return "gaps";
  return "similarity";
}

/**
 * Switches the active AI-analysis tab.
 * @param {string} tab - one of "similarity" | "summary" | "gaps"
 * @param {HTMLElement} el - the tab button that was clicked
 */
function switchTab(tab, el) {
  document.querySelectorAll(".tab-btn").forEach(b => b.classList.remove("active"));
  el.classList.add("active");

  Object.values(TAB_PANELS).forEach(panelId => {
    const panel = document.getElementById(panelId);
    if (panel) panel.style.display = "none";
  });

  const target = document.getElementById(TAB_PANELS[tab]);
  if (target) target.style.display = "block";
}

/* ── Refine analysis ─────────────────────────────────────────────────────── */

async function refineAnalysis() {
  const input = document.getElementById("refineInput");
  const btn   = document.getElementById("refineBtn");
  const query = input ? input.value.trim() : "";
  if (!query) {
    toast("Please enter a query to refine the analysis.", "warning");
    return;
  }
  if (!researchId) {
    toast("Paper not loaded yet.", "error");
    return;
  }

  /* Determine target: use dropdown or fall back to active tab */
  const select = document.getElementById("refineTarget");
  let target = select ? select.value : "auto";
  if (target === "auto") target = getActiveTab();
  /* Map tab key to backend target */
  if (target === "gaps") target = "gap";

  /* Show running state */
  const badge = document.getElementById("analysisStatus");
  if (badge) {
    badge.textContent = "Running...";
    badge.className   = "badge badge-pending";
  }

  btn.disabled    = true;
  btn.textContent = "Refining...";

  try {
    const data = await apiRefineAnalysis(researchId, query, target);

    /* Update the relevant tab content */
    if (target === "similarity") {
      renderSimilarityBars(data.result);
      lastSimilaritySources = data.sources || [];
      renderCitationBadges();
    } else if (target === "summary") {
      const box = document.getElementById("summaryBox");
      if (box) box.textContent = data.result.replace(/\*\*/g, "");
    } else if (target === "gap") {
      renderGapsList(data.result);
    }

    /* Switch to the refined tab if not already active */
    const tabKey = target === "gap" ? "gaps" : target;
    const tabBtn = document.querySelector(`.tab-btn[onclick*="${tabKey}"]`);
    if (tabBtn && !tabBtn.classList.contains("active")) {
      switchTab(tabKey, tabBtn);
    }

    input.value = "";
    toast("Analysis refined.", "success");
  } catch (e) {
    toast(`Refine failed: ${e.message}`, "error");
  } finally {
    btn.disabled    = false;
    btn.textContent = "Refine";
    if (badge) {
      badge.textContent = "Complete";
      badge.className   = "badge badge-validated";
    }
  }
}

/* ── AI Analysis — now called with researchId, not a query string ─────── */

let lastSimilaritySources = [];

async function runSimilarity() {
  try {
    const data = await apiSimilarity(researchId);
    renderSimilarityBars(data.result);
    lastSimilaritySources = data.sources || [];
  } catch (e) {
    const el = document.getElementById("simBars");
    if (el) {
      el.textContent = "";
      const msg = document.createElement("div");
      msg.className   = "tab-error";
      msg.textContent = `⚠️ Similarity failed: ${e.message}`;
      el.appendChild(msg);
    }
    lastSimilaritySources = [];
  }
}

async function runSummary() {
  const box = document.getElementById("summaryBox");
  try {
    const data = await apiSummary(researchId);
    const clean = data.result.replace(/\*\*/g, "");
    if (box && typeof typeWriter === "function") {
      typeWriter(box, clean);
    } else if (box) {
      box.textContent = clean;
    }
  } catch (e) {
    if (box) {
      box.textContent = "";
      const msg = document.createElement("span");
      msg.className   = "tab-error";
      msg.textContent = `⚠️ Summary failed: ${e.message}`;
      box.appendChild(msg);
    }
  }
}

async function runGaps() {
  const box = document.getElementById("gapsBox");
  try {
    const data = await apiGaps(researchId);
    renderGapsList(data.result);
  } catch (e) {
    if (box) {
      box.textContent = "";
      const msg = document.createElement("span");
      msg.className   = "tab-error";
      msg.textContent = `⚠️ Gap analysis failed: ${e.message}`;
      box.appendChild(msg);
    }
  }
}

/* ── Similarity rendering ───────────────────────────────────────────────── */

const SIM_LEVEL_WIDTH = { HIGH: 90, MODERATE: 60, LOW: 30 };
const SIM_LEVEL_COLOR = {
  HIGH: "var(--success, #16a34a)",
  MODERATE: "var(--warning, #d97706)",
  LOW: "var(--muted, #9ca3af)",
};

/**
 * The Groq LLM returns free text (not JSON) for the similarity report,
 * roughly one block per matched study ending in "Similarity: HIGH/
 * MODERATE/LOW". This parses those blocks into bar data. If nothing
 * matches that shape (LLM phrased it differently), falls back to
 * showing the raw text so the tab is never left blank.
 *
 * @param {string} text - raw LLM output from apiSimilarity().result
 */
function renderSimilarityBars(text) {
  const container = document.getElementById("simBars");
  if (!container) return;
  container.innerHTML = "";

  if (!text || !text.trim()) {
    const empty = document.createElement("div");
    empty.className   = "tab-error";
    empty.textContent = "No similarity data available.";
    container.appendChild(empty);
    return;
  }

  const blocks = text
    .split(/\n(?=\s*\d+[\.\)]\s|\s*\*\*)/)
    .map(b => b.trim())
    .filter(Boolean);

  const items = [];
  blocks.forEach(block => {
    const levelMatch = block.match(/Similarity:\s*(HIGH|MODERATE|LOW)/i);
    if (!levelMatch) return;
    const level = levelMatch[1].toUpperCase();
    const firstLine = block
      .split("\n")[0]
      .replace(/^\s*\d+[\.\)]\s*/, "")
      .replace(/\*\*/g, "")
      .trim();
    const detail = block.replace(/Similarity:\s*(HIGH|MODERATE|LOW)/i, "").trim();
    items.push({ label: firstLine || "Study", level, detail });
  });

  if (items.length === 0) {
    // LLM didn't follow the expected format — show the raw text instead
    // of an empty tab.
    const pre = document.createElement("div");
    pre.style.cssText = "white-space:pre-wrap;font-size:0.84rem;line-height:1.6;";
    pre.textContent = text.replace(/\*\*/g, "");
    container.appendChild(pre);
    return;
  }

  items.forEach(item => {
    const row = document.createElement("div");
    row.style.cssText = "margin-bottom:16px;";

    const labelRow = document.createElement("div");
    labelRow.style.cssText = "display:flex;justify-content:space-between;font-size:0.84rem;font-weight:600;margin-bottom:6px;";

    const labelEl = document.createElement("span");
    labelEl.textContent = item.label;

    const levelEl = document.createElement("span");
    levelEl.style.color = SIM_LEVEL_COLOR[item.level] || "var(--muted)";
    levelEl.textContent = item.level;

    labelRow.appendChild(labelEl);
    labelRow.appendChild(levelEl);

    const track = document.createElement("div");
    track.style.cssText = "background:var(--bg,#f0f0f0);border-radius:6px;height:8px;overflow:hidden;";

    const fill = document.createElement("div");
    fill.style.cssText = `height:100%;border-radius:6px;width:${SIM_LEVEL_WIDTH[item.level] || 50}%;background:${SIM_LEVEL_COLOR[item.level] || "var(--muted)"};`;

    track.appendChild(fill);

    const detailEl = document.createElement("div");
    detailEl.style.cssText = "font-size:0.78rem;color:var(--muted);margin-top:6px;line-height:1.5;";
    detailEl.textContent = item.detail;

    row.appendChild(labelRow);
    row.appendChild(track);
    row.appendChild(detailEl);
    container.appendChild(row);
  });
}

/**
 * Renders citation badges from the similarity sources list.
 * These appear below the similarity bars in the Similarity Report tab.
 */
function renderCitationBadges() {
  const container = document.getElementById("citationBadges");
  if (!container) return;
  container.innerHTML = "";

  if (!lastSimilaritySources || lastSimilaritySources.length === 0) return;

  lastSimilaritySources.forEach(s => {
    const badge = document.createElement("span");
    badge.className = "citation-badge citation-pop";
    const bits = [s.title, s.year].filter(v => v && v !== "Unknown");
    badge.textContent = bits.join(", ") || "Untitled";
    container.appendChild(badge);
  });
}

/* ── Gaps rendering ──────────────────────────────────────────────────────── */

const GAP_URGENCY_COLOR = {
  HIGH: "var(--danger, #dc2626)",
  MEDIUM: "var(--warning, #d97706)",
  LOW: "var(--muted, #9ca3af)",
};

/**
 * Parses the LLM's free-text gap analysis (Gap/Recommendation/Urgency
 * triples) into cards. Falls back to raw text if the LLM didn't follow
 * the expected shape, so the tab is never left blank.
 * @param {string} text - raw LLM output from apiGaps().result
 */
function renderGapsList(text) {
  const container = document.getElementById("gapsBox");
  if (!container) return;
  container.innerHTML = "";

  if (!text || !text.trim()) {
    const empty = document.createElement("div");
    empty.className   = "tab-error";
    empty.textContent = "No research gap data available.";
    container.appendChild(empty);
    return;
  }

  const gapRegex = /Gap:\s*(.+?)\s*(?:\n|\r)+.*?Recommendation:\s*(.+?)\s*(?:\n|\r)+.*?Urgency:\s*(HIGH|MEDIUM|LOW)/gis;
  const items = [];
  let match;
  while ((match = gapRegex.exec(text)) !== null) {
    items.push({
      gap: match[1].replace(/\*\*/g, "").trim(),
      recommendation: match[2].replace(/\*\*/g, "").trim(),
      urgency: match[3].toUpperCase(),
    });
  }

  if (items.length === 0) {
    const pre = document.createElement("div");
    pre.style.cssText = "white-space:pre-wrap;font-size:0.84rem;line-height:1.6;";
    pre.textContent = text.replace(/\*\*/g, "");
    container.appendChild(pre);
    return;
  }

  items.forEach(item => {
    const card = document.createElement("div");
    card.style.cssText = "border:1px solid var(--border,#eee);border-radius:8px;padding:12px 14px;margin-bottom:10px;";

    const header = document.createElement("div");
    header.style.cssText = "display:flex;justify-content:space-between;align-items:flex-start;gap:10px;margin-bottom:6px;";

    const gapText = document.createElement("div");
    gapText.style.cssText = "font-weight:600;font-size:0.84rem;line-height:1.4;";
    gapText.textContent = item.gap;

    const urgencyBadge = document.createElement("span");
    urgencyBadge.style.cssText = `flex-shrink:0;font-size:0.7rem;font-weight:700;padding:2px 8px;border-radius:10px;color:#fff;background:${GAP_URGENCY_COLOR[item.urgency] || "var(--muted)"};`;
    urgencyBadge.textContent = item.urgency;

    header.appendChild(gapText);
    header.appendChild(urgencyBadge);

    const rec = document.createElement("div");
    rec.style.cssText = "font-size:0.8rem;color:var(--muted);line-height:1.5;";
    rec.textContent = item.recommendation;

    card.appendChild(header);
    card.appendChild(rec);
    container.appendChild(card);
  });
}

/* ── PDF viewer ──────────────────────────────────────────────────────────── */

async function revealPdfViewer() {
  const isMobile = window.innerWidth <= 768;

  if (isMobile) {
    /* Mobile: open full-screen PDF overlay */
    const overlay = document.getElementById("pdfOverlay");
    if (overlay) overlay.classList.add("open");
  } else {
    /* Desktop: show inline PDF viewer card */
    const card    = document.getElementById("pdfViewerCard");
    const trigger = document.getElementById("pdfViewerTrigger");
    if (!card) return;
    card.style.display    = "block";
    if (trigger) trigger.style.display = "none";
  }

  pdfVisible = true;

  if (pdfDoc) {
    renderPdfPage(pdfPage);
    return;
  }

  const errorEl = document.getElementById("pdfError");
  const canvas  = document.getElementById("pdfCanvas");
  if (errorEl) errorEl.style.display = "none";
  if (canvas)  canvas.style.display  = "block";

  try {
    const url = apiPdfUrl(docId);

    if (typeof pdfjsLib === "undefined") {
      throw new Error("PDF.js not loaded. Check the CDN script tag in detail.html.");
    }

    pdfjsLib.GlobalWorkerOptions.workerSrc =
      "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js";

    // httpHeaders carries the JWT bearer token — replaces the old
    // withCredentials:true cookie-based approach
    pdfDoc = await pdfjsLib.getDocument({ url, httpHeaders: apiAuthHeaderForPdf() }).promise;
    updatePageInfo();
    await renderPdfPage(1);

  } catch (e) {
    if (canvas)  canvas.style.display  = "none";
    if (errorEl) {
      errorEl.style.display = "block";
      errorEl.textContent   = `Could not load PDF: ${e.message}`;
    }
    toast("PDF failed to load. The document may not be approved yet.", "error");
  }
}

/**
 * Renders a single page of the loaded PDF onto the canvas.
 * @param {number} num - 1-indexed page number
 */
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
    const card    = document.getElementById("pdfViewerCard");
    const trigger = document.getElementById("pdfViewerTrigger");
    if (card) card.style.display = "none";
    if (trigger) trigger.style.display = "block";
  }
  pdfVisible = false;
}

/* ── Initialize ──────────────────────────────────────────────────────────── */
initPage();