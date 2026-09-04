# Generated endpoint inventory

Status: source-generated-unaccepted. Do not implement against this file until the endpoint-access fixtures and contract acceptance are complete.

| Surface | Method | Path | Summary |
|---|---|---|---|
| public | GET | `/api/article-redirects/{locale}` | List article slug redirects for a locale |
| public | GET | `/api/articles/{locale}` | List published articles for a locale (paginated) |
| public | GET | `/api/articles/{locale}/{slug}` | Get one published article by slug |
| public | GET | `/api/books/{locale}` | List published books for a locale (paginated) |
| public | GET | `/api/books/{locale}/{slug}` | Get one published book by slug |
| public | POST | `/api/contact` | Contact form (emailed to owner, not stored). |
| public | GET | `/api/courses/{locale}` | List published courses for a locale (paginated) |
| public | GET | `/api/courses/{locale}/{slug}` | Get one published course by slug |
| public | GET | `/api/creative-works/{locale}` | List published creative works for a locale (paginated) |
| public | GET | `/api/creative-works/{locale}/{slug}` | Get one published creative work by slug |
| public | GET | `/api/creative/{locale}` | List published creative works for a locale (alias of /creative-works/) |
| public | GET | `/api/creative/{locale}/{slug}` | Get one published creative work by slug (alias) |
| public | GET | `/api/downloads/{locale}` | List published downloads for a locale (paginated) |
| public | GET | `/api/downloads/{locale}/{slug}` | Get one published download by slug |
| public | GET | `/api/downloads/{locale}/{slug}/file` | Stream a published public download file (active media only) |
| public | GET | `/api/graph/{locale}` | Active research graph for a locale (nodes + edges, no groups) |
| public | GET | `/api/home-composition/{locale}` | Home composition for a locale (published+visible rows, ordered) |
| public | GET | `/api/landings/{locale}` | List published landing pages for a locale |
| public | GET | `/api/landings/{locale}/{slug}` | Get one published landing page by slug |
| public | GET | `/api/profiles/{locale}` | List published profile pages for a locale |
| public | GET | `/api/profiles/{locale}/{slug}` | Get one published profile page by slug |
| public | GET | `/api/projects/{locale}` | List published projects shown on /projects/ (paginated) |
| public | GET | `/api/projects/{locale}/{slug}` | Get one published project listed on /projects/ by slug |
| public | GET | `/api/publications/{locale}` | List published publications for a locale (paginated) |
| public | GET | `/api/publications/{locale}/{slug}` | Get one published publication by slug (canonical) |
| public | GET | `/api/research/projects/{locale}` | List published projects for a locale (paginated) |
| public | GET | `/api/research/projects/{locale}/{slug}` | Get one published project by slug |
| public | GET | `/api/research/publications/{locale}` | List published publications for a locale (paginated) |
| public | GET | `/api/research/publications/{locale}/{slug}` | Get one published publication by slug |
| public | GET | `/api/research/statements/{locale}` | List published research statements for a locale |
| public | GET | `/api/research/statements/{locale}/{slug}` | Get one published research statement by slug |
| public | GET | `/api/research/topics/{locale}` | List published research topics for a locale (paginated) |
| public | GET | `/api/research/topics/{locale}/{slug}` | Get one published research topic by slug |
| public | GET | `/api/series/{locale}` | List published series for a locale |
| public | GET | `/api/site` | Public site settings (primaryColor + current CV/resume downloads) |
| public | GET | `/api/tags/{locale}` | List topic tags for a locale |
| public | GET | `/api/talks/{locale}` | List published talks for a locale (paginated) |
| public | GET | `/api/talks/{locale}/{slug}` | Get one published talk by slug |
| public | GET | `/api/teaching/{locale}` | List published teaching courses for a locale (alias of /courses/) |
| public | GET | `/api/teaching/{locale}/{slug}` | Get one published teaching course by slug (alias) |
| admin | GET | `/api/v1/admin/approval-queue` | Owner approval queue from the imported seed records. |
| admin | GET | `/api/v1/admin/auth/csrf` | Return the CSRF token and ensure the csrftoken cookie is set. |
| admin | POST | `/api/v1/admin/auth/login` | Admin login. |
| admin | POST | `/api/v1/admin/auth/logout` | End the admin session. |
| admin | GET | `/api/v1/admin/auth/me` | Current admin user. |
| admin | POST | `/api/v1/admin/auth/mfa/confirm` | Confirm a pending TOTP device. |
| admin | POST | `/api/v1/admin/auth/mfa/disable` | Disable TOTP and recovery codes. |
| admin | GET | `/api/v1/admin/auth/mfa/recovery-codes` | Pop one-time stashed codes. |
| admin | POST | `/api/v1/admin/auth/mfa/regenerate` | Replace recovery codes. |
| admin | GET | `/api/v1/admin/auth/mfa/status` | TOTP enrollment status. |
| admin | GET | `/api/v1/admin/composition` | List composition pages. |
| admin | POST | `/api/v1/admin/composition` | Create a composition page. |
| admin | GET | `/api/v1/admin/composition/schema` | Composition schema metadata. |
| admin | GET | `/api/v1/admin/composition/{page_id}` | Composition page detail. |
| admin | PUT | `/api/v1/admin/composition/{page_id}` | Replace a composition page (optimistic locking). |
| admin | POST | `/api/v1/admin/content/profile/{id}/sibling-locale` | Create the sibling-locale draft for a profile (G-G). |
| admin | GET | `/api/v1/admin/content/project/{id}/case-media` | List project diagrams and screenshots (Media FKs). |
| admin | PUT | `/api/v1/admin/content/project/{id}/diagrams/{diagram_id}` | Set diagram Media FK. |
| admin | PUT | `/api/v1/admin/content/project/{id}/screenshots/{screenshot_id}` | Set screenshot Media FK. |
| admin | GET | `/api/v1/admin/content/schema` | Writable-field metadata. |
| admin | GET | `/api/v1/admin/content/{entity}` | List content. |
| admin | POST | `/api/v1/admin/content/{entity}` | Create content. |
| admin | POST | `/api/v1/admin/content/{entity}/bulk-archive` | Bulk-archive content rows (feature-flagged). |
| admin | GET | `/api/v1/admin/content/{entity}/{id}` | Content detail with entity-specific fields. |
| admin | PUT | `/api/v1/admin/content/{entity}/{id}` | Update content (optimistic locking). |
| admin | POST | `/api/v1/admin/content/{entity}/{id}/preview-link` | Generate a short-lived public preview share link. |
| admin | GET | `/api/v1/admin/content/{entity}/{id}/revisions` | List immutable content revisions. |
| admin | POST | `/api/v1/admin/content/{entity}/{id}/revisions` | Create an immutable content snapshot. |
| admin | POST | `/api/v1/admin/content/{entity}/{id}/revisions/{revision_id}/restore` | Restore a revision as draft (never overwrites live published). |
| admin | POST | `/api/v1/admin/content/{entity}/{id}/transition` | Transition content lifecycle state. |
| admin | GET | `/api/v1/admin/dashboard/summary` | Action-oriented content counts. |
| admin | GET | `/api/v1/admin/featured` | List featured items. |
| admin | POST | `/api/v1/admin/featured` | Create a featured item. |
| admin | DELETE | `/api/v1/admin/featured/{id}` | Delete a featured item. |
| admin | PUT | `/api/v1/admin/featured/{id}` | Update a featured item (optimistic locking). |
| admin | GET | `/api/v1/admin/graph/validation/{version_id}` | Validator report for one version (no mutation). |
| admin | GET | `/api/v1/admin/graph/versions` | Every graph version with node/edge counts. |
| admin | POST | `/api/v1/admin/graph/versions` | Create an empty draft graph version. |
| admin | GET | `/api/v1/admin/graph/versions/{version_id}` | Full camel payload of one graph version. |
| admin | POST | `/api/v1/admin/graph/versions/{version_id}/activate` | Activate a DRAFT version; the previously active one is archived. |
| admin | PUT | `/api/v1/admin/graph/versions/{version_id}/payload` | Replace the whole payload of a DRAFT version (If-Match optimistic lock). |
| admin | GET | `/api/v1/admin/home-modules/{locale}` | Every home module row of a locale (draft+published), ordered. |
| admin | PUT | `/api/v1/admin/home-modules/{locale}` | Full-array bulk save with optimistic locking via If-Match. |
| admin | POST | `/api/v1/admin/home-modules/{locale}/validate` | Dry-run validation of a home composition payload (no writes). |
| admin | GET | `/api/v1/admin/media` | List media. |
| admin | POST | `/api/v1/admin/media` | Upload media (multipart). |
| admin | GET | `/api/v1/admin/media/licenses` | Reference list of licenses ordered by name (backs the AF select box). |
| admin | GET | `/api/v1/admin/media/orphans` | List media with zero usage references. |
| admin | DELETE | `/api/v1/admin/media/{media_id}` | Delete media (blocked while referenced by content). |
| admin | GET | `/api/v1/admin/media/{media_id}` | Media detail. |
| admin | PUT | `/api/v1/admin/media/{media_id}` | Update media (optimistic locking). |
| admin | PATCH | `/api/v1/admin/media/{media_id}/presentation` | Update media presentation metadata (optimistic locking via If-Match). |
| admin | POST | `/api/v1/admin/media/{media_id}/replace` | Replace the media file (MIME-family compatible). |
| admin | GET | `/api/v1/admin/overview/content-health` | Content health counts. |
| admin | GET | `/api/v1/admin/overview/translation-queue` | Per-slug translation queue. |
| admin | GET | `/api/v1/admin/site` | Get site settings. |
| admin | PUT | `/api/v1/admin/site` | Update site settings (optimistic locking). |
| admin | GET | `/api/v1/admin/tags` | List topic tags. |
| admin | POST | `/api/v1/admin/tags` | Create a topic tag. |
| admin | DELETE | `/api/v1/admin/tags/{id}` | Delete a topic tag. |
| admin | PUT | `/api/v1/admin/tags/{id}` | Update a topic tag. |
| admin | GET | `/api/v1/admin/timeline/{locale}` | Every timeline record of a locale (draft+published), ordered; optional profile filter. |
| admin | POST | `/api/v1/admin/timeline/{locale}` | Create a timeline record (append, or insert after after_id). |
| admin | POST | `/api/v1/admin/timeline/{locale}/reorder` | Reorder the locale's records; ids must be a full permutation. |
| admin | DELETE | `/api/v1/admin/timeline/{locale}/{id}` | Delete a timeline record (hard delete; 204 no content). |
| admin | PATCH | `/api/v1/admin/timeline/{locale}/{id}` | Edit timeline record fields (optimistic locking via If-Match). |
