/**
 * page-fade.js
 * -------------
 * Subtle page-entry animation, applied automatically on every page —
 * purely observational, requires no changes to any existing page
 * script for the STATIC part of this. Every authenticated page
 * already does this exact pattern:
 *   <body style="visibility:hidden;">
 *   ...then in that page's own JS, after the auth/role check passes:
 *   document.body.style.visibility = "visible";
 *
 * This script watches for that exact moment (via MutationObserver on
 * body's style attribute) and:
 *   1. Adds .page-fade-in to <body> — a brief whole-page fade+rise.
 *   2. Finds every static card-like block already sitting in the DOM
 *      (stat cards, profile cards, the upload card, detail-page
 *      cards, etc.) and staggers a fade-in across them individually.
 *
 * IMPORTANT LIMITATION: this only catches elements that already
 * exist in the HTML at the moment visibility flips to "visible".
 * Content built dynamically by a page's own script AFTER that point
 * (Browse's card grid, Dashboard's Recent Submissions list, Review's
 * submission list, My Submissions' cards) is invisible to this
 * script — those each need their own per-loop stagger, added
 * directly in browse.js / dashboard.js / review.js /
 * my-submissions.js, using the same .card-fade-in utility class
 * this file also uses.
 */
(function () {
  const body = document.body;
  if (!body) return;

  // Card-like static blocks across all pages — stat tiles, profile
  // cards, the upload card, detail-page cards, etc.
  const FADE_SELECTOR = ".card, .stat-card, .profile-avatar-card, .profile-form-card";

  function staggerStaticBlocks() {
    const blocks = document.querySelectorAll(FADE_SELECTOR);
    blocks.forEach((el, index) => {
      el.classList.add("card-fade-in");
      el.style.animationDelay = `${Math.min(index, 12) * 50}ms`;
      el.addEventListener("animationend", () => {
        el.classList.remove("card-fade-in");
        el.style.animationDelay = "";
      }, { once: true });
    });
  }

  const observer = new MutationObserver(() => {
    if (body.style.visibility === "visible") {
      body.classList.add("page-fade-in");
      staggerStaticBlocks();
      observer.disconnect();
    }
  });

  observer.observe(body, { attributes: true, attributeFilter: ["style"] });
})();