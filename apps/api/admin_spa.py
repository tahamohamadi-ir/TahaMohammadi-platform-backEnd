"""Production-ready route for the built React admin SPA (ADR-0026, ADM-1 cutover).

Serves the static build at ``apps/cms/admin-frontend/dist`` under ``/admin/``
with SPA client-side fallback to ``index.html``.  In the Docker image the dist
is baked in by the multi-stage build (Dockerfile.cms ``frontend-builder`` stage).
In development the dist is built locally via ``npm run build``.
"""

from pathlib import Path

from django.conf import settings
from django.http import FileResponse, Http404
from django.views.decorators.http import require_GET

# S1 (ADR-0032): the admin SPA moved to apps/admin. Default keeps the legacy
# in-image path; production overrides via ADMIN_SPA_ROOT (Caddy-served volume
# or baked copy). Missing builds fail loudly either way.
SPA_ROOT = Path(
    getattr(settings, "ADMIN_SPA_ROOT", "")
    or (Path(settings.BASE_DIR) / "admin-frontend" / "dist")
)


@require_GET
def serve_admin_ui(request, spa_path: str = "index.html"):
    """Serve a built admin SPA asset, falling back to ``index.html`` for routes.

    Path traversal is blocked by resolving the requested path and checking it
    stays inside the SPA root. Missing builds produce a 404 with a build hint.
    """
    resolved_root = SPA_ROOT.resolve()
    target = (resolved_root / spa_path).resolve()
    try:
        target.relative_to(resolved_root)
    except ValueError:
        raise Http404("Not found.") from None

    if not target.is_file():
        target = SPA_ROOT / "index.html"
    if not target.is_file():
        raise Http404(
            "Admin frontend not built — run `npm run build` in apps/cms/admin-frontend."
        )

    response = FileResponse(target.open("rb"))
    response["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    response["Cache-Control"] = "no-store"
    return response
