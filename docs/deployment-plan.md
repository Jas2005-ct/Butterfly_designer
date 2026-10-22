# Deployment Plan - Butterfly Shop Manager

> **Status:** CODE MERGED, AWS PREP DONE. No production database write and no
> billable compute (no Express service, no ALB, no Fargate task) exists yet.
> This document is the runbook for deploying to **Amazon ECS Express Mode
> (ap-south-1, Mumbai)** with **Supabase** for Postgres + object storage.

---

## 0. Platform decision: why not App Runner

The original plan targeted **AWS App Runner**. That is no longer possible for this
account:

| Fact | Detail |
|---|---|
| App Runner availability | AWS closed App Runner to **new customers on April 30, 2026**. Existing customers may continue; no new features will ship. |
| This account | The AWS account (id `558948635923`) was created **2026-10-16**, i.e. after the cutoff, so it is a new customer. |
| Proof | `aws apprunner list-services --region ap-south-1` returns `SubscriptionRequiredException: The AWS Access Key Id needs a subscription for the service`. |
| AWS's recommendation | **Amazon ECS Express Mode** - "a Fargate-based ECS service with a unique accessible URL" that provisions the ALB, TLS, autoscaling, security groups and CloudWatch logging for you. |

**Consequence for the pipeline:** App Runner could build the image straight from a
GitHub repo. Express Mode deploys an **image**, so the pipeline becomes:

```
git push -> GitHub Actions builds the image -> push to ECR -> ECS Express Mode pulls it
```

---

## 1. Target architecture

```
                    Internet
                       |
              +--------+---------+
              |                  |
   Custom domain (optional)   https://butterfly-designer.ecs.ap-south-1.on.aws
              |                  |
              +--------+---------+
                       |  HTTPS (ALB terminates TLS)
                       v
              +-----------------------+
              |  Amazon ECS Express   |   region: ap-south-1 (Mumbai)
              |  Mode  (Fargate)      |   1 vCPU / 2 GB, min 1 - max 1 task
              +-----------------------+
                 |            |      |
        WhiteNoise /static   |      |  S3-compatible HTTPS
        gunicorn :8000       |      |
                            |      v
                  +---------+---------+      +------------------------+
                  |  Supabase Postgres    |    |  Supabase Storage      |
                  |  (pooled, port 6543)  |    |  bucket: butterfly...  |
                  |  DATABASE_URL         |    |  boto3 / django-storages
                  +-----------------------+    +------------------------+
```

- **Express Mode** creates the Fargate service, an internet-facing ALB with an
  HTTPS listener, target group, security groups, autoscaling, a log group and a
  5XX rollback alarm.
- **Static files** are served by WhiteNoise from inside the container
  (`collectstatic` runs at boot).
- **Media files** go to Supabase Storage via `django-storages` + `boto3`
  (activated by the presence of `AWS_ACCESS_KEY_ID` in `core/settings.py`).
- **Region:** `ap-south-1` (Mumbai). The Supabase pooler host is
  `aws-1-ap-south-1.pooler.supabase.com`, i.e. the Supabase project is in Mumbai
  too, so the DB round-trip stays in-region. (An earlier draft assumed Supabase
  was in Singapore - it is not.)

---

## 2. Current state audit

| Area | Status | Notes |
|---|---|---|
| `Dockerfile` | OK. Ready | `python:3.11-slim`, installs `uv`, `uv sync --frozen`. |
| `entrypoint.sh` | OK. Fixed | `collectstatic` + `gunicorn` only. `migrate` moved out to a one-off task (see 4.6). A `migrate` argument passthrough lets a task override run `manage.py migrate`. |
| `.gitattributes` | OK. New | Forces LF for `*.sh` and `Dockerfile`. Without it, a Windows checkout with `core.autocrlf=true` bakes a CRLF `#!/bin/sh` into the image and the container dies with "exec format error". |
| WSGI (`core/wsgi.py`) | OK. Ready | Standard. |
| Static files | OK. Ready | WhiteNoise + compiled Tailwind (no CDN). One backend in both `STORAGES` branches, no manifest conflict. |
| Media + Supabase S3 | OK. Wired | Enabled when `AWS_ACCESS_KEY_ID` is set. |
| DB routing | OK. Ready | `DATABASE_URL` + Postgres; else SQLite (dev). |
| `DJANGO_SECRET_KEY` | OK. Fixed | Env-driven; raises `ImproperlyConfigured` when missing (`core/settings.py:36`). |
| `DJANGO_DEBUG` | OK. Fixed | Env-driven, default `false` (`core/settings.py:31`). |
| `ALLOWED_HOSTS` | OK. Fixed | Env-driven, comma separated (`core/settings.py:50`). |
| `CSRF_TRUSTED_ORIGINS` | OK. Fixed | Env-driven, comma separated (`core/settings.py:232`). |
| Health endpoint | OK. Ready | `GET /healthz` -> `{"status":"ok"}`, no DB hit (`core/urls.py:26`, `core/views.py:6`). |
| `SECURE_PROXY_SSL_HEADER` | OK. Ready | Trusts `X-Forwarded-Proto`, so `request.is_secure()` is correct behind the ALB. |
| `.dockerignore` | OK. Ready | Excludes `.env`, `.venv`, `.git`, `staticfiles/`, `media/`. |
| Migrations | OK. Ready | Run as a one-off task, not on boot. |

