# iPeek: rules for coding agents

## Stack
FastAPI backend, vanilla JS frontend, Python 3.11. Runs locally (no deployment).
Supabase Postgres (asyncpg) plus a PRIVATE storage bucket `papers` (pending/, public/,
versions/), served only through server-side signed URLs. ChromaDB is local and rebuilt
with reindex_chroma.py. Embeddings and reranker run locally (ONNX, FastEmbed). Groq is
the only LLM. SMTP is used for OTP and generated-password emails.

## Auth and roles
- iPeek has exactly two roles: student and librarian. Role is always derived on the
  server from the email domain; never trust a client role. @students.isatu.edu.ph =
  student; @isatu.edu.ph on the LIBRARIAN_EMAILS allowlist = librarian; every other
  address is rejected at the OTP request (400 "Only student school emails can
  register"). Never create a faculty user. `faculty` stays in RoleEnum only as a
  deprecated legacy value (the Postgres enum already has it); a legacy faculty
  account gets 403 "This account type is no longer supported".
- Passwords: bcrypt used directly (pinned 4.0.1). Never add passlib.
- Email is the login identifier and cannot be changed after registration.
- Registration requires students to pick a seeded college: an unresolved
  `department` is a 400, and only the canonical college name is stored.
  Librarians are exempt (they are not tied to a college).
- Password recovery is self-service by email OTP only, which emails a newly generated
  password. There is no admin password reset and no refresh token; JWT lifetime comes
  from ACCESS_TOKEN_EXPIRE_MINUTES in config.py.
- Users are soft-deleted only (is_active). Never hard-delete users or cascade-delete
  their research.

## Data rules
- create_all never alters existing tables. A new column needs an explicit idempotent
  migration in init_db().
- Row Level Security must be enabled on every public table. After adding a table, tell
  the user to run ALTER TABLE ... ENABLE ROW LEVEL SECURITY.
- Never print or log secrets or .env values.
- Storage object keys must only be built by safe_object_key() in
  services/storage.py — never by formatting pending/, public/, or versions/ keys by hand.
- Do not run scripts that write real data (bulk_ingest real run, reindex_chroma,
  migrations against Supabase) without asking first.

## Scope
- Ask Research (`POST /search/ask`) supports context-aware follow-ups for students
  and librarians using bounded, request-local conversation context. Resolve references
  into standalone retrieval questions; clarify ambiguity and allow explicit topic changes.
  Previous answers are context, never evidence. Ground answers in approved-paper passages;
  comparisons retrieve from the referenced papers without restricting later searches to them.
  Validate supplied research IDs and preserve permissions and college/year filters.
  Cache keys must isolate conversation context. Persisted conversation history stays out of scope.
- Students never receive the raw PDF: the view-only reader
  (`/repository/{id}/reader`) serves server-rendered, watermarked JPEG pages,
  and the raw `/repository/{source_stem}/pdf` route is librarian-only (owner
  included). Screenshots cannot be prevented; the frontend must disable
  right-click, drag and print shortcuts and must never embed a PDF URL.
- The OPAC / Library Viewing Station (kiosk sessions) is out of scope and has
  been removed from the backend. The `kiosk_sessions` table stays unused in
  Supabase. Background and a suggested approach live in
  `docs/recommendations_for_future_researchers.md`.
- The research detail page is standalone for ONE paper (`GET /repository/{id}`
  returns metadata, abstract and the reader gate). `/auth/me` returns a
  `landing` hint (`"ask"` for students, `"dashboard"` for librarians) for the
  frontend only — it is never used for access control.
- Minimal diffs. Do not touch unrelated areas. When a task says "investigate first",
  report findings before editing.
- Frontend design system: cream base, neomorphic raised elements, glass/blur only for
  floating overlays, navy/yellow/orange palette. Do not reinterpret it per page.

### Ask maintenance and verification
- Completed work: Ask, the watermarked reader, the student/librarian role model,
  and removal of active kiosk functionality follow the scope and auth rules above.
- Historical verification counts (not permanent expected values): reindex completed
  with 86 approved papers and 18,464 chunks, with no skipped or failed papers.
  The original Ask query now returns a relevant answer, as reported in prior verification.
- Confirmed historical citation bug: the model emitted `【1】` or `【1†L52-L55】`,
  but the parser recognized only `[1]`, producing an empty `citations` array.
  Current `backend/services/repository_qa.py` implements the fix; all six isolated
  tests in `backend/tests/test_repository_qa_citations.py` passed on 2026-11-14.
- Citation requirements: normalize supported alternate markers to `[n]` and validate
  numbers against retrieved sources. Use actual source/page metadata and discard
  generated line references. Never return `found:true` with zero citations or cache
  invalid answers; evict legacy cached answers with that invalid shape.
- Distinguish generation failures (including missing valid citations; HTTP 502)
  from genuine retrieval no-results responses (`found:false`).
- Unresolved finding: the earlier empty-index claim is not established; the
  pre-reindex backup contained 75 sources and 16,031 chunks.
- Pending verification: confirm the citation fix through the live Ask endpoint with
  the original query; isolated tests do not establish end-to-end behavior. Reconcile
  the earlier empty-index claim with the populated pre-reindex backup.

## Reference files
backend/bulk_ingest.py loads archive PDFs, with the folder name deciding the college.
check_users.py, cleanup_users.py and fix_approved_filepaths.py were removed — do not
recreate them (storage keys come from safe_object_key() only).

## Keeping this file current
- When the user states a new rule, changes an existing one, or makes a decision that
  should apply to every future session (a naming rule, a "never do X", a design
  choice), update this file in the same task.
- Only record rules and decisions the user stated explicitly. Do not add your own
  guesses, preferences, or task-specific details (current to-do items, file lists,
  progress notes).
- Edit the relevant section in place and remove anything the new rule makes outdated.
  Do not append duplicates or contradictory lines.
- Keep it short. If a rule is only relevant to one task, leave it out.
- At the end of your reply, say in one line what you changed in AGENTS.md and why, so
  the user can review it.


  ## Frontend work (also applies to Codex)
- The frontend is owned by the teammate: backend-only tasks must not edit frontend/.
  When a backend fix needs frontend work, record it in docs/frontend_function_map.md.
- Edit only files under frontend/. Do not modify anything in backend/. If the backend
  is missing something or has a bug, report it and stop instead of changing it.
- Backend routes and response shapes are fixed. Check the API helpers in
  frontend/js/api.js before calling or changing an endpoint, and do not invent new ones.
- Keep the design system unchanged (cream base, neomorphic raised elements, glass blur
  only on floating overlays, navy/yellow/orange palette).
- Ask Research uses a centered welcome/composer landing page and switches immediately
  to conversation layout on the first valid submission. Preserve cream/navy/yellow
  tactile styling, white surfaces and serif headings; no glassmorphism, backdrop blur
  or whole-page/nav fades. Keep role-specific navigation unchanged.
- Reuse one compact, rounded Ask composer on landing and in conversation: warm cream
  inset surface, navy text, Filter SVG button at bottom-left, character counter and
  accessible yellow circular send button at bottom-right. College/Year controls live
  in a focus-contained desktop filter panel/mobile bottom sheet, with active-filter
  count, visible selections, reset and Escape/focus restoration; use solid surfaces.
  Landing placeholder is “Ask a research question…”; conversation
  placeholder is “Ask a follow-up about this topic…”. Landing chips (“smart irrigation
  system”, “e-waste awareness in communities”, “AI in education”) fill/focus without
  submitting and hide after first submission.
- Ask exchanges append in page memory only. Requests send the current `query`,
  `college_id`, `year` and bounded recent context. Keep the context helper text,
  500-character validation, one active request, per-answer loading/errors and manual
  retries using the original filters, query and context snapshot.
- Desktop Ask Sources follow the selected answer (new submissions select their answer);
  mobile sources expand beneath their corresponding answer. Scope citation numbers
  and source links to each answer, use returned metadata, and preserve the existing
  research detail/watermarked-reader flow. Render server text safely.
- New conversation clears displayed exchanges and all context, and invalidates late responses. Preserve
  keyboard/IME handling, visible focus, restrained reduced-motion-aware transitions,
  responsive composer visibility and readers' position when viewing older messages.
- Ask frontend verification: mocked browser checks pass at 1440/1024/390/360px for
  landing, successive answers, scoped citations, sources, no-results, failures/retries,
  duplicate prevention and reset during loading; compact composer counters, filter
  reset/indicators, panel focus/Escape, mobile sheet bounds and short viewports are checked.
  Physical mobile keyboards, screen-reader output and live backend behavior remain
  unverified by these frontend checks.
- Dashboard styling uses solid white raised cards, warm shared shadows, dark navy
  serif headings, blue metric numbers, and compact gold primary buttons; no blur or
  hover lift on static cards. Preserve its current layout; keep skeletons within
  cards and pending-review loading neutral until actual pending submissions exist.
- Research detail uses glass-inspired framing over mounted Browse: a translucent
  navy dim layer, 94–97% warm-white outer panel, light border, soft shadow, and
  opaque reading surfaces. No backdrop blur, screenshot backgrounds, or duplicated
  background DOM. Preserve Paper / Insights mobile tabs, Related Research inside
  the insights conversation, and transform/opacity transitions. Show the shell
  before AI requests finish; avoid unnecessary rendering behind the overlay.
- Match cursors to behavior: pointer only on actionable controls/cards, text on
  editable fields, default on static content, and not-allowed on genuinely disabled
  controls with a visible disabled state. Preserve text selection and native resize
  handles; grab/grabbing only for actual dragging. Decorative SVGs inherit the
  control cursor and never intercept clicks. No page-wide cursor overrides or images.
- Use native links, buttons and forms for keyboard activation and submission, with
  visible focus and logical Tab order. Hidden/disabled controls must not be tabbable.
  Modals contain focus and restore it on close; Escape dismisses only the topmost
  dismissible layer. Preserve native field keys; no positive tabindex or typing shortcuts.
- Site-wide focus uses a crisp 1px navy outline with a 2px offset via `:focus-visible`.
  Avoid stacked indicators, focus border changes, heavy focus shadows and layout shifts;
  preserve contrast and existing tactile styling. Composite text controls show one
  container boundary while typing; their buttons retain individual keyboard focus.
- Use the shared outline SVG set for UI icons, with consistent sizing, strokes and
  currentColor. Hide decorative SVGs from screen readers and name icon-only controls.
  Keep photographs and research content in their original formats; preserve the
  navbar's no-fade behavior and solid sliding active pill.
- Delete and Sign out controls are icon-only red SVG buttons with hover, pressed,
  visible keyboard focus, accessible labels, and hover/focus tooltips. Mobile Sign
  out stays in the header with a 44px touch target for every role, including locked
  library terminals; preserve existing confirmations and session-clearing behavior.
- Context-aware Ask follow-ups are allowed; do not persist conversation
  history. Do not hardcode secrets,
  URLs, or test data in application code.

Stop the backend before running bulk_ingest.py or reindex_chroma.py, and start it again afterward (local Chroma is not safe for two processes).
