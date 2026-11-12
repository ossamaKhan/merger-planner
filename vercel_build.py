"""Runs on every Vercel deploy (see pyproject.toml): creates/updates the database tables in
Supabase and makes sure the login user from the environment variables exists."""
import os

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.contrib.auth import get_user_model  # noqa: E402
from django.core.management import call_command  # noqa: E402

call_command("migrate", interactive=False, verbosity=1)

user, pw = os.environ.get("PLANNER_ADMIN_USER"), os.environ.get("PLANNER_ADMIN_PASSWORD")
if user and pw:
    U = get_user_model()
    u, made = U.objects.get_or_create(username=user, defaults={"is_staff": True, "is_superuser": True})
    u.is_staff = u.is_superuser = True
    u.set_password(pw)
    u.save()
    print(("Created" if made else "Updated"), "login user", user)
else:
    print("PLANNER_ADMIN_USER / PLANNER_ADMIN_PASSWORD not set — no login user created.")
