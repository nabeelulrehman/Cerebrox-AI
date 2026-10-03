# CerebroX AI — Architecture

```
 USER
  │
  ▼
 Django Templates (Tailwind CDN, server-rendered) ── frontend/components + layouts
  │
  ▼
 Django Views (apps/*) ── session auth, role + ownership checks
  │
  ├──► SQLite (via Django ORM) — Users(+study_level), Courses, Topics, Questions,
  │                                Quizzes, QuizAnswers, AINotes — all scoped to
  │                                the owning student via FK chains
  │
  └──► apps/ai_engine ──► Gemini API (notes, MCQs — both take Course + Topic +
                            Study Level [+ Difficulty for quizzes]; optional
                            recommendation phrasing)
```

## Student-owned course model (current flow)

Courses and Topics are **not** global/shared content — each student creates
and manages their own (`Course.student`, `Topic.course.student`). There is no
admin-curated subject list. The flow is:

```
Register (incl. Study Level) → Add Courses → Login → Personal Dashboard
  → Course & Topic Selection → AI Notes / AI Quiz (using Study Level + Course + Topic [+ Difficulty])
  → Quiz Attempt → Performance Analysis → Strong & Weak Topics → AI Recommendations
  → Dashboard Automatically Updated
```

Every view that touches a Topic, Course, AINote, or Quiz filters by the
requesting student (`course__student=request.user` / `student=request.user`)
so one student's content is never visible to another. See
`tests/test_subjects.py` and `tests/test_topics.py` for the isolation tests.

## App responsibilities
- **accounts** — auth (register/login/logout/profile/password), `study_level` (+ custom text for "Other") as part of the learning profile, role-based access, custom admin panel — now oversight-only (platform stats + read-only view into any student's own courses/performance), since there's no global content left to manage.
- **learning** — student-owned `Course`/`Topic` CRUD (create/edit/delete are all ownership-checked), AI notes generation + storage (notes are generated using the student's Course + Topic + current Study Level).
- **ai_engine** — isolated Gemini provider layer (`gemini_client.py`) with an offline fallback generator so the app is fully runnable/demoable without an API key. Prompts for notes and MCQs both take `(course, topic, study_level, ...)`.
- **quizzes** — AI-generated MCQ storage (scoped to a student's own Topic), quiz lifecycle (generate → take → submit), pure-backend scoring (`evaluation.py`). Each `Quiz` snapshots the `study_level` used to generate it.
- **analytics** — dynamic performance aggregation (`course_wise_performance`, `topic_wise_performance`), threshold-based weak-topic detection, rule-based (optionally AI-phrased) recommendations — all computed only from that student's own quiz history.
- **dashboard** — personalized student home (course progress, scores, strong/weak topics, recommendations, recent activity) + public landing/about pages.
- **api/** — central REST aggregator (auth, learning) — quizzes/analytics also expose their own `/api/` sub-paths since those apps are primarily server-rendered.

## Design decisions worth knowing
- Performance/weak-topics are **calculated on read**, not stored, to avoid duplicate/stale data (per scope §21), and are always scoped to the requesting student.
- Study Level lives on the `User` model and is read at generation time — changing it in Profile affects all *future* AI notes/quizzes; past `AINote`/`Quiz` rows keep the level they were generated with (an intentional historical snapshot, not a live reference).
- AI is used only for notes, MCQs, and optional recommendation phrasing — scoring, weak-topic detection, and dashboard stats are pure backend logic (scope §18), keeping API costs low.
- The demo frontend HTML file was used as the **visual reference** (glass/dark theme, layout, quiz UI) and rebuilt as real Django templates rather than ported as a client-side SPA, to match the server-rendered project structure.
- The custom admin panel intentionally no longer creates/edits Subjects/Topics/Questions — that content is 100% student-owned now. Django's built-in `/django-admin/` still has full model access for a superuser if needed for support/debugging.
