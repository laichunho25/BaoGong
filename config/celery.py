"""Celery application. Beat schedules are declared by each app's tasks module."""

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

app = Celery("qs")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
