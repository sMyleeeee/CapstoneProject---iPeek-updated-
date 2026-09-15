/**
 * profile.js
 * ----------
 * Profile Settings page — accessible by any logged-in user.
 *
 * SECURITY: apiMe() confirms a valid session exists and now also
 * returns real stored profile data (email, contact) — not derived or
 * hardcoded values. If not logged in, redirects to index.html.
 */

let currentUser = null; /* full profile object from apiMe(), refreshed after saves */

/* ── Page guard ──────────────────────────────────────────────────────────── */

async function initNav() {
  try {
    currentUser = await apiMe();
    applyNav(currentUser.role);
    populateProfile(currentUser);
    saveOriginalValues();
  } catch {
    window.location.replace("index.html");
    return;
  }
  document.body.style.visibility = "visible";
}
initNav();

/* ── Profile population ──────────────────────────────────────────────────── */

/**
 * Fills the profile card and form fields using real session data.
 * All values set via textContent or .value — never innerHTML with user data.
 *
 * @param {Object} me - { user_id, role, username, full_name, email, contact, department }
 */
function populateProfile(me) {
  const fullName = me.full_name || me.username || "User";
  const parts    = fullName.split(" ");
  const initials = fullName.substring(0, 2).toUpperCase();

  /* Profile card */
  document.getElementById("avatarInitials").textContent  = initials;
  document.getElementById("profileName").textContent     = fullName;
  document.getElementById("profileEmail").textContent    = me.email || "";
  document.getElementById("profileRolePill").textContent =
    me.role.charAt(0).toUpperCase() + me.role.slice(1);
  document.getElementById("metaId").textContent = me.username;

  /* Form fields — now populated from real stored data, not hardcoded */
  document.getElementById("firstName").value = parts[0] || "";
  document.getElementById("lastName").value  = parts.slice(1).join(" ") || "";
  document.getElementById("email").value     = me.email || "";
  document.getElementById("contact").value   = me.contact || "";
}

/* Store original values so Cancel can restore them */
const origValues = {};

function saveOriginalValues() {
  ["firstName", "lastName", "email", "contact", "department"].forEach(id => {
    const el = document.getElementById(id);
    if (el) origValues[id] = el.value;
  });
}

/* ── Password strength meter ─────────────────────────────────────────────── */

/**
 * Updates the strength bar and label as the user types their new password.
 * @param {string} pw
 */
function checkStrength(pw) {
  const seg1 = document.getElementById("pwSeg1");
  const seg2 = document.getElementById("pwSeg2");
  const seg3 = document.getElementById("pwSeg3");
  const label = document.getElementById("pwLabel");
  const segs = [seg1, seg2, seg3];

  if (!pw) {
    segs.forEach(s => { s.className = "pw-segment"; });
    label.textContent = "";
    return;
  }

  let score = 0;
  if (pw.length >= 8)           score++;
  if (pw.length >= 12)          score++;
  if (/[A-Z]/.test(pw))         score++;
  if (/[0-9]/.test(pw))         score++;
  if (/[^A-Za-z0-9]/.test(pw)) score++;

  const levels = [
    { filled: 1, cls: "filled-weak",    text: "Weak"   },
    { filled: 1, cls: "filled-weak",    text: "Weak"   },
    { filled: 2, cls: "filled-medium",  text: "Medium" },
    { filled: 2, cls: "filled-medium",  text: "Medium" },
    { filled: 3, cls: "filled-strong",  text: "Strong" },
  ];

  const lvl = levels[Math.min(score, 4)];
  segs.forEach((s, i) => {
    s.className = "pw-segment";
    if (i < lvl.filled) s.classList.add(lvl.cls);
  });
  label.textContent = `Password strength: ${lvl.text}`;
}

/* ── Save changes ────────────────────────────────────────────────────────── */

/**
 * Persists profile edits (name/email/contact) and, if password fields
 * are filled in, the new password too — as two separate API calls so a
 * password error doesn't block the profile info from saving.
 */
async function saveChanges() {
  const firstName = document.getElementById("firstName").value.trim();
  const lastName  = document.getElementById("lastName").value.trim();
  const email     = document.getElementById("email").value.trim();
  const contact   = document.getElementById("contact").value.trim();
  const currentPw = document.getElementById("currentPw").value;
  const newPw     = document.getElementById("newPw").value;
  const confirmPw = document.getElementById("confirmPw").value;

  if (!firstName || !lastName) { toast("First and last name are required.", "error"); return; }
  if (!email || !email.includes("@")) { toast("Please enter a valid email address.", "error"); return; }

  const wantsPasswordChange = currentPw || newPw || confirmPw;
  if (wantsPasswordChange) {
    if (!currentPw)          { toast("Please enter your current password.", "error"); return; }
    if (newPw.length < 8)    { toast("New password must be at least 8 characters.", "error"); return; }
    if (newPw !== confirmPw) { toast("New passwords do not match.", "error"); return; }
  }

  const fullName = `${firstName} ${lastName}`.trim();

  /* Profile fields — saved first, always attempted */
  try {
    const updated = await apiUpdateProfile(fullName, email, contact);
    currentUser = updated;
    populateProfile(updated);
    saveOriginalValues();
    sessionStorage.setItem("uid", fullName);
  } catch (e) {
    toast(`Failed to save profile: ${e.message}`, "error");
    return; /* don't attempt password change if the basic save failed */
  }

  /* Password — separate call, only attempted if the user filled those fields */
  if (wantsPasswordChange) {
    try {
      await apiUpdatePassword(currentPw, newPw);
      ["currentPw", "newPw", "confirmPw"].forEach(id => { document.getElementById(id).value = ""; });
      ["pwSeg1", "pwSeg2", "pwSeg3"].forEach(id => { document.getElementById(id).className = "pw-segment"; });
      document.getElementById("pwLabel").textContent = "";
      toast("Profile and password updated successfully.", "success");
      return;
    } catch (e) {
      toast(`Profile saved, but password change failed: ${e.message}`, "error");
      return;
    }
  }

  toast("Profile updated successfully.", "success");
}

/* ── Cancel ──────────────────────────────────────────────────────────────── */

function cancelChanges() {
  Object.entries(origValues).forEach(([id, val]) => {
    const el = document.getElementById(id);
    if (el) el.value = val;
  });
  ["currentPw", "newPw", "confirmPw"].forEach(id => { document.getElementById(id).value = ""; });
  ["pwSeg1", "pwSeg2", "pwSeg3"].forEach(id => { document.getElementById(id).className = "pw-segment"; });
  document.getElementById("pwLabel").textContent = "";
  toast("Changes cancelled.", "info");
}