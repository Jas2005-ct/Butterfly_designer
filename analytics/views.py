from accounts.decorators import admin_required
from django.shortcuts import render


@admin_required
def dashboard(request):
    return render(request, "shop/analytics/dashboard.html")
