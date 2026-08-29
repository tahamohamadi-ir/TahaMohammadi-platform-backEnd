# Public Endpoint Reconciliation vs Route Registry (BACKEND-110)

Evidence sources:

- Registry: `Docs/02-architecture/ROUTE-REGISTRY.md`
- Accepted public OpenAPI artifact: `docs/contracts/openapi/current/public-openapi.json` (version `0.4.0`, 40 paths, SHA-256 `0f672693de28ed33286789e5119eb3226c062693fb15168b1aba5513c257c0a5`)
- Runtime URL wiring: `config/urls.py`
- Registry-referenced composition: `Docs/01-product/owner-content-seed-v1/cms-package/route-composition.v1.1-seed.json`

Reconciliation date: 2026-08-29. No schema or endpoint changes were made by this task.

## Family coverage map

| Registry family | Backend public endpoints | Coverage |
|---|---|---|
| Gateway `/` | none required | OK — static language gateway, no API dependency |
| Home `/{locale}/` | `/api/landings/{locale}/home`, `/api/home-composition/{locale}`, `/api/site`, profile availability | OK — slots in route-composition map to these surfaces |
| About `/{locale}/about/` | `/api/profiles/{locale}/about` | Partial — gaps A, B below |
| Research `/{locale}/research/` | `/api/research/topics/{locale}[/{slug}]`, `/api/research/statements/{locale}[/{slug}]`, `/api/research/projects/{locale}[/{slug}]`, `/api/research/publications/{locale}[/{slug}]` | OK |
| Publications `/{locale}/publications/` | `/api/publications/{locale}[/{slug}]`, `/api/books/{locale}[/{slug}]` | OK with note C2 |
| Projects `/{locale}/projects/` | `/api/projects/{locale}[/{slug}]` (show-listed), `/api/research/projects/{locale}[/{slug}]` (full) | OK |
| Writing `/{locale}/writing/` | `/api/articles/{locale}[/{slug}]` + support: `/api/series/{locale}`, `/api/tags/{locale}`, `/api/article-redirects/{locale}` | OK |
| Teaching `/{locale}/teaching/` | `/api/teaching/{locale}[/{slug}]` (alias of `/api/courses/...`) | OK |
| Creative `/{locale}/creative/` | `/api/creative/{locale}[/{slug}]` (alias of `/api/creative-works/...`) | OK |
| CV `/{locale}/cv/` | `/api/site` (active downloads only) + `/api/downloads/{locale}[/{slug}][{/file}]` | OK — download gate enforced server-side |
| Contact `/{locale}/contact/` | `/api/contact` POST | OK — error-shape fixtures tracked as BACKEND-130 |
| Search `/{locale}/search/` | none | OK — Pagefind per-locale index is build-time (PUBLIC-240); no backend search API dependency |

## Gap list

### Gap A — profile response shape: documented vs served (blocking for About)

The accepted artifact documents `ProfileOut` (locale, slug, title, body, seo_title,
seo_description, published_at) for `/api/profiles/{locale}` and
`/api/profiles/{locale}/{slug}`. At runtime both paths resolve to the Django views
`apps.content.public_api.public_profile_list` / `public_profile_detail`
(mounted before `path("api/", api.urls)` in `config/urls.py`), which serialize the
richer profile summary/detail shape (`apps/content/profile_api.py`): skills,
experience, education, certificates, social links, and per-entry detail-route fields.

Impact: consumer type generation from the accepted artifact under-types the About
page's data source. Resolution requires either aligning the artifact (reopens
PS-05 acceptance per `Docs/03-contracts/OPENAPI-ACCEPTANCE.md`) or routing the
surface through the Ninja API. Decision owner: backend + contract (COORD).

### Gap B — translation-unavailable 404 body undocumented

`public_profile_detail` returns 404 with `build_translation_unavailable(...)`
(locale, slug, available_locales) when the slug is published in another locale
only. The artifact documents no 404 response body for the profile detail path.
Registry fallback rule ("show the distinct untranslated state") depends on this
body. Same acceptance-flow resolution as Gap A.

### Gap C — accepted endpoints without a registry family

C1. `/api/graph/{locale}` — public research graph (BK-05). The registry defines no
graph family. Either the registry gains a family (owner decision) or the surface
stays build-time-only evidence until then.

C2. `/api/books/{locale}[/{slug}]` and `/api/talks/{locale}[/{slug}]` — no
`books`/`talks` family exists in the registry. Books plausibly fold under
Publications; talks have no registry home. Registry update or deprecation plan
required; do not surface in navigation until owned.

Supporting surfaces with no registry row (informational, no action):
`/api/site`, `/api/landings/{locale}[/{slug}]`, `/api/home-composition/{locale}`,
`/api/series/{locale}`, `/api/tags/{locale}`, `/api/article-redirects/{locale}`.
They serve registry families (route copy, home slots, writing support) and the
registry's rule that "detail slugs come only from accepted backend projections".

### Gap D — cross-locale availability probing outside profiles

Registry alternate-link rule needs "published in the alternate locale" evidence.
Only the profile surface answers this directly (Gap B 404 body). Other families
require querying the alternate locale's list and checking slug membership. No
per-record cross-locale field exists in the accepted contract. Candidate for the
non-breaking error/field normalization plan (BACKEND-150); no change now.

### Gap E — locale parameter strictness is uneven

`/api/home-composition/{locale}` and `/api/graph/{locale}` fail closed with 404
for locales outside `fa`/`en`. All other list endpoints accept any locale string
and return 200 with an empty collection (e.g. `/api/articles/xx` → empty items).
Detail paths 404 consistently. Honest-unavailable rendering covers the UI, but a
uniform 404 for non-registry locales is the cleaner contract; normalization is
deferred to BACKEND-150 to avoid breaking the accepted artifact.

## Registry rules verified against backend behavior

- "Detail slugs come only from accepted backend projections" — all detail paths
  404 on unknown slugs; no slug invention possible. Verified by
  `tests/test_content_seed_import.py::test_import_content_seed_public_api_non_leak`.
- "Downloads only when owner-approved file is available" — `/api/site` projects
  active CV/resume media only; `/api/downloads/{locale}/{slug}/file` streams
  published downloads with active media only (`apps/api/api.py`).
- Locale gateway and `/blog/**` redirect policy — frontend concerns; no backend
  route required; none exists.
