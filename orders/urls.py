from django.urls import path

from . import views
from . import tracking

app_name = "orders"

urlpatterns = [
    path("", views.board, name="board"),
    path("new/", views.order_create, name="create"),
    path("<int:order_id>/", views.order_detail, name="detail"),
    path("<int:order_id>/edit/", views.order_edit, name="edit"),
    path("<int:order_id>/stage/", views.order_advance_stage, name="advance_stage"),
    path("track/<uuid:token>/", tracking.track_order, name="track"),
]