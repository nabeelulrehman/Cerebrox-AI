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

Edit the provided `env` file (already present with safe local defaults):

```
SECRET_KEY=django-insecure-change-this-in-production
DEBUG=True
ALLOWED_HOSTS=127.0.0.1,localhost

GEMINI_API_KEY=            # required for real AI notes and quiz generation
GEMINI_MODEL=gemini-3.5-flash-lite
```

**No API key?** The app still runs fully: `apps/ai_engine` falls back to a
clearly-labelled offline generator for notes and MCQs, so every other
feature (courses, quizzes, scoring, weak-topic detection, recommendations,
dashboard) remains fully demoable at zero cost.

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
| `DisallowedHost` error | Your host/domain isn't in `ALLOWED_HOSTS` | Add it to `env` or `.env` |
| AI notes/MCQs cannot be generated | No `GEMINI_API_KEY` set or the Gemini request failed | Set a real key in `env`, restart the server, and run `manage.py diagnose_gemini` |
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
