from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("login/", views.shop_login, name="login"),
    path("logout/", views.shop_logout, name="logout"),
    path("accounts/", views.user_list, name="user_list"),
    path("accounts/new/", views.user_create, name="user_create"),
    path("accounts/<int:user_id>/toggle/", views.user_toggle_active, name="user_toggle"),
]
