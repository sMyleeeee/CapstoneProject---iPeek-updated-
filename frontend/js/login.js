/**
 * login.js
 * --------
 * Handles the login page: sign in, and the two-step registration flow
 * (details -> OTP verification -> account created).
 *
 * IMPORTANT: role is NOT chosen by the user anywhere in this file.
 * It's derived entirely server-side from the institutional email
 * domain (student email or an allowlisted librarian address). See
 * backend: services/otp_service.py::determine_role_from_email().
 * There is intentionally no role selector in this UI — trusting a
 * client-supplied role was the original security gap this replaces.
 */

let pendingEmail        = null; // set once OTP has been requested
let pendingRegistration = null; // holds form data between step 1 and step 2

/* ── Panel switching ─────────────────────────────────────────────────── */

function showLogin() {
  document.dispatchEvent(new CustomEvent("login-form-switch", { detail: { isRegister: false } }));
  document.getElementById("resetPanel").style.display = "none";
  document.getElementById("loginPanel").style.display    = "block";
  document.getElementById("registerPanel").style.display = "none";
  document.getElementById("otpPanel").style.display       = "none";
}

function showRegister() {
  document.dispatchEvent(new CustomEvent("login-form-switch", { detail: { isRegister: true } }));
  document.getElementById("resetPanel").style.display = "none";
  document.getElementById("loginPanel").style.display    = "none";
  document.getElementById("registerPanel").style.display = "block";
  document.getElementById("otpPanel").style.display       = "none";
}

function showOtpStep(email) {
  document.getElementById("resetPanel").style.display = "none";
  document.getElementById("loginPanel").style.display    = "none";
  document.getElementById("registerPanel").style.display = "none";
  document.getElementById("otpPanel").style.display       = "block";
  document.getElementById("otpEmailDisplay").textContent  = email;
  document.getElementById("otpCode").focus();
}

/* ── Sign in ─────────────────────────────────────────────────────────── */

/**
 * Submits credentials to the backend. Redirect destination is based
 * entirely on the REAL role the server returns in the response — there
 * is no client-side role choice to be inconsistent with.
 */
async function login() {
  const uid = document.getElementById("loginUid").value.trim();
  const pwd = document.getElementById("loginPwd").value.trim();

  if (!uid || !pwd) {
    showErr("loginErr", "Please enter your email and password.");
    document.getElementById(!uid ? "loginUid" : "loginPwd").focus();
    return;
  }

  const btn = document.querySelector("#loginPanel .btn-sign");
  btn.disabled    = true;
  btn.textContent = "Signing in...";

  try {
    const result = await apiLogin(uid, pwd);

    // Display-only — carries no authority. Every protected backend
    // route checks the real JWT via require_role(), not this value.
    sessionStorage.setItem("role", result.role);
    sessionStorage.setItem("uid",  result.full_name || uid);

    const me = await apiMe();
    window.location.href = landingPage(me);

  } catch (e) {
    showErr("loginErr", e.message || "Invalid credentials.");
    btn.disabled    = false;
    btn.textContent = "Sign In to Repository";
  }
}

/* ── Register — Step 1: collect details, request OTP ─────────────────── */

async function sendOtp() {
  const fullname = document.getElementById("regFullname").value.trim();
  const email    = document.getElementById("regEmail").value.trim();
  const dept     = document.getElementById("regDept").value;
  document.getElementById("regEmailErr").textContent = "";
  document.getElementById("regEmail").removeAttribute("aria-invalid");

  if (!fullname || !email) {
    showErr("regErr", "Please fill in your full name and institutional email.");
    return;
  }

  if (!dept) { showErr("regErr", "Please select your college."); return; }

  // Non-secret details only — NO password is collected or chosen here.
  // The server generates one after OTP verification and emails it.
  pendingRegistration = {
    fullname: fullname,
    email: email,
    department: dept || null
  };
  pendingEmail = email;

  const btn = document.getElementById("sendCodeBtn");
  btn.disabled = true;
  btn.textContent = "Sending code...";

  try {
    await apiRequestOtp(email);
    showOtpStep(email);
  } catch (e) {
    showErr("regEmailErr", e.message || "Could not send verification code.");
    document.getElementById("regEmail").setAttribute("aria-invalid", "true");
  } finally {
    btn.disabled = false;
    btn.textContent = "Send Verification Code";
  }
}

/* ── Register — Step 2: verify OTP, then create the account ─────────── */

