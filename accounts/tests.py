from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from .models import Profile, Role


class ProfileSignalTests(TestCase):
    def test_profile_auto_created_with_manager_default(self):
        user = User.objects.create_user(username="newuser", password="pass1234")
        self.assertTrue(Profile.objects.filter(user=user).exists())
        self.assertEqual(user.profile.role, Role.MANAGER)

    def test_is_admin_property(self):
        admin_user = User.objects.create_user(username="boss", password="pass1234")
        admin_user.profile.role = Role.ADMIN
        admin_user.profile.save()
        self.assertTrue(admin_user.profile.is_admin)


class SeedUsersCommandTests(TestCase):
    def test_seed_creates_three_users_with_roles(self):
        call_command("seed_users")
        self.assertEqual(User.objects.count(), 3)
        self.assertEqual(User.objects.get(username="admin").profile.role, Role.ADMIN)
        self.assertEqual(User.objects.get(username="admin2").profile.role, Role.ADMIN)
        self.assertEqual(User.objects.get(username="manager").profile.role, Role.MANAGER)

    def test_seed_is_idempotent(self):
        call_command("seed_users")
        call_command("seed_users")
        self.assertEqual(User.objects.count(), 3)

    def test_seed_sets_passwords(self):
        call_command("seed_users")
        user = User.objects.get(username="admin")
        self.assertTrue(user.check_password("admin@123"))


class LoginViewTests(TestCase):
    def test_login_page_renders(self):
        response = self.client.get(reverse("accounts:login"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Butterfly Shop")

    def test_login_success_redirects_to_board(self):
        User.objects.create_user(username="staff", password="secret123")
        response = self.client.post(
            reverse("accounts:login"),
            {"username": "staff", "password": "secret123"},
        )
        self.assertRedirects(response, reverse("orders:board"))

    def test_login_failure_shows_error(self):
        response = self.client.post(
            reverse("accounts:login"),
            {"username": "nobody", "password": "wrong"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Invalid username or password")

    def test_logout_requires_post_and_works(self):
        User.objects.create_user(username="staff", password="secret123")
        self.client.login(username="staff", password="secret123")
        response = self.client.post(reverse("accounts:logout"))
        self.assertRedirects(response, reverse("accounts:login"))

    def test_logout_get_does_not_log_out(self):
        User.objects.create_user(username="staff", password="secret123")
        self.client.login(username="staff", password="secret123")
        response = self.client.get(reverse("accounts:logout"))
        self.assertRedirects(response, reverse("orders:board"))
        self.assertTrue("_auth_user_id" in self.client.session)


class AccessControlTests(TestCase):
    def setUp(self):
        self.manager = User.objects.create_user(username="mgr", password="secret123")
        self.admin = User.objects.create_user(username="adm", password="secret123")
        self.admin.profile.role = Role.ADMIN
        self.admin.profile.save()

    def test_anonymous_redirected_from_shop(self):
        response = self.client.get(reverse("orders:board"))
        self.assertRedirects(response, f"{reverse('accounts:login')}?next={reverse('orders:board')}")

    def test_manager_blocked_from_analytics(self):
        self.client.login(username="mgr", password="secret123")
        response = self.client.get(reverse("analytics:dashboard"))
        self.assertEqual(response.status_code, 403)

    def test_admin_allowed_on_analytics(self):
        self.client.login(username="adm", password="secret123")
        response = self.client.get(reverse("analytics:dashboard"))
        self.assertEqual(response.status_code, 200)

    def test_manager_blocked_from_user_list(self):
        self.client.login(username="mgr", password="secret123")
        response = self.client.get(reverse("accounts:user_list"))
        self.assertEqual(response.status_code, 403)

    def test_admin_sees_user_list(self):
        self.client.login(username="adm", password="secret123")
        response = self.client.get(reverse("accounts:user_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "mgr")


class UserManagementTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username="adm", password="secret123")
        self.admin.profile.role = Role.ADMIN
        self.admin.profile.save()
        self.client.login(username="adm", password="secret123")

    def test_create_user_with_role(self):
        response = self.client.post(
            reverse("accounts:user_create"),
            {"username": "tailor1", "password": "pass1234", "role": Role.MANAGER},
        )
        self.assertRedirects(response, reverse("accounts:user_list"))
        user = User.objects.get(username="tailor1")
        self.assertTrue(user.check_password("pass1234"))
        self.assertEqual(user.profile.role, Role.MANAGER)

    def test_create_duplicate_username_rejected(self):
        response = self.client.post(
            reverse("accounts:user_create"),
            {"username": "adm", "password": "pass1234", "role": Role.MANAGER},
        )
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "Username already exists.", status_code=400)
        self.assertEqual(User.objects.filter(username="adm").count(), 1)

    def test_a_refused_user_create_keeps_the_typed_username(self):
        """A duplicate used to blank the whole form, so the admin retyped a
        username it had just been told was taken."""
        response = self.client.post(
            reverse("accounts:user_create"),
            {"username": "adm2", "password": "123", "role": Role.ADMIN},
        )
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "Password must be at least 6 characters.", status_code=400)
        self.assertContains(response, 'value="adm2"', status_code=400)
        self.assertFalse(User.objects.filter(username="adm2").exists())

    def test_toggle_deactivate_and_activate(self):
        target = User.objects.create_user(username="staff2", password="secret123")
        url = reverse("accounts:user_toggle", args=[target.id])
        self.client.post(url)
        target.refresh_from_db()
        self.assertFalse(target.is_active)
        self.client.post(url)
        target.refresh_from_db()
        self.assertTrue(target.is_active)

    def test_cannot_deactivate_self(self):
        url = reverse("accounts:user_toggle", args=[self.admin.id])
        self.client.post(url)
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active)

    def test_can_deactivate_second_admin(self):
        target = User.objects.create_user(username="boss2", password="secret123")
        target.profile.role = Role.ADMIN
        target.profile.save()
        url = reverse("accounts:user_toggle", args=[target.id])
        self.client.post(url)
        target.refresh_from_db()
        self.assertFalse(target.is_active)

    def test_short_password_rejected(self):
        response = self.client.post(
            reverse("accounts:user_create"),
            {"username": "weak", "password": "123", "role": Role.MANAGER},
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(User.objects.filter(username="weak").exists())
