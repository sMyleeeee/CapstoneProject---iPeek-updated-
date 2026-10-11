/**
 * users.js
 * --------
 * Librarian-only user management (soft-delete + admin-created accounts).
 *
 * Soft-delete rules: deactivate only flips the is_active flag — user rows
 * and their submission history are NEVER removed. Deactivated users can't
 * log in and are hidden from the default list (toggle above shows them).
 */

let usersCache = [];
let pendingConfirmAction = null;

async function initPage() {
  try {
    const me = await apiMe();
    if (me.role !== "librarian") {
      window.location.replace("browse.html"); return;
    }
    applyNav(me.role);
    document.body.style.visibility = "visible";
    const avatarEl = document.getElementById("avatarEl");
    const rolePill = document.getElementById("rolePill");
    if (avatarEl) avatarEl.textContent = (me.full_name || me.username).substring(0, 2).toUpperCase();
    if (rolePill) rolePill.textContent = "Librarian";
  } catch {
    window.location.replace("index.html"); return;
  }

  document.getElementById("includeInactive").addEventListener("change", loadUsers);
  await loadUsers();
}

initPage();

async function loadUsers() {
  const includeInactive = document.getElementById("includeInactive").checked;
  try {
    usersCache = await apiListUsers(includeInactive);
    renderUsers();
  } catch (e) {
    toast(`Could not load users: ${e.message}`, "error");
  }
}

function renderUsers() {
  const list = document.getElementById("userList");
  const count = document.getElementById("userCount");
  list.innerHTML = "";
  count.textContent = `${usersCache.length} account${usersCache.length !== 1 ? "s" : ""}`;

  if (usersCache.length === 0) {
    list.innerHTML =
      '<div class="empty-state">' +
      '<div class="empty-state-icon">👥</div>' +
      '<div class="empty-detail-title">No users found</div>' +
      '<div class="empty-detail-sub">Try toggling “Include deactivated accounts”.</div>' +
      "</div>";
    return;
  }

  usersCache.forEach((u) => {
    const row = document.createElement("div");
    row.className = "user-row";

    const info = document.createElement("div");
    const name = document.createElement("div");
    name.className = "user-row-name";
    name.textContent = u.fullname;
    const meta = document.createElement("div");
    meta.className = "user-row-meta";
    meta.textContent =
      `${u.email}` +
      (u.department ? ` · ${u.department}` : "") +
      (u.is_active === false ? " · deactivated" : "");
    info.appendChild(name);
    info.appendChild(meta);

    const right = document.createElement("div");
    right.className = "user-row-tags";

    const roleBadge = document.createElement("span");
    roleBadge.className = `badge badge-${u.role}`;
    roleBadge.textContent = u.role;
    right.appendChild(roleBadge);

    if (u.is_active === false) {
      const statusBadge = document.createElement("span");
      statusBadge.className = "badge badge-inactive";
      statusBadge.textContent = "deactivated";
      right.appendChild(statusBadge);
    }

    const actions = document.createElement("div");
    actions.className = "user-actions";

    if (u.is_active === false) {
      const reactBtn = document.createElement("button");
      reactBtn.className = "btn btn-ghost btn-sm";
      reactBtn.textContent = "Reactivate";
      reactBtn.addEventListener("click", () => askToggle(u, true));
      actions.appendChild(reactBtn);
    } else {
      const deactBtn = document.createElement("button");
      deactBtn.className = "btn btn-ghost btn-sm";
      deactBtn.textContent = "Deactivate";
      deactBtn.title = "Blocks login — submissions and records are kept";
      deactBtn.addEventListener("click", () => askToggle(u, false));
      actions.appendChild(deactBtn);
    }

    right.appendChild(actions);
    row.appendChild(info);
    row.appendChild(right);
    list.appendChild(row);
  });

  if (typeof staggerIn === "function") staggerIn(list);
}

/* ── Deactivate / Reactivate confirm ─────────────────────── */

function askToggle(user, toActive) {
  const title = document.getElementById("confirmTitle");
  const msg = document.getElementById("confirmMsg");
  const okBtn = document.getElementById("confirmOkBtn");

  if (toActive) {
    title.textContent = "Reactivate account?";
    msg.textContent = `${user.fullname} will be able to log in again.`;
    okBtn.textContent = "Reactivate";
    okBtn.className = "btn btn-success";
  } else {
    title.textContent = "Deactivate account?";
    msg.textContent =
      `${user.fullname} will no longer be able to log in. ` +
      `Their submitted research and records are kept and stay visible.`;
    okBtn.textContent = "Deactivate";
    okBtn.className = "btn btn-danger";
  }

  pendingConfirmAction = async () => {
    try {
      if (toActive) {
        await apiReactivateUser(user.id);
        toast(`${user.fullname} reactivated.`, "success");
      } else {
        await apiDeactivateUser(user.id);
        toast(`${user.fullname} deactivated.`, "success");
      }
      await loadUsers();
    } catch (e) {
      toast(`Failed: ${e.message}`, "error");
    }
  };

  document.getElementById("confirmModal").style.display = "flex";
}

function closeConfirm() {
  document.getElementById("confirmModal").style.display = "none";
  pendingConfirmAction = null;
}

document.addEventListener("DOMContentLoaded", () => {
  const okBtn = document.getElementById("confirmOkBtn");
  if (okBtn) {
    okBtn.addEventListener("click", async () => {
      const action = pendingConfirmAction;
      closeConfirm();
      if (action) await action();
    });
  }
});

/* ── Add user ────────────────────────────────────────────── */

function openAddUserModal() {
  document.getElementById("addFormView").style.display = "";
  document.getElementById("addResultView").style.display = "none";
  document.getElementById("newFullname").value = "";
  document.getElementById("newEmail").value = "";
  document.getElementById("newDepartment").value = "";
  document.getElementById("addModal").style.display = "flex";
  document.getElementById("newFullname").focus();
}

function closeAddModal() {
  document.getElementById("addModal").style.display = "none";
}

async function createUser() {
  const fullname = document.getElementById("newFullname").value.trim();
  const email = document.getElementById("newEmail").value.trim().toLowerCase();
  const department = document.getElementById("newDepartment").value || null;
  const btn = document.getElementById("createUserBtn");

  if (!fullname) { toast("Full name is required.", "warning"); return; }
  if (!email.includes("@")) { toast("Enter a valid institutional email.", "warning"); return; }

  btn.disabled = true;
  btn.textContent = "Creating…";
  try {
    const result = await apiAdminCreateUser({ fullname, email, department });
    document.getElementById("createdSummary").textContent =
      `${result.user.fullname} · ${result.user.email} · ${result.user.role}`;
    document.getElementById("generatedPw").textContent = result.generated_password;
    document.getElementById("addFormView").style.display = "none";
    document.getElementById("addResultView").style.display = "";
    toast("Account created.", "success");
    await loadUsers();
  } catch (e) {
    toast(`Create failed: ${e.message}`, "error");
  } finally {
    btn.disabled = false;
    btn.textContent = "Create account";
  }
}

async function copyGeneratedPw() {
  const pw = document.getElementById("generatedPw").textContent;
  try {
    await navigator.clipboard.writeText(pw);
    toast("Password copied.", "success");
  } catch {
    toast("Copy failed — select the password and copy manually.", "warning");
  }
}
