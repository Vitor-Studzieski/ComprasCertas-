"""Rate limiting with the API's stable public error envelope."""

from rest_framework.exceptions import Throttled
from rest_framework.throttling import ScopedRateThrottle


class IdentityScopedRateThrottle(ScopedRateThrottle):
    pass
