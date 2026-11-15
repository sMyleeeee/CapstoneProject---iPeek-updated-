/**
 * upload.js
 * ---------
 * Submit Research — single-step file upload with AI-extraction confirm modal.
 *
 * uploadInProgress guards against silent data loss: navigating away
 * mid-request doesn't cleanly cancel anything server-side (the backend
 * keeps running via asyncio.to_thread), it just kills the browser's
 * ability to see the result — so we warn before letting that happen.
 *
 * ADDED: viewUploadedPdf() — opens the staged (not-yet-confirmed) PDF
 * in a new tab so the uploader can visually verify it's the correct
 * file, not just check the AI-extracted metadata text. Uses the new
 * GET /repository/upload/preview/{preview_id}/pdf backend route.
 */

let selectedFile = null;
let currentPreviewId = null;
let uploadInProgress = false;
let selectedFileBlobUrl = null;


/* ── Navigation guard ───────────────────────────────────────────────── */

window.addEventListener("beforeunload", (e) => {
  if (uploadInProgress) {
    e.preventDefault();
    e.returnValue = ""; // required for the browser's native confirm dialog
  }
});


/* ── Page guard ─────────────────────────────────────────────────────── */

async function initPage() {
  let me;

  try {
    me = await apiMe();
  } catch {
    window.location.replace("index.html");
    return;
  }

  try {
    applyNav(me.role);
  } catch (e) {
    console.error("applyNav failed:", e);
  }

  const name     = me.full_name || me.username || "User";
  const avatarEl = document.getElementById("avatarEl");
  const rolePill = document.getElementById("rolePill");
  if (avatarEl) avatarEl.textContent = name.substring(0, 2).toUpperCase();
  if (rolePill) rolePill.textContent = me.role.charAt(0).toUpperCase() + me.role.slice(1);

  document.body.style.visibility = "visible";
}

initPage();


/* ── File upload ────────────────────────────────────────────────────── */

function dragOver(e) {
  e.preventDefault();

  document
    .getElementById("dropZone")
    .classList.add("over");
}


function dragLeave() {
  document
    .getElementById("dropZone")
    .classList.remove("over");
}


function dropped(e) {
  e.preventDefault();

  dragLeave();

  const file = e.dataTransfer.files[0];

  if (file) {
    setFile(file);
  }
}


function fileChosen(e) {
  const file = e.target.files[0];

  if (file) {
    setFile(file);
  }
}


function fileFeedback(message, error = true) {
  const status = document.getElementById("fileStatus");
  status.textContent = message;
  status.classList.toggle("is-error", error);
  document.getElementById("dropZone").setAttribute("aria-invalid", String(error && !!message));
}

function setFile(file) {
  if (!file.name.toLowerCase().endsWith(".pdf")) {
    fileFeedback("Only PDF files are accepted.");
    return;
  }

  if (file.size > 25 * 1024 * 1024) {
    fileFeedback("File exceeds the 25MB limit.");
    return;
  }

  fileFeedback("", false);
  selectedFile = file;

  if (selectedFileBlobUrl) URL.revokeObjectURL(selectedFileBlobUrl);
  selectedFileBlobUrl = URL.createObjectURL(file);

  document.getElementById("fileName").textContent = file.name;

  document.getElementById("fileSize").textContent =
    (file.size / 1024 / 1024).toFixed(2) + " MB";

  document.getElementById("fileInfo").style.display = "flex";

  document.getElementById("dropZone").style.display = "none";
  document.querySelector('#fileInfo button').focus();
}

/** Opens the locally-selected file (before any upload) so the user can
 * check it's the right PDF prior to submitting. No network call needed
 * — it's just the browser's own copy of the file. */
function viewSelectedFile() {
  if (!selectedFileBlobUrl) {
    fileFeedback("No file selected.");
    return;
  }
  window.open(selectedFileBlobUrl, "_blank");
}

