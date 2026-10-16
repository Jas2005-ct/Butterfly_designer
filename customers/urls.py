from django.urls import path

from . import views

app_name = "customers"

urlpatterns = [
    path("customers/", views.customer_list, name="list"),
    path("customers/new/", views.customer_create, name="create"),
    path("customers/search/", views.customer_search, name="search"),
    path("customers/<int:customer_id>/", views.customer_detail, name="detail"),
    path("customers/<int:customer_id>/edit/", views.customer_edit, name="edit"),
]