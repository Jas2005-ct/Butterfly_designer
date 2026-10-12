"""Django forms for billing.

Why forms.py (not inline validation): field types, limits, and error
messages live in one place; the template re-renders entered values on
error so counter staff never retype a payment.
"""

from decimal import Decimal

from django import forms

from .models import PaymentMode


class PaymentForm(forms.Form):
    """Record a payment against an order.

    Why a Form: amount must be a positive decimal and mode must be a
    known choice — the Form rejects bad input before any DB contact
    (a failed money query would poison the surrounding transaction).
    """

    amount = forms.DecimalField(
        max_digits=10,
        decimal_places=2,
        min_value=Decimal("0.01"),
        widget=forms.NumberInput(
            attrs={
                "step": "0.01",
                "min": "0.01",
                "placeholder": "0.00",
                "class": "w-full text-lg px-4 py-3 rounded-xl border border-slate-300 "
                "focus:border-indigo-500 focus:ring-2 focus:ring-indigo-200 outline-none",
            }
        ),
    )
    mode = forms.ChoiceField(
        choices=PaymentMode.choices,
        initial=PaymentMode.CASH,
        widget=forms.RadioSelect,
    )
    note = forms.CharField(
        max_length=255,
        required=False,
        widget=forms.TextInput(
            attrs={
                "placeholder": "Reference (optional, e.g. UPI txn id)",
                "class": "w-full px-4 py-3 rounded-xl border border-slate-300 "
                "focus:border-indigo-500 focus:ring-2 focus:ring-indigo-200 outline-none",
            }
        ),
    )
