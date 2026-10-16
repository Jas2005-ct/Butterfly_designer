from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from customers.models import Customer


class CustomerModelTests(TestCase):
    def test_customer_str(self):
        c = Customer.objects.create(name="Priya", phone="9876543210")
        self.assertEqual(str(c), "Priya (9876543210)")


class CustomerSearchTests(TestCase):
    """Live picker search: name/phone match, min length, login guard."""

    def setUp(self):
        self.user = User.objects.create_user(username="mgr", password="secret123")
        self.client.login(username="mgr", password="secret123")
        Customer.objects.create(name="Priya Nair", phone="9876543210")
        Customer.objects.create(name="Deepa Rao", phone="9123456780")

    def url(self, q=""):
        return reverse("customers:search") + (f"?q={q}" if q else "")

    def test_match_by_name(self):
        response = self.client.get(self.url("pri"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Priya Nair")
        self.assertNotContains(response, "Deepa Rao")

    def test_match_by_phone(self):
        response = self.client.get(self.url("9123"))
        self.assertContains(response, "Deepa Rao")
        self.assertNotContains(response, "Priya Nair")

    def test_short_query_returns_hint(self):
        response = self.client.get(self.url("p"))
        self.assertContains(response, "at least 2")
        self.assertNotContains(response, "Priya Nair")

    def test_no_match_state(self):
        response = self.client.get(self.url("zzz-no-such-customer"))
        self.assertContains(response, "No match")

    def test_requires_login(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.url("pri")).status_code, 302)

    def test_create_page_has_search_and_preselect(self):
        customer = Customer.objects.get(name="Priya Nair")
        response = self.client.get(reverse("orders:create"))
        self.assertContains(response, "customer-search")
        response = self.client.get(reverse("orders:create") + f"?customer={customer.id}")
        self.assertContains(response, "Priya Nair")