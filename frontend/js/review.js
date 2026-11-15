/**
 * review.js
 * ---------
 * Librarian Review Queue page.
 * Approve/return now go through ONE backend call (apiValidateResearch)
 * instead of two — the new backend's /validate route does both at once.
 * Delete now uses numeric research id, not source_stem.
 *
 * ADDED: "View PDF" action in the detail panel — lets the librarian
 * open the actual submitted document to verify it before approving or
 * returning it, using the now-fixed GET /repository/{source_stem}/pdf
 * route (previously approved-only, now also allowed for librarians on
 * pending/returned research).
 */

let currentFilter = "all";
let currentId     = null;
let submissions   = [];
let currentUserRole = null;
let selectedIds   = new Set(); // now stores numeric research ids

async function initPage() {
  try {
    const me = await apiMe();
    if (me.role !== "librarian") {
      window.location.replace(landingPage(me)); return;
    }
    currentUserRole = me.role;
    applyNav(me.role);
    document.body.style.visibility = "visible";
    const avatarEl = document.getElementById("avatarEl");
    const rolePill = document.getElementById("rolePill");
    if (avatarEl) avatarEl.textContent = (me.full_name || me.username).substring(0,2).toUpperCase();
    if (rolePill) rolePill.textContent = me.role.charAt(0).toUpperCase() + me.role.slice(1);
  } catch {
    window.location.replace("index.html"); return;
  }
  await loadSubmissions();
  const params = new URLSearchParams(location.search);
  const status = params.get("status");
  if (["all", "pending", "returned"].includes(status)) {
    const button = document.querySelector(`.filter-tab[data-status="${status}"]`);
    if (button) filterList(status, button);
  }
  const requestedId = Number(params.get("id"));
  if (requestedId && submissions.some(paper => paper.id === requestedId)) selectSubmission(requestedId);
}

initPage();

async function loadSubmissions() {
  try {
    const data  = await apiSubmissions();
    submissions = data.submissions || [];
    renderList();
  } catch (e) {
    toast("Failed to load submissions: " + e.message, "error");
  }
}

function renderList() {
  const q    = document.getElementById("searchInput").value.toLowerCase();
  const list = document.getElementById("subList");

  const filtered = submissions.filter(s => {
    if (currentFilter !== "all" && s.status !== currentFilter) return false;
    if (q && !s.title.toLowerCase().includes(q) &&
             !(s.lead_researcher || "").toLowerCase().includes(q)) return false;
    return true;
  });

  document.getElementById("cntAll").textContent      = submissions.length;
  document.getElementById("cntPending").textContent  = submissions.filter(s => s.status === "pending").length;
  document.getElementById("cntReturned").textContent = submissions.filter(s => s.status === "returned").length;

  list.innerHTML = "";

  if (filtered.length === 0) {
    const msg = document.createElement("div");
    msg.style.cssText    = "text-align:center;padding:40px 0;color:var(--muted);font-size:0.84rem;";
    msg.textContent      = "No submissions found.";
    list.appendChild(msg);
    return;
  }

  filtered.forEach(s => {
    const item = document.createElement("div");
    item.className   = `sub-item${currentId === s.id ? " active" : ""}`;
    item.style.cssText = "display:flex;align-items:flex-start;gap:8px;";

    if (currentUserRole === "librarian") {
      const cb = document.createElement("input");
      cb.type    = "checkbox";
      cb.className = "sub-item-checkbox";
      cb.checked = selectedIds.has(s.id);
      cb.addEventListener("click", (e) => e.stopPropagation());
      cb.addEventListener("change", () => toggleSelect(s.id, cb.checked));
      item.appendChild(cb);
    }

    const content = document.createElement("button");
    content.type = "button";
    content.className = "sub-item-open";
    content.addEventListener("click", () => selectSubmission(s.id));

    const title = document.createElement("div");
    title.className   = "sub-item-title";
    title.textContent = s.title;

    const meta = document.createElement("div");
    meta.className   = "sub-item-meta";
    meta.textContent = `${(s.lead_researcher || "Unknown").split("·")[0].trim()} · ${s.department || ""}`;

    const footer = document.createElement("div");
    footer.className = "sub-item-footer";

    const time = document.createElement("span");
    time.className   = "sub-item-time";
    time.textContent = s.submitted_at?.slice(0, 10) || "";

    const badge = document.createElement("span");
    badge.className   = `badge badge-${s.status}`;
    badge.textContent = s.status;

    footer.appendChild(time);
    footer.appendChild(badge);

    content.appendChild(title);
    content.appendChild(meta);
    content.appendChild(footer);

    item.appendChild(content);
    list.appendChild(item);
  });

  staggerIn(list); // subtle staggered fade-in, from motion.js
}

