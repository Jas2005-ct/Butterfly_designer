from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.contrib.auth.models import User

from .models import Profile


class ProfileInline(admin.StackedInline):
    model = Profile
    can_delete = False
    verbose_name_plural = "Profile"


class UserAdmin(DjangoUserAdmin):
    inlines = (ProfileInline,)

    def get_inline_instances(self, request, obj=None):
        """Keep the profile inline off the ADD page.

        Why: a new user's profile is created by the post_save signal
        (accounts.signals.create_profile) inside the same request
        transaction. On the add page the inline then inserted a second
        profile row for the same user and crashed on
        accounts_profile_user_id_key's unique key - and the rollback took
        the new user with it, so the admin reported a duplicate id while
        pgAdmin showed no such user anywhere. Edit keeps the inline: the
        profile already exists there, and the inline is how the role gets
        corrected by hand.

        Args:
            request: the current admin request.
            obj: the User being edited, or None on the add page.

        Returns:
            Inline instances - none on add, the stored inlines otherwise.
        """
        if obj is None:
            return []
        return super().get_inline_instances(request, obj)


admin.site.unregister(User)
admin.site.register(User, UserAdmin)