function clearFile() {
  fileFeedback("", false);
  selectedFile = null;

  if (selectedFileBlobUrl) {
    URL.revokeObjectURL(selectedFileBlobUrl);
    selectedFileBlobUrl = null;
  }

  document.getElementById("fileInfo").style.display = "none";

  document.getElementById("dropZone").style.display = "block";
  document.getElementById("dropZone").focus();

  document.getElementById("fileInput").value = "";

  setStep(1);
}


/* ── Step tracker helpers ──────────────────────────────────────────────── */

function setStep(num) {
  document.querySelectorAll(".steps .step").forEach((el, i) => {
    el.classList.remove("active", "done");
    if (i + 1 < num) el.classList.add("done");
    if (i + 1 === num) el.classList.add("active");
  });
}

/* ── Step 1: upload + AI extraction ─────────────────────────────────── */

async function submitPaper() {
  if (!selectedFile) {
    fileFeedback("Please select a PDF first.");
    return;
  }

  const btn = document.getElementById("submitBtn");
  const status = document.getElementById("submitStatus");

  btn.disabled = true;
  btn.textContent = "Reading document...";

  status.style.display = "block";

  status.style.cssText =
    "display:block;padding:12px 14px;border-radius:var(--radius-sm);" +
    "font-size:0.84rem;color:var(--muted);" +
    "background:var(--bg);box-shadow:var(--shadow-inset-sm);";

  status.textContent =
    "Extracting metadata from your PDF... please don't close or navigate away.";

  uploadInProgress = true;
  setStep(2);

  const emptyFormData = {
    title: "",
    department: "",
    year: "",
    members: "",
    abstract: ""
  };

  try {
    const preview = await apiIngestPreview(
      selectedFile,
      emptyFormData
    );

    currentPreviewId = preview.preview_id;

    status.style.display = "none";

    btn.style.display = "none";

    showStep3Review(preview.ai_metadata);
    setStep(3);

  } catch (e) {
    status.style.cssText =
      "display:block;padding:12px 14px;border-radius:var(--radius-sm);" +
      "font-size:0.84rem;color:var(--danger);" +
      "background:#fef2f2;box-shadow:var(--shadow-inset-sm);";

    status.textContent = e.message;

    btn.disabled = false;

    btn.replaceChildren("Submit Research ", createUiIcon("arrow-right"));

  } finally {
    uploadInProgress = false;
  }
}


/* ── Step 3: in-flow review + public-PDF consent ─────────────────────── */

function showStep3Review(meta) {
  document.getElementById("metaTitle").value =
    meta.title || "—";

  document.getElementById("metaAuthors").value =
    meta.authors || "—";

  document.getElementById("metaYear").value =
    meta.year_normalized || (meta.year === "Unknown" ? "" : meta.year) || "";

  document.getElementById("metaCollege").value =
    meta.college || "—";

  document.getElementById("metaAbstract").value =
    meta.abstract || "—";

  document.getElementById("metaKeywords").textContent =
    meta.keywords || "—";

  /* Consent statement comes from the single swappable constant in
     consent-text.js — never hardcoded here. */
  const consentEl = document.getElementById("consentText");
  if (consentEl) {
    consentEl.textContent =
      typeof PUBLIC_PDF_CONSENT_STATEMENT === "string"
        ? PUBLIC_PDF_CONSENT_STATEMENT
        : "Consent statement unavailable.";
  }

  /* No implicit default: clear any prior choice and keep Confirm
     disabled until the user actively selects one option. */
  document.querySelectorAll('input[name="publicPdfConsent"]').forEach(r => {
    r.checked = false;
  });
  document.getElementById("confirmMetaBtn").disabled = true;
  const hint = document.getElementById("consentHint");
  if (hint) {
    hint.textContent = "Select one option to continue.";
    hint.classList.remove("selected");
  }

  const s3status = document.getElementById("step3Status");
  if (s3status) s3status.style.display = "none";

  document.getElementById("step1Pane").style.display = "none";
  document.getElementById("step3Review").style.display = "block";
  document.getElementById("metaTitle").focus();
}

