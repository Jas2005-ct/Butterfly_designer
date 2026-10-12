from django.test import TestCase

from customers.models import Customer


class CustomerModelTests(TestCase):
    def test_customer_str(self):
        c = Customer.objects.create(name="Priya", phone="9876543210")
        self.assertEqual(str(c), "Priya (9876543210)")