from django.db import models


class PaymentMode(models.TextChoices):
    CASH = "cash", "Cash"
    UPI = "upi", "UPI"
    CARD = "card", "Card"
    OTHER = "other", "Other"


class PaymentKind(models.TextChoices):
    """Why the money came in, and what it was against.

    Straight from the client: "paid a partial amount one each delivery and
    they may give any advance while given a order". Those are two different
    events in the life of an order -- one happens before any cloth is cut,
    the other can happen several times at hand-over -- so the ledger records
    which one it was.

    All three count towards the balance identically, because the balance is
    simply total minus everything received. The kind is not a second
    arithmetic; it is the shop's own memory of what a payment was for.
    """

    ADVANCE = "advance", "Advance"
    DELIVERY = "delivery", "Against delivery"
    ADJUSTMENT = "adjustment", "Adjustment"


class Payment(models.Model):
    order = models.ForeignKey("orders.Order", on_delete=models.CASCADE, related_name="payments")
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    mode = models.CharField(max_length=10, choices=PaymentMode.choices, default=PaymentMode.CASH)
    # Which of the events above this money was for. Defaults to Advance so
    # that recording an order-stage payment -- the case the form used to
    # describe and the only case the old rows describe -- stays correct
    # without any backfill judgement call.
    kind = models.CharField(max_length=12, choices=PaymentKind.choices, default=PaymentKind.ADVANCE)
    # Why a nullable FK to the delivery, and why only a DELIVERY payment may
    # carry one. "Paid a partial amount one each delivery" ties money to a
    # hand-over; an advance has nothing to tie to, and letting an advance
    # name a delivery would let one hand-over absorb two customers' cash.
    # SET_NULL rather than PROTECT: deleting a mis-entered delivery record
    # must not erase money that was genuinely received -- the payment keeps
    # its amount and note and simply stops naming the visit.
    delivery = models.ForeignKey(
        "orders.Delivery",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="payments",
    )
    note = models.CharField(max_length=255, blank=True)
    recorded_by = models.ForeignKey(
        "auth.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="payments_recorded"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.order.order_number}: {self.amount} ({self.get_kind_display()})"