/** Enables Confirm & Submit only after an explicit consent choice. */
function onConsentChange(el) {
  const btn = document.getElementById("confirmMetaBtn");
  if (btn) btn.disabled = !el.checked;

  const hint = document.getElementById("consentHint");
  if (hint && el.checked) {
    hint.textContent = el.value === "true"
      ? "After approval, other students may read your paper in the watermarked reader."
      : "Other students cannot open the full paper. You and librarians retain reader access.";
    hint.classList.add("selected");
  }
}

/**
 * Opens the file the student just uploaded (still staged, not yet
 * confirmed) so they can visually verify it's the correct PDF before
 * submitting - separate from checking the AI-extracted metadata text.
 * Wired to the "View Uploaded PDF" button inside #step3Review.
 */
function viewUploadedPdf() {
  if (!currentPreviewId) {
    toast("No file to preview.", "error");
    return;
  }
  viewPdfInNewTab(apiPreviewPdfUrl(currentPreviewId));
}


function cancelMetaConfirm() {
  document.getElementById("step3Review").style.display = "none";
  document.getElementById("step1Pane").style.display = "block";

  /* Restore the step-1 submit button (it was hidden when extraction
     succeeded), so Cancel always returns to a usable step 1. */
  const submitBtn = document.getElementById("submitBtn");
  submitBtn.style.display = "";
  submitBtn.disabled = false;
  submitBtn.replaceChildren("Submit Research ", createUiIcon("arrow-right"));

  currentPreviewId = null;
  setStep(1);
}


/* ── Step 3: student confirms → actually save to DB ─────────────────── */

async function confirmMetaAndSubmit() {
  if (!currentPreviewId) {
    toast("Preview expired — please re-upload.", "error");
    return;
  }

  /* Required consent: no selected radio = no submission. The button is
     already disabled, but re-check the UI state here too, so the rule
     can't be bypassed via devtools/Enter key. */
  const consentInput = document.querySelector('input[name="publicPdfConsent"]:checked');
  if (!consentInput) {
    toast("Please choose whether to permit public viewing of your PDF.", "error");
    return;
  }
  const publicPdfConsent = consentInput.value === "true";

  const btn = document.getElementById("confirmMetaBtn");

  btn.disabled = true;

  btn.textContent = "Submitting...";

  const status = document.getElementById("step3Status");

  status.style.display = "block";

  status.style.cssText =
    "display:block;padding:12px 14px;border-radius:var(--radius-sm);" +
    "font-size:0.84rem;color:var(--muted);" +
    "background:var(--bg);box-shadow:var(--shadow-inset-sm);";

  status.textContent =
    "Uploading and indexing document into repository... please don't close or navigate away.";

  uploadInProgress = true;

  try {
    const result = await apiIngestConfirm(
      currentPreviewId,
      Object.fromEntries([["title", "Title"], ["authors", "Authors"], ["department", "College"], ["year", "Year"], ["abstract", "Abstract"]].map(([key, field]) => [key, document.getElementById(`meta${field}`).value.trim()])),
      publicPdfConsent
    );

    document.getElementById("step1Pane").style.display = "none";

    status.style.cssText =
      "display:block;padding:12px 14px;border-radius:var(--radius-sm);" +
      "font-size:0.84rem;color:var(--success);" +
      "background:#f0fdf4;box-shadow:var(--shadow-inset-sm);";

    status.textContent =
      `"${result.metadata?.title || selectedFile.name}" submitted — ` +
      `${result.chunks} chunks indexed. Pending librarian review.`;

    toast(
      "Research submitted successfully!",
      "success"
    );

    setTimeout(() => {
      window.location.href = "my-submissions.html";
    }, 2500);

  } catch (e) {
    status.style.cssText =
      "display:block;padding:12px 14px;border-radius:var(--radius-sm);" +
      "font-size:0.84rem;color:var(--danger);" +
      "background:#fef2f2;box-shadow:var(--shadow-inset-sm);";

    status.textContent =
      `Submission failed: ${e.message}`;

    btn.disabled = false;

    btn.replaceChildren("Confirm & Submit ", createUiIcon("arrow-right"));

  } finally {
    uploadInProgress = false;
  }
}