---

## 3. Prerequisites (one-time setup)

1. **AWS account** `558948635923` with an IAM user/role that can create ECR
   repositories and ECS Express services.
2. **Supabase project** with:
   - a Postgres connection string (use the **pooled** string, port 6543),
   - a Storage bucket (`butterflydesinger`) with the right visibility,
   - an S3-compatible access key + secret
     (Supabase Dashboard -> Storage -> S3 settings).
3. **ECR repository** `butterfly-designer` in `ap-south-1`. - *created*
4. **GitHub OIDC role** `github-actions-deploy` so CI can deploy without
   long-lived AWS keys. - *created*
5. **ECS service roles** `ecsTaskExecutionRole` and
   `ecsInfrastructureRoleForExpressServices`. - *created*
6. **(Optional) Custom domain** + DNS.

---

## 4. Pre-deployment hardening (done)

### 4.1 `core/settings.py` - secret key and debug
Env-driven, fail closed:

```python
DEBUG = os.environ.get("DJANGO_DEBUG", "false").lower() in ("1", "true", "yes")

_secret = os.environ.get("DJANGO_SECRET_KEY") or os.environ.get("SECRET_KEY")
if not _secret:
    raise ImproperlyConfigured("Set DJANGO_SECRET_KEY (or SECRET_KEY) in the environment for production.")
```

Generate a key once: `python -c "from django.core.management.utils import get_random_secret_key as g; print(g())"`.

### 4.2 `core/settings.py` - allowed hosts and CSRF
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
In production:
`ALLOWED_HOSTS=butterfly-designer.ecs.ap-south-1.on.aws`
`CSRF_TRUSTED_ORIGINS=https://butterfly-designer.ecs.ap-south-1.on.aws`

### 4.3 Static storage
One backend in both branches of `STORAGES`:
`whitenoise.storage.CompressedStaticFilesStorage` (non-manifest), so
`collectstatic` cannot fail with `ManifestMissingFileError`.

### 4.4 Health endpoint
`core/views.py` returns `JsonResponse({"status": "ok"})` without touching the
database, so the ALB health check stays fast and does not fail while the schema
is still being migrated.

### 4.5 `.dockerignore` and `.gitattributes`
`.dockerignore` keeps `.env` and `.venv` out of the image.
`.gitattributes` keeps `*.sh` and `Dockerfile` LF-only so the container
entrypoint is executable on Linux.

### 4.6 `entrypoint.sh` - migrations are a one-off task
`migrate` was removed from the boot path. Express Mode performs **canary
deployments** (5% -> 100%), so two containers can start simultaneously; running
DDL from both can corrupt a migration. Migrations now run as a single dedicated
ECS task (see 6.4) before traffic is switched.

The entrypoint keeps a passthrough so a task override reaches Django:

```sh
if [ "$#" -gt 0 ] && [ "$1" = "migrate" ]; then
    exec python manage.py "$@"
fi
```

---

## 5. Environment variables (Express service config)

Set these on the Express service -> **Container environment variables** (never
commit them):

| Key | Value | Required |
|---|---|---|
| `DJANGO_SECRET_KEY` | *(generated, 4.1)* | yes |
| `DJANGO_DEBUG` | `false` | yes |
| `ALLOWED_HOSTS` | `butterfly-designer.ecs.ap-south-1.on.aws` | yes |
| `CSRF_TRUSTED_ORIGINS` | `https://butterfly-designer.ecs.ap-south-1.on.aws` | yes |
| `DATABASE_URL` | `postgresql://postgres.<ref>:<pw>@aws-1-ap-south-1.pooler.supabase.com:6543/postgres` | yes |
| `AWS_ACCESS_KEY_ID` | *(Supabase S3 key)* | yes (enables media -> S3) |
| `AWS_SECRET_ACCESS_KEY` | *(Supabase S3 secret)* | yes |
| `AWS_STORAGE_BUCKET_NAME` | `butterflydesinger` | yes |
| `AWS_S3_ENDPOINT_URL` | `https://<ref>.storage.supabase.co/storage/v1/s3` | yes |
| `AWS_S3_REGION_NAME` | `ap-south-1` | yes |

