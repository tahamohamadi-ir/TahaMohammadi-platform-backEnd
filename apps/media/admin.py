"""Admin registration for Media — upload validators wired explicitly."""

from django import forms
from django.contrib import admin

from apps.media.models import Media
from apps.media.validators import validate_file_size, validate_file_type


class MediaAdminForm(forms.ModelForm):
    file = forms.FileField(validators=[validate_file_type, validate_file_size])

    class Meta:
        model = Media
        fields = ["file", "title", "alt_text", "is_active"]


@admin.register(Media)
class MediaAdmin(admin.ModelAdmin):
    form = MediaAdminForm
    list_display = ("title", "mime", "size", "is_active", "created_at")
    list_filter = ("is_active", "mime")
    search_fields = ("title", "alt_text")
