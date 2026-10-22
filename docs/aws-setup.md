# AWS Setup & Configuration

A complete inventory of the AWS resources behind the Butterfly Shop Manager.
This is a *reference snapshot*, not a plan — see `flow.md` for how releases
run and `deployment-plan.md` for the original runbook.

**Account** `558948635923` · **Region** `ap-south-1` (Asia Pacific, Mumbai)
**Live URL** <https://bu-28531a874a7140ab85cd8fe102fba400.ecs.ap-south-1.on.aws>
**Snapshot taken** 2026-10-22 (after the Tier 1 right-sizing release)

---

## 1. How a request flows

```
Browser
  │  HTTPS :443  (SNI: bu-<hash>.ecs.ap-south-1.on.aws)
  ▼
┌─ Application Load Balancer ─────────────────────────────────────────────┐
│  ecs-express-gateway-alb-8d471870                                      │
│  • internet-facing, 3 AZs, 3 public IPv4 addresses                    │
│  • TLS terminates here, ACM cert (EC-prime256v1, ISSUED)              │
│  • weighted forward: one target group at 100%, the other at 0%         │
│  • health check: GET /healthz every 30 s, expects 200                  │
│  • default rule: fixed 404 (no host match)                             │
│  • single listener on 443 — there is no :80 listener                   │
└────────────────────────────────┬───────────────────────────────────────┘
                                 │ HTTP :8000  (inside the VPC — no cost)
                                 ▼
┌─ Fargate task ──────────────────────────────────────────────────────────┐
│  default-butterfly-designer:5  ·  512 vCPU-units / 1024 MiB            │
│  entrypoint.sh → collectstatic → gunicorn :8000 → Django               │
│  no container health check — health is decided by the ALB              │
└──────────────┬─────────────────────────────────────┬───────────────────┘
               │ postgres :6543 (IPv4, SSL)          │ S3-compatible API
               ▼                                     ▼
     Supabase Postgres pooler               Supabase Storage (bucket
                                             `butterflydesinger`)
```

Note the **IPv4-only** Postgres endpoint: Fargate tasks get no IPv6 egress, so
the Supabase *pooler* endpoint (`*.pooler.supabase.com:6543`) is used rather
than the direct hostname, which resolves to IPv6 first.

---

## 2. Compute — ECS Express Mode service

Express Mode is a managed ECS path: AWS provisions the service, ALB, ACM
certificate and autoscaling as one unit. We do not create those pieces
individually; we pass a service name, image, roles and container port to the
`amazon-ecs-deploy-express-service` action.

| Property | Value |
|---|---|
| Service | `butterfly-designer` in cluster `default` |
| Created | 2026-10-20 23:26:58 IST |
| State | ACTIVE · desired 1 / running 1 / pending 0 |
| Resource management | `ECS` · scheduling `REPLICA` · AZ rebalancing **ENABLED** |
| Service revision | `7581968923165874400` |
| Launch type | `FARGATE`, platform `1.4.0`, network mode `awsvpc` |
| **Task role** | **none** — the container has no AWS API access |
| ECS Exec | **disabled** (`enableExecuteCommand: False`) |

### Deployment behaviour

| Setting | Value |
|---|---|
| Strategy | **CANARY** — 5% of traffic → 3 min bake → 100% |
| Approlling | max 200% / min healthy 100% |
| Circuit breaker | **enabled with rollback** |
| Rollback alarm | `default/butterfly-designer/RollbackAlarm` (threshold 1.0, `GreaterThanThreshold`, 3 evaluation periods, state `OK`, no actions — the circuit breaker does the work) |
| Canaries use | two target groups, weights swapped between releases |

### Autoscaling

| Setting | Value |
|---|---|
| Min / max tasks | **1 / 1** (so it never scales — the policy is effectively inert) |
| Policy type | TargetTracking |
| Metric | `ECSServiceAverageCPUUtilization` |
| Target | **60%** |
| Cooldowns | none set |
| Scheduled scaling | none |
| Execution role | `AWSServiceRoleForApplicationAutoScaling_ECSService` |

> **Why min=max=1:** at ~70 requests/day a single task is enough, and a fixed
> count keeps the cost predictable. The 60% target cannot trigger anything
> while max is 1. See §11 for the scale-to-zero caveat.

---

## 3. Task definition

`default-butterfly-designer:5` (revision 5, registered 2026-10-22 09:15:12)