function toggleSelect(id, checked) {
  if (checked) selectedIds.add(id);
  else selectedIds.delete(id);
  updateBulkBar();
}

function updateBulkBar() {
  const bar   = document.getElementById("bulkBar");
  const count = document.getElementById("bulkCount");
  if (!bar || !count) return;
  if (selectedIds.size === 0) {
    bar.style.display = "none";
  } else {
    bar.style.display = "flex";
    count.textContent = `${selectedIds.size} selected`;
  }
}

function clearSelection() {
  selectedIds.clear();
  updateBulkBar();
  renderList();
}

let bulkDeleteBusy = false;
async function confirmBulkDelete() {
  if (bulkDeleteBusy) return;
  const ids = Array.from(selectedIds);
  if (ids.length === 0) return;

  const ok = confirm(
    `Permanently delete ${ids.length} paper${ids.length !== 1 ? "s" : ""}?\n\n` +
    `This removes them from the repository, the search index, and disk. ` +
    `This cannot be undone.`
  );
  if (!ok) return;

  bulkDeleteBusy = true;
  const buttons = document.querySelectorAll('[onclick="confirmBulkDelete()"]');
  buttons.forEach(button => button.disabled = true);
  let result;
  try { result = await apiBulkDeletePapers(ids); }
  catch (error) { toast(error.message, "error"); await loadSubmissions(); return; }
  finally { bulkDeleteBusy = false; buttons.forEach(button => button.disabled = false); }
  if (result.deleted.length) toast(`${result.deleted.length} papers deleted.`, "success");
  if (result.failed.length) toast(`${result.failed.length} papers could not be deleted.`, "error");

  selectedIds.clear();
  result.failed.forEach(id => selectedIds.add(id));
  currentId = null;
  document.getElementById("detailPanel").innerHTML =
    `<div class='empty-detail'><div class='icon'>${uiIconMarkup("my-submissions")}</div><p class='empty-detail-title'>No submission selected</p><p class='empty-detail-sub'>Click a submission on the left to review it.</p></div>`;

  await loadSubmissions();
  updateBulkBar();
}

