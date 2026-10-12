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
