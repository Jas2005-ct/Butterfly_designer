#!/bin/sh
# entrypoint.sh – collect static files then start gunicorn
# This ensures static assets are up‑to‑date in the container image.

# Exit on any error
set -e

# Collect static files (no input)
python manage.py collectstatic --noinput

# Exec gunicorn (replace with any additional flags you need)
exec gunicorn core.wsgi:application --bind 0.0.0.0:${PORT:-8000}
