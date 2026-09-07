# Backend Architecture

<!-- PRODUCT-V2.1 -->
Current execution target: research-first bilingual portfolio, independently publishable detail pages and broad CMS editing under ADR-0010. Dispatch only this repository's packets from `../Docs/05-delivery/concept-alignment-v2/EXECUTION.md` (paths here are repository-relative). Older scaffold/phase status below is a dated baseline, not current feature acceptance. Preserve current endpoints until the additive target contract is implemented and exported.
<!-- /PRODUCT-V2.1 -->

The migrated service is a Django application with Django Ninja public and admin APIs. Domain apps are `api`, `composition`, `content`, `health`, `media`, `rebuild`, `security`, `siteconfig`, and `users`.

Top-level route families currently include public preview, health, public media, a legacy admin SPA, Django staff fallback, `/api/v1/admin/`, profile APIs, the general `/api/` surface, and a rebuild trigger. These routes remain for compatibility during extraction; removal requires a deprecation ADR and consumer evidence.

## Boundaries

- Models and services own business and publication truth.
- Schemas validate API input/output.
- Views/routers translate HTTP concerns.
- Security middleware/decorators own access boundaries.
- Frontends consume contracts and never substitute for server authorization.
- Infrastructure deploys the service but must not embed application secrets.
