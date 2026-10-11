# Butterfly Shop Manager — Implementation Plan

Order tracking, billing and analytics for **Butterfly Designer**.
Primary device: 8" tablet (shop counter). Desktop web later via responsive design.

## 1. Problem

The shop needs to track incoming customer orders, advance them through completion
stages, let customers see their order's progress (professional image), and give the
owners real analytics on orders, sales, profit and revenue.

## 2. Locked decisions

| Topic | Decision |
|---|---|
| Placement | New apps inside this repo; public landing page untouched |
| Roles | `admin` (2 partners) and `manager` (shop user). Nothing else. |
| Permissions | All logged-in users do daily ops; `/shop/analytics/` + `/shop/accounts/` are `admin`-only |
| Money | Manager sees & records advance/balance (counter reality) |
| Stack | Django 5.2 + HTMX + Tailwind CDN (matches existing site) |
| DB | Postgres (docker-compose), SQLite fallback in dev. All money = `DecimalField` |
| Auth | Django session auth, 3 seeded accounts, no signup/reset/OTP |
| Customer page | `/track/<uuid-token>/` — no login, milestone chart |
| WhatsApp | **Deferred** (decision after Phase 5). Seam: every stage change goes through `orders/services.py::advance_stage()` and writes `OrderStageHistory` — enabling WhatsApp later is additive, not a refactor |
| Measurements / photos / GST invoices | Not in v1 |
| Language | English |
| Timezone | `Asia/Kolkata` |
| Data migration | None — start fresh |
| PR base branch | `developement` (existing branch spelling) |

## 3. Order lifecycle (stages)

```
measurement → cutting → stitching → fitting → ready → delivered
```

- `cancelled` is an extra terminal state (reason note required)
- Backward move allowed with a required note (e.g. fitting → stitching)
- Every transition records `OrderStageHistory` (from, to, by, at, note)

## 4. Applications

```
core/                     existing settings/urls — minimal edits (INSTALLED_APPS, TIME_ZONE)
accounts/                 profile with role, login/logout, @admin_required decorator, seed command
customers/                Customer CRUD + order history
orders/                   Order, OrderItem, stage machine + history, kanban board, /track/
billing/                  Payment (advance/balance), /shop/dues/
analytics/                admin-only dashboards
templates/shop/           base_shop.html (tablet layout, bottom nav) + all pages
static/shop/              small shop-specific JS/CSS if needed (HTMX init)
```

### Models (v1)

**accounts**

- `Profile` — OneToOne(User), `role` ∈ {admin, manager}
- `seed_users` management command → 3 accounts (usernames/passwords documented at seed time)

**customers**

- `Customer` — name, phone (indexed), notes, created_at, created_by

**orders**

- `Order` — order_number (auto `BD-26-0001`), customer FK, service_type ∈
  {Blouse, Saree Alteration, Bridal, Other}, description, total (Decimal),
  material_cost (Decimal, optional — profit analytics), promised_date (optional),
  stage ∈ lifecycle, track_token (UUID4, unique), notes, created_by, created_at, updated_at
- `OrderItem` — order FK, description, price (for multi-item orders)
- `OrderStageHistory` — order FK, from_stage, to_stage, changed_by, changed_at, note

**billing**

- `Payment` — order FK, amount, mode ∈ {Cash, UPI, Card, Other}, note, recorded_by, created_at
- Balance (computed) = order.total − Σ payments

**analytics** — read-only queries over the above; no new tables in v1

### Routes

| URL | Access | Phase |
|---|---|---|
| `/shop/login/`, `/shop/logout/` | public | 1 |
| `/shop/` order board (kanban by stage + search) | logged in | 2 |
| `/shop/orders/new/`, `/shop/orders/<id>/` | logged in | 2 |
| `/shop/orders/<id>/stage/` (HTMX POST partial) | logged in | 2 |
| `/shop/customers/`, `/shop/customers/<id>/`, new/edit | logged in | 2 |
| `/track/<token>/` public milestone page | anyone with link | 2 |
| `/shop/orders/<id>/payments/`, `/shop/dues/` | logged in | 3 |
| `/shop/analytics/` | **admin only** | 4 |
| `/shop/accounts/` user list/create/toggle | **admin only** | 1 (simple), polished in 4 |

Existing `/` landing, `/review/`, `/admin/` unchanged.

## 5. Phases (one branch each → PR to `developement`)

### Phase 1 — Foundation

- `plan.md` (this file) + Dockerfile uv fix (already uncommitted) included
- New apps skeleton, all models + migrations, `TIME_ZONE=Asia/Kolkata`
- Profile/role, login page, `@login_required` / `@admin_required`
- Seed command creating the 3 accounts
- `templates/shop/base_shop.html` — tablet-first: top bar, bottom nav
  (Orders / Customers / Dues / Analytics-admin-only / Logout), big tap targets
- Tests: role decorator, seed command, profile auto-creation

### Phase 2 — Orders & tracking

- Customer CRUD; Order create/edit (order number auto, token auto)
- Stage machine in `orders/services.py::advance_stage()` (validation, history write)
- HTMX stage buttons → partial swap, no reload
- Kanban board `/shop/` (columns per stage, tap card → detail), search by name/phone/order no
- `/track/<token>/` milestone chart (stage dots + history timestamps, promised date)
- Tests: transition rules, backward+note, cancel, token page no-login, order number format

### Phase 3 — Billing

- Payments add/list on order detail (HTMX), payment modes
- Balance shown everywhere relevant; over/under-payment warning
- `/shop/dues/` list of unpaid balances, sortable
- Tests: balance math, dues query

### Phase 4 — Analytics (admin only)

- `/shop/analytics/`: revenue by day/week/month, profit (Σ total − Σ material_cost),
  orders by stage, service mix, stuck orders (>3 days in same stage),
  top customers by revenue, delivered-vs-open counts
- Simple CSS bar charts (Tailwind divs — no chart lib in v1)
- Tests: stuck-order query, profit calc, admin-only guard returns 403 for manager

### Phase 5 — Polish & decision point

- PWA: `manifest.json` + icons → "Add to Home Screen" on tablet
- Print-friendly order receipt (browser print CSS)
- **WhatsApp decision**: either activate (outbox table + Twilio sender + templates)
  or explicitly drop with client sign-off

## 6. Verification (end of every phase)

```bash
uv run python manage.py check
uv run python manage.py makemigrations --check --dry-run
uv run python manage.py test
```

- Subagent code review (bugs, security, Django best practices) before PR opens
- Manual smoke: `/` unchanged, `/shop/login/` at 800×1280 viewport

## 7. Tablet testing (user perspective)

1. **Dev:** PC + tablet same Wi-Fi → `runserver 0.0.0.0:8000` → tablet browser
   `http://<PC-LAN-IP>:8000/shop/` (allow Python in firewall)
2. **Layout during build:** Chrome DevTools device mode, 800×1280 (Galaxy Tab)
3. **UAT after Phase 5:** deploy → tablet Chrome → "Add to Home Screen" →
   full-screen app; walk through create order → advance stages → payment →
   open `/track/` link as a customer would (share via WhatsApp manually)

## 8. Risks & notes

- Tailwind/HTMX via CDN needs internet on the tablet; if offline support becomes a
  requirement, switch to locally built assets (Phase 5+)
- `db.sqlite3` is tracked in git despite .gitignore — leave untouched in v1
- Gunicorn entrypoint gains `migrate --noinput` in Phase 1
- Production host details needed before Phase 5 UAT (HTTPS for shareable track links)
