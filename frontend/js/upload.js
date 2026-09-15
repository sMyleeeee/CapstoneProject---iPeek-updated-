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


function setFile(file) {
  if (!file.name.toLowerCase().endsWith(".pdf")) {
    toast("Only PDF files are accepted.", "error");
    return;
  }

  if (file.size > 25 * 1024 * 1024) {
    toast("File exceeds the 25MB limit.", "error");
    return;
  }

  selectedFile = file;

  if (selectedFileBlobUrl) URL.revokeObjectURL(selectedFileBlobUrl);
  selectedFileBlobUrl = URL.createObjectURL(file);

  document.getElementById("fileName").textContent = file.name;

  document.getElementById("fileSize").textContent =
    (file.size / 1024 / 1024).toFixed(2) + " MB";

  document.getElementById("fileInfo").style.display = "flex";

  document.getElementById("dropZone").style.display = "none";
}

/** Opens the locally-selected file (before any upload) so the user can
 * check it's the right PDF prior to submitting. No network call needed
 * — it's just the browser's own copy of the file. */
function viewSelectedFile() {
  if (!selectedFileBlobUrl) {
    toast("No file selected.", "error");
    return;
  }
  window.open(selectedFileBlobUrl, "_blank");
}

function clearFile() {
  selectedFile = null;

  if (selectedFileBlobUrl) {
    URL.revokeObjectURL(selectedFileBlobUrl);
    selectedFileBlobUrl = null;
  }

  document.getElementById("fileInfo").style.display = "none";

  document.getElementById("dropZone").style.display = "block";

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
    toast("Please select a PDF first.", "error");
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
    "background:var(--bg);box-shadow:var(--neo-inset);";

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

    showMetaModal(preview.ai_metadata);
    setStep(3);

  } catch (e) {
    status.style.cssText =
      "display:block;padding:12px 14px;border-radius:var(--radius-sm);" +
      "font-size:0.84rem;color:var(--danger);" +
      "background:#fef2f2;box-shadow:var(--neo-inset);";

    status.textContent = e.message;

    btn.disabled = false;

    btn.textContent = "Submit Research →";

  } finally {
    uploadInProgress = false;
  }
}


/* ── Modal: show AI-extracted metadata ──────────────────────────────── */

function showMetaModal(meta) {
  document.getElementById("metaTitle").textContent =
    meta.title || "—";

  document.getElementById("metaAuthors").textContent =
    meta.authors || "—";

  document.getElementById("metaYear").textContent =
    meta.year || "—";

  document.getElementById("metaCollege").textContent =
    meta.college || "—";

  document.getElementById("metaAbstract").textContent =
    meta.abstract || "—";

  document.getElementById("metaKeywords").textContent =
    meta.keywords || "—";

  const modal = document.getElementById("metaModal");
  modal.style.display = "flex";
}

/**
 * Opens the file the student just uploaded (still staged, not yet
 * confirmed) so they can visually verify it's the correct PDF before
 * submitting - separate from checking the AI-extracted metadata text.
 * Wire this to a button inside the #metaModal markup, e.g.:
 *   <button class="btn btn-ghost" onclick="viewUploadedPdf()">📄 View Uploaded PDF</button>
 */
function viewUploadedPdf() {
  if (!currentPreviewId) {
    toast("No file to preview.", "error");
    return;
  }
  viewPdfInNewTab(apiPreviewPdfUrl(currentPreviewId));
}


function cancelMetaConfirm() {
  document.getElementById("metaModal").style.display = "none";

  currentPreviewId = null;
  setStep(1);
}


/* ── Step 2: student confirms → actually save to DB ─────────────────── */

async function confirmMetaAndSubmit() {
  if (!currentPreviewId) {
    toast("Preview expired — please re-upload.", "error");
    return;
  }

  const btn = document.getElementById("confirmMetaBtn");

  btn.disabled = true;

  btn.textContent = "Submitting...";

  const status = document.getElementById("submitStatus");

  status.style.display = "block";

  status.style.cssText =
    "display:block;padding:12px 14px;border-radius:var(--radius-sm);" +
    "font-size:0.84rem;color:var(--muted);" +
    "background:var(--bg);box-shadow:var(--neo-inset);";

  status.textContent =
    "Uploading and indexing document into repository... please don't close or navigate away.";

  uploadInProgress = true;

  try {
    const result = await apiIngestConfirm(
      currentPreviewId,
      {}
    );

    document.getElementById("metaModal").style.display = "none";

    status.style.cssText =
      "display:block;padding:12px 14px;border-radius:var(--radius-sm);" +
      "font-size:0.84rem;color:var(--success);" +
      "background:#f0fdf4;box-shadow:var(--neo-inset);";

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
      "background:#fef2f2;box-shadow:var(--neo-inset);";

    status.textContent =
      `Submission failed: ${e.message}`;

    btn.disabled = false;

    btn.textContent = "Confirm & Submit →";

  } finally {
    uploadInProgress = false;
  }
}