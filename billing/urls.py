from django.urls import path

from . import views

app_name = "billing"

urlpatterns = [
    path("dues/", views.dues, name="dues"),
]
