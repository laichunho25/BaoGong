"""Celery application. Beat schedules live in ``CELERY_BEAT_SCHEDULE`` (settings)."""

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

app = Celery("qs")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
