from django.urls import path

from . import views

app_name = "customers"

urlpatterns = [
    path("customers/", views.customer_list, name="list"),
]
