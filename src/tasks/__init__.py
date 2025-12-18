"""
Celery task modules for data collection.

Tasks are scheduled by Celery Beat and run by Celery Workers.
"""

from src.tasks.celery_app import app

__all__ = ["app"]
