from django.urls import path
from . import views

urlpatterns=[
    path("",views.home,name="home"),
    path("employee/<int:id>/", views.employee_detail, name="employee_detail"),
    path("review/", views.review_form, name="review_form"),
]