Notes:

- `ALLOWED_HOSTS` takes a **host only** (no scheme); `CSRF_TRUSTED_ORIGINS`
  takes a **full origin** (with `https://`). The ALB health check sends the
  service domain as the `Host` header, so it must be in `ALLOWED_HOSTS`.
- `AWS_S3_ENDPOINT_URL` must be the **full** S3 API path
  (`.../storage/v1/s3`), not the bare project domain - boto3 needs the path.
- `AWS_S3_REGION_NAME` must match the Supabase project region
  (`ap-south-1`). `core/settings.py` would otherwise default to `us-east-1`.
- Use the **pooler host**. The direct `db.<ref>.supabase.co` endpoint is
  IPv6-only and Fargate has no IPv6 egress. Port 6543 is safe here because the
  project uses psycopg2, which binds parameters client-side and does not use
  server-side prepared statements (PgBouncer transaction mode would break
  psycopg3).
- Never use the production `DATABASE_URL` for local testing.

---

## 6. Deployment runbook (ECS Express Mode)

### 6.1 IAM roles for the service
Express Mode needs two roles. The console creates them for you; the CLI does not.

```powershell
aws iam create-role --role-name ecsTaskExecutionRole --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ecs-tasks.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
aws iam create-role --role-name ecsInfrastructureRoleForExpressServices --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ecs.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
aws iam attach-role-policy --role-name ecsTaskExecutionRole --policy-arn arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy
aws iam attach-role-policy --role-name ecsInfrastructureRoleForExpressServices --policy-arn arn:aws:iam::aws:policy/service-role/AmazonECSInfrastructureRoleforExpressGatewayServices
```

Wait ~1 minute for IAM propagation before creating the service.

### 6.2 ECR repository
```powershell
aws ecr create-repository --repository-name butterfly-designer --region ap-south-1
```

### 6.3 Build and push the image
Local (good for learning):
```powershell
docker build -t 558948635923.dkr.ecr.ap-south-1.amazonaws.com/butterfly-designer:latest .
aws ecr get-login-password --region ap-south-1 | docker login --username AWS --password-stdin 558948635923.dkr.ecr.ap-south-1.amazonaws.com
docker push 558948635923.dkr.ecr.ap-south-1.amazonaws.com/butterfly-designer:latest
```

CI (what you will actually use): `.github/workflows/build.yml` builds and pushes
on every push to `master` using GitHub OIDC - no AWS keys in GitHub secrets.
`.github/workflows/deploy.yml` updates the running service (manual dispatch).

### 6.4 Create the Express service
Console: ECS console -> **Express services** -> Create express service -> name
`butterfly-designer` -> image `558948635923.dkr.ecr.ap-south-1.amazonaws.com/butterfly-designer:latest`
-> port 8000 -> CPU 1 vCPU / memory 2 GB -> health check path `/healthz` ->
environment variables (section 5) -> scaling min 1 / max 1 -> Create.

CLI:
```powershell
aws ecs create-express-gateway-service `
  --region ap-south-1 `
  --service-name butterfly-designer `
  --primary-container "image=558948635923.dkr.ecr.ap-south-1.amazonaws.com/butterfly-designer:latest,containerPort=8000,environment=[...]" `
  --execution-role-arn arn:aws:iam::558948635923:role/ecsTaskExecutionRole `
  --infrastructure-role-arn arn:aws:iam::558948635923:role/ecsInfrastructureRoleForExpressServices `
  --health-check-path /healthz `
  --cpu 1024 --memory 2048 `
  --scaling-target minTaskCount=1,maxTaskCount=1 `
  --monitor-resources
```

`--cpu 1024` = 1 vCPU, `--memory 2048` = 2 GB.

Prefer Secrets Manager over plain environment values: switch the container
`environment` entries to `secrets=[{name=DJANGO_SECRET_KEY,valueFrom=arn:aws:secretsmanager:...}]`.

### 6.5 Run migrations as a one-off task

> **Now automated.** `deploy.yml` runs this exact task as its step 2, against
> the release image, and fails the release if the container's `exitCode` is not
> 0. The commands below stay documented as the break-glass path for the rare
> migration you must apply by hand.

```powershell
$svc = aws ecs describe-services --cluster default --services butterfly-designer --region ap-south-1 --output json | ConvertFrom-Json
$td  = $svc.services[0].taskDefinition
$sg  = $svc.services[0].securityGroups[0]
$sub = @("subnet-0dcb582aab2b0200f","subnet-0c3224681fe6f4964","subnet-00c09a8f2fdfdf3ed")

