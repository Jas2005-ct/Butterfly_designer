# Deployment Flow

How a code change in this repo becomes a running application on AWS.

Two flows are documented here:

| Part | Flow | Status |
|---|---|---|
| [1](#part-1--current-flow-manual) | **Current** — build and publish by hand, release by hand | ✅ working today |
| [2](#part-2--github-actions-flow) | **GitHub Actions flow** — CI builds and publishes on merge, release stays a deliberate step | ✅ enabled |

Everything below reflects the real state of account `558948635923` in region
`ap-south-1` (Mumbai) as of 2026-10-21.

---

## Part 0 — The shape of it

Three steps, always:

```
1. build   : source code  ->  container image
2. publish : image        ->  ECR (private registry)
3. release : ECR          ->  running Fargate task
```

Steps 1–2 produce an **artifact**. Step 3 is a **decision**. That split is the
reason the build and the deploy are separate workflows: a broken build must
never reach production, and a rollback should be one deliberate action.

**A new image sitting in ECR does not change the running service.** ECS pulls
the image only when a task starts, so the release step is what actually moves
traffic.

---

## Part 1 — Current flow (manual)

### 1.1 The pipeline

```
┌─ YOUR MACHINE ─────────────────────────────────────────────┐
│                                                             │
│  1. edit code                                               │
│     core/middleware.py, core/settings.py                    │
│            │                                                │
│  2. git commit  →  3. git push origin master                │
│     (gh token, scope "repo")                                │
└────────────┼────────────────────────────────────────────────┘
             │
             ▼
┌─ GITHUB ───────────────────────────────────────────────────┐
│  master = a9d1067 (+ your commit)                           │
│                                                             │
│  ⚠ NOTHING HAPPENS HERE unless the workflows are committed. │
│  Pushing code does NOT build anything by itself.           │
└────────────┼────────────────────────────────────────────────┘
             │
             │  (you switch back to the terminal)
             ▼
┌─ YOUR MACHINE (again) ─────────────────────────────────────┐
│  4. docker build  -t <ecr-uri>:latest .                    │
│     └─ Dockerfile: python:3.11-slim → uv → deps → COPY .   │
│                                                             │
│  5. aws ecr get-login-password | docker login               │
│     aws ecr push <ecr-uri>:latest                           │
└────────────┼────────────────────────────────────────────────┘
             │  image + digest
             ▼
┌─ AWS ───────────────────────────────────────────────────────┐
│                                                             │
│  ┌─ ECR (private registry) ─────────────────────────────┐   │
│  │  butterfly-designer:latest → sha256:d8b048f8...      │   │
│  │  sha256:fa12fec6...  (previous image, still there)   │   │
│  └──────────────────────────────────────────────────────┘   │
│             │                                                │
│             │  6. aws ecs update-express-gateway-service     │
│             │     --primary-container image=...@sha256:d8b0 │
│             ▼                                                │
│  ┌─ ECS Express Mode service ───────────────────────────┐   │
│  │  ALB (internet-facing) + ACM cert + SG + autoscaling │   │
│  │        │                                               │   │
│  │        ▼  pulls image via ecsTaskExecutionRole       │   │
│  │  Fargate task: 0.5 vCPU / 1 GB                        │   │
│  │    └─ container: gunicorn :8000  (your image)         │   │
│  │         └─ entrypoint.sh → collectstatic → gunicorn   │   │
│  └──────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

### 1.2 The exact commands

```powershell
# 4. build
docker build -t 558948635923.dkr.ecr.ap-south-1.amazonaws.com/butterfly-designer:latest .

# 5. publish
$env:AWS_REGION = "ap-south-1"
aws ecr get-login-password --region ap-south-1 |
    docker login --username AWS --password-stdin 558948635923.dkr.ecr.ap-south-1.amazonaws.com
docker push 558948635923.dkr.ecr.ap-south-1.amazonaws.com/butterfly-designer:latest

# 6. release  (deploy by digest, never by a moving tag)
aws ecs update-express-gateway-service `
    --service-arn arn:aws:ecs:ap-south-1:558948635923:service/default/butterfly-designer `
    --primary-container image=558948635923.dkr.ecr.ap-south-1.amazonaws.com/butterfly-designer@sha256:<digest> ...
```

The service's environment variables live on the task definition and are
**not** re-sent on every update, so a redeploy cannot silently drop them.

### 1.3 Runtime request path

```
browser
  │  https://bu-28531a874a7140ab85cd8fe102fba400.ecs.ap-south-1.on.aws/shop/login/
  ▼
┌─ ALB (internet-facing) ──────────────────────────────┐
│  • TLS terminated here (ACM cert for the .on.aws name)│
│  • health check → GET /healthz → Host: 172.31.x.x    │
│     → HealthCheckHostMiddleware rewrites the Host     │
│     → Django 200 {"status":"ok"} → target = healthy   │
│  • host rule: unknown Host → 404 (no rule matches)    │
└───────────────┬──────────────────────────────────────┘
                │ HTTP :8000 (ALB → container)
                ▼
┌─ Fargate task ───────────────────────────────────────┐
│  gunicorn → Django                                  │
│    ├─ ALLOWED_HOSTS check (400 on a bad Host)         │
│    ├─ CSRF_TRUSTED_ORIGINS → 403 without a token      │
│    ├─ DATABASE_URL ──────────────┐                    │
│    └─ AWS_ACCESS_KEY_ID ────────┤                    │
└────────────────────────────────┼────────────────────┘
                                 │
        ┌────────────────────────┴─────────────────┐
        ▼                                          ▼
┌─ Supabase Postgres ────────┐        ┌─ Supabase Storage ─┐
│  pooled :6543, SSL         │        │  S3-compatible API  │
│  (IPv4-only host, so the   │        │  bucket:            │
│   direct db.* endpoint     │        │  butterflydesinger  │
│   is NOT used)             │        └─────────────────────┘
└────────────────────────────┘
```

### 1.4 Where the state lives

| What | Value |
|---|---|
| Public URL | `https://bu-28531a874a7140ab85cd8fe102fba400.ecs.ap-south-1.on.aws` |
| Service | `butterfly-designer` in cluster `default` |
| Image | `558948635923.dkr.ecr.ap-south-1.amazonaws.com/butterfly-designer` |
| Task definition | `default-butterfly-designer` (auto-numbered) |
| Runtime | 0.5 vCPU / 1 GiB — a `deploy.yml` release input (`cpu` / `memory`), min 1 / max 1 task, port 8000 |
| Health check | HTTP, path `/healthz`, interval 30 s, 5 healthy checks before a task takes traffic |
| Canary | 5% → 3 min bake → 100%, 4xx+5xx alarm → auto rollback |
| Deployment | `developement` → `master` (master is what the build watches) |
| Cost | ≈ $35/month (Fargate ~$18 + ALB ~$16.5 + logs/ECR ~$0.5) — see Part 6 |

---

## Part 2 — GitHub Actions flow

### 2.1 The pipeline

```
┌─ YOUR MACHINE ──────────┐
│ edit → commit → push PR │────►┌─ GITHUB ─────────────────────────────────┐
└─────────────────────────┘     │ PR opened                               │
                                │   └─ tests.yml (runs the 110 tests)     │
                                │      ● green ● → merge allowed          │
                                │      ● red   ● → blocked                 │
                                │                                           │
                                │ merge to master                          │
                                │   └─ build.yml (automatic)                │
                                │      ├─ checkout commit                   │
                                │      ├─ OIDC assume role ────────────────┼─┐
                                │      ├─ docker build (Linux runner)        │ │
                                │      └─ push to ECR  latest + <sha>       │ │
                                │                                           │ │
                                │ dispatch deploy.yml (manual)              │ │
                                │   ├─ 1. run migrate one-off task ─────────┼─┼─┐
                                │   └─ 2. update service by <sha> tag ──────┼─┼─┤
                                └───────────────────────────────────────────┼─┼─┼─┘
                                                                      │ │ │
                     ┌───────────────────────────────────────────────┘ │ │
                     ▼                                                 │ ▼
              ┌─ ECR ─────────┐                             ┌─ Fargate task ──┐
              │ latest        │                             │  new image      │
              │ sha256:<git>  │                             │  canary 5% →    │
              └───────────────┘                             │  3 min → 100%   │
                     ▲                                       └─────────────────┘
                     └───────────────────────────────────────────────┘
                                ▲
                     if 4xx+5xx > 1% for 2 datapoints
                     → automatic rollback to the previous task
```

### 2.2 The three workflows

All three live in `.github/workflows/`.

**`tests.yml`** — the quality gate, on every PR:

| Step | Action |
|---|---|
| environment | `DATABASE_URL` empty → `core/settings.py` falls back to SQLite, so no database service is needed. A dummy `DJANGO_SECRET_KEY` is required because settings.py fails closed without one. |
| install | `astral-sh/setup-uv` pinned to the repo's uv version, then `uv sync --frozen` |
| check | `uv run --frozen python manage.py check` |
| test | `uv run --frozen python manage.py test` — the 110-test Django suite, ~2 min |

**`build.yml`** — automatic on push to `master`:

| Step | Action |
|---|---|
| authenticate | `aws-actions/configure-aws-credentials` with `role-to-assume: .../github-actions-deploy` via **OIDC** — no access keys in GitHub |
| build | `docker/build-push-action` on a Linux runner (matches Fargate), pushed through the ECR credentials from `aws-actions/amazon-ecr-login` |
| publish | tags `latest` **and** `<commit-sha>` — the SHA tag is what you deploy and roll back to |

**`deploy.yml`** — manual (`workflow_dispatch`), picks a tag:

| Step | Action |
|---|---|
| 1. validate | reject `latest`; check `cpu`/`memory` are sizes Fargate actually offers |
| 2. migrate | `aws ecs run-task` with `command: ["migrate","--noinput"]` against the **new** image, then wait for the task and require exit **0** |
| 3. release | `aws-actions/amazon-ecs-deploy-express-service` with the chosen tag |

Migrations run **before** the code shifts, so the canary never serves an app
whose schema does not exist yet — and because step 2 blocks on the task's exit
code, a failed migration fails the workflow and the service is never updated.
On failure the step also prints the container log, since the Django traceback
lives there and `stoppedReason` never carries it.

The `migrate` input (default `true`) exists for one case: rolling back to an
older image, whose migrations are already applied and must not be replayed.
**Migrations must stay additive** — the previous containers keep serving during
the gap between step 2 and the rollout.

### 2.3 What the Actions flow buys

| Property | Why it matters |
|---|---|
| Reproducible artifact | Built on a clean Linux runner, not a laptop with a dirty tree |
| Build parity | A Linux runner is much closer to Fargate than a Windows workstation |
| Audit trail | Image tagged with the commit SHA — you always know which commit is live |
| Human step becomes a button | Merge = image built; deploy = one dispatch, one tag, one rollback path |
| Quality gate | 110 Django tests run on every PR, before any image is built |
| No static keys in CI | OIDC issues short-lived credentials, scoped to the repo |

### 2.4 The `workflow` scope

Pushing anything under `.github/workflows/` requires the `workflow` scope on
your GitHub token. Without it GitHub rejects the push outright:

```
refusing to allow an OAuth App to create or update workflow
'.github/workflows/build.yml' without 'workflow' scope
```

Grant it with:

```powershell
gh auth refresh -h github.com -s workflow     # browser: paste the code at
                                              # https://github.com/login/device
```

Check it landed with:

```powershell
gh auth status | Select-String "Token scopes"   # must contain 'workflow'
```

### 2.5 Release control

Merging to `master` **builds** an image. It does **not** deploy it. The
release is a separate, deliberate act.

#### What is a SHA?

**SHA = Secure Hash Algorithm** — a 40-character fingerprint of a piece of
content, here a git commit. `a7c2941aa02d295d3a1751eec3594e4c33824968` is the
SHA of the current `master` commit.

It matters for three reasons:

| Where | What the SHA identifies |
|---|---|
| **git** | exactly one commit. `git log`, `git show <sha>`, `git checkout <sha>` |
| **ECR** | exactly one image build. `build.yml` pushes the image twice: as `latest` **and** as `<commit-sha>` |
| **the deploy** | exactly one artifact going live, and the one you roll back to |

A tag like `latest` is a **moving pointer** — it means "whatever was built
last", which changes with every merge. A SHA is a **fixed address**. That is
the whole difference, and it is why you deploy a SHA:

```
latest  ->  "the newest thing someone pushed"   (ambiguous, changes under you)
23397a31...  ->  "the image built from commit 23397a31"  (permanent, provable)
```

The image digest (`sha256:de637c9e...`) is the same idea one level down: it
fingerprints the image **bytes** rather than the source, so it is still exact
even if the tag is moved. Deploying by digest is the strictest form:

```powershell
--primary-container image=558948635923.dkr.ecr.ap-south-1.amazonaws.com/butterfly-designer@sha256:de637c9e...
```

#### The ruleset on `master` (`Merge_deploy`)

| Rule | Effect |
|---|---|
| `deletion` | the branch cannot be deleted |
| `non_fast_forward` | no force-push — history is append-only |
| `pull_request` | every change arrives through a PR; **no direct pushes** |
| `required_status_checks` | the `Django test suite` check must pass before merging |
| `required_review_thread_resolution` | resolve all review conversations first |

Two settings exist for a reason:

* **`required_approving_review_count: 0`** — a single-owner repository cannot
  satisfy a count of 1, because GitHub does not let you approve your own pull
  request. The PR requirement is kept; the approval requirement is not.
* **`bypass_mode: pull_request`** — the owner can merge without waiting for
  an approval, but still **through a PR**. With `always`, a bypass actor could
  push straight to `master` and the ruleset would be decorative.

The ruleset targets `~DEFAULT_BRANCH`, which for this repository is
`master`.

#### Shipping several features together

```
feature-A ──┐
feature-B ──┼──► developement ──(one merge, when you choose)──► master
feature-C ──┘        │                                              │
                     │ every PR tested by tests.yml                 │ build.yml
                     └──────────────────────────────────────────────┘ builds ONE
                                                                           image from
                                                                           that commit
```

`master` stays frozen while you accumulate features. When you are ready,
merge `developement` → `master` **once**: one image, one canary, all three
features live together. Each feature branch also produced its own SHA-tagged
image, so they can be shipped one at a time instead if you prefer.

#### The release step

```powershell
# what images exist, and which SHA each one is
aws ecr list-images --repository-name butterfly-designer --region ap-south-1

# release the one you want
gh workflow run deploy.yml --ref master -f image_tag=a7c2941aa02d295d3a1751eec3594e4c33824968
```

`deploy.yml` **refuses** the tag `latest` outright, because a moving tag is
not a release decision.

---

## Part 3 — Identities and credentials

| Identity | Type | Holds | Used for |
|---|---|---|---|
| `gh` OAuth token | GitHub token | `repo`, `workflow`, `admin:public_key`, `gist`, `read:org` | `git push`, PRs, `gh` CLI |
| AWS root (CLI default profile) | IAM user | full account | ⚠️ currently used for every deploy |
| `butterfly-designer` | IAM user | inline `ecs-deploy` + inline `s3-butterfly-only` + managed `ButterflyConsoleRead`; console login | console access; **no access key yet** |
| `github-actions-deploy` | IAM role | inline `ecs-deploy` | CI → assumed via OIDC, no stored keys |
| `ecsTaskExecutionRole` | IAM role | `AmazonECSTaskExecutionRolePolicy` | ECS pulling the image + writing logs |
| `ecsInfrastructureRoleForExpressServices` | IAM role | `AmazonECSInfrastructureRoleforExpressGatewayServices` + inline `express-mode-extra` | Express Mode creating ALB / ACM / SG / autoscaling |

**Users** hold credentials you can leak. **Roles** are assumed by AWS, so no
long-lived secret exists. Get off root by giving `butterfly-designer` an
access key and using `--profile butterfly-designer`.

The policies are versioned in `infra/`:

| File | Attached to | Why |
|---|---|---|
| `infra/ecs-deploy-policy.json` | `github-actions-deploy` role **and** the `butterfly-designer` user | ECR push + Express service API + `iam:PassRole` + logs |
| `infra/express-mode-extra-policy.json` | `ecsInfrastructureRoleForExpressServices` role | the permissions the AWS-managed infrastructure policy does **not** grant |
| `infra/console-read-policy.json` | `butterfly-designer` user, as the **managed** policy `ButterflyConsoleRead` | read-only ECS / logs / autoscaling / infrastructure calls, so the AWS console renders |
| `infra/ecr-lifecycle-policy.json` | the ECR repository | keep the 5 most recent tagged releases (rollback targets) and expire untagged images after 30 days |

`console-read` is a **managed** policy rather than an inline one: its document
is 2078 bytes and IAM caps inline user policies at 2048. Managed policies
allow 6144.

`infra/setup-aws-deploy-role.ps1` creates the whole set idempotently.

---

## Part 4 — Gotchas discovered while setting this up

Each of these cost real time; they are here so nobody repeats them.

**1. App Runner is closed to new customers (2026-04-30).**
`aws apprunner list-services` → `SubscriptionRequiredException`. It was the
"connect GitHub → pick branch → deploy" service, and it is gone for accounts
created after the cutoff. ECS Express Mode is the replacement, which is why
the pipeline has a build step at all.

**2. `uv` installs to a new location.**
The installer moved from `/root/.cargo/bin` to `/root/.local/bin`, so the
Dockerfile's `ENV PATH` made `RUN uv sync --frozen` fail with
`uv: not found`. Fixed with an explicit `ENV UV_INSTALL_DIR=/opt/uv`.

**3. The AWS-managed infrastructure policy is not sufficient.**
`AmazonECSInfrastructureRoleforExpressGatewayServices` does not grant
`ec2:DescribeAccountAttributes`, `ecs:DescribeServices`, `ecs:UpdateService`,
`cloudwatch:DescribeAlarms` or `iam:PassRole` — all of which Express Mode
calls. The ALB creation fails with a masked *"An unknown error occurred"*.
The supplementary inline policy `express-mode-extra` fixes it.

**4. The load balancer's health check breaks Django's Host check.**
The ALB probes the target with `Host: 172.31.16.84:8000` — the container's
private IP, which changes on every task replacement, so it can never be
listed in `ALLOWED_HOSTS`. Django's `CommonMiddleware` calls
`request.get_host()` on **every** request, so the probe gets
`400 DisallowedHost` → target unhealthy → the ALB answers `503` to real
traffic. `core.middleware.HealthCheckHostMiddleware` rewrites the Host for
`/healthz` only; it must be **first** in `MIDDLEWARE`.

**5. The public hostname is generated, not the service name.**
The endpoint is `bu-<hash>.ecs.ap-south-1.on.aws`, not
`<service-name>.ecs.ap-south-1.on.aws`. `ALLOWED_HOSTS` and
`CSRF_TRUSTED_ORIGINS` must carry the generated name, and the ACM
certificate is issued for it.

**6. Use the Supabase pooler, not the direct endpoint.**
`db.<ref>.supabase.co` is IPv6-only and Fargate has no IPv6 egress. Use
`aws-1-ap-south-1.pooler.supabase.com:6543`. Port 6543 (transaction mode) is
safe here because the project uses **psycopg2**, which binds parameters
client-side and never uses server-side prepared statements.

**7. Line endings break the container.**
With `core.autocrlf=true` a Windows checkout gives `entrypoint.sh` a CRLF
shebang, and the container dies with `exec format error`. `.gitattributes`
forces LF for `*.sh` and `Dockerfile`.

**8. `migrate` must not run on boot.**
Express Mode canary deployments start two containers at once; concurrent DDL
against Postgres is unsafe. Migrations run as a one-off task, and
`entrypoint.sh` keeps a passthrough so a task override reaches
`manage.py migrate`. The one-off task is launched by `deploy.yml` itself
(step 2) and blocks the release on a non-zero exit.

**9. The AWS CLI cannot run in a non-interactive shell.**
Its colour module aborts with *"Found xterm-256color, while expecting a
Windows console"*. Use `boto3` from the project venv for anything scripted
(it is already a dependency) — same APIs, no console requirement.

---

## Part 5 — Deploy checklist

Before any release:

- [ ] Migrations applied — `deploy.yml` step 2 runs the one-off task and fails the release on a non-zero exit (set `migrate: false` only when rolling back)
- [ ] Image pushed and tagged (`<commit-sha>`)
- [ ] Service updated by digest, not by `latest`
- [ ] `GET /healthz` → `200 {"status": "ok"}`
- [ ] Target group reports `healthy`
- [ ] `/shop/login/` renders and a login POST succeeds
- [ ] CloudWatch logs free of `DisallowedHost`, `CSRF verification failed`

To roll back: deploy the previous `<commit-sha>` tag. Migrations are
forward-only, so prefer additive migrations so a code rollback stays safe.

---

## Part 6 — Cost control

The ALB bills hourly whether or not traffic arrives, so a forgotten service
burns credits quietly. The bill is ~99.8% idle capacity: measured over the
first days of production the traffic was ~70 requests/day and ~400 KB/day,
with the task averaging **0.3% CPU** and **~63 MB of memory**. Nothing in this
stack is billed per request.

Safeguards, in the order they pay off:

* **Right-size the task** — `deploy.yml` exposes `cpu` / `memory` as release
  inputs (defaults `512` / `1024`). Dropping 1 vCPU / 2 GB → 0.5 vCPU / 1 GB
  cuts the Fargate line from ~$36/mo to ~$18/mo with no user-visible change.
* **Budget alarm** — Billing → Budgets → Create → monthly threshold → email
  (the billing APIs do not run in a non-interactive shell, so use the console).
* **ECR lifecycle policy** — `infra/ecr-lifecycle-policy.json`, applied to the
  `butterfly-designer` repository. It keeps the 5 most recent tagged releases
  so rollback targets always exist, and expires untagged images after 30 days.
  buildx pushes an OCI image *index* plus its child manifests, so most
  untagged entries in the repository are normal and the image a task is
  running may itself be untagged — hence the generous window. ECR refuses to
  expire a manifest that a live tagged index still references, so a tagged
  release cannot be broken by this rule.
* **Scale to zero** — read the warning below first.
* **Full teardown** — `aws ecs delete-express-gateway-service --service-arn ...`
  plus deleting the ECR repository and the IAM roles.

### What a task actually costs

| Task size | Fargate /mo | Total with ALB | $200 credit lasts |
|---|---|---|---|
| 1 vCPU / 2 GiB *(as provisioned)* | ~$36.04 | ~$52.6 | ~3.8 months |
| **0.5 vCPU / 1 GiB** *(deploy default)* | ~$18.02 | **~$34.6** | **~5.8 months** |
| 0.25 vCPU / 0.5 GiB | ~$9.01 | ~$25.6 | ~7.8 months |
| 0.5 vCPU / 1 GiB, scaled to zero | ~$0–2 | ~$17–19 | ~10–12 months |

Fargate = 730 h × (vCPU × $0.04048 + GB × $0.004445). ALB = 730 h × $0.0225
plus ~$0.13/mo of LCU at current traffic. CloudWatch Logs and ECR storage are
rounding errors. Rates are us-east-1; Mumbai runs ~5–10% higher — enable Cost
Explorer (Billing → Cost Explorer → Enable) for exact figures, since the cost
APIs return `AccessDenied` until you do. Note that **$16.43/mo of ALB is the
floor** inside Express Mode: it bills whether or not anyone visits.

### ⚠ Before you scale to zero

`--scaling-target minTaskCount=0,maxTaskCount=1` (or the action's
`min-task-count: 0`) stops the Fargate task when idle. Three things to know
first:

1. **The ALB still bills ~$16/mo**, so that becomes the floor rather than the
   saving.
2. **The first request after idle returns 503.** With no healthy target the
   ALB answers immediately; the task then cold-starts (pull image from ECR →
   `collectstatic` → gunicorn), roughly 20–45 s, after which a retry works.
3. **The scale-out metric must be `REQUEST_COUNT_PER_TARGET`.** The service
   currently scales on `AVERAGE_CPU` at 60%, and with zero tasks running there
   is no CPU metric to scale out on — the service can sit at zero indefinitely.
   Change `auto-scaling-metric` in the same dispatch that sets
   `min-task-count: 0`.

Test the scale-*back up* path before relying on it, and keep the restore
command ready:
`aws ecs update-express-gateway-service --service-arn <arn> --scaling-target minTaskCount=1,maxTaskCount=1`.
