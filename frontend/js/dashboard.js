let currentUserRole = null;

async function initPage() {
  try {
    const me = await apiMe();
    if (me.role !== "librarian") {
      window.location.replace("browse.html"); return;
    }
    currentUserRole = me.role;
    applyNav(me.role);
    document.body.style.visibility = "visible";
    document.getElementById("greeting").textContent = me.full_name || me.username;
    const avatarEl = document.getElementById("avatarEl");
    const rolePill = document.getElementById("rolePill");
    if (avatarEl) avatarEl.textContent = (me.full_name || me.username).substring(0,2).toUpperCase();
    if (rolePill) rolePill.textContent = me.role.charAt(0).toUpperCase() + me.role.slice(1);
  } catch {
    window.location.replace("index.html"); return;
  }

  try {
    const data        = await apiSubmissions();
    const submissions = data.submissions || [];
    renderStats(submissions);
    renderRecentList(submissions.slice(0, 4));
  } catch {
    toast("Could not load submissions.", "warning");
  }

  loadRepositoryStatus();
}

initPage();

function renderStats(submissions) {
  const total     = submissions.length;
  const pending   = submissions.filter(s => s.status === "pending").length;
  const validated = submissions.filter(s => s.status === "approved").length;
  const returned  = submissions.filter(s => s.status === "returned").length;

  document.getElementById("statTotal").textContent     = total;
  document.getElementById("statPending").textContent   = pending;
  document.getElementById("statValidated").textContent = validated;
  document.getElementById("statRejected").textContent  = returned;

  document.getElementById("pendingMsg").textContent =
    `${pending} research paper${pending !== 1 ? "s" : ""} awaiting review and validation.`;
}

function renderRecentList(submissions) {
  const list = document.getElementById("recentList");
  list.innerHTML = "";

  if (submissions.length === 0) {
    const empty = document.createElement("p");
    empty.className   = "text-muted";
    empty.textContent = "No submissions yet.";
    list.appendChild(empty);
    return;
  }

  submissions.forEach(s => {
    const row = document.createElement("div");
    row.className = "recent-item";

    const left = document.createElement("div");

    const title = document.createElement("div");
    title.className   = "recent-item-title";
    title.textContent = s.title;

    const meta = document.createElement("div");
    meta.className   = "recent-item-meta";
    meta.textContent = `${s.lead_researcher || "Unknown"} · ${s.submitted_at?.slice(0, 10) || ""}`;

    left.appendChild(title);
    left.appendChild(meta);

    const right = document.createElement("div");
    right.style.cssText = "display:flex;align-items:center;gap:8px;";

    const badge = document.createElement("span");
    badge.className   = `badge badge-${s.status}`;
    badge.textContent = s.status;
    right.appendChild(badge);

    if (currentUserRole === "librarian") {
      const delBtn = document.createElement("button");
      delBtn.className   = "btn btn-ghost btn-sm";
      delBtn.textContent = "🗑";
      delBtn.title       = "Permanently delete this paper";
      delBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        confirmDeletePaper(s.id, s.title);   // ← changed from s.source_stem
      });
      right.appendChild(delBtn);
    }

    row.appendChild(left);
    row.appendChild(right);
    list.appendChild(row);
  });
}

async function confirmDeletePaper(id, title) {
  const ok = confirm(
    `Permanently delete "${title}"?\n\n` +
    `This removes it from the repository, the search index, and disk. ` +
    `This cannot be undone.`
  );
  if (!ok) return;

  try {
    await apiDeletePaper(id);
    toast(`"${title}" deleted.`, "success");
    await refreshDashboard();
  } catch (e) {
    toast(`Delete failed: ${e.message}`, "error");
  }
}

async function refreshDashboard() {
  try {
    const data        = await apiSubmissions();
    const submissions = data.submissions || [];
    renderStats(submissions);
    renderRecentList(submissions.slice(0, 4));
  } catch {
    toast("Could not refresh submissions.", "warning");
  }
}

async function loadRepositoryStatus() {
  try {
    const data = await apiStatus();
    if (data.chunks_indexed > 0) {
      toast(`Repository: ${data.chunks_indexed} chunks indexed in ChromaDB`, "success");
    }
  } catch {
    /* Silent — non-critical */
  }
}