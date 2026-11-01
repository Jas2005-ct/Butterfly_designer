# WhatsApp Notifications — Implementation Blueprint

Status: **BLUEPRINT ONLY — not built.** Build starts only after explicit
sign-off (needs a WhatsApp sender account + per-message budget approval).

Goal (from the client): when a customer hands over cloth, they get a
WhatsApp message; on every stage change (measurement → stitching → ready →
delivery) they get another update; the long-term vision is a milestone
chart inside the message thread.

## 1. Where it hooks in (already prepared)

- Every stage change flows through `orders/services.py::advance_stage()`
  and writes an `OrderStageHistory` row. The seam is marked:
  ```python
  # TODO: Future WhatsApp seam — emit event here
  # notifications.emit_stage_changed(order, history)
  ```
- No refactor needed: enabling WhatsApp = adding a `notifications` app
  that subscribes at that one line, plus an outbox table. All Phase 1–4
  code keeps working unchanged.

## 2. Message flow (full working design)

```
Staff taps "Advance to Stitching"
  → advance_stage() writes OrderStageHistory (existing)
  → emit_stage_changed(order, history)      (new, 3 lines)
      → creates NotificationOutbox row: PENDING (new table)
      → returns immediately (staff never waits on WhatsApp)

Background worker (every 30s, management command via cron/systemd):
  → picks PENDING rows oldest-first, LOCKED for this worker
  → renders template for (to_stage) with order context
  → sends via provider API (section 4)
  → SENT (+ provider message id) or FAILED (+ error, retry_count++)
  → retries with backoff: 1 min, 10 min, 1 h, then DEAD (max 3 tries)

Customer receives WhatsApp text (section 3). Track link included in
every message so the milestone chart is one tap away.
```

Why an outbox table instead of sending inline:

- The counter tablet often has flaky internet — inline sending would
  hang stage updates or lose messages silently.
- Retries, audit ("did the customer get told?"), and provider swaps all
  become data problems, not code problems.
- The owner can see a simple outbox log: pending / sent / failed.

## 3. Message templates (one per stage, English + Tamil-ready)

Variables: `{name}`, `{order_number}`, `{stage}`, `{promised_date}`,
`{track_link}`, `{shop_name}`, `{shop_phone}`.

| Stage | Template (utility category) |
|---|---|
| Cloth received (order created) | `Hello {name}! 🦋 {shop_name} received your order {order_number} ({service}). Promised: {promised_date}. Track it live here: {track_link}` |
| Stitching | `Update on {order_number}: 🧵 stitching in progress. Track: {track_link}` |
| Ready | `Great news {name}! ✅ Order {order_number} is READY for delivery. Balance due: ₹{balance}. Track: {track_link}` |
| Delivery | `Thank you {name}! 📦 Order {order_number} delivered. Loved our work? Reply with a rating ⭐⭐⭐⭐⭐` |
| Cancelled | `Update on {order_number}: this order was cancelled ({reason}). Call us at {shop_phone} with questions.` |

There is deliberately **no message for a trial/fitting** — cutting and
fitting were removed as separate stages in `orders/0002_simplify_stages`, so
a stage change now only happens on measurement, stitching, ready and
delivery. Reintroducing a trial message would mean reintroducing the stage.

Rules:

- First message (order created) requires explicit opt-in: a checkbox on
  the order form "Send WhatsApp updates to {phone}" (default ON, stored
  per order as `whatsapp_opt_in`). No opt-in → no messages, ever.
- Every message carries the track link (the milestone chart lives on the
  web page, not in WhatsApp — see section 6 for the image-chart upgrade).
- Ready message includes the live balance (computed at send time, not
  stored in the template).
- Delivered message asks for a rating (feeds the existing review flow).

## 4. Provider options (India, 2026)

