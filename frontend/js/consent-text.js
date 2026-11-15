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

Please choose whether other students and librarians may read your approved paper in iPeek's watermarked reader.

If you permit public viewing:
- After approval, other students can read the complete paper as watermarked page images.
- The reader identifies the viewer by email and date. Screenshots cannot be prevented.

If you do not permit public viewing:
- Other students cannot open the full paper in the reader. Repository metadata and AI analyses may still be available.
- You and librarians retain access regardless of your choice or review status. Librarians can access the PDF for review.

Your choice does not authorize copying, plagiarism, or misuse. This draft statement remains subject to the institution's final policy.`;
