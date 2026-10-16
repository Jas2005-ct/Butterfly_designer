from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from customers.models import Customer
from orders.models import (
    Order,
    OrderItem,
    OrderStage,
    OrderStageHistory,
    ServiceType,
    generate_order_number,
)
from orders.services import advance_stage, can_transition, VALID_NEXT_STAGES


class OrderModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="mgr", password="secret123")
        self.customer = Customer.objects.create(name="Priya", phone="9876543210")

    def test_order_number_auto_generated_and_sequential(self):
        o1 = Order.objects.create(customer=self.customer)
        o2 = Order.objects.create(customer=self.customer)
        year = o1.created_at.strftime("%y")
        self.assertEqual(o1.order_number, f"BD-{year}-0001")
        self.assertEqual(o2.order_number, f"BD-{year}-0002")

    def test_order_number_helper(self):
        number = generate_order_number()
        self.assertRegex(number, r"^BD-\d{2}-\d{4}$")

    def test_track_token_defaults_unique(self):
        o1 = Order.objects.create(customer=self.customer)
        o2 = Order.objects.create(customer=self.customer)
        self.assertIsNotNone(o1.track_token)
        self.assertNotEqual(o1.track_token, o2.track_token)

    def test_defaults(self):
        order = Order.objects.create(customer=self.customer)
        self.assertEqual(order.stage, OrderStage.MEASUREMENT)
        self.assertEqual(order.service_type, ServiceType.OTHER)
        self.assertEqual(order.total, 0)

    def test_stage_history_str(self):
        order = Order.objects.create(customer=self.customer)
        history = OrderStageHistory.objects.create(
            order=order,
            from_stage="",
            to_stage=OrderStage.CUTTING,
            changed_by=self.user,
        )
        self.assertIn("cutting", str(history))


class OrderStageMachineTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="mgr", password="secret123")
        self.admin = User.objects.create_user(username="adm", password="secret123")
        self.admin.profile.role = "admin"
        self.admin.profile.save()
        self.customer = Customer.objects.create(name="Test Customer", phone="9876543210")

    def test_forward_transitions_allowed(self):
        """All forward transitions in lifecycle are allowed."""
        order = Order.objects.create(customer=self.customer, stage=OrderStage.MEASUREMENT)
        advance_stage(order, OrderStage.CUTTING, self.user)
        order.refresh_from_db()
        self.assertEqual(order.stage, OrderStage.CUTTING)

        advance_stage(order, OrderStage.STITCHING, self.user)
        order.refresh_from_db()
        self.assertEqual(order.stage, OrderStage.STITCHING)

        advance_stage(order, OrderStage.FITTING, self.user)
        order.refresh_from_db()
        self.assertEqual(order.stage, OrderStage.FITTING)

        advance_stage(order, OrderStage.READY, self.user)
        order.refresh_from_db()
        self.assertEqual(order.stage, OrderStage.READY)

        advance_stage(order, OrderStage.DELIVERED, self.user)
        order.refresh_from_db()
        self.assertEqual(order.stage, OrderStage.DELIVERED)

    def test_backward_transition_requires_note(self):
        """Moving backward requires a note."""
        order = Order.objects.create(customer=self.customer, stage=OrderStage.STITCHING)

        # Without note - should fail
        with self.assertRaises(ValueError):
            advance_stage(order, OrderStage.CUTTING, self.user)

        # With note - should succeed
        advance_stage(order, OrderStage.CUTTING, self.user, note="Customer requested change")
        order.refresh_from_db()
        self.assertEqual(order.stage, OrderStage.CUTTING)

    def test_cancel_requires_note(self):
        """Cancelling requires a note."""
        order = Order.objects.create(customer=self.customer, stage=OrderStage.FITTING)
        with self.assertRaises(ValueError):
            advance_stage(order, OrderStage.CANCELLED, self.user)

        advance_stage(order, OrderStage.CANCELLED, self.user, note="Customer cancelled")
        order.refresh_from_db()
        self.assertEqual(order.stage, OrderStage.CANCELLED)

    def test_admin_can_force_any_transition(self):
        """Admin can force any transition with note."""
        order = Order.objects.create(customer=self.customer, stage=OrderStage.MEASUREMENT)
        # Admin forces jump to READY
        advance_stage(order, OrderStage.READY, self.admin, note="Admin override", force=True)
        order.refresh_from_db()
        self.assertEqual(order.stage, OrderStage.READY)

    def test_history_recorded(self):
        """Each transition creates a history record."""
        order = Order.objects.create(customer=self.customer, stage=OrderStage.MEASUREMENT)
        advance_stage(order, OrderStage.CUTTING, self.user, note="Cutting started")

        history = OrderStageHistory.objects.filter(order=order)
        self.assertEqual(history.count(), 1)
        h = history.first()
        self.assertEqual(h.from_stage, OrderStage.MEASUREMENT)
        self.assertEqual(h.to_stage, OrderStage.CUTTING)
        self.assertEqual(h.changed_by, self.user)
        self.assertEqual(h.note, "Cutting started")

    def test_can_transition_helper(self):
        """can_transition helper works correctly."""
        self.assertTrue(can_transition(OrderStage.MEASUREMENT, OrderStage.CUTTING))
        self.assertTrue(can_transition(OrderStage.CUTTING, OrderStage.STITCHING))
        self.assertFalse(can_transition(OrderStage.MEASUREMENT, OrderStage.STITCHING))  # skip not allowed
        self.assertFalse(can_transition(OrderStage.DELIVERED, OrderStage.MEASUREMENT))  # backward not allowed for non-admin

    def test_reopen_cancelled(self):
        """Cancelled order can be reopened to measurement."""
        order = Order.objects.create(customer=self.customer, stage=OrderStage.FITTING)
        advance_stage(order, OrderStage.CANCELLED, self.user, note="Customer changed mind")
        advance_stage(order, OrderStage.MEASUREMENT, self.user, note="Customer returned")
        order.refresh_from_db()
        self.assertEqual(order.stage, OrderStage.MEASUREMENT)


class OrderViewsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="mgr", password="secret123")
        self.client.login(username="mgr", password="secret123")
        self.customer = Customer.objects.create(name="Test", phone="9876543210")

    def test_board_renders(self):
        response = self.client.get(reverse("orders:board"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Order Board")

    def test_shop_pages_use_local_assets_not_cdn(self):
        """Compiled CSS + vendored HTMX: no CDN dependency on shop pages."""
        order = Order.objects.create(customer=self.customer, stage=OrderStage.MEASUREMENT)
        for url in [
            reverse("orders:board"),
            reverse("orders:detail", args=[order.id]),
        ]:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertNotContains(response, "cdn.tailwindcss.com")
            self.assertNotContains(response, "unpkg.com")
            self.assertContains(response, "styles")
            self.assertContains(response, "shop/manifest.json")
        # Login page renders for anonymous users (logged-in users redirect).
        self.client.logout()
        response = self.client.get(reverse("accounts:login"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "cdn.tailwindcss.com")
        self.assertContains(response, "styles")

    def test_detail_shows_stepper_and_primary_action(self):
        order = Order.objects.create(customer=self.customer, stage=OrderStage.STITCHING)
        response = self.client.get(reverse("orders:detail", args=[order.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "order-stepper")
        self.assertContains(response, "Advance to")
        self.assertContains(response, "More actions")

    def test_advance_refreshes_status_blocks(self):
        order = Order.objects.create(customer=self.customer, stage=OrderStage.MEASUREMENT)
        response = self.client.post(
            reverse("orders:advance_stage", args=[order.id]),
            {"to_stage": OrderStage.CUTTING},
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "hx-swap-oob")

    def test_board_active_tab_hides_delivered(self):
        delivered = Order.objects.create(customer=self.customer, stage=OrderStage.DELIVERED)
        response = self.client.get(reverse("orders:board"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, delivered.order_number)
        response = self.client.get(reverse("orders:board") + "?tab=delivered")
        self.assertContains(response, delivered.order_number)

    def test_board_has_pwa_manifest_link(self):
        response = self.client.get(reverse("orders:board"))
        self.assertContains(response, "shop/manifest.json")

    def test_detail_has_print_receipt(self):
        order = Order.objects.create(customer=self.customer, stage=OrderStage.READY)
        response = self.client.get(reverse("orders:detail", args=[order.id]))
        self.assertContains(response, "Print receipt")
        self.assertContains(response, "order-receipt")
        self.assertContains(response, "Balance due")

    def test_board_overdue_badge(self):
        yesterday = timezone.now().date() - timedelta(days=1)
        order = Order.objects.create(
            customer=self.customer, stage=OrderStage.CUTTING, promised_date=yesterday
        )
        response = self.client.get(reverse("orders:board"))
        self.assertContains(response, "Overdue")
        self.assertContains(response, order.order_number)

    def test_create_order_with_new_customer(self):
        response = self.client.post(
            reverse("orders:create"),
            {
                "customer": "",
                "customer_name": "New Customer",
                "customer_phone": "9999999999",
                "service_type": "blouse",
                "total": "500.00",
                "description": "Test order",
            },
        )
        order = Order.objects.get(order_number__startswith="BD-")
        self.assertRedirects(response, reverse("orders:detail", args=[order.id]))
        self.assertEqual(order.customer.name, "New Customer")

    def test_create_order_with_existing_customer(self):
        response = self.client.post(
            reverse("orders:create"),
            {
                "customer": str(self.customer.id),
                "service_type": "bridal",
                "total": "5000.00",
            },
        )
        order = Order.objects.get(order_number__startswith="BD-")
        self.assertRedirects(response, reverse("orders:detail", args=[order.id]))
        self.assertEqual(order.customer, self.customer)

    def test_stage_advance_htmx(self):
        order = Order.objects.create(customer=self.customer, stage=OrderStage.MEASUREMENT)
        response = self.client.post(
            reverse("orders:advance_stage", args=[order.id]),
            {"to_stage": OrderStage.CUTTING},
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(response.status_code, 200)
        order.refresh_from_db()
        self.assertEqual(order.stage, OrderStage.CUTTING)

    def test_create_order_invalid_total_rejected(self):
        response = self.client.post(
            reverse("orders:create"),
            {
                "customer": str(self.customer.id),
                "service_type": "blouse",
                "total": "not-a-number",
            },
        )
        self.assertRedirects(response, reverse("orders:create"))
        self.assertFalse(Order.objects.exists())

    def test_stage_advance_invalid_returns_400(self):
        order = Order.objects.create(customer=self.customer, stage=OrderStage.MEASUREMENT)
        # Skip stage
        response = self.client.post(
            reverse("orders:advance_stage", args=[order.id]),
            {"to_stage": OrderStage.STITCHING},
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(response.status_code, 400)


class TrackingViewTests(TestCase):
    def setUp(self):
        self.customer = Customer.objects.create(name="Track Customer", phone="9876543210")
        self.order = Order.objects.create(customer=self.customer, stage=OrderStage.CUTTING)

    def test_track_page_no_login(self):
        """Public tracking page works without login."""
        url = reverse("orders:track", args=[self.order.track_token])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.order.order_number)

    def test_track_page_uses_compiled_css(self):
        url = reverse("orders:track", args=[self.order.track_token])
        response = self.client.get(url)
        self.assertNotContains(response, "cdn.tailwindcss.com")
        self.assertContains(response, "styles")

    def test_track_page_shows_milestones(self):
        url = reverse("orders:track", args=[self.order.track_token])
        response = self.client.get(url)
        self.assertContains(response, "Measurement")
        self.assertContains(response, "Cutting")
        # Current stage should be highlighted
        self.assertContains(response, "Current")

    def test_track_page_cancelled_order(self):
        """Cancelled orders render without crashing (regression: index lookup)."""
        user = User.objects.create_user(username="mgr", password="secret123")
        advance_stage(self.order, OrderStage.CANCELLED, user, note="Customer cancelled")
        url = reverse("orders:track", args=[self.order.track_token])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Cancelled")


class CustomerViewsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="mgr", password="secret123")
        self.client.login(username="mgr", password="secret123")

    def test_customer_list_renders(self):
        response = self.client.get(reverse("customers:list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Customers")

    def test_create_customer(self):
        response = self.client.post(
            reverse("customers:create"),
            {"name": "New Customer", "phone": "9999999999"},
        )
        self.assertRedirects(response, reverse("customers:list"))
        self.assertTrue(Customer.objects.filter(name="New Customer").exists())

    def test_customer_detail_shows_orders(self):
        customer = Customer.objects.create(name="Test", phone="9876543210")
        Order.objects.create(customer=customer, stage=OrderStage.MEASUREMENT)
        response = self.client.get(reverse("customers:detail", args=[customer.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Order History")


class GenerateDummyDataCommandTests(TestCase):
    def test_generate_dummy_data_command(self):
        from io import StringIO
        from django.core.management import call_command

        out = StringIO()
        call_command(
            "generate_dummy_data",
            customers=3,
            orders=5,
            days=15,
            seed=42,
            stdout=out,
        )
        self.assertIn("DUMMY DATA GENERATION COMPLETE", out.getvalue())
        self.assertGreaterEqual(Customer.objects.count(), 3)
        self.assertGreaterEqual(Order.objects.count(), 5)
        self.assertTrue(OrderItem.objects.exists())