| Field | Value |
|---|---|
| **CPU / Memory** | **512 (0.5 vCPU) / 1024 MiB (1 GiB)** |
| Container | `Main`, essential, port `8000/tcp` |
| Image | by digest: `558948635923.dkr.ecr.ap-south-1.amazonaws.com/butterfly-designer@sha256:c6d25440…` |
| Container health check | none |
| Logs | `awslogs` → `/aws/ecs/default/butterfly-designer-4f18`, stream prefix `ecs` |
| Mount points / Linux params | none |
| **Environment variables** | **10, all plain text** |
| **Secrets** | **0** |

### Environment variables

| Name | Notes |
|---|---|
| `DATABASE_URL` | Supabase pooled Postgres, `:6543`, SSL |
| `DJANGO_SECRET_KEY` | ⚠️ plain text in the task definition |
| `DJANGO_DEBUG` | `false` |
| `ALLOWED_HOSTS` | the `bu-<hash>.ecs.ap-south-1.on.aws` domain |
| `CSRF_TRUSTED_ORIGINS` | `https://bu-<hash>…on.aws` |
| `AWS_ACCESS_KEY_ID` | ⚠️ plain text — Supabase Storage key |
| `AWS_SECRET_ACCESS_KEY` | ⚠️ plain text — Supabase Storage secret |
| `AWS_S3_REGION_NAME` | `ap-south-1` |
| `AWS_S3_ENDPOINT_URL` | Supabase S3-compatible endpoint |
| `AWS_STORAGE_BUCKET_NAME` | `butterflydesinger` |

These are deliberately stored **on the service**, not in `.github/workflows`,
so a redeploy cannot silently overwrite them (see the note at the top of
`deploy.yml`).

### Running task

| | |
|---|---|
| Task id | `f05da53e867f44…` |
| Started | 2026-10-22 09:16:33 IST |
| ENI subnet | `subnet-0c3224681fe6f4964` (ap-south-1b) |
| Private IP | `172.31.30.140` |
| Measured | CPU **0.05% avg**, memory **5.57%** (~57 MB of 1,024 MiB) |

---

## 4. Network

| | |
|---|---|
| VPC | `vpc-0cbd1e5c894fc7d9b` — the **default VPC**, `172.31.0.0/16` |
| Subnets | 3, **all public** (`MapPublicIpOnLaunch: True`): `subnet-0c3224681fe6f4964` (ap-south-1b, 172.31.0.0/20) · `subnet-0dcb582aab2b0200f` (ap-south-1c, 172.31.16.0/20) · `subnet-00c09a8f2fdfdf3ed` (ap-south-1a, 172.31.32.0/20) |
| Route table | `rtb-0ee7cd1cb56c6dd29` → `local` + `0.0.0.0/0` → `igw-054813a4d1222c6da` |
| Internet gateway | `igw-054813a4d1222c6da`, attached |
| **NAT Gateway** | **none** — there is no private subnet and no NAT bill. Tasks egress through the internet gateway directly. |

### Security groups

| Group | Ingress | Egress |
|---|---|---|
| `sg-0d7b2baa46c4620bb` (task) | tcp **8000 from the ALB SG only** | all |
| `sg-0dfa148e08ac21568` (ALB) | tcp **80 and 443 from 0.0.0.0/0** | self, and tcp 8000 → task SG |
| `sg-0869b8dc566d424cc` (default) | self only | all — **unused** |

The container port is **never** reachable from the internet: only the ALB may
open 8000.

> The ALB allows inbound **80**, but there is no listener on 80, so `http://`
> does not redirect to HTTPS — the connection is simply refused. Worth adding a
> `:80 → :443` redirect if you ever want the bare-IP/direct form to work.

---

## 5. Load balancing

| | |
|---|---|
| ALB | `ecs-express-gateway-alb-8d471870` · `application` · **internet-facing** · `active` · created 2026-10-20 |
| ARN | `arn:aws:elasticloadbalancing:ap-south-1:558948635923:loadbalancer/app/ecs-express-gateway-alb-8d471870/0a57a68a4b4ae0aa` |
| DNS | `ecs-express-gateway-alb-8d471870-1079938700.ap-south-1.elb.amazonaws.com` |
| Availability zones | ap-south-1a, ap-south-1b, ap-south-1c |
| **Public IPv4** | **3**, all `ServiceManaged: alb` — `13.205.121.23`, `13.232.8.106`, `13.234.52.187` |

### Listener (443 / HTTPS)

- Certificate: `arn:aws:acm:ap-south-1:558948635923:certificate/210b667e-56ba-4d30-a99b-59a27a434bd2`
  - Domain `bu-28531a874a7140ab85cd8fe102fba400.ecs.ap-south-1.on.aws`, `AMAZON_ISSUED`, status `ISSUED`, key `EC-prime256v1`
