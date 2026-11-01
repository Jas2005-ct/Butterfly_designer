"""Django forms for billing.

Why forms.py (not inline validation): field types, limits, and error
messages live in one place; the template re-renders entered values on
error so counter staff never retype a payment.
"""

from decimal import Decimal

from django import forms

from .models import PaymentKind, PaymentMode


class PaymentForm(forms.Form):
    """Record a payment against an order.

    Why a Form: amount must be a positive decimal and mode must be a
    known choice — the Form rejects bad input before any DB contact
    (a failed money query would poison the surrounding transaction).

    Why the delivery picker is built in __init__ rather than declared on the
    class: which deliveries exist is a property of one order, not of the
    Form. Declaring it statically would offer every hand-over the shop has
    ever made, and an operator picking the wrong one would quietly move a
    customer's money onto another customer's order.
    """

    amount = forms.DecimalField(
        max_digits=10,
        decimal_places=2,
        min_value=Decimal("0.01"),
        # A text box, not a number widget: on a refused POST the bound form
        # echoes the raw amount, and a browser sanitizes a non-numeric value
        # into an empty box before it is drawn - the panel could carry "abc"
        # and still look blank to the person whose amount just bounced.
        # inputmode keeps the numeric keyboard; min_value above stays the
        # only validator, exactly as the callers and tests assume.
        widget=forms.TextInput(
            attrs={
                "inputmode": "decimal",
                "placeholder": "0.00",
                "class": "w-full text-lg px-4 py-3 rounded-xl border border-slate-300 "
                "focus:border-indigo-500 focus:ring-2 focus:ring-indigo-200 outline-none",
            }
        ),
    )
    kind = forms.ChoiceField(
        choices=PaymentKind.choices,
        initial=PaymentKind.ADVANCE,
        required=False,
        widget=forms.RadioSelect,
    )
    delivery = forms.ChoiceField(
        choices=[("", "— none —")],
        required=False,
        widget=forms.Select(
            attrs={
                "class": "w-full text-sm px-3 py-2.5 rounded-xl border border-slate-300 "
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

    def __init__(self, *args, order=None, **kwargs):
        """Offer only this order's hand-overs, each named by its date.

        Why the date in the label: the operator recognises "12 Oct hand-over"
        and cannot recognise a database id, and misreading which delivery a
        payment is against is the one error this feature can introduce.

        Args:
            *args: Standard positional args, the POST data when bound.
            order: The Order whose deliveries may be named, or None when the
                form is only being displayed (the picker then offers nothing).
            **kwargs: Standard keyword args.

        Returns:
            None. The instance's delivery choices are set on the field.
        """
        super().__init__(*args, **kwargs)
        self.order = order
        choices = [("", "— none —")]
        if order is not None:
            choices += [
                (str(d.id), f"{d.delivered_on:%d %b %Y} — {d.total_quantity} pieces")
                for d in order.deliveries.all()
            ]
        self.fields["delivery"].choices = choices

    def clean_kind(self):
        """A missing kind means Advance, not an error.

        Why: every payment recorded before this field existed was an advance
        against the order, so a POST that omits it — a stale browser tab, an
        import, a script — describes the old behaviour exactly. Rejecting it
        would break callers for a distinction they never had to make.

        Returns:
            str: One of PaymentKind's values, defaulting to "advance".
        """
        return self.cleaned_data.get("kind") or PaymentKind.ADVANCE

    def clean(self):
        """Keep the kind and the named delivery telling the same story.

        Why both directions are checked: the field alone cannot express the
        rule, because each half is legal on its own. An advance naming a
        delivery would let one hand-over absorb money that was never given
        at it; a delivery payment naming none would file cash against an
        event that has not happened.

        Returns:
            dict|None: The cleaned data, or None if an error was raised.
        """
        cleaned = super().clean()
        kind = cleaned.get("kind")
        delivery_id = cleaned.get("delivery")
        if kind == PaymentKind.DELIVERY and not delivery_id:
            self.add_error("delivery", "Choose which delivery this payment is against.")
        elif kind and kind != PaymentKind.DELIVERY and delivery_id:
            self.add_error("delivery", "Only an 'Against delivery' payment can name one.")
        return cleaned
