from django.db import models


class Customer(models.Model):
    name = models.CharField(max_length=100)
    phone = models.CharField(max_length=15, db_index=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        "auth.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="customers_created"
    )

    class Meta:
        verbose_name_plural = "Customers"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name} ({self.phone})"


class MeasurementFields(models.Model):
    """Body measurements in inches, shared by the customer and the order.

    Why an abstract mixin instead of writing the eight columns out twice: the
    customer's live measurements and the per-order snapshot must always agree
    on names, precision and labels, so there is one definition and both tables
    inherit it.

    Every field is optional. A blouse needs bust/waist/shoulder but not height,
    and a saree alteration may need none of them, so there is no required set
    and a partially measured customer is never rejected.

    Ranges are enforced at the edge (customers/views.py::_parse_inches) rather
    than with MinValueValidator, so an untouched form field stays blank instead
    of failing validation.
    """

    # Labelled in the order a tailor reads them off a body.
    MEASUREMENT_LABELS = (
        ("bust", "Bust"),
        ("waist", "Waist"),
        ("hips", "Hips"),
        ("shoulder", "Shoulder"),
        ("sleeve_length", "Sleeve length"),
        ("blouse_length", "Blouse length"),
        ("height", "Height"),
        ("neck_depth", "Neck depth"),
    )

    # Inches to two decimals, which covers the quarter-inch steps the shop
    # actually works in. max_digits=5 allows up to 999.99, so no real body
    # measurement is ever truncated or rejected for being "too big".
    bust = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    waist = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    hips = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    shoulder = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    sleeve_length = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    blouse_length = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    height = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    neck_depth = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)

    class Meta:
        abstract = True

    def measured(self):
        """The measurements that were actually taken.

        Why: every field is optional, so a customer measured only for a blouse
        should show four numbers, not eight slots with four of them empty.

        Returns:
            list of (label, inches) tuples in MEASUREMENT_LABELS order,
            skipping fields that were never filled in.
        """
        return [
            (label, getattr(self, attr))
            for attr, label in self.MEASUREMENT_LABELS
            if getattr(self, attr) is not None
        ]

    def has_measurements(self):
        """Whether at least one measurement was taken.

        Returns:
            bool. False for a customer who was never measured.
        """
        return any(getattr(self, attr) is not None for attr, _ in self.MEASUREMENT_LABELS)

    def as_form_values(self):
        """Every field as a string, for pre-filling an editing form.

        Why not let the template read the columns directly: a form has to
        show something in all eight boxes, and the alternative is eight
        blocks of template that each special-case a missing value.

        Returns:
            dict of field name to "" when not measured, otherwise the inch
            value as a string - so the template can loop it blindly.
        """
        return {
            attr: "" if (value := getattr(self, attr)) is None else str(value)
            for attr, _ in self.MEASUREMENT_LABELS
        }


class CustomerMeasurement(MeasurementFields):
    """A customer's current measurements.

    One row per customer, written the first time a measurement is saved and
    replaced afterwards, which makes "edit customer" double as "edit
    measurements". Orders do not point here - they keep their own copy, see
    orders.models.OrderMeasurement.
    """

    customer = models.OneToOneField(
        Customer, on_delete=models.CASCADE, related_name="measurement"
    )
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Measurements — {self.customer.name}"
