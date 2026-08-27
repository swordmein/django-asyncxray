from django.urls import path

from demoapp.views import async_probe


urlpatterns = [
    path("xray/", async_probe, name="async-probe"),
]
