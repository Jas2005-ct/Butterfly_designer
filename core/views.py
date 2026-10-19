"""Project-level views for deployment (health checks)."""

from django.http import JsonResponse


def healthz(request):
    """Lightweight liveness probe for the App Runner health check.

    Deliberately does not touch the database so the probe stays fast and does
    not fail while migrations are still running during a deploy.

    Args:
        request: The current HttpRequest.

    Returns:
        HttpResponse: A 200 JSON payload {"status": "ok"}.
    """
    return JsonResponse({"status": "ok"})
