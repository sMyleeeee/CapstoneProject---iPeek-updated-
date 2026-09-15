/**
 * my-submissions.js
 * ------------------
 * Student "My Submissions" page — accessible by any logged-in user.
 * Shows the current status of every paper the logged-in user has
 * submitted, plus librarian feedback when a paper has been returned.
 *
 * All dynamic content uses textContent/createElement — never innerHTML
 * with data values (matches XSS-safe pattern used across the app).
 */

/* ── Page guard ──────────────────────────────────────────────────────────── */

async function initPage() {
  try {
    const me = await apiMe();
    applyNav(me.role);
    const avatarEl = document.getElementById("avatarEl");
    const rolePill = document.getElementById("rolePill");
    if (avatarEl) avatarEl.textContent = (me.full_name || me.username).substring(0,2).toUpperCase();
    if (rolePill) rolePill.textContent = me.role.charAt(0).toUpperCase() + me.role.slice(1);
  } catch {
    window.location.replace("index.html"); return;
  }
  document.body.style.visibility = "visible";
  await loadSubmissions();
}

initPage();

/* ── Data loading ────────────────────────────────────────────────────────── */

async function loadSubmissions() {
  const loading = document.getElementById("loading");
  const empty   = document.getElementById("empty");

  try {
    const data        = await apiMySubmissions();
    const submissions = data.submissions || [];
    loading.style.display = "none";

    if (submissions.length === 0) {
      empty.style.display = "block";
      return;
    }

    renderList(submissions);
  } catch (e) {
    loading.style.display = "none";
    toast("Failed to load your submissions: " + e.message, "error");
  }
}

/* ── Rendering ───────────────────────────────────────────────────────────── */

/**
 * Renders each submission as a card showing title, status, submitted
 * date, and — if returned — the librarian's feedback.
 *
 * @param {Array} submissions
 */
function renderList(submissions) {
  const list = document.getElementById("subList");
  list.innerHTML = "";

  submissions.forEach(s => {
    const card = document.createElement("div");
    card.className = "card";
    card.style.marginBottom = "14px";

    /* Header row: title + status badge */
    const header = document.createElement("div");
    header.style.cssText = "display:flex;align-items:flex-start;justify-content:space-between;gap:12px;margin-bottom:8px;";

    const title = document.createElement("div");
    title.style.cssText = "font-weight:700;font-size:0.95rem;line-height:1.4;";
    title.textContent = s.title;

    const badge = document.createElement("span");
    badge.className   = `badge badge-${s.status}`;
    badge.textContent = s.status;
    badge.style.flexShrink = "0";

    header.appendChild(title);
    header.appendChild(badge);

    /* Meta row: department, school year, submitted date */
    const meta = document.createElement("div");
    meta.style.cssText = "font-size:0.78rem;color:var(--muted);margin-bottom:10px;";
    const parts = [s.department, s.school_year, s.submitted_at?.slice(0, 10)].filter(Boolean);
    meta.textContent = parts.join(" · ");

    card.appendChild(header);
    card.appendChild(meta);

    /* Feedback box — only shown when status is "returned" */
    if (s.status === "returned" && s.feedback) {
      const fbBox = document.createElement("div");
      fbBox.style.cssText =
        "background:#fef2f2;border:1px solid #fecaca;border-radius:8px;" +
        "padding:10px 12px;font-size:0.82rem;color:#991b1b;margin-top:8px;";

      const fbLabel = document.createElement("div");
      fbLabel.style.cssText = "font-weight:700;margin-bottom:3px;";
      fbLabel.textContent = "Librarian Feedback:";

      const fbText = document.createElement("div");
      fbText.textContent = s.feedback;

      fbBox.appendChild(fbLabel);
      fbBox.appendChild(fbText);
      card.appendChild(fbBox);
    }

    /* Approved date shown when applicable */
    if (s.status === "approved" && s.approved_at) {
      const approvedNote = document.createElement("div");
      approvedNote.style.cssText = "font-size:0.76rem;color:var(--success);margin-top:6px;";
      approvedNote.textContent = `✅ Approved on ${s.approved_at.slice(0, 10)}`;
      card.appendChild(approvedNote);
    }

    list.appendChild(card);
  });
}