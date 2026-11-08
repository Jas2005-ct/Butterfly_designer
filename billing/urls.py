from django.urls import path

from . import views

app_name = "billing"

urlpatterns = [
    path("dues/", views.dues, name="dues"),
    path("orders/<int:order_id>/payments/add/", views.order_payment_add, name="order_payment_add"),
    path("orders/<int:order_id>/balance/settle/", views.order_settle_balance, name="order_settle_balance"),
]
