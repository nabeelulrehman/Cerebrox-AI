# Vercel deployment

## Fit and current limits

Vercel's Python runtime can host this Django project as a WSGI function. The web UI, REST endpoints, database-backed quizzes and marketplace, and LiveKit signaling/webhooks can run there. `config/asgi.py` remains available for ASGI hosts, but the Vercel entry point uses the existing WSGI app.

Teacher materials now use a browser-to-private-object-storage upload, so Django only receives small metadata requests and the existing 100 MB file limit remains intact. Django creates a 10-minute, single-key SigV4 presigned `PUT` bound to the exact content length, MIME type, upload ID, and owner. Before parsing or scanning, Django copies the object to a server-only `upload-snapshots/` key and verifies its size, MIME type, and ownership metadata. The worker scans and promotes that immutable snapshot, so replaying the browser's still-live URL cannot swap the bytes after a clean verdict. The browser shows transfer progress; Django checks object metadata and size before creating the resource row. Profile avatars use the same flow because their 5 MB limit can exceed Vercel's request limit. Authorized downloads redirect to a five-minute signed GET so large file responses also bypass the Function. Cloudflare R2 supports presigned PUT but does not support presigned HTML POST uploads, so this implementation uses PUT. ([R2 presigned URL support](https://developers.cloudflare.com/r2/api/s3/presigned-urls/))

Vercel's writable filesystem is temporary. PostgreSQL and an S3-compatible object store are required. Existing SQLite records and files are not migrated automatically. Back up and migrate them before pointing real traffic at the deployment. Keep the bucket private; avatar/media and teacher files use `media/` and `private/` object prefixes, and teacher downloads use time-limited signed reads.

The Django function is capped at 300 seconds in `vercel.json` (the current Hobby maximum). Configure `GEMINI_REQUEST_TIMEOUT_MS=60000` and `GEMINI_MAX_RETRIES=1` in Vercel so retries and a fallback model fit within that cap in the worst case. Pro/Enterprise plans may support longer functions, but these synchronous requests should still be bounded. Move durable, scheduled, or long-running jobs to an external worker.

LiveKit video, audio, screen sharing, and data-channel chat are delivered between browsers by LiveKit Cloud; Django issues the room tokens and handles webhooks. Recording requires LiveKit Egress plus a private S3-compatible destination. SMTP delivery and Gemini AI require their own credentials. Long AI streaming/generation requests are subject to Vercel function duration and streaming behavior; use a persistent app/worker platform if they exceed the plan's limits.

## Vercel project settings

- Import the repository and keep **Root Directory** at the repository root.
- Framework preset: **Other** (or Django if Vercel detects it; retain the checked-in `vercel.json`).
- Build command: `python manage.py collectstatic --noinput` (also set in `vercel.json`).
- Output directory: leave unset.
- Install command: leave automatic so Vercel installs `requirements.txt`.
- Add all required variables below to Production, Preview, and Development as appropriate. Use different databases and credentials for non-production environments.
- Configure Production `DATABASE_URL` in the Vercel project environment with the existing Neon PostgreSQL URL. This application reads that variable only when Vercel sets `VERCEL`; local `.env` and local SQLite are never consulted for production database selection. Use a separate Neon database or branch for Preview and Development deployments so those environments cannot change production data.
- Attach a custom domain, then set its hostname in `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, and `SITE_URL`.
- Use exact hostnames and HTTPS origins in `ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS`; wildcard domains are rejected in Vercel mode. The active `VERCEL_URL` deployment hostname is added automatically.

### Verify production security settings without Neon

For a local production-settings check, set `DJANGO_PRODUCTION_SECURITY_CHECKS=1` and provide process environment values for `DEBUG=False`, `SECRET_KEY`, `ALLOWED_HOSTS`, and `CSRF_TRUSTED_ORIGINS`, then run `venv/Scripts/python.exe manage.py check --deploy`. This mode reads configuration from process environment only, selects local SQLite, and does not require or connect to `DATABASE_URL`. Use a disposable secret for this check, not a production credential. The check rejects non-HTTPS or non-approved CSRF origins. Vercel itself continues to use its own process environment and Neon configuration.

Login attempt throttling stores only keyed hashes in `LoginRateLimitBucket` and is shared by the web and API login endpoints. Apply the new account migration (`python manage.py migrate`) to each deployment database before serving login requests; Vercel does not run migrations automatically.

## Environment variables

Required for a production deployment:

| Variable | Value |
| --- | --- |
| `SECRET_KEY` | Long, random Django secret, unique per environment |
| `DEBUG` | `False` |
| `ALLOWED_HOSTS` | Comma-separated hostnames, e.g. `your-project.vercel.app,learn.example.com`; no scheme |
| `CSRF_TRUSTED_ORIGINS` | Comma-separated origins including scheme, e.g. `https://your-project.vercel.app,https://learn.example.com` |
| `DATABASE_URL` | Managed PostgreSQL connection URL; use the provider's pooled/serverless URL if offered and require TLS (`sslmode=require`) |
| `OBJECT_STORAGE_BUCKET` | Private S3-compatible bucket for uploads |
| `OBJECT_STORAGE_REGION` | Provider region (`us-east-1` default; use `auto` for Cloudflare R2) |
| `OBJECT_STORAGE_ACCESS_KEY` | Restricted object-storage access key |
| `OBJECT_STORAGE_SECRET_KEY` | Matching secret |
| `OBJECT_STORAGE_ENDPOINT_URL` | S3-compatible endpoint (required for R2 and many compatible providers; blank for AWS S3) |

Add these when the related feature is enabled:

| Variables | Purpose |
| --- | --- |
| `GEMINI_API_KEY` | AI notes, quiz generation, teacher AI |
| `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` | Live classroom token issuance and webhooks |
| `LIVEKIT_RECORDING_ENABLED`, `LIVEKIT_RECORDING_VERIFIED`, `LIVEKIT_RECORDING_S3_BUCKET`, `LIVEKIT_RECORDING_S3_REGION`, `LIVEKIT_RECORDING_S3_ACCESS_KEY`, `LIVEKIT_RECORDING_S3_SECRET`, `LIVEKIT_RECORDING_S3_ENDPOINT` | LiveKit Egress recording; leave both flags `False` until a real Egress and private playback test succeeds |
| `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `EMAIL_USE_TLS`, `DEFAULT_FROM_EMAIL` | SMTP delivery; without `EMAIL_HOST`, Django uses console mail and no email is delivered |
| `SITE_URL` | Public site origin used in shareable links |

Optional security tuning: `SECURE_SSL_REDIRECT=True`, `SECURE_HSTS_SECONDS=31536000`, `SECURE_HSTS_INCLUDE_SUBDOMAINS=True`. HTTPS cookies, proxy HTTPS handling, and secure content defaults are enabled whenever `DEBUG=False`.

Never set production secrets in source control, `.env`, or `.env.example`. Vercel reserved variables are supplied by Vercel; the app accepts `VERCEL_URL` automatically as an allowed host. In local development, migrations target only `BASE_DIR/db.sqlite3`. On Vercel, migrations target the Neon URL configured in Vercel's Environment Variables; run them from a trusted production migration shell with Vercel's production environment loaded. The Vercel build does not automatically migrate a database.

## Database and media migration

1. Create a managed PostgreSQL database and a private S3-compatible bucket. Give the application key only required object access, including Put/Get/Head/Delete and server-side copy. Configure bucket CORS for the exact production and preview origins: allow `PUT` and request headers `content-type`, `x-amz-meta-upload-id`, `x-amz-meta-owner-id`. Set private lifecycle retention for `upload-staging/` and `upload-snapshots/` longer than the worker outage and scan retry window (for example, 30 days); the database cleanup command removes expired objects sooner. Do not make the bucket public.
2. Back up `db.sqlite3`, `media/`, and `private_media/`. Export the existing rows to a fixture outside the repository: `venv/Scripts/python.exe manage.py dumpdata --natural-foreign --natural-primary --exclude contenttypes --exclude auth.permission --output ..\\cerebrox-data.json`.
3. In a trusted migration shell, set the target PostgreSQL `DATABASE_URL`, `SECRET_KEY`, and `DEBUG=False`; run `venv/Scripts/python.exe manage.py migrate`, then `venv/Scripts/python.exe manage.py loaddata ..\\cerebrox-data.json`. Django fixtures preserve primary keys and relations but do not copy file bytes. Compare representative user, course, quiz, class, resource, and booking counts before switching traffic.
4. Copy avatar files to the bucket's `media/` prefix and teacher resource files to its `private/` prefix, preserving each stored relative filename. Sample GET/HEAD checks without credentials should be denied; keep the SQLite and file backups until migration is accepted.
5. Set Vercel variables and deploy. Vercel does not run migrations automatically. Before real users, test login/CSRF, static assets, a 100 MB resource upload/download/delete, a profile avatar upload, and the LiveKit webhook using real provider credentials.

### Private teacher upload scanning

Teacher uploads remain in the private `upload-staging` prefix after Django validates their byte format. Their resources stay in `pending` state and are excluded from student listings and downloads. Only a ClamAV clean verdict allows the worker to copy an object into permanent `teacher-resources` storage and mark it `clean`. Malware is marked `infected` and copied to the private `quarantine` prefix; if that copy fails, the original remains private and denied until cleanup can remove it. Scanner errors and timeouts mark a resource `failed`; no failure is treated as a clean verdict.

Vercel functions do not run the scanner worker. Run `python manage.py scan_teacher_resources --batch-size 10` repeatedly from a separate, persistent worker/container that can reach PostgreSQL, the private S3/R2 bucket, and a private ClamAV `clamd` service. Set `CLAMAV_HOST`, `CLAMAV_PORT`, and `CLAMAV_TIMEOUT_SECONDS` only in that worker. Use `python manage.py scan_teacher_resources --retry-failed` to retry failed scans. Run `python manage.py purge_pending_uploads` periodically from the same worker to remove expired staging objects and orphaned avatars. Set ClamAV's `StreamMaxLength` above the configured 100 MB upload limit. Keep the TCP listener on a private network: ClamAV documents that its TCP protocol has no authentication or encryption and must not be exposed to untrusted networks. See the [ClamD protocol documentation](https://docs.clamav.net/manual/Usage/ClamdProtocol.html) and [ClamAV scanning security guidance](https://docs.clamav.net/manual/Usage/Scanning.html).

The application integration requires real deployment verification with the selected object-storage provider and a running ClamAV daemon. Do not expose new or migrated resources to students until their status is `clean`.
6. Before enabling recording, configure the LiveKit webhook at `https://<domain>/market/livekit/webhook/`, then run the README's consent-confirmed Egress and private playback verification. Keep recording flags off until it succeeds.

Run `python manage.py purge_pending_uploads` on an external schedule to delete expired authorization rows and unfinished objects. No persistent worker or scheduler is configured. Booking reminders are currently triggered when students visit relevant pages; use an external scheduler/worker if delivery must happen at a fixed time regardless of visits.

## Push and deploy

From the repository root, review the changes and push the current branch:

```powershell
git status
git add config/settings.py apps/marketplace/storage.py apps/marketplace/models.py apps/marketplace/forms.py apps/marketplace/views.py apps/marketplace/uploads.py apps/marketplace/scanning.py apps/marketplace/urls.py apps/marketplace/migrations/0006_pendingobjectupload.py apps/marketplace/migrations/0007_orphanedavatar.py apps/marketplace/migrations/0008_pendingobjectupload_resource_resource_scan_attempts_and_more.py apps/marketplace/migrations/0009_pendingobjectupload_scan_key.py apps/marketplace/management/commands/purge_pending_uploads.py apps/marketplace/management/commands/scan_teacher_resources.py apps/accounts/views.py apps/accounts/urls.py templates/marketplace/teacher_resources.html templates/accounts/profile.html frontend/components/sidebar.html static/js/resource_upload.js static/js/profile_avatar_upload.js tests/test_marketplace.py tests/test_authentication.py tests/test_teacher_ecosystem.py api/index.py vercel.json requirements.txt .env.example .python-version VERCEL_DEPLOYMENT.md README.md
git commit -m "Prepare Django project for Vercel deployment"
git push origin <your-branch>
```

In Vercel, import the Git repository, set the production environment variables, and deploy the pushed branch. Apply database migrations before directing users to the new deployment. Preview the deployment and complete the checks above before assigning production traffic.
