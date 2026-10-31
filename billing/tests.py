from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from customers.models import Customer
from orders.models import Delivery, Order, OrderStage
from .forms import PaymentForm
from .models import Payment, PaymentKind, PaymentMode
from .services import annotate_balances, order_balance, record_payment, total_paid


class BalanceMathTests(TestCase):
    """Balance = total − Σ payments, computed identically everywhere."""

    def setUp(self):
        self.user = User.objects.create_user(username="mgr", password="secret123")
        self.customer = Customer.objects.create(name="Priya", phone="9876543210")

    def test_no_payments_full_balance(self):
        order = Order.objects.create(customer=self.customer, total=Decimal("500.00"))
        self.assertEqual(total_paid(order), Decimal("0"))
        self.assertEqual(order_balance(order), Decimal("500.00"))

    def test_partial_payment(self):
        order = Order.objects.create(customer=self.customer, total=Decimal("500.00"))
        record_payment(order, Decimal("200.00"), PaymentMode.CASH, self.user)
        self.assertEqual(total_paid(order), Decimal("200.00"))
        self.assertEqual(order_balance(order), Decimal("300.00"))

    def test_multiple_payments_modes(self):
        order = Order.objects.create(customer=self.customer, total=Decimal("1000.00"))
        record_payment(order, "400", PaymentMode.UPI, self.user, note="txn123")
        record_payment(order, "600", PaymentMode.CASH, self.user)
        self.assertEqual(total_paid(order), Decimal("1000.00"))
        self.assertEqual(order_balance(order), Decimal("0"))

    def test_overpayment_negative_balance(self):
        order = Order.objects.create(customer=self.customer, total=Decimal("500.00"))
        record_payment(order, "600", PaymentMode.CASH, self.user)
        self.assertEqual(order_balance(order), Decimal("-100.00"))

    def test_annotate_balances(self):
        o1 = Order.objects.create(customer=self.customer, total=Decimal("500.00"))
        o2 = Order.objects.create(customer=self.customer, total=Decimal("300.00"))
        o3 = Order.objects.create(customer=self.customer, total=Decimal("400.00"))
        record_payment(o1, "200", PaymentMode.CASH, self.user)
        record_payment(o3, "500", PaymentMode.UPI, self.user)  # overpaid
        annotated = {o.id: o for o in annotate_balances(Order.objects.all())}
        self.assertEqual(annotated[o1.id].paid, Decimal("200.00"))
        self.assertEqual(annotated[o1.id].balance, Decimal("300.00"))
        self.assertEqual(annotated[o2.id].paid, Decimal("0"))
        self.assertEqual(annotated[o2.id].balance, Decimal("300.00"))
        self.assertEqual(annotated[o3.id].paid, Decimal("500.00"))
        self.assertEqual(annotated[o3.id].balance, Decimal("-100.00"))


class RecordPaymentValidationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="mgr", password="secret123")
        self.customer = Customer.objects.create(name="Priya", phone="9876543210")
        self.order = Order.objects.create(customer=self.customer, total=Decimal("500.00"))

    def test_zero_rejected(self):
        with self.assertRaises(ValueError):
            record_payment(self.order, "0", PaymentMode.CASH, self.user)
        self.assertFalse(Payment.objects.exists())

    def test_negative_rejected(self):
        with self.assertRaises(ValueError):
            record_payment(self.order, "-50", PaymentMode.CASH, self.user)
        self.assertFalse(Payment.objects.exists())

    def test_non_numeric_rejected(self):
        with self.assertRaises(ValueError):
            record_payment(self.order, "not-a-number", PaymentMode.CASH, self.user)
        self.assertFalse(Payment.objects.exists())

    def test_invalid_mode_rejected(self):
        with self.assertRaises(ValueError):
            record_payment(self.order, "100", "cheque", self.user)
        self.assertFalse(Payment.objects.exists())

    def test_records_who_and_note(self):
        payment = record_payment(self.order, "150", PaymentMode.UPI, self.user, note="txn9")
        self.assertEqual(payment.recorded_by, self.user)
        self.assertEqual(payment.note, "txn9")


class PaymentViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="mgr", password="secret123")
        self.client.login(username="mgr", password="secret123")
        self.customer = Customer.objects.create(name="Priya", phone="9876543210")
        self.order = Order.objects.create(customer=self.customer, total=Decimal("500.00"))

    def url(self):
        return reverse("billing:order_payment_add", args=[self.order.id])

    def test_add_payment_htmx(self):
        response = self.client.post(
            self.url(),
            {"amount": "200.00", "mode": "upi", "note": "txn1"},
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.order.payments.count(), 1)
        payment = self.order.payments.get()
        self.assertEqual(payment.amount, Decimal("200.00"))
        self.assertEqual(payment.mode, PaymentMode.UPI)
        self.assertEqual(payment.recorded_by, self.user)

    def test_add_invalid_amount_returns_400(self):
        response = self.client.post(
            self.url(),
            {"amount": "abc", "mode": "cash"},
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Payment.objects.exists())

    def test_a_refused_payment_keeps_what_was_typed(self):
        """Every rejection site re-renders the panel with the bound form.

        Why this test: the rejections share one `rejected()` closure in the
        view, and this is the only one a browser POST can actually reach -
        the service layer's ValueError is unreachable because the Form
        co-validates kind/delivery, so it cannot be driven without mocks.
        Asserting binding here exercises the physical thing users should
        never lose: the amount, mode and note coming back on screen.
        """
        response = self.client.post(
            self.url(),
            {"amount": "0", "mode": "card", "note": "upi later"},
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "Please fix the highlighted fields.", status_code=400)
        self.assertContains(response, 'value="0"', status_code=400)
        self.assertContains(response, 'value="upi later"', status_code=400)
        self.assertContains(response, 'checked', status_code=400)
        self.assertFalse(Payment.objects.exists())

    def test_the_typed_echo_survives_the_browser(self):
        """The bad amount must be visible, not merely present in the HTML.

        A number widget blanks a non-numeric value at render time, so the
        panel could carry "abc" and still show an empty box - the same
        browser behaviour the order and customer forms were converted for.
        The widget is a text input with a numeric keyboard now (the form's
        min_value remains the only validator), so the typed value and the
        message explaining the refusal are on screen together.
        """
        response = self.client.post(
            self.url(),
            {"amount": "abc", "mode": "cash"},
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, 'type="text" name="amount"', status_code=400)
        self.assertContains(response, 'value="abc"', status_code=400)

    def test_add_zero_returns_400(self):
        response = self.client.post(
            self.url(),
            {"amount": "0", "mode": "cash"},
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Payment.objects.exists())

    def test_get_not_allowed(self):
        response = self.client.get(self.url())
        self.assertEqual(response.status_code, 405)

    def test_anonymous_redirected(self):
        self.client.logout()
        response = self.client.post(self.url(), {"amount": "100", "mode": "cash"})
        self.assertEqual(response.status_code, 302)

    def test_detail_shows_balance(self):
        record_payment(self.order, "200", PaymentMode.CASH, self.user)
        response = self.client.get(reverse("orders:detail", args=[self.order.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Payments")
        self.assertContains(response, "order-payments")

    def test_board_shows_due_badge(self):
        record_payment(self.order, "200", PaymentMode.CASH, self.user)
        response = self.client.get(reverse("orders:board"))
        self.assertContains(response, "due")


class DuesViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="mgr", password="secret123")
        self.client.login(username="mgr", password="secret123")
        self.customer = Customer.objects.create(name="Priya", phone="9876543210")

    def test_dues_lists_only_positive_balances(self):
        owing = Order.objects.create(
            customer=self.customer, total=Decimal("500.00"), stage=OrderStage.STITCHING
        )
        record_payment(owing, "200", PaymentMode.CASH, self.user)
        settled = Order.objects.create(
            customer=self.customer, total=Decimal("300.00"), stage=OrderStage.STITCHING
        )
        record_payment(settled, "300", PaymentMode.CASH, self.user)
        delivered = Order.objects.create(
            customer=self.customer, total=Decimal("900.00"), stage=OrderStage.DELIVERED
        )
        record_payment(delivered, "100", PaymentMode.CASH, self.user)

        response = self.client.get(reverse("billing:dues"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, owing.order_number)
        self.assertNotContains(response, settled.order_number)
        self.assertNotContains(response, delivered.order_number)
        self.assertContains(response, "300.00")  # outstanding total

    def test_dues_sort_by_balance(self):
        small = Order.objects.create(
            customer=self.customer, total=Decimal("500.00"), stage=OrderStage.STITCHING
        )
        record_payment(small, "400", PaymentMode.CASH, self.user)  # 100 due
        big = Order.objects.create(
            customer=self.customer, total=Decimal("2000.00"), stage=OrderStage.STITCHING
        )
        record_payment(big, "100", PaymentMode.CASH, self.user)  # 1900 due

        response = self.client.get(reverse("billing:dues") + "?sort=balance")
        content = response.content.decode()
        self.assertLess(content.index(big.order_number), content.index(small.order_number))

    def test_dues_empty_state(self):
        response = self.client.get(reverse("billing:dues"))
        self.assertContains(response, "No dues pending")

    def test_dues_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("billing:dues"))
        self.assertEqual(response.status_code, 302)


class UnpricedOrderTests(TestCase):
    """An order with no agreed total reads as unknown, not as free.

    The whole distinction rests on one line: 0 means "settled in full", which
    is exactly backwards for a price nobody has fixed yet. So the balance is
    None, callers treat that as "not counted", and the dues report files those
    orders separately instead of dropping them.
    """

    def setUp(self):
        self.user = User.objects.create_user(username="mgr", password="secret123")
        self.client.login(username="mgr", password="secret123")
        self.customer = Customer.objects.create(name="Priya", phone="9876543210")

    def unpriced(self, **kwargs):
        """An order with no total, which is the default now."""
        return Order.objects.create(customer=self.customer, **kwargs)

    def test_balance_is_none_not_zero(self):
        order = self.unpriced()
        self.assertIsNone(order.total)
        self.assertIsNone(order_balance(order))
        # Spelled out: the bug this guards against is returning Decimal("0"),
        # which would read as "fully paid" everywhere in the app.
        self.assertFalse(order.is_priced)

    def test_payments_still_count_even_without_a_total(self):
        """Money is real even when the price is not - only the balance is unknown."""
        order = self.unpriced()
        record_payment(order, "200", PaymentMode.CASH, self.user)
        self.assertEqual(total_paid(order), Decimal("200"))
        self.assertIsNone(order_balance(order))

    def test_a_zero_total_is_still_a_price(self):
        """Free is not the same as unset, and conflating them would make an
        order priced at zero indistinguishable from one nobody has quoted."""
        order = self.unpriced(total=Decimal("0"))
        self.assertTrue(order.is_priced)
        self.assertEqual(order_balance(order), Decimal("0"))

    def test_annotated_balance_is_none_for_an_unpriced_order(self):
        """The board reads balances off the annotation, so it has to agree
        with order_balance() rather than inventing a zero."""
        order = self.unpriced()
        record_payment(order, "200", PaymentMode.CASH, self.user)
        annotated = annotate_balances(Order.objects.filter(id=order.id)).get()
        self.assertEqual(annotated.paid, Decimal("200"))
        self.assertIsNone(annotated.balance)

    def test_payment_panel_reports_an_unknown_balance(self):
        order = self.unpriced()
        response = self.client.get(reverse("orders:detail", args=[order.id]))
        # "Amount TBD" names the situation, and the collected figure next to it
        # stops the shop forgetting money it is already holding — the client
        # raised both halves together: an unset total and advances taken anyway.
        self.assertContains(response, "Amount TBD")
        self.assertContains(response, "collected so far")
        # The three summaries must not read as a settled account.
        self.assertNotContains(response, "Paid in full")

    def test_order_page_never_prints_an_invented_total(self):
        order = self.unpriced()
        response = self.client.get(reverse("orders:detail", args=[order.id]))
        self.assertContains(response, "Total not set")
        # The customer-facing receipt is the risky one: ₹0.00 there is a
        # promise the shop never made.
        self.assertContains(response, "To be confirmed")

    def test_unpriced_orders_drop_out_of_the_money_list(self):
        """Correct for the total, but it is why the report needs a second group."""
        unpriced = self.unpriced()
        priced = self.unpriced(total=Decimal("500"))
        open_orders = annotate_balances(
            Order.objects.exclude(stage__in=[OrderStage.DELIVERED, OrderStage.CANCELLED])
        )
        owing = list(open_orders.filter(balance__gt=0))
        self.assertIn(priced, owing)
        self.assertNotIn(unpriced, owing)

    def test_dues_report_keeps_unpriced_orders_in_their_own_group(self):
        unpriced = self.unpriced()
        self.unpriced(total=Decimal("500"))
        response = self.client.get(reverse("billing:dues"))
        self.assertContains(response, "Amount not set")
        # Present on the page, so it cannot be quietly forgotten.
        self.assertContains(response, unpriced.order_number)

    def test_dues_report_omits_delivered_and_cancelled_unpriced_orders(self):
        """Handover settles money either way, so an unpriced delivered order
        is not a loose end."""
        self.unpriced(stage=OrderStage.DELIVERED)
        self.unpriced(stage=OrderStage.CANCELLED)
        response = self.client.get(reverse("billing:dues"))
        self.assertNotContains(response, "Amount not set")


class DeliveryPaymentTests(TestCase):
    """Branch 7 -- money arrives twice in this shop's life, and for two reasons.

    The client: "paid a partial amount one each delivery and they may give any
    advance while given a order". Those are genuinely different events -- one
    happens before any work exists, the other happens at hand-over and can
    repeat several times for a single order. So a payment can name the delivery
    it came from, but is never required to, and the balance is blind to which
    it was: everything received counts the same.
    """

    def setUp(self):
        self.user = User.objects.create_user(username="mgr", password="secret123")
        self.client.login(username="mgr", password="secret123")
        self.customer = Customer.objects.create(name="Priya", phone="9876543210")
        self.order = Order.objects.create(customer=self.customer, total=Decimal("1000"))
        self.delivery = Delivery.objects.create(
            order=self.order, delivered_on=date(2026, 10, 12)
        )

    # --- the service ------------------------------------------------------

    def test_an_advance_names_no_delivery(self):
        """The common case, and the one that must keep working untouched."""
        p = record_payment(
            self.order, "500", PaymentMode.UPI, self.user,
            kind=PaymentKind.ADVANCE,
        )
        self.assertEqual(p.kind, PaymentKind.ADVANCE)
        self.assertIsNone(p.delivery)
        self.assertEqual(order_balance(self.order), Decimal("500"))

    def test_a_payment_can_be_tied_to_a_delivery(self):
        p = record_payment(
            self.order, "400", PaymentMode.CASH, self.user,
            kind=PaymentKind.DELIVERY, delivery=self.delivery,
        )
        self.assertEqual(p.delivery, self.delivery)
        # Balance is blind to the reason -- this is the whole point of one ledger.
        self.assertEqual(order_balance(self.order), Decimal("600"))

    def test_kind_delivery_without_a_delivery_is_rejected(self):
        """Naming an event that does not exist would silently misfile the money."""
        with self.assertRaisesMessage(ValueError, "Choose which delivery"):
            record_payment(
                self.order, "100", PaymentMode.CASH, self.user,
                kind=PaymentKind.DELIVERY,
            )

    def test_a_delivery_from_another_order_is_rejected(self):
        """The picker is scoped to one order, but the service is the last line
        of defence and cannot assume the caller was honest."""
        other = Order.objects.create(customer=self.customer, total=Decimal("300"))
        other_delivery = Delivery.objects.create(order=other, delivered_on=date(2026, 10, 13))
        with self.assertRaisesMessage(ValueError, "different order"):
            record_payment(
                self.order, "100", PaymentMode.CASH, self.user,
                kind=PaymentKind.DELIVERY, delivery=other_delivery,
            )

    def test_only_a_delivery_payment_may_name_a_delivery(self):
        """An advance against a delivery is a contradiction in terms, and
        quietly dropping the link would leave the ledger saying nothing about
        money that is visibly attached to an event."""
        with self.assertRaisesMessage(ValueError, "Against delivery"):
            record_payment(
                self.order, "100", PaymentMode.CASH, self.user,
                kind=PaymentKind.ADVANCE, delivery=self.delivery,
            )

    def test_an_unknown_kind_is_rejected(self):
        with self.assertRaisesMessage(ValueError, "Invalid payment type"):
            record_payment(self.order, "100", PaymentMode.CASH, self.user, kind="bribe")

    def test_balance_sums_both_kinds_indifferently(self):
        """Under- and over-payment both stay legal: the shop takes what the
        customer hands over, and the ledger reports the difference."""
        record_payment(self.order, "200", PaymentMode.CASH, self.user, kind=PaymentKind.ADVANCE)
        record_payment(
            self.order, "900", PaymentMode.CASH, self.user,
            kind=PaymentKind.DELIVERY, delivery=self.delivery,
        )
        self.assertEqual(order_balance(self.order), Decimal("-100"))

    # --- the form ---------------------------------------------------------

    def test_form_requires_a_delivery_for_the_delivery_kind(self):
        form = PaymentForm(
            {"amount": "100", "kind": PaymentKind.DELIVERY, "delivery": "", "mode": "cash"},
            order=self.order,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("delivery", form.errors)

    def test_form_rejects_a_delivery_on_an_advance(self):
        form = PaymentForm(
            {
                "amount": "100",
                "kind": PaymentKind.ADVANCE,
                "delivery": str(self.delivery.id),
                "mode": "cash",
            },
            order=self.order,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("delivery", form.errors)

    def test_form_only_offers_this_orders_deliveries(self):
        """The single most likely operator error is paying against another
        order's delivery, so the picker never offers one."""
        other = Order.objects.create(customer=self.customer, total=Decimal("300"))
        Delivery.objects.create(order=other, delivered_on=date(2026, 10, 13))
        form = PaymentForm(order=self.order)
        ids = [value for value, _label in form.fields["delivery"].choices]
        self.assertIn(str(self.delivery.id), ids)
        self.assertEqual(len(ids), 2)  # this delivery plus the blank option

    def test_form_accepts_a_delivery_payment(self):
        form = PaymentForm(
            {
                "amount": "100",
                "kind": PaymentKind.DELIVERY,
                "delivery": str(self.delivery.id),
                "mode": "cash",
            },
            order=self.order,
        )
        self.assertTrue(form.is_valid(), form.errors)

    # --- the view ---------------------------------------------------------

    def test_recording_a_delivery_payment_end_to_end(self):
        self.client.post(
            reverse("billing:order_payment_add", args=[self.order.id]),
            {
                "amount": "150",
                "kind": PaymentKind.DELIVERY,
                "delivery": str(self.delivery.id),
                "mode": "cash",
                "note": "",
            },
            headers={"hx-request": "true"},
        )
        p = self.order.payments.get()
        self.assertEqual(p.kind, PaymentKind.DELIVERY)
        self.assertEqual(p.delivery, self.delivery)

    def test_an_advance_payment_still_works_with_no_delivery(self):
        self.client.post(
            reverse("billing:order_payment_add", args=[self.order.id]),
            {"amount": "500", "kind": PaymentKind.ADVANCE, "delivery": "", "mode": "upi", "note": ""},
            headers={"hx-request": "true"},
        )
        p = self.order.payments.get()
        self.assertEqual(p.kind, PaymentKind.ADVANCE)
        self.assertIsNone(p.delivery)

    def test_a_post_without_a_kind_is_an_advance(self):
        """A stale browser tab, an import, or a script predates the field.
        They all described the same thing — money against the order — so they
        must keep working rather than turning into a 400."""
        self.client.post(
            reverse("billing:order_payment_add", args=[self.order.id]),
            {"amount": "500", "mode": "cash", "note": ""},
            headers={"hx-request": "true"},
        )
        p = self.order.payments.get()
        self.assertEqual(p.kind, PaymentKind.ADVANCE)
        self.assertIsNone(p.delivery)

    def test_a_delivery_from_another_order_is_refused_at_the_view(self):
        """The form only offers this order's deliveries, so reaching this path
        means the POST was hand-built. Say so rather than filing the money."""
        other = Order.objects.create(customer=self.customer, total=Decimal("300"))
        other_delivery = Delivery.objects.create(order=other, delivered_on=date(2026, 10, 13))
        resp = self.client.post(
            reverse("billing:order_payment_add", args=[self.order.id]),
            {
                "amount": "150",
                "kind": PaymentKind.DELIVERY,
                "delivery": str(other_delivery.id),
                "mode": "cash",
                "note": "",
            },
            headers={"hx-request": "true"},
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(self.order.payments.count(), 0)

    def test_picker_lists_this_orders_deliveries_with_a_date(self):
        response = self.client.get(reverse("orders:detail", args=[self.order.id]))
        # The operator picks by "the 12 Oct hand-over", not by an opaque id.
        self.assertContains(response, "12 Oct 2026")

    def test_history_says_what_each_payment_was_for(self):
        record_payment(self.order, "500", PaymentMode.CASH, self.user, kind=PaymentKind.ADVANCE)
        record_payment(
            self.order, "300", PaymentMode.CASH, self.user,
            kind=PaymentKind.DELIVERY, delivery=self.delivery,
        )
        response = self.client.get(reverse("orders:detail", args=[self.order.id]))
        self.assertContains(response, "Advance")
        self.assertContains(response, "Against delivery")
        self.assertContains(response, "against delivery of 12 Oct 2026")

    def test_an_unpriced_order_reports_what_was_collected(self):
        """"Amount TBD" alone hides money the shop is already holding, which is
        exactly the situation this whole requirement was raised for."""
        order = Order.objects.create(customer=self.customer)  # total not agreed
        record_payment(order, "500", PaymentMode.CASH, self.user, kind=PaymentKind.ADVANCE)
        response = self.client.get(reverse("orders:detail", args=[order.id]))
        self.assertContains(response, "500.00")
        self.assertContains(response, "Amount TBD")
