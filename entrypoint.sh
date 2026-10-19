#!/bin/sh
# entrypoint.sh – collect static files then start gunicorn
# This ensures static assets are up‑to‑date in the container image.

# Exit on any error
set -e

# One-off task override.
# ECS task definitions override the image CMD but NOT the ENTRYPOINT, so a
# task override arrives here as arguments. This lets a deploy run migrations
# as a single dedicated task instead of on every container boot.
#   command: ["migrate", "--noinput"]  ->  python manage.py migrate --noinput
if [ "$#" -gt 0 ] && [ "$1" = "migrate" ]; then
    exec python manage.py "$@"
fi

# Collect static files (no input)
python manage.py collectstatic --noinput

# Migrations are deliberately NOT run here.
# ECS Express Mode canary deployments can start two containers at the same
# time, and concurrent DDL against Postgres is unsafe. Run migrations as a
# one-off task instead (see docs/deployment-plan.md).

# Exec gunicorn (replace with any additional flags you need)
exec gunicorn core.wsgi:application --bind 0.0.0.0:${PORT:-8000}
