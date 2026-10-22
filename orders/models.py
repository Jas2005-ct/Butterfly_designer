import uuid

from django.db import models, transaction
from django.utils import timezone


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
    total = models.DecimalField(max_digits=10, decimal_places=2, default=0)
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


class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    description = models.CharField(max_length=255)
    price = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    def __str__(self):
        return f"{self.description} ({self.price})"


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