- Rule priority **44990**: weighted forward
  - `ecs-gateway-tg-675917a0e25cacd69` → **weight 100** (currently serves all traffic)
  - `ecs-gateway-tg-16c4daf83088efb70` → **weight 0** (parked for the next canary)
  - stickiness: disabled
- Default rule: `fixed-response` **404** `Not Found` (`text/plain`)

### Target groups

Both are otherwise identical:

| Setting | Value |
|---|---|
| Protocol / port | `HTTP 8000` |
| Health check path | `/healthz` |
| Interval / timeout | **30 s** / 5 s |
| Thresholds | **5 healthy** / **2 unhealthy** |
| Matcher | `HttpCode 200` |

So a freshly launched task needs **5 consecutive passes at 30 s = ~2.5 minutes**
to be considered healthy, and 2 failures (60 s) before it is pulled.

---

## 6. Container registry (ECR)

| | |
|---|---|
| Repository | `butterfly-designer` · `558948635923.dkr.ecr.ap-south-1.amazonaws.com/butterfly-designer` |
| Created | 2026-10-19 23:22:38 IST |
| Encryption | `AES256` |
| Scan on push | **disabled** |
| Images | 18 total (4 tagged, 14 untagged), **~2.17 GB** (~$0.21/month) |
| Lifecycle policy | **applied** — see below |

Tags are **commit SHAs** (`<github.sha>`) plus `latest`. `deploy.yml` refuses
`latest`, so a SHA is always required for a release.

Lifecycle policy (`infra/ecr-lifecycle-policy.json`):

1. keep the **5 most recent tagged** releases, expire older tagged images
2. expire **untagged** images after **30 days**

Why 30 days: buildx pushes an OCI image *index* plus its child manifests, so
untagged entries in this repo are normal, and the image a task is running may
itself be untagged. AWS additionally refuses to expire a manifest that a live
tagged index references, so a tagged release cannot be broken by this rule.

---

## 7. Identity

| Principal | Type | Grants |
|---|---|---|
| `github-actions-deploy` | role | assumed by GitHub Actions via OIDC. Inline `ecs-deploy`: ECR push, Express service management, `iam:PassRole` to the two ECS roles |
| `ecsTaskExecutionRole` | role | AWS-managed `AmazonECSTaskExecutionRolePolicy` — pull image, write logs |
| `ecsInfrastructureRoleForExpressServices` | role | AWS-managed `AmazonECSInfrastructureRoleforExpressGatewayServices` — ALB, ACM, SG, autoscaling |
| `AWSServiceRoleForApplicationAutoScaling_ECSService` | role | created by AWS for autoscaling |
| `AWSServiceRoleForECS` | role | created by AWS |
| `butterfly-designer` | **user** | inline `ecs-deploy` + `s3-butterfly-only`; reads needed for console login (`infra/console-read-policy.json`). **0 access keys created** |
| root | user | the only local CLI credential (`~/.aws/credentials` → `[default]`) |

There is **no static AWS key in GitHub** — CI assumes the role by OIDC.

### Gap — CI role drift (2026-10-22)

The deploy workflow logged this during the 2026-10-22 release:

```
not authorized to perform: ecs:ListServiceDeployments … because no
identity-based policy allows the ecs:ListServiceDeployments action
```

The deploy still succeeded (the action issues the update first), but the
workflow **cannot watch the rollout to completion**, so it reports success
without confirming the canary finished.

Comparing `infra/ecs-deploy-policy.json` against the policy **actually on the
role** found two distinct drifts:

| Action | In repo file | On live role | Why it is needed |
|---|---|---|---|
| `ecs:ListServiceDeployments` | ❌ was missing | ❌ | lets `deploy.yml` follow the canary to `COMPLETED` |
| `ecs:ListClusters` / `DescribeClusters` | ✅ added in PR #37 | ❌ | shipped in the file but the setup script was never re-run |
| `logs:GetLogEvents` | ✅ added in this PR | ❌ | lets the new migrate step print the Django traceback when a migration fails |

**The file is fixed. Applying it is a separate step** — the policy only reaches
AWS when `infra/setup-aws-deploy-role.ps1` runs (it does `put-role-policy` on
`github-actions-deploy`). Until then CI still runs with the old 15-action
policy.

---

## 8. Observability

| | |
|---|---|
| Log group | `/aws/ecs/default/butterfly-designer-4f18` · 0 MB stored · **retention: never** |
| ECS metrics | `AWS/ECS` — `CPUUtilization`, `MemoryUtilization` |
| ALB metrics | `AWS/ApplicationELB` — `RequestCount`, `NewConnectionCount`, `ActiveConnectionCount`, `ProcessedBytes`, `ConsumedLCUs` |
| Alarm | `default/butterfly-designer/RollbackAlarm` (deployment circuit breaker) |
| **Cost Explorer** | **not enabled** — `ce:GetCostAndUsage` returns `AccessDenied` |

