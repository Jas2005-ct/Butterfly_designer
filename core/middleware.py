"""Middleware that keeps container health checks working behind a load balancer."""

from django.conf import settings
from django.http import HttpRequest, HttpResponse


class HealthCheckHostMiddleware:
    """Allow the load balancer's health check to pass Django's Host check.

    An Application Load Balancer probes the target with the container's
    private IP as the ``Host`` header (for example ``172.31.16.84:8000``).
    That address changes every time the task is replaced, so it can never be
    listed in ``ALLOWED_HOSTS``. Django rejects the probe with
    ``400 DisallowedHost`` - because ``CommonMiddleware`` calls
    ``request.get_host()`` for every request - the target is then marked
    unhealthy and the load balancer answers ``503`` to real traffic.

    This middleware rewrites the ``Host`` header for the health path only,
    reusing a host that is already allowed. Every other request keeps the
    standard Host validation.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        if request.path == "/healthz" and settings.ALLOWED_HOSTS:
            request.META["HTTP_HOST"] = settings.ALLOWED_HOSTS[0]
        return self.get_response(request)