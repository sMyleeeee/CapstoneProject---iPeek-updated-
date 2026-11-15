(() => {
/** Research reader. Existing analysis endpoints only; refinements send no history. */
let canUseAI = false;
let paperData = null;
let researchId = null;
let activePaper = null;
let readerView = "paper";
let readerEventsBound = false;
let pageReader = null;
// Reopening a paper while its analysis is pending shares the existing request.
// Settled results remain the server cache's responsibility.
const pendingAnalyses = new Map();
const TARGET_LABELS = { similarity: "Research Similarity", summary: "Summary", gap: "Research Gaps", imrad: "IMRaD" };
const ANALYSIS = {
  similarity: { box: "similarityBox", load: apiSimilarity, loading: "Finding related studies…" },
  summary: { box: "summaryBox", load: apiSummary, loading: "Preparing the repository summary…" },
  gap: { box: "gapsBox", load: apiGaps, loading: "Looking for research gaps…" },
  imrad: { box: "imradBox", load: apiImrad, loading: "Preparing the reading guide…" },
};
const byId = id => document.getElementById(id);
const currentSource = () => new URLSearchParams(window.location.search).get("id");
const isCurrent = context => context === activePaper && context.source === currentSource();

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}
function readable(value) {
  return value == null || /^(unknown|untitled|\?)$/i.test(String(value).trim()) ? "" : String(value).trim();
}
function resultText(data) { return typeof data?.result === "string" ? data.result.trim() : ""; }

// Preserve page citations and returned prose; never interpret response HTML.
function analysisProse(text) {
  const prose = element("div", "analysis-text");
  let offset = 0;
  for (const match of text.matchAll(/\*\*([^*]+)\*\*/g)) {
    prose.append(document.createTextNode(text.slice(offset, match.index)), element("strong", "", match[1]));
    offset = match.index + match[0].length;
  }
  prose.appendChild(document.createTextNode(text.slice(offset)));
  return prose;
}
function renderText(container, text, empty = "No analysis was returned.") {
  container.replaceChildren(text ? analysisProse(text) : element("p", "insight-empty", empty));
}
function distinctSources(sources) {
  const distinct = new Map();
  for (const source of Array.isArray(sources) ? sources : []) {
    if (!source || typeof source !== "object") continue;
    const key = source.research_id != null ? `id:${source.research_id}` : source.source_stem ? `stem:${source.source_stem}` :
      [source.title, source.authors, source.year, source.college].map(value => readable(value).toLowerCase()).join("|");
    if (key === "|||") continue;
    const previous = distinct.get(key);
    const pages = [...new Set([...(previous?.pages || []), ...(Array.isArray(source.pages) ? source.pages : [])])];
    distinct.set(key, { ...source, pages });
  }
  return [...distinct.values()];
}
function sourceLabel(source) {
  const metadata = [source.title, source.authors, source.year, source.college].map(readable).filter(Boolean);
  if (source.pages?.length) metadata.push(`pp. ${source.pages.join(", ")}`);
  return metadata.join(" · ");
}
function appendSources(container, sources) {
  const unique = distinctSources(sources);
  if (!unique.length) return;
  const details = element("details", "source-references");
  details.appendChild(element("summary", "", "Retrieved source references"));
  const list = element("ul");
  unique.forEach(source => list.appendChild(element("li", "", sourceLabel(source))));
  details.appendChild(list);
  container.appendChild(details);
}

// Render all returned prose, including older structured cache entries. No heading parser.
function renderSimilarity(container, data) {
  renderText(container, resultText(data), 'No matching studies found.');
  appendSources(container, data.sources);
}
function renderRelated(data, refined = false) {
  const box = byId("relatedBox");
  box.replaceChildren();
  box.setAttribute("aria-busy", "false");
  byId("relatedOrigin").textContent = refined
    ? "Sources from the latest similarity refinement, in returned order. Up to five unique papers."
    : "Sources from the opening similarity analysis, in returned order. Up to five unique papers.";
  const sources = distinctSources((Array.isArray(data.sources) ? data.sources : [])
    .filter(source => Number.isInteger(source?.research_id) && source.research_id > 0));
  if (!sources.length) {
    box.appendChild(element("p", "insight-empty", "No related studies returned"));
    return;
  }
  for (const source of sources.slice(0, 5)) {
    const card = element("article", "related-card");
    const heading = element("h3");
    const link = element("a", "", readable(source.title) || "Untitled study");
    link.href = `detail.html?id=${encodeURIComponent(source.research_id)}`;
    heading.appendChild(link);
    card.append(heading, element("p", "insight-note", [readable(source.authors) || "Authors unavailable", readable(source.year) || "Year unavailable"].join(" · ")));
    const abstract = readable(source.abstract);
    if (abstract) {
      card.appendChild(element("h4", "excerpt-label", "Abstract excerpt"));
      card.appendChild(element("p", "analysis-text", abstract.length > 280 ? abstract.slice(0, 280).trimEnd() + "…" : abstract));
    } else card.appendChild(element("p", "insight-empty", "Abstract unavailable"));
    if (source.pages?.length) card.appendChild(element("p", "source-references", `Source pages: ${source.pages.join(", ")}`));
    box.appendChild(card);
  }
}
function relatedFailure(context, message) {
  const box = byId("relatedBox");
  box.setAttribute("aria-busy", "false");
  const retry = element("button", "btn btn-ghost btn-sm", "Retry");
  retry.type = "button";
  retry.setAttribute("aria-label", "Retry Related Research");
  retry.addEventListener("click", () => { if (isCurrent(context)) runSimilarity(); });
  box.replaceChildren(element("p", "analysis-error", `Related research could not be loaded: ${message}`), retry);
}
function setLoading(container, label) {
  const status = element("p", "insight-loading");
  const spinner = element("span", "spinner");
  spinner.setAttribute("aria-hidden", "true");
  status.append(spinner, document.createTextNode(label));
  container.replaceChildren(status);
  container.setAttribute("aria-busy", "true");
}
function updateAnalysisStatus(context) {
  const states = Object.values(context.jobs);
  const running = states.includes("loading");
  const failed = states.filter(state => state === "failed").length;
  const badge = byId("analysisStatus");
  badge.textContent = running ? "Running…" : failed === states.length ? "Unavailable" : failed ? "Partial" : "Complete";
  badge.className = `badge ${running || failed ? "badge-pending" : "badge-validated"}`;
}
async function runAnalysis(target) {
  if (!canUseAI || !activePaper?.id) return false;
  const context = activePaper;
  const relatedRevision = context.relatedRevision;
  if (context.jobs[target] === "loading" || context.jobs[target] === "complete") return false;
  const config = ANALYSIS[target];
  const box = byId(config.box);
  context.jobs[target] = "loading";
  setLoading(box, config.loading);
  if (target === "similarity" && !relatedRevision) setLoading(byId("relatedBox"), "Finding related studies…");
  updateAnalysisStatus(context);
  try {
    const key = `${context.id}:${target}`;
    if (!pendingAnalyses.has(key)) {
      pendingAnalyses.set(key, config.load(context.id).finally(() => pendingAnalyses.delete(key)));
    }
    const data = await pendingAnalyses.get(key);
    if (!isCurrent(context)) return false;
    if (target === "similarity") {
      renderSimilarity(box, data);
      if (!relatedRevision && !context.relatedRevision) renderRelated(data);
    }
    else {
      renderText(box, resultText(data));
      appendSources(box, data.sources);
    }
    context.jobs[target] = "complete";
    return true;
  } catch (error) {
    if (!isCurrent(context)) return false;
    context.jobs[target] = "failed";
    if (target === "similarity" && !relatedRevision && !context.relatedRevision) relatedFailure(context, error.message);
    const failure = element("div", "analysis-error");
    failure.appendChild(element("p", "", `${TARGET_LABELS[target]} could not be loaded: ${error.message}`));
    const retry = element("button", "btn btn-ghost btn-sm", "Retry");
    retry.type = "button";
    retry.setAttribute("aria-label", `Retry ${TARGET_LABELS[target]}`);
    retry.addEventListener("click", () => { if (isCurrent(context)) runAnalysis(target); });
    failure.appendChild(retry);
    box.replaceChildren(failure);
    return false;
  } finally {
    if (isCurrent(context)) {
      box.setAttribute("aria-busy", "false");
      updateAnalysisStatus(context);
    }
  }
}
function runSimilarity() { return runAnalysis("similarity"); }
function runSummary() { return runAnalysis("summary"); }
function runGaps() { return runAnalysis("gap"); }
function runImrad() { return runAnalysis("imrad"); }

function appendMessage(label, text, user = false) {
  const message = element("article", `insight-message${user ? " user-message" : ""}`);
  message.appendChild(element("div", "message-label", label));
  const body = element("div");
  renderText(body, text);
  message.appendChild(body);
  byId("refinementThread").appendChild(message);
  return { message, body };
}
async function refineAnalysis(event) {
  event?.preventDefault();
  if (!canUseAI || !activePaper?.id || activePaper.refining) return false;
  const context = activePaper;
  const input = byId("refineInput");
  const query = input.value.trim();
  if (!query) { input.focus(); return false; }
  // Auto previously resolved to the active tab (similarity by default).
  // All sections are now visible; keep that default explicit in the selector.
  const selected = byId("refineTarget").value;
  const target = selected === "auto" ? "similarity" : selected;
  if (!TARGET_LABELS[target]) return false;
  context.refining = true;
  const button = byId("refineBtn");
  button.disabled = true;
  button.textContent = "Refining…";
  input.readOnly = true;
  byId("refineTarget").disabled = true;
  byId("refineStatus").textContent = "Refining the selected analysis…";
  appendMessage("You", query, true);
  const response = appendMessage(`iPeek · ${TARGET_LABELS[target]}`, "Working on your refinement…");
  response.message.setAttribute("aria-busy", "true");
  byId("insightsScroll").scrollTop = byId("insightsScroll").scrollHeight;
  try {
    const data = await apiRefineAnalysis(context.id, query, target);
    if (!isCurrent(context)) return false;
    if (target === "similarity") {
      renderSimilarity(response.body, data);
      context.relatedRevision++;
      renderRelated(data, true);
    }
    else { renderText(response.body, resultText(data)); appendSources(response.body, data.sources); }
    input.value = "";
    byId("refineStatus").textContent = "Refinement added below the opening analysis.";
    return true;
  } catch (error) {
    if (!isCurrent(context)) return false;
    renderText(response.body, `Refinement failed: ${error.message}`);
    byId("refineStatus").textContent = "Your prompt is preserved. Select Refine to try again.";
    return false;
  } finally {
    if (isCurrent(context)) {
      context.refining = false;
      response.message.setAttribute("aria-busy", "false");
      button.disabled = false;
      button.textContent = "Refine";
      input.readOnly = false;
      byId("refineTarget").disabled = false;
    }
  }
}
function renderPaperInfo(paper) {
  byId("paperTitle").textContent = paper.title || paper.source || "Untitled";
  byId("paperMeta").textContent = readable(paper.authors) || "Authors not provided.";
  byId("metaChips").replaceChildren();
  [paper.college_ref?.name || paper.department, paper.year, paper.status].map(readable).filter(Boolean).forEach(label => byId("metaChips").appendChild(element("span", "meta-chip", label)));
  byId("abstractText").textContent = paper.abstract || "No abstract provided.";
}
function syncReaderViews() {
  const mobile = window.matchMedia("(max-width: 768px)").matches;
  document.querySelector(".reader-tabs").hidden = !mobile;
  for (const view of ["paper", "insights"]) {
    const active = readerView === view;
    const tab = byId(`${view}Tab`);
    const panel = byId(`${view}Panel`);
    tab.setAttribute("aria-selected", String(active));
    tab.tabIndex = active ? 0 : -1;
    panel.hidden = mobile && !active;
    if (mobile) { panel.setAttribute("role", "tabpanel"); panel.setAttribute("aria-labelledby", tab.id); panel.tabIndex = 0; }
    else { panel.removeAttribute("role"); panel.removeAttribute("tabindex"); panel.setAttribute("aria-labelledby", view === "paper" ? "paperTitle" : "insightsHeading"); }
  }
}
function bindReaderEvents() {
  if (readerEventsBound) return;
  readerEventsBound = true;
  const views = ["paper", "insights"];
  for (const view of views) {
    byId(`${view}Tab`).addEventListener("click", () => { readerView = view; syncReaderViews(); });
    byId(`${view}Tab`).addEventListener("keydown", event => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      readerView = event.key === "Home" ? views[0] : event.key === "End" ? views.at(-1) : views[(views.indexOf(view) + (event.key === "ArrowRight" ? 1 : views.length - 1)) % views.length];
      syncReaderViews(); byId(`${readerView}Tab`).focus();
    });
  }
  window.matchMedia("(max-width: 768px)").addEventListener("change", syncReaderViews);
  byId("refineForm").addEventListener("submit", refineAnalysis);

}
async function initPage() {
  bindReaderEvents();
  pageReader?.destroy();
  pageReader = null;
  const context = { source: currentSource(), id: null, jobs: {}, refining: false, relatedRevision: 0 };
  activePaper = context;
  canUseAI = false; paperData = null; researchId = null;
  readerView = "paper";
  syncReaderViews();
  byId("refinementThread").replaceChildren();
  byId("refineForm").reset();
  byId("refineForm").hidden = true;
  byId("refineInput").readOnly = false;
  byId("refineTarget").disabled = false;
  byId("refineBtn").disabled = false;
  byId("refineBtn").textContent = "Refine";
  byId("refineStatus").textContent = "";
  for (const id of ["watermarkedReader", "imradViewerCard", "consentNotice", "aiAccessNotice"]) byId(id).hidden = true;
  byId("insightsScroll").hidden = false;
  byId("analysisStatus").textContent = "Loading…";
  byId("relatedOrigin").textContent = "Sources from the opening similarity analysis, in returned order. Up to five unique papers.";
  setLoading(byId("relatedBox"), "Finding related studies…");
  renderPaperInfo({ title: "Loading research…", abstract: "Loading…" });
  for (const config of Object.values(ANALYSIS)) setLoading(byId(config.box), config.loading);
  const auth = apiMe().catch(() => null);
  const paper = /^[1-9]\d*$/.test(context.source || "") ? apiDocumentById(context.source) : Promise.reject(new Error("No document ID specified."));
  const [identity, documentResult] = await Promise.allSettled([auth, paper]);
  if (!isCurrent(context)) return;
  const me = identity.value;
  if (!me) { window.location.replace("index.html"); return; }
  canUseAI = !!me && ["student", "librarian"].includes(me.role);
  if (!document.getElementById("grid") && !document.getElementById("askMain")) applyNav(me?.role || "guest");
  if (me) {
    const avatar = byId("avatarEl");
    const role = byId("rolePill");
    if (avatar) avatar.textContent = (me.full_name || me.username || "User").substring(0, 2).toUpperCase();
    if (role) role.textContent = me.role.charAt(0).toUpperCase() + me.role.slice(1);
  }
  document.body.style.visibility = "visible";
  if (documentResult.status === "rejected") {
    renderPaperInfo({ title: "Unable to load research", abstract: documentResult.reason.message });
    byId("insightsScroll").hidden = true;
    byId("aiAccessNotice").hidden = false;
    byId("aiAccessNotice").textContent = "Research Insights is unavailable because the paper could not be loaded.";
    byId("analysisStatus").textContent = "Unavailable";
    renderText(byId("relatedBox"), "Related Research is unavailable because the paper could not be loaded.");
    byId("relatedBox").setAttribute("aria-busy", "false");
    const retry = element("button", "btn btn-ghost btn-sm", "Retry research details");
    retry.type = "button";
    retry.addEventListener("click", initPage);
    byId("abstractText").appendChild(retry);
    return;
  }
  paperData = documentResult.value;
  context.id = researchId = paperData.id;
  renderPaperInfo(paperData);
  const allowed = paperData.can_view_pdf === true;
  byId("watermarkedReader").hidden = !allowed;
  byId("consentNotice").hidden = allowed;
  if (!allowed) {
    const messages = {
      not_approved: "This paper is not approved for public viewing.",
      not_consented: "The author has not allowed public viewing of this paper.",
    };
    byId("consentNotice").textContent = messages[paperData.pdf_block_reason] || "Reader permission is unavailable. Please reopen this paper to try again.";
  } else pageReader = new WatermarkedReader(byId("readerPages"), context.id);
  if (!canUseAI) {
    byId("insightsScroll").hidden = true;
    byId("aiAccessNotice").hidden = false;
    byId("analysisStatus").textContent = "Reading only";
    return;
  }
  byId("refineForm").hidden = false;
  byId("imradViewerCard").hidden = allowed;
  await Promise.all([runSummary(), runSimilarity(), runGaps(), ...(!allowed ? [runImrad()] : [])]);
}

window.addEventListener("pagehide", () => pageReader?.destroy());
// A browser back/forward-cache restore needs fresh Blob URLs after pagehide cleanup.
window.addEventListener("pageshow", event => {
  if (event.persisted && activePaper) initPage();
});

window.iPeekDetail = {
  open: initPage,
  close() { activePaper = null; canUseAI = false; pageReader?.destroy(); pageReader = null; },
};
})();