Log group has **no retention set**, so it will grow without bound. Low impact
today (0 MB) but worth a 30-day expiry.

---

## 9. What we do *not* run

No NAT Gateway · no Route 53 (the endpoint is AWS-managed) · no EKS · no EC2 ·
no RDS · no S3 of our own (Supabase Storage instead) · no CloudFront · no
WAF · no private subnets · no task role · no Secrets Manager · no CloudFormation.

Everything above is one ECS service plus the ALB Express Mode created for it.

---

## 10. Monthly run rate (2026-10-22, post-Tier-1)

| Line | Basis | Monthly |
|---|---|---|
| Fargate | 730 h × (0.5 × $0.04048 + 1 × $0.004445) | **$18.02** |
| ALB hourly | 730 h × $0.0225 | **$16.43** |
| Public IPv4 | 3 × 730 h × $0.005 | **$10.95** |
| ALB LCU | max-dimension ≈ 0.00215 LCU → ~1.6 LCU-h | ~$0.01 |
| ECR storage | ~2.17 GB × $0.10/GB-mo (shrinks after lifecycle expiry) | ~$0.21 |
| CloudWatch Logs | 0 MB stored | ~$0.00 |
| **Total** | | **≈ $45.6/mo** |

**$200 of credit ≈ 4.4 months** at this rate.

Before the Tier 1 release (1 vCPU / 2 GiB) the same configuration was
**≈ $63.6/mo** — the right-sizing saves **$18.11/month**.

Two structural facts worth remembering:

- **The ALB + public IPv4 floor is $27.38/month** and it bills whether or not
  anyone visits. That is 60% of the bill and the hardest thing to remove
  inside Express Mode.
- **Nothing here is billed per request.** Measured production traffic is
  ~70 requests/day and ~400 KB/day; the LCU line is about one cent. You are
  paying for capacity, not usage.

---

## 11. Levers if the budget gets tight

Ordered by saving, with the trade-off each one costs:

1. **Right-size the task** — *done*. `cpu` / `memory` are release inputs in
   `deploy.yml`, defaulting to `512` / `1024`. Rollback is a dispatch with
   `cpu: 1024`, `memory: 2048`.
2. **Scale to zero** — sets `min-task-count: 0`. Saves the whole Fargate line
   (~$18/mo) but read `flow.md` Part 6 first: the ALB still bills, the first
   request after idle returns **503** until the task cold-starts, and the
   scale-out metric must be `REQUEST_COUNT_PER_TARGET` — the current
   `AVERAGE_CPU` policy cannot scale *out* from zero.
3. **Fewer availability zones** — one fewer AZ means one fewer public IPv4
   ($3.65/mo), but Express Mode owns the ALB and its subnet list.
4. **Full teardown** — `aws ecs delete-express-gateway-service` plus deleting
   the ECR repository and the IAM roles. Stops all charges immediately.

---

## 12. Verification runbook

Run these after any release (PowerShell — note no `&&`, use `;`).

```powershell
# Task definition: size + env vars survived
uv run python -c "import boto3; td=boto3.client('ecs',region_name='ap-south-1').describe_task_definition(taskDefinition='default-butterfly-designer')['taskDefinition']; print('rev',td['revision'],'cpu',td['cpu'],'mem',td['memory'],'envs',len(td['containerDefinitions'][0]['environment']))"

# Deployments: COMPLETED on the revision you expect
uv run python -c "import boto3; s=boto3.client('ecs',region_name='ap-south-1').describe_services(cluster='default',services=['butterfly-designer'])['services'][0]; print([(d['status'],d['rolloutState'],d['taskDefinition'].split('/')[-1]) for d in s['deployments']], s['runningCount'])"

# Target health
uv run python -c "import boto3; e=boto3.client('elbv2',region_name='ap-south-1'); [print(t['TargetGroupName'],[x['TargetHealth']['State'] for x in e.describe_target_health(TargetGroupArn=t['TargetGroupArn'])['TargetHealthDescriptions']]) for t in e.describe_target_groups()['TargetGroups']]"

# Public health endpoint
curl https://bu-28531a874a7140ab85cd8fe102fba400.ecs.ap-south-1.on.aws/healthz
```

Manual checks that no script can do for you: log in through the browser,
create an order, confirm it appears on the board, and confirm CloudWatch has no
`DisallowedHost` or `CSRF verification failed` entries.
