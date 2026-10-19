# Deployment Plan — Butterfly Shop Manager

> **Status:** PLANNING ONLY. No production database or AWS resource has been touched.
> This document is the runbook for deploying to **AWS App Runner (ap-south-1, Mumbai)**
> with **Supabase** for Postgres + object storage. Execution is on hold until you lift it.

---

## 1. Target architecture

```
                    Internet
                       │
             ┌─────────▼──────────┐
             │  Custom domain      │  (optional; App Runner gives a
             │  shop.example.com   │   *.awsapprunner.com URL first)
             └─────────┬──────────┘
                       │ HTTPS
             ┌─────────▼──────────┐
             │  AWS App Runner     │  region: ap-south-1 (Mumbai)
             │  (Docker image)     │  runs: gunicorn core.wsgi
             │  WhiteNoise → /static
             └───────┬───────┬─────┘
                     │       │
        Postgres SSL │       │ S3-compatible HTTPS
                     │       │
        ┌────────────▼──┐ ┌──▼───────────────┐
        │ Supabase DB    │ │ Supabase Storage  │
        │ (DATABASE_URL) │ │ (boto3 / storages)│
        └────────────────┘ └───────────────────┘
```

- **App Runner** builds straight from the GitHub repo (`Jas2005-ct/Butterfly_designer`) using the
  existing `Dockerfile`. No image push to ECR required (though it's an option — see §8).
- **App Runner → Supabase over public internet** with TLS. (Private VPC networking is a later
  hardening step, not required to launch.)
- **Static files** served by WhiteNoise from inside the container.
- **Media files** (receipts/uploads) go to Supabase Storage via `django-storages` + `boto3`
  (already wired in `core/settings.py`, triggered by the presence of `AWS_ACCESS_KEY_ID`).

### ⚠️ Region / latency decision
Supabase does **not** offer a Mumbai region; the nearest is **Singapore (`ap-southeast-1`)**.
App Runner in Mumbai → Supabase in Singapore = roughly **40–70 ms** of extra DB round-trip per
query. For a shop with ~90–200 active orders this is fine. If you later want minimum latency,
either move App Runner to `ap-southeast-1` (colocate with Supabase) or use a Supabase
dedicated region. **Decision needed before go-live** (see §9).

---

## 2. Current state audit

| Area | Status | Notes |
|---|---|---|
| `Dockerfile` | ✅ Ready | `python:3.11-slim`, installs `uv`, `uv sync --frozen`. |
| `entrypoint.sh` | ⚠️ Review | Runs `collectstatic` → `migrate` → `gunicorn`. Migrate-on-boot has a multi-instance race (§6). |
| WSGI (`core/wsgi.py`) | ✅ Ready | Standard. |
| Static files | ✅ Ready | WhiteNoise + compiled Tailwind (no CDN). |
| Media → Supabase S3 | ✅ Wired | Enabled when `AWS_ACCESS_KEY_ID` is set. |
| DB routing | ✅ Ready | `DATABASE_URL` → Postgres; else SQLite (dev). |
| **`SECRET_KEY`** | 🔴 Blocked | Hardcoded & insecure (`settings.py:29`). Must come from env. |
| **`DEBUG` flag** | 🔴 Blocked | `DEBUG = 'RENDER' not in os.environ` (`:32`) → **DEBUG=True on App Runner** (no `RENDER` var). Must switch to a `DJANGO_DEBUG` env flag. |
| **`ALLOWED_HOSTS`** | 🔴 Blocked | `['*']` (`:34`). Must pin to the App Runner domain(s). |
| **`CSRF_TRUSTED_ORIGINS`** | 🔴 Blocked | Defaults to ngrok hosts (`:211`). Must set production origin. |
| **Health endpoint** | 🔴 Missing | App Runner health check needs a lightweight `/healthz`. None exists. |
| **Static storage conflict** | 🟡 Fix | `STATICFILES_STORAGE` (`:150`) uses *Manifest* backend, but `STORAGES["staticfiles"]` (`:182`,`:193`) uses *non-manifest*. These disagree — resolve to one. |
| `.env` present | 🟡 Caution | Local `.env` exists; must **not** be baked into the image (verify `.dockerignore`). |
| Migrations | ✅ Ready | All apps have migrations; `migrate` is idempotent. |

**All 🔴 items must be fixed before the first deploy.** §4 gives the exact changes.

---

## 3. Prerequisites (one-time setup)

1. **AWS account** with an IAM user/role that can manage App Runner.
2. **Supabase project** with:
   - a Postgres connection string (use the **pooled** connection string for serverless),
   - a Storage bucket (e.g. `butterflydesinger`) set to the correct visibility,
   - an S3-compatible access key + secret (Supabase Dashboard → Storage → S3 settings).
3. **(Optional) Custom domain** + its DNS (CNAME to the App Runner URL).
4. Decide the **region** (Mumbai default; see §1 latency note).

---

## 4. Pre-deployment hardening (code changes required)

Make these on a branch, test locally (SQLite + a staging Postgres), then merge to
`developement` before wiring App Runner.

### 4.1 `core/settings.py` — secret key & debug
Replace the hardcoded key and the `RENDER`-based DEBUG with env-driven values:

```python
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY")
if not SECRET_KEY:
    raise ImproperlyConfigured("DJANGO_SECRET_KEY env var is required in production")

# DEBUG only when explicitly enabled; safe default = off.
DEBUG = os.environ.get("DJANGO_DEBUG", "false").lower() in ("1", "true", "yes")
```

- Keep the dev fallback **out** of prod: run locally with `DJANGO_DEBUG=true`.
- Generate the key once: `python -c "from django.core.management.utils import get_random_secret_key as g; print(g())"`.

### 4.2 `core/settings.py` — allowed hosts & CSRF
```python
ALLOWED_HOSTS = [
    h.strip()
    for h in os.environ.get("ALLOWED_HOSTS", "127.0.0.1,localhost").split(",")
    if h.strip()
]

CSRF_TRUSTED_ORIGINS = [
    o.strip()
    for o in os.environ.get("CSRF_TRUSTED_ORIGINS", "").split(",")
    if o.strip()
]
```
- In production set `ALLOWED_HOSTS=shop.example.com,<service-id>.awsapprunner.com`
  and `CSRF_TRUSTED_ORIGINS=https://shop.example.com`.

### 4.3 Static storage — pick one backend
Delete the legacy `STATICFILES_STORAGE` line (`:150`) and keep the modern `STORAGES` dict.
Use the **manifest** backend in production for cache-busting:

```python
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}
```
(Match the `staticfiles` entry in **both** the Supabase and local branches so behaviour is identical.)

### 4.4 Add a health endpoint
`core/views.py`:
```python
from django.http import JsonResponse

def healthz(request):
    """Lightweight liveness check for the App Runner health probe.

    Args:
        request: The current HttpRequest.

    Returns:
        HttpResponse: 200 JSON {"status": "ok"} — no DB hit, so it stays fast
        and does not fail during migrations.
    """
    return JsonResponse({"status": "ok"})
```
`core/urls.py`: `path("healthz", core_views.healthz, name="healthz")`.

### 4.5 `.dockerignore`
Ensure it contains at least:
```
.env
.venv
__pycache__/
*.pyc
db.sqlite3
media/
staticfiles/
.git
```
> `media/` is ignored (files live in Supabase Storage, not the image).
> `static/` is **NOT** ignored (compiled Tailwind + vendored HTMX must ship in the image).

### 4.6 `entrypoint.sh` — migrate-on-boot
Current script runs `migrate` on **every** boot. With App Runner auto-scaling >1 instance,
concurrent `migrate` can collide. Choose one (see §6):
- **A (simple):** keep `migrate` in the entrypoint and cap App Runner to **1 instance** at launch.
- **B (robust):** remove `migrate` from the entrypoint and run it as a one-off deploy step
  (`aws apprunner ...` or a CI step) so only one process migrates.

**Recommendation:** start with **A** for launch, move to **B** when you add CI.

---

## 5. Environment variables (App Runner service config)

Set these in the App Runner service → **Environment variables** (never commit them):

| Key | Example / source | Required |
|---|---|---|
| `DJANGO_SECRET_KEY` | *(generated, §4.1)* | ✅ |
| `DJANGO_DEBUG` | `false` | ✅ |
| `ALLOWED_HOSTS` | `shop.example.com,<id>.awsapprunner.com` | ✅ |
| `CSRF_TRUSTED_ORIGINS` | `https://shop.example.com` | ✅ |
| `DATABASE_URL` | `postgres://...@db.<ref>.supabase.co:5432/postgres?sslmode=require` (pooled) | ✅ |
| `AWS_ACCESS_KEY_ID` | *(Supabase S3 key)* | ✅ (enables media→S3) |
| `AWS_SECRET_ACCESS_KEY` | *(Supabase S3 secret)* | ✅ |
| `AWS_STORAGE_BUCKET_NAME` | `butterflydesinger` | ✅ |
| `AWS_S3_ENDPOINT_URL` | `https://<ref>.storage.supabase.co` | ✅ |
| `AWS_S3_REGION_NAME` | `ap-southeast-1` | ✅ |

> **Never** use the production `DATABASE_URL` for local testing. Local tests already switch to
> SQLite when `test` is in `argv`; for manual dev use the local Postgres DB from `docker-compose.yml`.

---

## 6. Deployment runbook (App Runner)

1. **Push the hardened code** to `developement`, then merge → `master` (release branch).
   - App Runner watches `master` (or a tagged release) so it doesn't rebuild on WIP branches.
2. **Create the service** in the AWS console (ap-south-1):
   - Source: **GitHub** → repo `Jas2005-ct/Butterfly_designer`, branch `master`.
   - Build: **Dockerfile** (auto-detected). No custom build command.
   - Runtime: **CPU 1 vCPU / 2 GB** to start (enough for collectstatic + migrate + gunicorn).
   - Port: **8000** (entrypoint honours `${PORT:-8000}`; App Runner injects `PORT`, so 8000 is fine).
   - Health check: **HTTP**, path **`/healthz`**, interval 10 s (from §4.4).
   - Autoscaling: **min 1 / max 2** to start (keeps migrate-on-boot safe under §4.6-A).
3. **Add the environment variables** from §5.
4. **Deploy.** App Runner builds the image and runs `entrypoint.sh`
   (`collectstatic` → `migrate` → `gunicorn`).
5. **First-run data:**
   - Create a superuser via a one-off command, e.g. App Runner/CI:
     `python manage.py createsuperuser` — **or** promote an existing row.
   - Do **not** run `loaddata`/dummy-data commands in production (volume kit stays in `stash@{0}`).
6. **Verify** (§7).
7. **Attach custom domain** (optional): App Runner → Custom domains → add CNAME.

---

## 7. Post-deploy verification checklist

- [ ] `GET https://<service>/healthz` → `200 {"status":"ok"}`.
- [ ] Landing page loads over HTTPS (no mixed-content / insecure warnings).
- [ ] `/shop/login/` accepts a login POST (proves `CSRF_TRUSTED_ORIGINS` is correct).
- [ ] Board renders with real data; **DEBUG is off** (no debug 500 pages, no `django-debug` toolbar).
- [ ] Static assets: page source references `/static/...` and files return 200 (WhiteNoise + manifest OK).
- [ ] Upload a file (receipt/image) → it lands in **Supabase Storage**, not the container.
- [ ] `collectstatic` ran without `ManifestMissingFileError` (would indicate the §4.3 conflict).
- [ ] Migrations applied exactly once (check App Runner logs for a single `Applying ...` run).
- [ ] Logs free of `DisallowedHost`, `CSRF verification failed`, `InsecureResponse`.

---

## 8. CI/CD & rollbacks

- **CI (recommended, later):** GitHub Actions → on push to `master`: run the test suite
  (SQLite), build the Docker image, then let App Runner rebuild from `master`.
- **Rollback:** App Runner keeps prior deployments — **Rollback** to the previous revision in
  the console. Because DB migrations are forward-only, avoid irreversible schema changes in a
  single release; prefer additive migrations so a code rollback is safe.
- **ECR alternative:** build → push to ECR → point App Runner at the image instead of building
  from source. Faster cold builds once the repo grows.

---

## 9. Open decisions (need your call before go-live)

1. **Region:** App Runner **Mumbai (ap-south-1)** with ~50 ms cross-region DB latency to
   Supabase Singapore — **or** colocate both in `ap-southeast-1` for lowest latency?
2. **Domain:** use the `*.awsapprunner.com` URL for launch, or attach a custom domain now?
3. **Instances:** launch at **min 1 / max 1** (safe for migrate-on-boot) or **min 1 / max 2**
   with migrations moved out of the entrypoint (§4.6-B)?
4. **Media bucket visibility:** keep the Supabase bucket **public-read** (current
   `AWS_QUERYSTRING_AUTH=False`) — fine for receipts, but confirm nothing private is uploaded.
5. **Secrets store:** put `DJANGO_SECRET_KEY` + DB/S3 credentials directly in App Runner env now,
   or adopt AWS Secrets Manager / SSM from day one?

---

## 10. What I will NOT do without your go-ahead

- Run any migration or write against the **production** Supabase DB.
- Create/modify AWS resources or spend money.
- Merge `master` or trigger a live rebuild.
- Enable anything that was explicitly deferred (e.g. WhatsApp notifications — see
  `docs/whatsapp-blueprint.md`).

**Next step:** say **"apply hardening"** to start §4 on a branch (code-only, safe, testable
locally), or **"deploy"** once you've lifted the hold and answered §9.
