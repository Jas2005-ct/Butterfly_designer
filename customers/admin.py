from django.contrib import admin

from .models import Customer, CustomerMeasurement


class CustomerMeasurementInline(admin.StackedInline):
    """Measurements edit alongside the customer, exactly as in the shop UI."""

    model = CustomerMeasurement
    extra = 0
    max_num = 1


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ("name", "phone", "created_at")
    search_fields = ("name", "phone")
    inlines = (CustomerMeasurementInline,)
