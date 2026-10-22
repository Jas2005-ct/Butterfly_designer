from django.contrib import admin

from .models import (
    Delivery,
    DeliveryLine,
    Order,
    OrderItem,
    OrderMeasurement,
    OrderStageHistory,
)


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 1


class DeliveryLineInline(admin.TabularInline):
    """The pieces in one hand-over, editable alongside the visit.

    Kept editable to match how Payment is exposed: the admin is for fixing
    mistakes, and the shop's normal path - record_delivery() - still refuses
    over-delivery for everyone using the app.
    """

    model = DeliveryLine
    extra = 1


class OrderMeasurementInline(admin.TabularInline):
    """The order's frozen measurements, shown read-only.

    Deliberately not editable: this row is written once when the order is
    placed and records what the garment was actually cut to. Allowing an edit
    would let an order quietly claim numbers nobody ever used, which is the
    exact thing the snapshot exists to prevent - re-measuring a customer is
    what the customer page is for.
    """

    model = OrderMeasurement
    extra = 0
    max_num = 1
    can_delete = False
    readonly_fields = tuple(field.name for field in OrderMeasurement._meta.fields)


class OrderStageHistoryInline(admin.TabularInline):
    model = OrderStageHistory
    extra = 0
    readonly_fields = ("from_stage", "to_stage", "changed_by", "changed_at", "note")


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ("order_number", "customer", "service_type", "stage", "total", "promised_date", "created_at")
    list_filter = ("stage", "service_type")
    search_fields = ("order_number", "customer__name", "customer__phone")
    inlines = (OrderItemInline, OrderMeasurementInline, OrderStageHistoryInline)


@admin.register(OrderStageHistory)
class OrderStageHistoryAdmin(admin.ModelAdmin):
    list_display = ("order", "from_stage", "to_stage", "changed_by", "changed_at")


@admin.register(Delivery)
class DeliveryAdmin(admin.ModelAdmin):
    list_display = ("order", "delivered_on", "recorded_by", "created_at")
    list_filter = ("delivered_on",)
    search_fields = ("order__order_number", "order__customer__name", "order__customer__phone")
    inlines = (DeliveryLineInline,)
