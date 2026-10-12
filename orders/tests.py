from django.contrib.auth.models import User
from django.test import TestCase

from customers.models import Customer
from .models import Order, OrderStage, OrderStageHistory, ServiceType, generate_order_number


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