function selectSubmission(id) {
  currentId = id;
  const s   = submissions.find(s => s.id === id);
  if (!s) return;
  renderList();

  const panel = document.getElementById("detailPanel");
  document.querySelector(".sub-item.active .sub-item-open")?.focus({preventScroll: true});
  const sheetContent = document.getElementById("reviewSheetContent");
  const isMobile = window.innerWidth <= 768;

  const target = isMobile ? sheetContent : panel;
  panel.replaceChildren();
  sheetContent.replaceChildren();
  document.getElementById("reviewDetailSheet").scrollTop = 0;

  const titleEl = document.createElement("div");
  titleEl.className   = "detail-title";
  titleEl.textContent = s.title;

  const authorEl = document.createElement("div");
  authorEl.className   = "detail-author";
  authorEl.style.cssText = "font-size:0.84rem;color:var(--muted);margin-bottom:8px;";
  authorEl.textContent = s.lead_researcher || "Unknown";

  const chips = document.createElement("div");
  chips.className = "detail-chips";
  [s.department, s.school_year, s.submitted_at?.slice(0, 10)].forEach(val => {
    if (!val) return;
    const chip = document.createElement("span");
    chip.className   = "detail-chip";
    chip.textContent = val;
    chips.appendChild(chip);
  });
  const statusBadge = document.createElement("span");
  statusBadge.className   = `badge badge-${s.status}`;
  statusBadge.textContent = s.status;
  chips.appendChild(statusBadge);

  const absCard = document.createElement("div");
  absCard.className = "card";
  absCard.style.marginBottom = "14px";

  const absLabel = document.createElement("div");
  absLabel.className   = "section-label";
  absLabel.textContent = "Abstract";

  const absText = document.createElement("p");
  absText.className   = "abstract-text";
  absText.textContent = s.abstract || "No abstract provided.";

  absCard.appendChild(absLabel);
  absCard.appendChild(absText);

  const aiNotice = document.createElement("div");
  aiNotice.className   = "ai-notice";
  aiNotice.textContent = "AI Similarity analysis will run after approval.";

  const fbCard = document.createElement("div");
  fbCard.className = "card";
  fbCard.style.marginBottom = "14px";

  const fbLabel = document.createElement("label");
  fbLabel.className   = "form-label";
  fbLabel.htmlFor = "feedbackInput";
  fbLabel.textContent = "Librarian Feedback";

  const fbTextarea = document.createElement("textarea");
  fbTextarea.className   = "form-input";
  fbTextarea.id          = "feedbackInput";
  fbTextarea.rows        = 3;
  fbTextarea.placeholder = "Enter feedback or reason for returning...";
  fbTextarea.textContent = s.feedback || "";
  fbTextarea.setAttribute("aria-describedby", "feedbackHelp reviewStatus");

  const fbHelp = document.createElement("p");
  fbHelp.id = "feedbackHelp";
  fbHelp.className = "form-help";
  fbHelp.textContent = "Feedback is required when returning a paper.";
  const status = document.createElement("p");
  status.id = "reviewStatus";
  status.className = "form-status";
  status.setAttribute("role", "status");

  fbCard.appendChild(fbLabel);
  fbCard.appendChild(fbTextarea);
  fbCard.appendChild(fbHelp);
  fbCard.appendChild(status);

  const actionRow = document.createElement("div");
  actionRow.className = "action-row";

  const viewPdfBtn = document.createElement("button");
  viewPdfBtn.className   = "btn btn-ghost";
  viewPdfBtn.textContent = "View PDF";
  viewPdfBtn.addEventListener("click", () => {
    if (!s.source_stem) {
      toast("No document available for this submission.", "error");
      return;
    }
    viewPdfInNewTab(apiPdfUrl(s.source_stem));
  });
  actionRow.appendChild(viewPdfBtn);

  if (currentUserRole === "librarian") {
    const deleteBtn = createActionIconButton("trash", "Delete", `Delete ${s.title}`);
    deleteBtn.addEventListener("click", () => deletePaperFromDetail(s.id, s.title));
    actionRow.appendChild(deleteBtn);
  }

  const returnBtn = document.createElement("button");
  returnBtn.className   = "btn btn-ghost";
  returnBtn.id = "returnReviewBtn";
  returnBtn.textContent = "Return";
  returnBtn.addEventListener("click", () => returnSubmission(s.id));

  const validateBtn = document.createElement("button");
  validateBtn.className   = "btn btn-primary";
  validateBtn.id = "approveReviewBtn";
  validateBtn.textContent = "Approve";
  validateBtn.addEventListener("click", () => validateSubmission(s.id));

  actionRow.appendChild(returnBtn);
  actionRow.appendChild(validateBtn);

  target.appendChild(titleEl);
  target.appendChild(authorEl);
  target.appendChild(chips);
  target.appendChild(absCard);
  target.appendChild(aiNotice);
  target.appendChild(fbCard);
  target.appendChild(actionRow);

  /* On mobile, open the glass detail sheet */
  if (isMobile && typeof openReviewSheet === "function") {
    openReviewSheet();
  }
}

