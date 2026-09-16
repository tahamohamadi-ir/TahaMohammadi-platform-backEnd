"""Content admin surface (post-DEBT-0003).

SPA owns CRUD via ``/api/v1/admin/*``. Staff HTML preview and legacy profile
editor live under ``/staff/`` (see ``urls_staff``).
"""

from django.contrib import admin

from apps.content.models import Method


@admin.register(Method)
class MethodAdmin(admin.ModelAdmin):
    """Staff HTML fallback for Atlas node source records."""

    list_display = ("title", "locale", "status", "sort_order")
    search_fields = ("title", "slug")
