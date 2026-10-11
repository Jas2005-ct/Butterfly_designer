from django.contrib.auth.decorators import login_required
from django.shortcuts import render


@login_required
def dues(request):
    return render(request, "shop/billing/dues.html")
