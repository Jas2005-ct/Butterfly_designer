from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from .models import Customer


@login_required
def customer_list(request):
    customers = Customer.objects.all()[:50]
    return render(request, "shop/customers/list.html", {"customers": customers})
