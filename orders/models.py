import uuid

from django.db import models, transaction
from django.utils import timezone

from customers.models import MeasurementFields


class OrderStage(models.TextChoices):
    # Four stages, as the shop runs them: measurement -> stitching -> ready ->
    # delivery. "Cutting" and "fitting" were removed as separate stages -
    # cutting is part of the stitching work, and a fitting trial is not a
    # stage an order sits in. A data migration folds existing cutting/fitting
    # orders into stitching (orders/0002).
    MEASUREMENT = "measurement", "Measurement"
    STITCHING = "stitching", "Stitching"
    READY = "ready", "Ready"
    # Slug stays "delivered": it is wired through ?tab=delivered, the
    # counts.delivered key and a page of tests. Only the label changed so the
    # board column and stepper read "Delivery" as the shop calls it.
    DELIVERED = "delivered", "Delivery"
    CANCELLED = "cancelled", "Cancelled"


class ServiceType(models.TextChoices):
    BLOUSE = "blouse", "Blouse"
    SAREE_ALTERATION = "saree_alteration", "Saree Alteration"
    BRIDAL = "bridal", "Bridal"
    OTHER = "other", "Other"


class MaterialSource(models.TextChoices):
    """Where the cloth for a line item comes from.

    Straight from the client: some customers hand over their own cloth,
    others buy it at the shop, and one order can contain both.

    MIXED is never stored on an item - an item is exactly one or the other -
    but an order holding both needs a word for it, so Order.material_source
    returns it. That is why the item field below lists only SHOP and
    CUSTOMER rather than using these .choices wholesale.
    """

    SHOP = "shop", "Shop supplies"
    CUSTOMER = "customer", "Customer supplies"
    MIXED = "mixed", "Mixed"


def generate_order_number():
    year = timezone.now().strftime("%y")
    prefix = f"BD-{year}-"
    with transaction.atomic():
        last = (
            Order.objects.select_for_update()
            .filter(order_number__startswith=prefix)
            .order_by("-order_number")
            .values_list("order_number", flat=True)
            .first()
        )
        if last:
            next_num = int(last.rsplit("-", 1)[-1]) + 1
        else:
            next_num = 1
    return f"{prefix}{next_num:04d}"


class Order(models.Model):
    order_number = models.CharField(max_length=20, unique=True, blank=True)
    customer = models.ForeignKey("customers.Customer", on_delete=models.PROTECT, related_name="orders")
    service_type = models.CharField(max_length=20, choices=ServiceType.choices, default=ServiceType.OTHER)
    description = models.TextField(blank=True)
    # Nullable on purpose: the client's own words were "the mentioned amount
    # may varies", so a price is agreed as the job progresses and an order can
    # exist before anyone has settled on one. Default None rather than 0,
    # because 0 reads as "agreed to be free", which is a very different claim
    # from "not agreed yet" - billing.services.order_balance returns None for
    # these rather than 0, and the dues report counts them separately.
    total = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True, default=None
    )
    material_cost = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    promised_date = models.DateField(null=True, blank=True)
    stage = models.CharField(max_length=20, choices=OrderStage.choices, default=OrderStage.MEASUREMENT)
    track_token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        "auth.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="orders_created"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.order_number} — {self.customer.name}"

    def save(self, *args, **kwargs):
        if not self.order_number:
            self.order_number = generate_order_number()
        super().save(*args, **kwargs)

    @property
    def is_priced(self):
        """Whether a price has been agreed for this order.

        Why a property rather than repeating `total is None` in templates:
        half a dozen places need to tell "not agreed yet" apart from "agreed
        at zero", and one named question reads better than that test copied
        around — and unlike the raw test, it can be reasoned about.

        Returns:
            bool. False when the total was never set, which is not the same
            thing as an order priced at zero.
        """
        return self.total is not None

    @property
    def material_source(self):
        """Where the cloth for this whole order comes from.

        Derived rather than stored on purpose: one order can mix shop cloth
        and customer-supplied cloth line by line, so a stored flag would go
        stale the moment a line was added or edited.

        Walks items.all() rather than querying values_list() on purpose:
        the order page prefetches items, and going through the manager
        reuses that cache instead of costing an extra query per page.

        Returns:
            A MaterialSource member - SHOP when every line is shop cloth,
            CUSTOMER when all of them are the customer's, MIXED when both
            appear. An order with no lines at all reports SHOP: that is what
            it meant before line items existed, and staff have no other
            signal to go on.
        """
        sources = {item.material_source for item in self.items.all()}
        if not sources:
            return MaterialSource.SHOP
        if len(sources) == 1:
            return MaterialSource(sources.pop())
        return MaterialSource.MIXED


class OrderItem(models.Model):
    """One line of an order.

    `price` is the price for a SINGLE unit, not the line total - that is
    quantity * price, exposed as line_total. The name stays `price` rather
    than becoming `unit_price` because the deploy step runs migrations before
    the new code lands, and a rename would break the still-running old code
    during that window.
    """

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    description = models.CharField(max_length=255)
    quantity = models.PositiveIntegerField(default=1)
    price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    # Only SHOP and CUSTOMER: an item is never MIXED (see MaterialSource).
    # Written as explicit pairs rather than MaterialSource.choices, which
    # would admit MIXED - a value that only ever exists in the derived
    # Order.material_source and must never be stored on a line.
    material_source = models.CharField(
        max_length=20,
        choices=[
            (MaterialSource.SHOP.value, MaterialSource.SHOP.label),
            (MaterialSource.CUSTOMER.value, MaterialSource.CUSTOMER.label),
        ],
        default=MaterialSource.SHOP,
    )

    def __str__(self):
        return f"{self.quantity} x {self.description} ({self.price})"

    @property
    def line_total(self):
        """What this line costs on its own, before any order-level figure.

        Returns:
            Decimal. Zero-priced lines are entirely normal: a customer who
            supplied the cloth spent nothing at the shop, so the line is
            stitching labour only.
        """
        return self.quantity * self.price


class OrderStageHistory(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="stage_history")
    from_stage = models.CharField(max_length=20, choices=OrderStage.choices, blank=True)
    to_stage = models.CharField(max_length=20, choices=OrderStage.choices)
    changed_by = models.ForeignKey(
        "auth.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="stage_changes"
    )
    changed_at = models.DateTimeField(auto_now_add=True)
    note = models.TextField(blank=True)

    class Meta:
        verbose_name_plural = "Order stage histories"
        ordering = ["changed_at"]

    def __str__(self):
        return f"{self.order.order_number}: {self.from_stage or 'new'} → {self.to_stage}"


class OrderMeasurement(MeasurementFields):
    """The measurements this order was cut to, frozen at order time.

    Why a copy instead of reading them off the customer: measurements change.
    A blouse cut to a 34 bust must still show 34 on a delivery three months
    later, even though the customer has since been re-measured for something
    else. The customer row stays live; this one is the record.

    Written by orders.services::snapshot_measurements when the order is
    created, and never touched again.
    """

    order = models.OneToOneField(Order, on_delete=models.CASCADE, related_name="measurement")

    def __str__(self):
        return f"Measurements — {self.order.order_number}"

