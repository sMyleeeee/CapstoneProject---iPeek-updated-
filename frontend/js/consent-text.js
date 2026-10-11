/**
 * consent-text.js
 * ---------------
 * SINGLE SOURCE OF TRUTH for the submission-time public-PDF consent
 * statement shown at Step 3 of the Submit Research flow (upload.html).
 *
 * The wording is a DRAFT pending official policy from the
 * librarian/institution and will likely be replaced wholesale. To swap
 * it, edit the text below — no other file needs to change. Do not
 * duplicate this string anywhere else in the codebase.
 *
 * Rendered with textContent + white-space:pre-wrap (never innerHTML),
 * matching the XSS-safe pattern used across the app.
 */
const PUBLIC_PDF_CONSENT_STATEMENT = `Public Availability of Your Research — Please Read Before Submitting

You're about to submit your research for librarian review. Please indicate whether you permit the full PDF of your paper to be publicly viewable by other students and faculty within iPeek.

If you permit public viewing:
- The complete PDF will be viewable by other verified students and faculty on the research's detail page.
- You acknowledge that making your work publicly accessible carries an inherent risk that it could be copied, improperly cited, or plagiarized by others.
- iPeek and its developers are not responsible for any unauthorized copying, plagiarism, or misuse of your research resulting from this visibility. Responsibility for monitoring and addressing such misuse rests with you and/or the institution.
- Your paper remains accessible for in-person library viewing either way, per institutional policy.

If you do not permit public viewing:
- Other students and faculty will only see an AI-generated summary (Introduction, Methods, Results, Discussion) of your research — not the full document.
- The full PDF remains accessible only in-person at the library, under supervision.

This is a draft statement pending final policy from the institution/librarian — not a substitute for formal legal review.`;
