from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from customers.models import Customer, CustomerMeasurement


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


class CustomerMeasurementTests(TestCase):
    """The eight inch fields on the customer forms, and the rules around them."""

    def setUp(self):
        self.user = User.objects.create_user(username="mgr", password="secret123")
        self.client.login(username="mgr", password="secret123")

    def test_create_records_measurements(self):
        response = self.client.post(
            reverse("customers:create"),
            {
                "name": "Priya",
                "phone": "9876543210",
                "bust": "34",
                "waist": "28",
                "hips": "38",
                "shoulder": "14.5",
                "sleeve_length": "22",
                "blouse_length": "15",
                "height": "64",
                "neck_depth": "7.5",
            },
        )
        self.assertRedirects(response, reverse("customers:list"))
        measurement = Customer.objects.get(name="Priya").measurement
        self.assertEqual(measurement.bust, Decimal("34"))
        self.assertEqual(measurement.shoulder, Decimal("14.5"))
        self.assertEqual(measurement.neck_depth, Decimal("7.5"))
        # Guards against a ninth field being added to the model but not to
        # the form, which would silently never be collectable.
        self.assertEqual(len(measurement.MEASUREMENT_LABELS), 8)

    def test_create_leaves_unmeasured_fields_blank(self):
        """A blouse customer is not made to supply a height."""
        self.client.post(
            reverse("customers:create"),
            {"name": "Priya", "phone": "9876543210", "bust": "34"},
        )
        measurement = Customer.objects.get(name="Priya").measurement
        self.assertEqual(measurement.bust, Decimal("34"))
        self.assertIsNone(measurement.height)
        self.assertIsNone(measurement.hips)

    def test_create_without_any_measurement_stores_no_row(self):
        """Blank boxes must not manufacture an empty measurement record."""
        self.client.post(reverse("customers:create"), {"name": "Deepa", "phone": "9123456780"})
        customer = Customer.objects.get(name="Deepa")
        self.assertFalse(CustomerMeasurement.objects.filter(customer=customer).exists())
        # hasattr is False rather than raising: a missing reverse one-to-one
        # surfaces as an AttributeError, which is what the templates rely on.
        self.assertFalse(hasattr(customer, "measurement"))

    def test_non_numeric_measurement_is_rejected(self):
        response = self.client.post(
            reverse("customers:create"),
            {"name": "Priya", "phone": "9876543210", "waist": "twenty"},
        )
        self.assertContains(response, "Waist must be a number", status_code=400)

    def test_out_of_range_measurement_is_rejected(self):
        """Zero, negatives and transposed digits are typos, not measurements."""
        for bad in ("0", "-3", "430", "100.01"):
            with self.subTest(value=bad):
                response = self.client.post(
                    reverse("customers:create"),
                    {"name": "Priya", "phone": "9876543210", "hips": bad},
                )
                self.assertContains(response, "Hips must be more than 0", status_code=400)

    def test_rejected_measurement_creates_no_customer(self):
        """Validating before writing means a typo leaves no half-made row."""
        self.client.post(
            reverse("customers:create"),
            {"name": "Priya", "phone": "9876543210", "waist": "twenty"},
        )
        self.assertFalse(Customer.objects.filter(name="Priya").exists())

    def test_rejected_measurement_repopulates_the_form(self):
        """Fixing one bad box must not cost a retyped form."""
        response = self.client.post(
            reverse("customers:create"),
            {"name": "Priya", "phone": "9876543210", "bust": "34", "waist": "twenty"},
        )
        self.assertContains(response, 'value="34"', status_code=400)
        self.assertContains(response, "twenty", status_code=400)

    def test_the_typed_echo_survives_the_browser(self):
        """The echo must be visible, not merely present in the HTML.

        A number widget sanitizes a non-numeric value into an empty box
        before it is drawn, so the refusal page could carry "twenty" and
        still LOOK blank in a real browser - silently undoing the whole
        keep-what-was-typed contract for the most common typo class. Text
        plus inputmode shows what was typed and keeps the numeric
        keyboard; the 0-100 range rules belong to the view alone.
        """
        response = self.client.post(
            reverse("customers:create"),
            {"name": "Priya", "phone": "9876543210", "waist": "twenty"},
        )
        self.assertContains(response, 'type="text" name="waist"', status_code=400)
        self.assertContains(response, 'value="twenty"', status_code=400)
        self.assertNotIn('type="number"', response.content.decode())

    def test_a_refused_customer_keeps_the_typed_name_and_phone(self):
        """The identity boxes used to render empty on any refusal, because
        the view passed `form` over and the template ignored it."""
        response = self.client.post(
            reverse("customers:create"),
            {"name": "", "phone": "9876543210", "notes": "calls after 6"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "Name and phone are required.", status_code=400)
        self.assertContains(response, 'value="9876543210"', status_code=400)
        self.assertContains(response, "calls after 6", status_code=400)
        self.assertFalse(Customer.objects.exists())

    def test_edit_refusal_keeps_the_typed_identity_fields(self):
        """An edited name must not be silently replaced by the stored copy
        just because a measurement elsewhere in the form was wrong."""
        customer = Customer.objects.create(name="Priya", phone="9876543210", notes="stored")
        response = self.client.post(
            reverse("customers:edit", args=[customer.id]),
            {"name": "Priya Nair", "phone": "9876500000", "waist": "twenty"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, 'value="Priya Nair"', status_code=400)
        self.assertContains(response, 'value="9876500000"', status_code=400)
        self.assertContains(response, "Waist must be a number", status_code=400)
        customer.refresh_from_db()
        # Nothing written on a refusal.
        self.assertEqual(customer.name, "Priya")
        self.assertEqual(customer.phone, "9876543210")
        self.assertFalse(CustomerMeasurement.objects.filter(customer=customer).exists())

    def test_edit_prefills_stored_measurements(self):
        customer = Customer.objects.create(name="Priya", phone="9876543210")
        CustomerMeasurement.objects.create(customer=customer, bust=Decimal("34"))
        response = self.client.get(reverse("customers:edit", args=[customer.id]))
        self.assertContains(response, 'value="34.00"')

    def test_edit_saves_changed_measurements(self):
        customer = Customer.objects.create(name="Priya", phone="9876543210")
        CustomerMeasurement.objects.create(customer=customer, bust=Decimal("34"))
        self.client.post(
            reverse("customers:edit", args=[customer.id]),
            {"name": "Priya", "phone": "9876543210", "bust": "36"},
        )
        customer.measurement.refresh_from_db()
        self.assertEqual(customer.measurement.bust, Decimal("36"))

    def test_edit_clears_a_measurement_left_blank(self):
        """The form comes pre-filled, so an emptied box means "erase it"."""
        customer = Customer.objects.create(name="Priya", phone="9876543210")
        CustomerMeasurement.objects.create(
            customer=customer, bust=Decimal("34"), waist=Decimal("28")
        )
        self.client.post(
            reverse("customers:edit", args=[customer.id]),
            {"name": "Priya", "phone": "9876543210", "waist": "28"},
        )
        customer.measurement.refresh_from_db()
        self.assertIsNone(customer.measurement.bust)
        self.assertEqual(customer.measurement.waist, Decimal("28"))

    def test_edit_clearing_everything_removes_the_row(self):
        customer = Customer.objects.create(name="Priya", phone="9876543210")
        CustomerMeasurement.objects.create(customer=customer, bust=Decimal("34"))
        self.client.post(
            reverse("customers:edit", args=[customer.id]),
            {"name": "Priya", "phone": "9876543210"},
        )
        self.assertFalse(CustomerMeasurement.objects.filter(customer=customer).exists())

    def test_detail_page_shows_only_what_was_measured(self):
        customer = Customer.objects.create(name="Priya", phone="9876543210")
        CustomerMeasurement.objects.create(customer=customer, bust=Decimal("34"))
        response = self.client.get(reverse("customers:detail", args=[customer.id]))
        self.assertContains(response, "Bust")
        self.assertContains(response, "34.00")
        self.assertNotContains(response, "Neck depth")

    def test_detail_page_offers_an_empty_state(self):
        customer = Customer.objects.create(name="Deepa", phone="9123456780")
        response = self.client.get(reverse("customers:detail", args=[customer.id]))
        self.assertContains(response, "No measurements on file")

    def test_customer_form_requires_login(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse("customers:create")).status_code, 302)
        self.assertEqual(self.client.post(reverse("customers:create"), {}).status_code, 302)