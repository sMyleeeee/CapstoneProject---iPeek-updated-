# iPeek: rules for coding agents

## Stack
FastAPI backend, vanilla JS frontend, Python 3.11. Runs locally (no deployment).
Supabase Postgres (asyncpg) plus a PRIVATE storage bucket `papers` (pending/, public/,
versions/), served only through server-side signed URLs. ChromaDB is local and rebuilt
with reindex_chroma.py. Embeddings and reranker run locally (ONNX, FastEmbed). Groq is
the only LLM. SMTP is used for OTP and generated-password emails.

## Auth and roles
- Role is always derived on the server from the email domain. Never trust a client role.
  @students.isatu.edu.ph = student; @isatu.edu.ph on the LIBRARIAN_EMAILS allowlist =
  librarian, otherwise faculty. Faculty has the same access as students, EXCEPT the
  /ai/* routes: faculty is intentionally blocked there (403). Do not "fix" that guard.
- Passwords: bcrypt used directly (pinned 4.0.1). Never add passlib.
- Email is the login identifier and cannot be changed after registration.
- Registration requires students and faculty to pick a seeded college: an
  unresolved `department` is a 400, and only the canonical college name is
  stored. Librarians are exempt (they are not tied to a college).
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
- No Chat-with-AI feature anywhere.
- Minimal diffs. Do not touch unrelated areas. When a task says "investigate first",
  report findings before editing.
- Frontend design system: cream base, neomorphic raised elements, glass/blur only for
  floating overlays, navy/yellow/orange palette. Do not reinterpret it per page.

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
- Do not reintroduce Chat-with-AI, and do not hardcode secrets, URLs, or test data.

Stop the backend before running bulk_ingest.py or reindex_chroma.py, and start it again afterward (local Chroma is not safe for two processes).