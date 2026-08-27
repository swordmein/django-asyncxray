from __future__ import annotations

import time


class LegacySyncMiddleware:
    sync_capable = True
    async_capable = False

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Deliberately synchronous work so AsyncXRay can separate
        # execution time from executor queue wait.
        time.sleep(0.05)
        response = self.get_response(request)
        response["X-Legacy-Middleware"] = "1"
        return response
