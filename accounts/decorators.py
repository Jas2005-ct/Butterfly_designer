from functools import wraps

from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden

from .models import Role


def admin_required(view_func):
    @wraps(view_func)
    @login_required
    def _wrapped(request, *args, **kwargs):
        profile = getattr(request.user, "profile", None)
        if profile is None or profile.role != Role.ADMIN:
            return HttpResponseForbidden("Admin access required.")
        return view_func(request, *args, **kwargs)

    return _wrapped
