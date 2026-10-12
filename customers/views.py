from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from .models import Customer


@login_required
def customer_list(request):
    """Searchable customer list."""
    query = request.GET.get("q", "").strip()
    customers = Customer.objects.all().order_by("-created_at")

    if query:
        customers = customers.filter(
            Q(name__icontains=query) | Q(phone__icontains=query)
        )

    return render(
        request,
        "shop/customers/list.html",
        {"customers": customers[:100], "query": query},
    )


@login_required
@require_http_methods(["GET", "POST"])
def customer_create(request):
    """Create a new customer."""
    if request.method == "POST":
        name = (request.POST.get("name") or "").strip()
        phone = (request.POST.get("phone") or "").strip()
        notes = (request.POST.get("notes") or "").strip()

        if not name or not phone:
            messages.error(request, "Name and phone are required.")
        else:
            Customer.objects.create(name=name, phone=phone, notes=notes, created_by=request.user)
            messages.success(request, "Customer added.")
            return redirect("customers:list")

    return render(request, "shop/customers/create.html")


@login_required
@require_http_methods(["GET", "POST"])
def customer_detail(request, customer_id):
    """Customer detail with order history."""
    customer = get_object_or_404(
        Customer.objects.prefetch_related("orders__items", "orders__stage_history"),
        id=customer_id,
    )
    orders = customer.orders.select_related("customer").order_by("-created_at")
    return render(request, "shop/customers/detail.html", {"customer": customer, "orders": orders})


@login_required
@require_http_methods(["GET", "POST"])
def customer_edit(request, customer_id):
    """Edit customer details."""
    customer = get_object_or_404(Customer, id=customer_id)

    if request.method == "POST":
        customer.name = (request.POST.get("name") or "").strip()
        customer.phone = (request.POST.get("phone") or "").strip()
        customer.notes = (request.POST.get("notes") or "").strip()
        customer.save()
        messages.success(request, "Customer updated.")
        return redirect("customers:detail", customer_id=customer.id)

    return render(request, "shop/customers/edit.html", {"customer": customer})