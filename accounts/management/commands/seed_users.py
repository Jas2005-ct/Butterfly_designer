from django.contrib.auth.models import User
from django.core.management.base import BaseCommand

from accounts.models import Role

# Initial shop accounts. Change these passwords immediately in production.
SEED_USERS = [
    {"username": "admin", "password": "admin@123", "role": Role.ADMIN},
    {"username": "admin2", "password": "admin2@123", "role": Role.ADMIN},
    {"username": "manager", "password": "manager@123", "role": Role.MANAGER},
]


class Command(BaseCommand):
    help = "Create the initial shop accounts (2 admins + 1 manager). Idempotent."

    def handle(self, *args, **options):
        for spec in SEED_USERS:
            user, created = User.objects.get_or_create(
                username=spec["username"],
                defaults={"is_active": True},
            )
            if created:
                user.set_password(spec["password"])
                user.save(update_fields=["password"])
                self.stdout.write(self.style.SUCCESS(f"Created user '{spec['username']}'"))
            else:
                self.stdout.write(f"User '{spec['username']}' already exists, skipping")
            profile = user.profile
            if profile.role != spec["role"]:
                profile.role = spec["role"]
                profile.save(update_fields=["role"])
                self.stdout.write(
                    self.style.SUCCESS(f"Set role '{spec['role']}' for '{spec['username']}'")
                )
        self.stdout.write(self.style.SUCCESS("Seed complete."))
