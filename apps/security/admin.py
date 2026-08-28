"""Read-only Django admin registration for AuditLog (P3 admin-security slice)."""

from django.contrib import admin

from apps.security.models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "action", "user", "model_name", "object_id", "ip")
    list_filter = ("action", "model_name")
    search_fields = ("action", "model_name", "object_id", "ip", "detail")
    date_hierarchy = "created_at"
    ordering = ("-created_at",)
    readonly_fields = tuple(field.name for field in AuditLog._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser
