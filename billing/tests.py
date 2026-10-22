from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from customers.models import Customer
from orders.models import Order, OrderStage
from .models import Payment, PaymentMode
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
        self.assertContains(response, "Amount not agreed yet")
        self.assertContains(response, "TBD")
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