$task = aws ecs run-task --cluster default --task-definition $td --launch-type FARGATE --region ap-south-1 `
  --network-configuration "awsvpcConfiguration={subnets=[$($sub -join ',')],assignPublicIp=ENABLED,securityGroups=[$sg]}" `
  --overrides '{"containerOverrides":[{"name":"Main","command":["migrate","--noinput"]}]}' --output json

aws ecs describe-tasks --tasks $task.tasks[0].taskArn --region ap-south-1 --query "tasks[0].[lastStatus,stopCode,stoppedReason]"
```

Success = `STOPPED` with the container `exitCode` 0. The container name is
`Main`; confirm with
`aws ecs describe-task-definition --task-definition $td --query "taskDefinition.containerDefinitions[].name"`.

Console: Clusters -> Default cluster -> **Tasks** -> *Run new task* -> pick the
service's task definition -> networking (3 subnets + service security group) ->
**Override** -> container `Main` -> Command `migrate --noinput` -> Run.

### 6.6 Create a superuser
```powershell
$env:DATABASE_URL="postgresql://postgres.<ref>:<pw>@aws-1-ap-south-1.pooler.supabase.com:6543/postgres"
python manage.py createsuperuser
```
(or promote an existing row; do **not** run `loaddata` in production).

### 6.7 Deploy order for later releases
1. Push to `master` -> CI builds and pushes a new image to ECR.
2. Dispatch `deploy.yml` with that commit SHA. It **runs the migration one-off
   task itself (step 2)** and only proceeds if the container exits 0.
3. The same dispatch updates the service to the new image (canary).
4. Verify.

Doing migrations first means the canary never serves a schema-mismatched app.

---

## 7. Post-deploy verification checklist

- [ ] `GET https://butterfly-designer.ecs.ap-south-1.on.aws/healthz` -> `200 {"status":"ok"}`
- [ ] Landing page loads over HTTPS (no mixed-content warnings).
- [ ] `/shop/login/` renders and accepts a login POST (proves `CSRF_TRUSTED_ORIGINS`).
- [ ] Board renders with real data; DEBUG is off (no debug 500 pages).
- [ ] Static assets return 200 (WhiteNoise + `collectstatic` OK).
- [ ] Uploading a file lands in **Supabase Storage**, not the container.
- [ ] Migrations applied exactly once (one `Applying ...` run in the task logs).
- [ ] CloudWatch logs free of `DisallowedHost`, `CSRF verification failed`, `InsecureResponse`.

---

## 8. CI/CD and rollbacks

- **CI:** `.github/workflows/build.yml` (push to `master` -> build -> ECR) and
  `.github/workflows/deploy.yml` (manual -> update the Express service).
  GitHub authenticates to AWS with **OIDC**, so no access keys are stored in
  GitHub.
- **Rollback:** the previous image tag stays in ECR. Re-deploy the older tag
  (or update the service to the previous task definition revision). Migrations
  are forward-only, so prefer additive migrations so a code rollback is safe.

---

## 9. Cost estimate

| Item | Monthly (ap-south-1) |
|---|---|
| Fargate 1 vCPU / 2 GB, always on | ~$36 |
| Application Load Balancer | ~$18 |
| CloudWatch Logs, ECR storage, data transfer | ~$1-5 |
| **Total** | **~$55-60** |

App Runner was cheaper (~$25-40) but is closed to new customers. If this cost
is a problem, Google Cloud Run's free tier, Render or Fly.io are materially
cheaper for a shop of this size - the Dockerfile and entrypoint work unchanged
on all three.

---

## 10. Open decisions

1. **Custom domain:** launch on `*.ecs.ap-south-1.on.aws` first, or attach one now?
2. **Scaling:** keep min 1 / max 1 while migrations live in a one-off task, or
   raise the max once CI is proven?
3. **Secrets:** plain environment variables for launch, or Secrets Manager from
   day one?
4. **Media bucket visibility:** `AWS_QUERYSTRING_AUTH=False` means the bucket is
   public-read - fine for receipts, confirm nothing private is uploaded.

---

## 11. Not done without explicit approval

- Running any migration or write against the production Supabase database.
- Creating billable AWS resources (Express service, ALB, Fargate tasks).
