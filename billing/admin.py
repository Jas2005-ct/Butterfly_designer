from django.contrib import admin

from .models import Payment


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ("order", "amount", "mode", "recorded_by", "created_at")
    list_filter = ("mode",)
    search_fields = ("order__order_number", "order__customer__name")
