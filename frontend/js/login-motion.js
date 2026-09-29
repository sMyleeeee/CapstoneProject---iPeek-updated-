/**
 * login-motion.js
 * ----------------
 * Additive motion layer for the redesigned Sign In / Register / Verify
 * flow. Does NOT modify, duplicate, or depend on login.js's internals —
 * it only:
 *   1. Listens on the SAME elements login.js's inline handlers already
 *      wire (showRegisterLink, showLoginLink) to also toggle the
 *      desktop image-panel slide orientation.
 *   2. Calls the SAME global functions login.js already exposes
 *      (showLogin(), showRegister()) from the new mobile toggle pill,
 *      so the toggle pill is just a second entry point into logic
 *      that already exists — not a new code path.
 *   3. Drives the mobile welcome screen dismissal (pure UI state,
 *      no auth logic involved).
 *   4. DESKTOP ONLY — sequences the form content to fade in AFTER the
 *      image-panel's slide finishes, instead of both changing at
 *      once. login.js already hard-cuts the panel content (loginPanel
 *      → registerPanel etc.) the instant the click happens; this
 *      just hides that already-swapped content for a beat, then
 *      reveals it right as the slide's transitionend fires.
 *
 * Load AFTER login.js so showLogin()/showRegister() already exist
 * in global scope by the time this file's listeners can call them.
 */

document.addEventListener("DOMContentLoaded", () => {

  const authShell    = document.getElementById("authShell");
  const imagePanel    = document.getElementById("imagePanel");
  const authToggle    = document.getElementById("authToggle");
  const toggleSignIn = document.getElementById("toggleSignIn");
  const toggleSignUp = document.getElementById("toggleSignUp");

  const isDesktopSlide = () => window.matchMedia("(min-width: 901px)").matches;

  // Tracks the currently-pending transitionend listener so a rapid
  // second click doesn't leave two competing listeners attached.
  let pendingSlideHandler = null;

  function sequenceFormFade() {
    // Guard: on mobile there's no image-panel slide at all
    // (.image-panel is display:none there) — its transitionend would
    // never fire, which would leave the form permanently hidden.
    // Skip the whole fade sequence there; the form just shows as-is.
    if (!isDesktopSlide() || !imagePanel) return;

    const loginBox = authShell ? authShell.querySelector(".login-right") : null;
    if (!loginBox) return;

    if (pendingSlideHandler) {
      imagePanel.removeEventListener("transitionend", pendingSlideHandler);
      pendingSlideHandler = null;
    }

    loginBox.classList.remove("form-fade-in");
    loginBox.classList.add("form-fade-pending");

    pendingSlideHandler = (e) => {
      if (e.propertyName !== "left") return; // only the slide itself, not other transitions
      loginBox.classList.remove("form-fade-pending");
      loginBox.classList.add("form-fade-in");
      loginBox.addEventListener("animationend", () => {
        loginBox.classList.remove("form-fade-in");
      }, { once: true });
      imagePanel.removeEventListener("transitionend", pendingSlideHandler);
      pendingSlideHandler = null;
    };
    imagePanel.addEventListener("transitionend", pendingSlideHandler);
  }

  /* ── Shared orientation setter — desktop slide + mobile toggle state ── */
  function setOrientation(isRegister) {
    if (authShell)  authShell.classList.toggle("register-orientation", isRegister);
    if (authToggle) authToggle.classList.toggle("on-register", isRegister);
    if (toggleSignIn) toggleSignIn.classList.toggle("active", !isRegister);
    if (toggleSignUp) toggleSignUp.classList.toggle("active", isRegister);
    sequenceFormFade();
  }

  /* ── Desktop: hook the existing text links to also slide the image ── */
  const showRegisterLink = document.getElementById("showRegisterLink");
  const showLoginLink    = document.getElementById("showLoginLink");

  if (showRegisterLink) {
    showRegisterLink.addEventListener("click", () => setOrientation(true));
  }
  if (showLoginLink) {
    showLoginLink.addEventListener("click", () => setOrientation(false));
  }

  /* ── Mobile: Sign In / Sign Up toggle pill ──────────────────────────
     Calls the SAME showLogin()/showRegister() functions the existing
     text links already call — this is not a new/duplicate code path,
     just a second UI entry point into it. */
  if (toggleSignIn) {
    toggleSignIn.addEventListener("click", () => {
      if (typeof showLogin === "function") showLogin();
      setOrientation(false);
    });
  }
  if (toggleSignUp) {
    toggleSignUp.addEventListener("click", () => {
      if (typeof showRegister === "function") showRegister();
      setOrientation(true);
    });
  }

  /* ── Mobile welcome screen dismissal ─────────────────────────────── */
  const mobileWelcomeSignIn = document.getElementById("mobileWelcomeSignIn");
  if (mobileWelcomeSignIn) {
    mobileWelcomeSignIn.addEventListener("click", () => {
      document.body.classList.add("welcome-dismissed");
    });
  }

});