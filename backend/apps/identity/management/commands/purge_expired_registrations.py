"""Remove expired signup intents and their password hashes."""

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.identity.models import PendingRegistration


class Command(BaseCommand):
    help = "Delete expired, unconfirmed signup intents."

    def handle(self, *args, **options):
        deleted, _ = PendingRegistration.objects.filter(expires_at__lte=timezone.now()).delete()
        self.stdout.write(f"Removed {deleted} expired registration record(s).")
