"""URL conf for the test suite: mounts the Django admin and a non-admin route."""

from django.contrib import admin
from django.http import HttpResponse
from django.urls import path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("public/", lambda request: HttpResponse("public"), name="public"),
]
