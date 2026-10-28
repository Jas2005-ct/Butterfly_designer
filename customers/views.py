from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from .models import Customer, CustomerMeasurement, MeasurementFields


def _parse_inches(raw, label):
    """Parse one optional measurement, in inches.

    Why reject instead of clamping: "3" typed for "34" makes a garment that
    does not fit, and quietly storing it is worse than an error the staff can
    fix while the customer is still standing at the counter.

    Args:
        raw: the submitted string; blank or missing means "not measured".
        label: human name of the field, used in the error message.

    Returns:
        (value, error) - value is a Decimal, or None for a blank field.
        error is None, or a message naming the field that needs fixing.
    """
    raw = (raw or "").strip()
    if not raw:
        return None, None
    try:
        value = Decimal(raw)
    except InvalidOperation:
        return None, f"{label} must be a number in inches (e.g. 34)."
    # 0 and below is a typo, not a measurement; 100 inches is 8'4", so the
    # ceiling catches transposed digits like "430" without any real value
    # ever reaching it.
    if not (0 < value <= Decimal("100")):
        return None, f"{label} must be more than 0 and no more than 100 inches."
    return value, None


def _measurements_from_post(request):
    """Read all eight measurement inputs from a submitted form.

    A blank field means "no measurement", and it clears anything stored: the
    edit form is pre-filled, so an emptied box is a deliberate erasure, not
    an omitted field the staff forgot to retype.

    Args:
        request: the POST request whose fields are named after
            MeasurementFields.MEASUREMENT_LABELS.

    Returns:
        (values, error) - values maps every measurement field name to a
        Decimal or None, ready to save as-is. error is None, or the first
        problem found; on error, values is None and nothing should be saved.
    """
    values = {}
    for attr, label in MeasurementFields.MEASUREMENT_LABELS:
        value, error = _parse_inches(request.POST.get(attr), label)
        if error:
            return None, error
        values[attr] = value
    return values, None


def _save_measurements(customer, values):
    """Create or replace a customer's measurement row.

    Args:
        customer: the Customer the measurements belong to.
        values: dict of every measurement field name to a Decimal or None,
            as returned by _measurements_from_post.

    Returns:
        The CustomerMeasurement, or None when every field was left blank -
        an all-empty row would clutter the detail page and wrongly imply
        this customer had been measured.
    """
    if not any(value is not None for value in values.values()):
        CustomerMeasurement.objects.filter(customer=customer).delete()
        return None
    measurement, _ = CustomerMeasurement.objects.update_or_create(
        customer=customer, defaults=values
    )
    return measurement


def _posted_measurements(request):
    """Every measurement field exactly as it was submitted.

    Why: when a form comes back after an error it must not forget the eight
    boxes the staff already typed, or a typo in "waist" costs them the whole
    set plus the name and phone.

    Args:
        request: the failed POST request.

    Returns:
        dict of measurement field name to the submitted string ("" when the
        box was left blank), ready to hand straight to the template.
    """
    return {
        attr: (request.POST.get(attr) or "") for attr, _ in MeasurementFields.MEASUREMENT_LABELS
    }


@login_required
def customer_search(request):
    """HTMX: live customer lookup by name or phone for pickers.

    Why a dedicated endpoint: the order form must stay fast with hundreds
    of customers — a 100-radio list neither fits nor searches. Returns an
    HTML partial (top 8 matches) the picker swaps in as you type.
    """
    query = request.GET.get("q", "").strip()
    results = []
    if len(query) >= 2:
        results = Customer.objects.filter(
            Q(name__icontains=query) | Q(phone__icontains=query)
        ).order_by("-created_at")[:8]
    return render(
        request,
        "shop/customers/_search_results.html",
        {"results": results, "query": query},
    )


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
    """Create a new customer, optionally recording measurements with them."""
    if request.method == "POST":
        name = (request.POST.get("name") or "").strip()
        phone = (request.POST.get("phone") or "").strip()
        notes = (request.POST.get("notes") or "").strip()
        values, measurement_error = _measurements_from_post(request)

        if not name or not phone:
            messages.error(request, "Name and phone are required.")
        elif measurement_error:
            # Check the measurements before creating anything. Creating the
            # customer first and then rejecting a typo would leave a
            # half-registered row behind while the form re-renders.
            messages.error(request, measurement_error)
        else:
            customer = Customer.objects.create(
                name=name, phone=phone, notes=notes, created_by=request.user
            )
            _save_measurements(customer, values)
            messages.success(request, "Customer added.")
            return redirect("customers:list")

        # Repopulate: a typo in one measurement should never cost eight
        # retypes plus the name and phone.
        return render(
            request,
            "shop/customers/create.html",
            {"form": request.POST, "measurements": _posted_measurements(request)},
        )

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
    """Edit customer details and their measurements."""
    customer = get_object_or_404(Customer, id=customer_id)

    if request.method == "POST":
        values, measurement_error = _measurements_from_post(request)
        if measurement_error:
            # Nothing has been written yet, so the stored record is intact
            # and the staff can just correct the one bad box.
            messages.error(request, measurement_error)
            return render(
                request,
                "shop/customers/edit.html",
                {
                    "customer": customer,
                    "form": request.POST,
                    "measurements": _posted_measurements(request),
                },
            )

        customer.name = (request.POST.get("name") or "").strip()
        customer.phone = (request.POST.get("phone") or "").strip()
        customer.notes = (request.POST.get("notes") or "").strip()
        customer.save()
        # Replacing the whole row is correct here: the edit form comes
        # pre-filled, so an empty box means the measurement was cleared
        # on purpose, not that it should be kept as it was.
        _save_measurements(customer, values)
        messages.success(request, "Customer updated.")
        return redirect("customers:detail", customer_id=customer.id)

    # getattr with a default, rather than customer.measurement: a customer who
    # has never been measured has no row, and asking for it would raise.
    existing = getattr(customer, "measurement", None)
    return render(
        request,
        "shop/customers/edit.html",
        {
            "customer": customer,
            "measurements": existing.as_form_values() if existing else {},
        },
    )