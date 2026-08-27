from __future__ import annotations

import asyncio

from django.http import JsonResponse


async def async_probe(request):
    await asyncio.sleep(0.01)

    return JsonResponse(
        {
            "ok": True,
            "view": "async_probe",
        }
    )
