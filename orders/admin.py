from django.contrib import admin

from .models import Order, OrderItem, OrderStageHistory


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 1


class OrderStageHistoryInline(admin.TabularInline):
    model = OrderStageHistory
    extra = 0
    readonly_fields = ("from_stage", "to_stage", "changed_by", "changed_at", "note")


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ("order_number", "customer", "service_type", "stage", "total", "promised_date", "created_at")
    list_filter = ("stage", "service_type")
    search_fields = ("order_number", "customer__name", "customer__phone")
    inlines = (OrderItemInline, OrderStageHistoryInline)


@admin.register(OrderStageHistory)
class OrderStageHistoryAdmin(admin.ModelAdmin):
    list_display = ("order", "from_stage", "to_stage", "changed_by", "changed_at")
