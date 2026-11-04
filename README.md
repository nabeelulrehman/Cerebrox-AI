# CerebroX AI — AI-Powered Personalized Learning & Assessment System

Final Year Project. Each student builds their **own** courses and topics
(nothing predefined/global), sets a **Study Level**, and CerebroX AI
generates notes and quizzes tailored to that Course + Topic + Study Level.

**Flow:** Register (incl. Study Level) → Add Courses → Login → Personal
Dashboard → Course & Topic Selection → AI Notes / AI Quiz → Quiz Attempt →
Performance Analysis → Strong & Weak Topics → AI Recommendations →
Dashboard Automatically Updated.

Stack: Django 5 (server-rendered templates + DRF for the API layer), SQLite,
Tailwind CSS (CDN), vanilla JS, Gemini API (optional — see AI Configuration).

---

## 1. Setup & Installation

```bash
cd CerebroX-AI
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

## 2. Environment Variables

Copy the example file to `.env` in the project root (the same directory as
`manage.py`), then edit it:

```powershell
Copy-Item .env.example .env
```

Set `GEMINI_API_KEY` in `.env` to your own Gemini API key. The `.env` file is
git-ignored; do not commit it. Environment variables already set by the
process take precedence over values in `.env` when they contain a non-empty
key. Create or replace a key at [Google AI Studio](https://aistudio.google.com/apikey);
never paste it into source code or commit it.

Run `python manage.py check_gemini_config` to safely check whether a key is
configured and which source Django uses. This does not contact Google. Run
`python manage.py diagnose_gemini` to test an actual Gemini request.

The default model is the faster `gemini-3.5-flash-lite`, with
`gemini-3.8-flash` as a capacity fallback. Notes stream to the page and are
saved only after a complete, validated response. The Gemini SDK is configured
server-side; the key is never needed in frontend code. Requests use a 150-second
SDK timeout and at most two retries for transient network, timeout, rate-limit,
or server errors, with exponential backoff. The SDK exposes one request timeout
for this client rather than independent connect and read values; change
`GEMINI_REQUEST_TIMEOUT_MS`, `GEMINI_MAX_RETRIES`, or
`GEMINI_RETRY_BASE_SECONDS` in `.env` if your deployment needs different limits.

**No API key?** The app still runs, but AI notes and teacher AI features are
unavailable. The student quiz generator can use clearly labelled placeholder
MCQs; rule-based learning recommendations continue to work.

## 3. Database Setup & Migrations

```bash
python3 manage.py makemigrations   # already generated and committed; re-run only if you change models
python3 manage.py migrate
```

## 4. Seed demo data (demo accounts + starter courses for the demo student)

```bash
python3 manage.py seed_demo_data
```
Creates:
- **admin / AdminPass123** (admin role → oversight panel at `/admin-panel/`)
- **student / StudentPass123** (study level: Undergraduate) with two starter
  courses (Mathematics, Physics) and their topics — just examples on that one
  account; every student creates their own from scratch via `/learning/courses/`.

## 5. Run the backend

```bash
python3 manage.py runserver
```
Visit **http://127.0.0.1:8000/**. New registrations are routed straight to
"Add Courses" (`/learning/courses/`) before reaching the dashboard.

## 6. "Run the frontend"

No separate frontend build step — templates are served directly by Django
(`templates/`, `frontend/components`, `frontend/layouts`) with Tailwind
loaded from the CDN in `templates/base.html`. Static files live in `static/`.

For production, additionally run: `python3 manage.py collectstatic`

---

## 7. API Documentation
See `docs/api_documentation.md`. All endpoints are scoped to the logged-in
student's own data — there's no endpoint that exposes another student's
courses, notes, or quizzes.

## 8. Demo / Test Accounts
| Role | Username | Password | Study Level |
|---|---|---|---|
| Admin | admin | AdminPass123 | Graduate |
| Student | student | StudentPass123 | Undergraduate |

## 9. Testing

```bash
python3 manage.py test tests
```
21 tests, all passing: authentication (incl. Study Level on registration),
student-owned course/topic CRUD **and cross-student isolation**, AI
generation (offline fallback), the full quiz lifecycle, performance/weak-topic
calculation, and recommendations.

### Manual testing checklist
- [ ] Register a new student with a Study Level (including "Other" + custom text) → redirected to Add Courses
- [ ] Add a course and a topic under it
- [ ] Generate AI notes for that topic with `GEMINI_API_KEY` set — confirm the note reflects your Study Level
- [ ] Generate a quiz (choose Difficulty), answer questions, submit before/after the timer runs out
- [ ] Confirm quiz result shows correct score/percentage and per-question review
- [ ] Confirm dashboard reflects updated course progress, weak topics and recommendations after a low-scoring quiz
- [ ] Change your Study Level in Profile, confirm a newly-generated note/quiz reflects it while old ones keep their original snapshot
- [ ] Log in as a second student, confirm you **cannot** see the first student's courses/topics (404 on direct URL access)
- [ ] Log in as admin, confirm `/admin-panel/` shows platform stats and a read-only view of any student's own courses/performance (no ability to create global content)
- [ ] Hit a few `/api/...` endpoints with a logged-in session and confirm they only return your own data

## 10. Common Errors & Solutions

| Symptom | Likely cause | Fix |
|---|---|---|
| `DisallowedHost` error | Your host/domain isn't in `ALLOWED_HOSTS` | Add it to `.env` or set the `ALLOWED_HOSTS` process variable |
| Gemini reports `API_KEY_INVALID` | `.env` is missing, an old process environment key overrides it, or the configured key is invalid/revoked | Run `python manage.py check_gemini_config`; create a new key in Google AI Studio, update `GEMINI_API_KEY` in the root `.env` (or process environment), restart Django, then run `python manage.py diagnose_gemini` |
| AI notes/MCQs cannot be generated | `.env` is missing or the Gemini request failed | Copy `.env.example` to `.env`, set a real key, restart the server, and run `python manage.py diagnose_gemini` |
| `django.db.utils.OperationalError: no such table` | Migrations not applied | `python3 manage.py migrate` |
| 404 when opening a course/topic/quiz link | It belongs to a different student's account | Ownership is enforced by design — log in as the account that created it |
| Static styles missing in production | `collectstatic` not run / `DEBUG=False` without a static server | Run `collectstatic` and serve `staticfiles/` (e.g. via WhiteNoise or your web server) |
| 403/CSRF errors on form submit | Missing `{% csrf_token %}` or cookies blocked | All included templates already have it — check browser cookie settings |
| Admin panel redirects you to login | Logged in as a `student`-role account | Use the `admin` demo account or set `role='admin'` on your user |

---

## 11. Final Project Structure

```
CerebroX-AI/
├── manage.py, requirements.txt, env, .gitignore, db.sqlite3
├── config/                # settings, urls, wsgi, asgi
├── apps/
│   ├── accounts/          # auth, study_level, roles, oversight-only admin panel
│   ├── learning/          # student-owned Course, Topic, AINote + web/API views
│   ├── ai_engine/         # Gemini client, prompts, notes/mcq/recommendation generators (course+topic+study_level aware)
│   ├── quizzes/           # Question, Quiz(+study_level snapshot), QuizAnswer, services.py, evaluation.py
│   ├── analytics/         # performance.py (course_wise + topic_wise), weak_topics.py, recommendations.py
│   └── dashboard/         # personalized student home + public landing/about
├── api/                   # REST aggregator (auth, learning) — all scoped per-student
├── templates/              public/, accounts/, student/, admin/ page templates
├── frontend/                components/ (navbar, sidebar, cards, alert) + layouts/
├── static/                 css/theme.css, js/*.js, images/
├── media/profiles/          user avatars
├── database/                schema.md, seed_data.json
├── docs/                    architecture.md, api_documentation.md, database_design.md
└── tests/                   21 tests, all passing (incl. cross-student isolation checks)
```

## Scope note
Per the FYP scope document, this build intentionally excludes OCR, voice
interaction, real-time/WebSocket features, live classes, teacher
marketplace/payments, and OAuth — these are documented as future extensions,
not implemented here.