async function verifyAndRegister() {
  const code = document.getElementById("otpCode").value.trim();
  const btn = document.getElementById("verifyBtn");

  if (!pendingEmail || !pendingRegistration) {
    showErr("otpErr", "Your registration session expired. Please request a new code.");
    return;
  }

  if (!/^\d{6}$/.test(code)) {
    showErr("otpErr", "Please enter the 6-digit verification code.");
    return;
  }

  btn.disabled = true;
  btn.textContent = "Verifying...";

  try {
    await apiVerifyOtp(pendingEmail, code);
    // No password is sent — the server generates one after verification
    // and emails it to the verified address.
    await apiRegister(pendingRegistration);

    const registeredEmail = pendingEmail;
    pendingEmail = null;
    pendingRegistration = null;
    document.getElementById("otpCode").value = "";
    showLogin();
    showErr(
      "loginErr",
      `Your account is ready for ${registeredEmail}. We sent your password to your email — you can now sign in.`
    );
  } catch (e) {
    if (e.status === 400 && /college/i.test(e.message)) { showRegister(); showErr("regErr", e.message); await loadColleges(); }
    showErr("otpErr", e.message || "Could not verify the code or create your account.");
  } finally {
    btn.disabled = false;
    btn.textContent = "Verify & Create Account";
  }
}

/* ── Shared error display ───────────────────────────────────────────── */

/**
 * @param {string} boxId - id of the .err-box element for the active panel
 * @param {string} msg
 */
function showErr(boxId, msg) {
  const el = document.getElementById(boxId);
  el.textContent   = msg;
  el.style.display = "block";
}
async function loadColleges() {
  const select = document.getElementById("regDept"); const previous = select.value;
  const status = document.getElementById("collegeStatus");
  status.textContent = "Loading colleges...";
  status.classList.remove("is-error");
  select.disabled = true; select.replaceChildren(new Option("Loading colleges...", ""));
  try {
    const colleges = await apiColleges();
    select.replaceChildren(new Option("Select your college", "")); select.options[0].disabled = true;
    colleges.forEach(c => select.add(new Option(`${c.code} - ${c.name}`, c.name)));
    select.value = colleges.some(c => c.name === previous) ? previous : "";
    select.disabled = !colleges.length;
    status.textContent = colleges.length ? "" : "No colleges available. Please try again later.";
    status.classList.toggle("is-error", !colleges.length);
  } catch (error) { select.replaceChildren(new Option("Colleges unavailable", "")); status.textContent = error.message; status.classList.add("is-error"); }
}
let resetEmail = null;
function showReset() {
  ["loginPanel", "registerPanel", "otpPanel"].forEach(id => document.getElementById(id).style.display = "none");
  document.getElementById("resetPanel").style.display = "block";
  document.getElementById("resetEmail").focus();
}
async function requestReset() {
  const email = document.getElementById("resetEmail");
  if (!email.value.trim() || !email.reportValidity()) { showErr("resetErr", "Please enter your school email."); return; }
  const btn = document.getElementById("resetRequest"); btn.disabled = true; btn.textContent = "Sending...";
  document.getElementById("resetConfirm").disabled = true;
  resetEmail = null;
  try {
    const value = email.value.trim(); const result = await apiRequestPasswordReset(value);
    resetEmail = value; document.getElementById("resetCode").value = "";
    document.getElementById("resetCodePane").style.display = "block";
    document.getElementById("resetCode").focus();
    showErr("resetErr", result.message);
  } catch (error) { showErr("resetErr", error.message); }
  finally { btn.disabled = false; btn.textContent = "Send / resend reset code"; document.getElementById("resetConfirm").disabled = !resetEmail; }
}
async function confirmReset() {
  const code = document.getElementById("resetCode").value.trim();
  if (!resetEmail || resetEmail !== document.getElementById("resetEmail").value.trim()) { showErr("resetErr", "Request a code for this email first."); return; }
  if (!/^\d{6}$/.test(code)) { showErr("resetErr", "Enter the 6-digit code."); return; }
  const btn = document.getElementById("resetConfirm"); btn.disabled = true; btn.textContent = "Resetting...";
  document.getElementById("resetRequest").disabled = true;
  try {
    await apiConfirmPasswordReset(resetEmail, code); resetEmail = null;
    document.getElementById("resetCodePane").style.display = "none";
    showLogin(); showErr("loginErr", "Check your email for your new password.");
  } catch (error) { showErr("resetErr", error.message); }
  finally { btn.disabled = false; btn.textContent = "Verify and reset password"; document.getElementById("resetRequest").disabled = false; }
}
// Existing sessions go to their server-suggested home.
async function initializeHome() {
  if (_getToken()) {
    try { window.location.replace(landingPage(await apiMe())); return; }
    catch (error) {
      if (error.status === 403) sessionStorage.clear();
      showErr("loginErr", error.message);
    }
  }
  await loadColleges();
}
initializeHome();
if (new URLSearchParams(window.location.search).has("expired")) showErr("loginErr", "Your session expired. Please log in again.");
