# Adviser frontend implementation

Primary checklist: `../frontend_wiring_adviser_changes.md`. The function map is
supporting context; future-research recommendations remain deferred.

## Completed

- `ask.html` and `js/ask.js`: independent questions, API-fed college/year filters,
  own-college default and All options, 500-character limit, one replaceable answer,
  server-numbered citation links, complete source cards, and numeric research links.
- Desktop answer-left/source-right layout; stacked mobile results. Cream/navy/gold
  styling uses the shared shadows. No new backdrop filters.
- Explicit loading, empty-result, validation, rate-limit and service-error states.
  Questions survive errors; retries are manual. Failed filters allow an unfiltered
  question while offering a filter reload.
- Numeric detail lookup and resolved college information. Browse, Ask, related
  research, approved dashboard entries and My Submissions use numeric IDs.
- `js/reader.js`: server-authorized watermarked JPEGs fetched with Bearer headers,
  Blob URLs, lazy viewport loading, at most two concurrent page requests, page/info
  retries, cancellation, and URL cleanup on close/navigation. No raw-PDF fallback.
- Reader permission reasons, context-menu/drag/print/save restrictions, print CSS,
  and an explicit screenshot limitation. Paper analyses and independent refinement
  remain available; modal focus and mobile Paper/Insights tabs are preserved.
- Students land on Ask; librarians land on Dashboard. Both have an Ask nav link.
- Removed faculty UI, Library Station files, kiosk helpers/polling, terminal links,
  dashboard session panels/styles and the unused source-stem detail helper.
- Registration retains API-fed required college choices, generated-password/OTP
  flow, invalid-college reload and inline errors; domain errors are associated with
  the email field. Consent copy describes the watermarked reader and owner/librarian
  access, without the removed station or faculty role.
- Profile email stays read-only. College options now come from the API and selected
  profile department changes are sent through the existing profile endpoint.
- Preserved upload metadata editing and consent, notifications, bulk deletion,
  real repository health, dashboard user counts and self-service password recovery.

## Verification

Run from the repository root:

```powershell
node frontend/tests/adviser-contracts.cjs
node frontend/tests/adviser.cjs
```

The browser suite uses the existing Playwright installation in
`frontend/.detail-check/node_modules/playwright`, or `PLAYWRIGHT_MODULE` when set,
and installed Microsoft Edge. It starts and closes its own local static server.

- **Passed, mocked adapter checks:** request payloads, authorization, landing hint,
  JPEG response enforcement, 401/403/422/429 handling, profile/registration/upload
  payloads, dashboard and bulk-delete endpoints, removed helpers.
- **Passed, mocked Edge browser checks:** 1440, 1024, 390 and 360px; Ask layout,
  citations, independent requests, blank/long-input controls, empty/errors/manual
  retries, unavailable filters, numeric links, reader auth/laziness/permissions/
  retries/Blob cleanup, modal focus, refinement, no reader backdrop blur, redirects,
  dashboard user count, registration errors and profile college/email controls.
- **Passed:** syntax checks for all frontend JavaScript and `git diff --check`.
- Desktop and mobile Ask screenshots were visually inspected.
- **Live read-only check:** the running backend's `/openapi.json` advertises Ask,
  numeric detail, reader, profile and dashboard contracts. No missing required
  endpoint was found in the backend code or route document.

Mocks contain clearly artificial answers, accounts and JPEGs. They do not prove
Groq answer quality, actual PDF watermarks, Supabase permissions/storage, SMTP
delivery or real database writes. Those still need authenticated integration tests.
No backend startup, migration, seed, ingestion or reindex operation was run.

The pre-existing local `tests/contracts.cjs` fails at its assertion that
`apiKioskSetLockFor` still exists (line 45). That expectation contradicts the new
removal requirement. Existing local test files were left intact; the two adviser
suites cover the new contract. The full legacy test suite is not claimed to pass.

## Remaining coordination

- No frontend integration blocker was found in the declared required endpoints.
  Verify real Ask citations, owner/non-owner/librarian reader access, OTP email
  delivery, and upload persistence with authorized test accounts and papers.
- The previously identified backend upload hazard remains unchanged:
  `services/ingestor.py:confirm_upload` uploads/deletes the staged PDF before
  `_validated_metadata` runs. A rejected metadata edit can therefore make the same
  preview impossible to retry. This needs backend-owner coordination.
- Future OPAC work, embargoes, tags, feedback history, logout revocation and admin
  password reset remain out of scope. No backend files were edited, and no commit
  or push was made.