/* ── Actions — now ONE call each, via apiValidateResearch ────────────── */

async function validateSubmission(id) {
  const validateBtn = document.getElementById("approveReviewBtn");
  const returnBtn = document.getElementById("returnReviewBtn");
  reviewFeedback("Approving submission...");
  if (validateBtn) validateBtn.disabled = true;
  if (returnBtn)    returnBtn.disabled  = true;

  try {
    const feedback = document.getElementById("feedbackInput")?.value.trim() || "";
    await apiValidateResearch(id, "validated", feedback);

    toast("Submission validated and approved.", "success");
    closeReviewSheet();
    currentId = null;
    await loadSubmissions();
    document.getElementById("detailPanel").innerHTML =
      `<div class='empty-detail'><div class='icon'>${uiIconMarkup("review")}</div><p class='empty-detail-title'>Submission validated.</p></div>`;
  } catch (e) {
    reviewFeedback(`Approval failed: ${e.message}`, true);
    if (validateBtn) validateBtn.disabled = false;
    if (returnBtn)    returnBtn.disabled  = false;
  }
}

async function returnSubmission(id) {
  const feedback = document.getElementById("feedbackInput")?.value.trim();
  if (!feedback) {
    reviewFeedback("Enter feedback before returning this paper.", true);
    document.getElementById("feedbackInput").setAttribute("aria-invalid", "true");
    document.getElementById("feedbackInput").focus();
    return;
  }

  const validateBtn = document.getElementById("approveReviewBtn");
  const returnBtn = document.getElementById("returnReviewBtn");
  document.getElementById("feedbackInput").removeAttribute("aria-invalid");
  reviewFeedback("Returning submission...");
  if (validateBtn) validateBtn.disabled = true;
  if (returnBtn)    returnBtn.disabled  = true;

  try {
    await apiValidateResearch(id, "returned", feedback);
    toast("Submission returned with feedback.", "warning");
    closeReviewSheet();
    currentId = null;
    await loadSubmissions();
    document.getElementById("detailPanel").innerHTML =
      `<div class='empty-detail'><div class='icon'>${uiIconMarkup("return")}</div><p class='empty-detail-title'>Submission returned to student.</p></div>`;
  } catch (e) {
    reviewFeedback(`Return failed: ${e.message}`, true);
    if (validateBtn) validateBtn.disabled = false;
    if (returnBtn)    returnBtn.disabled  = false;
  }
}

function reviewFeedback(message, error = false) {
  const status = document.getElementById("reviewStatus");
  if (!status) return;
  status.textContent = message;
  status.classList.toggle("is-error", error);
}

async function deletePaperFromDetail(id, title) {
  const ok = confirm(
    `Permanently delete "${title}"?\n\n` +
    `This removes it from the repository, the search index, and disk. ` +
    `This cannot be undone.`
  );
  if (!ok) return;

  try {
    await apiDeletePaper(id);
    toast(`"${title}" deleted.`, "success");
    currentId = null;
    selectedIds.delete(id);
    await loadSubmissions();
    document.getElementById("detailPanel").innerHTML =
      `<div class='empty-detail'><div class='icon'>${uiIconMarkup("trash")}</div><p class='empty-detail-title'>Submission deleted.</p></div>`;
    updateBulkBar();
  } catch (e) {
    toast(`Delete failed: ${e.message}`, "error");
  }
}

function filterList(filter, el) {
  currentFilter = filter;
  document.querySelectorAll(".filter-tab").forEach(b => b.classList.remove("active"));
  el.classList.add("active");
  renderList();
}

let searchTimer;
document.getElementById("searchInput").addEventListener("input", e => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(renderList, 300);
});
