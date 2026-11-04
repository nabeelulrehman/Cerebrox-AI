# CerebroX-AI — Optional Teacher Ecosystem

Independent AI learning (Mode A) is unchanged and has **no dependency** on anything below.
Teacher features (Mode B) live in separate apps/tables and are strictly additive.

## Architecture
| App | Purpose |
|---|---|
| `apps.accounts` (existing) | + `teacher` role, `verified_teacher_required` decorator, admin approval screens |
| `apps.teachers` | `TeacherProfile` (pending → approved/rejected/suspended), public directory |
| `apps.classrooms` | `Classroom`, `Enrollment` (invite / request / join-code, consent flag), announcements |
| `apps.class_quizzes` | Teacher quizzes, attempts, QR tokens, audit log, grading, reports |
| `apps.marketplace` | Materials (private storage), purchase claims, sessions, guidance, reviews |

**Separation guarantees:** independent `apps.quizzes.Quiz` has no teacher/class field; class quizzes use
separate tables; `apps.analytics` reads only `apps.quizzes`, `class_quizzes.reporting` reads only class tables.
Enrollment never moves ownership of a student's courses, notes or quizzes.

## Setup
```
pip install -r requirements.txt
cp .env.example .env        # fill in values; never commit .env
python manage.py migrate    # additive only; existing rows untouched
python manage.py createsuperuser   # or use your existing admin (role=admin)
python manage.py runserver
```
Teachers register at `/teachers/register/`, then an **admin approves** them at `/admin-panel/teachers/`.
Payments/reviews are confirmed/moderated at `/market/admin/queue/`.

## Security model (all enforced server-side)
* Role + ownership checked in every view; class/quiz/answer lookups are filtered by `classroom__teacher=request.user`.
* Share links carry an unguessable UUID but grant nothing: login, active enrollment, schedule and attempts re-checked.
  Logged-out students return to the exact shared link after login using a safe `?next=` redirect. Non-members see
  an enrollment-denied message without quiz questions or answers; unpublished or invalid links return 404.
  Every attempt endpoint rechecks student role and active enrollment in the quiz's exact class.
* QR tokens: 256-bit random, only SHA-256 stored, single-use (atomic conditional UPDATE), short TTL, superseded when
  re-issued, teacher authorization checked *before* consumption, identical error for every failure.
* Deadline is server-controlled (`deadline_at`); late answers are ignored; DB constraint allows one open attempt per student per quiz.
* Files stored outside `MEDIA_ROOT` under random names, served only via a permission-checked view as attachments (`nosniff`);
  extension whitelist (no html/svg), size cap.
* AI grading is a *suggestion*: clamped to max marks, student text is delimited as untrusted, and it is never applied until the teacher approves.
* Teachers see an independent-practice summary only while the student's consent flag is on; removal revokes it.

## Known limitations (be honest with users)
* **Tab/window monitoring is a deterrent, not prevention.** A student can use a second device or a browser that suppresses events. Pauses are signals, not proof.
* **QR camera scanning** needs a browser with `BarcodeDetector` (Chromium); otherwise paste the code shown under the QR.
* **No payment gateway is integrated.** Purchases/bookings are *claims*; access is granted only after an admin manually confirms payment. Plug a real provider in behind `services.decide_payment` before taking real money.
* Email uses console backend until `EMAIL_HOST` is set; delivery is untested against a real SMTP server.
* Concurrency safety relies on DB transactions/constraints; tests run on SQLite and cannot exercise true parallel requests. Use PostgreSQL in production.
* Not built: recorded-lecture streaming/transcoding (files are plain downloads), real-time chat, teacher-defined negative-marking per question, in-app notifications.
* Uploaded files are not virus-scanned.

## Manual verification checklist
1. With zero teachers: register a student, add a course/topic, generate notes + quiz, submit, check dashboard/analytics.
2. Register a teacher → confirm class tools are locked → approve as admin → create a class.
3. Student joins via code (approval policy) → teacher approves → student sees class page.
4. Teacher creates quiz (manual + AI MCQ + descriptive), publishes, copies link; open link logged-out → login → returns to that quiz.
5. Non-enrolled student opens the link → clear enrollment denial with no quiz questions or answers.
6. Supervised quiz: student starts → QR shown → teacher scans (or pastes code) → quiz starts; switch tab → paused → re-scan to resume.
7. Teacher grades descriptive answer, tries AI suggestion, approves, releases results; student sees result labelled teacher-assigned.
8. Confirm personal analytics did not change and teacher report excludes personal quizzes.
9. Upload public / class / paid files; verify access from each kind of account; paid unlocks only after admin confirms.
10. Student toggles progress sharing; teacher sees summary only while on.