| Provider | Type | Approx. cost | Effort | Notes |
|---|---|---|---|---|
| **Twilio WhatsApp API** | BSP (official) | ~₹0.50–0.90/msg + Meta conversation fee | Low — best docs, sandbox testing | Recommended to start. Needs Twilio account + WhatsApp sender approval (1–3 days). |
| **Meta WhatsApp Cloud API** (direct) | Official, self-serve | Meta conversation fee only (~₹0.35–0.70) | Medium — own number + Meta Business verification | Cheapest long-term; more setup. |
| **Gupshup / Interakt / WATI** | Indian BSPs | ₹0.20–0.60/msg + platform fee | Low-Medium | Local support, UPI billing; template approval via their dashboards. |

Recommendation: **Twilio for pilot** (fastest to working messages), behind a
`NotificationChannel` abstraction so a later switch to Meta Cloud API
(cheaper at volume) is config, not surgery:

```python
# notifications/channels.py (to be built)
class NotificationChannel(Protocol):
    def send_text(self, to_phone: str, body: str) -> str: ...  # returns provider id

class TwilioChannel(NotificationChannel): ...
class MetaCloudChannel(NotificationChannel): ...  # later
```

Prerequisites before build: (a) sender account approved, (b) utility
templates approved per stage (marketing templates get rejected — keep
them transactional), (c) monthly budget sign-off (~500–2000 msgs/month
for this shop size ≈ ₹250–1500/month).

## 5. Data model (to be built)

```python
class OutboxStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    SENT = "sent", "Sent"
    FAILED = "failed", "Failed"
    DEAD = "dead", "Dead (retries exhausted)"

class NotificationOutbox(models.Model):
    order = models.ForeignKey("orders.Order", ...)
    history = models.ForeignKey("orders.OrderStageHistory", ...)  # what triggered it
    to_phone = models.CharField(max_length=15)
    body = models.TextField()          # rendered at enqueue time (stable record)
    status = ...default PENDING
    provider = models.CharField(...)   # "twilio" / ...
    provider_id = models.CharField(blank=True)  # for delivery receipts
    error = models.TextField(blank=True)
    retry_count = models.IntegerField(default=0)
    locked_at = models.DateTimeField(null=True)  # worker claim
    created_at / sent_at
```

Plus on `Order`: `whatsapp_opt_in = models.BooleanField(default=True)`.

Worker: `notifications/management/commands/process_outbox.py`
(single-shot; cron every minute or a systemd timer; `--once` flag for tests).

## 6. Milestone-chart-in-WhatsApp (the long-term vision, phase 2 of this)

WhatsApp text can't render progress bars, but two supported upgrades exist:

- **Now (no extra work):** every message links the track page — the chart
  the customer already sees, one tap away. This satisfies 80% of the ask.
- **Later (image message):** render the milestone chart server-side
  (Pillow, same dots as `/track/`) → PNG → send as WhatsApp image with
  caption = stage text. Requires media-URL support on the provider
  (Twilio supports it). Estimated +1–2 days on top of the text flow.

Recommendation: ship text + track-link first, add the chart image only
if customers ask for it.

## 7. Costed rollout plan (for sign-off)

| Step | Owner | Effort |
|---|---|---|
| Approve provider + budget; get sender/template approvals | Client + us | 3–7 days wall time |
| Build `notifications` app (outbox, channel abstraction, Twilio sender, worker, opt-in checkbox, outbox log page) | Us | 3–4 days |
| Sandbox testing (test number) + template approval | Us | 2–3 days |
| Pilot on live orders (1 tailor counter, 1 week), monitor DEAD rate | Shop | 1 week |
| Decide: keep Twilio / move to Meta Cloud / add chart image | Client + us | — |

## 8. What could go wrong (and the answers)

- **Template rejected by Meta:** keep messages transactional (order
  updates), no greetings/promotions in utility templates; the Delivered
  rating ask may need marketing category — fallback: plain thank-you.
- **Customer changed number:** messages fail → DEAD with error; staff
  updates phone on customer record; no code needed.
- **Tablet offline at advance time:** outbox row waits; worker sends when
  back online. History timestamp (not send time) is the source of truth.
- **Spam complaints:** opt-in checkbox + at most ~7 messages per order
  lifetime + no marketing content = safe.
