# Atlas Payload Contract

Contract notes for the Knowledge Atlas v1 subsystem. This file is created in
Plan A Task 1 with the preflight report format, and expanded by Task 17.

## Preflight report — `atlas_preflight` (v1)

`manage.py atlas_preflight` is a **read-only** audit of bilingual pairing for
Atlas candidate records. It runs no writes and never modifies content data.

- **Candidates**: published records (`status="published"`) of every model in the
  `--models` allow-list (default `profile,research_topic`).
- **Key vocabulary**: `--models` keys, the report's `model` field and
  `AtlasNodeType.canonical_source` all use the **canonical-source** spelling
  (`research_topic`). The owning Django model's own name is reported separately
  as `modelName` (`researchtopic`), so the two vocabularies never blur. (The
  public payload's `canonical.family` value stays the existing record-resolver
  family slug — see `apps/api/record_resolver.py`.)
- **Pairing rule**: a candidate is paired when its `translation_key` is non-null
  and a record of the same model with the same `translation_key` exists in the
  other locale (`en` ⇄ `fa`). A row with `translation_key=null`, or whose
  counterpart is missing, is **blocking**.
- **Unknown key**: an unrecognised `--models` key is a `CommandError`, not a
  traceback.

### JSON report shape (`--json <path>`)

```json
{
  "generated_at": "<ISO-8601 timestamp>",
  "candidates": [
    {
      "model": "research_topic",
      "modelName": "researchtopic",
      "locale": "en",
      "pk": 1,
      "slug": "example",
      "translation_key": "…uuid… | null",
      "published": true,
      "partner_locale_present": true
    }
  ],
  "blocking": "[same entry objects, repeated for every unpaired candidate]"
}
```

### Exit-code contract

| Exit code | Meaning |
|---|---|
| `0` | Every candidate is paired (`blocking == []`). |
| `1` | At least one candidate lacks a usable pairing. |

The command also prints a summary line to stdout:
`atlas_preflight: candidates=<n> blocking=<m>`.

Recording a non-zero exit is a **finding about data**, not a failure of the
command: the preflight is the Plan D prerequisite document (spec §23.2) and
must never repair data itself.

---

# Atlas wire contract (Plan A Task 17)

The human-readable companion to the served payload. Every field name, key
grammar rule, ordering, header and status code below is quoted from the
implemented code and from the spec,
`Docs/05-delivery/knowledge-atlas/KNOWLEDGE-ATLAS-V1-DESIGN-SPEC.md` §10
(cross-referenced as `I09 — Atlas` in
`Docs/03-contracts/PRODUCT-INTERFACES-V2.md`). Nothing here is invented: if a
name is on this page it is on the wire.

## Routes (as registered in `apps/api/api.py`)

The two public routes this plan adds, in registration order (the literal
`/atlas/preview` comes **before** the `/atlas/{locale}` parameter route so
`preview` is never captured as a locale):

```text
GET /api/atlas/{locale}
GET /api/atlas/preview?locale=<en|fa>
```

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/atlas/{locale}` | The **active** version's §10.2 payload for `locale ∈ {en, fa}`. Permanently draft-free. |
| `GET` | `/api/atlas/preview?locale=<en\|fa>` | The draft projection behind a Bearer credential (§10.10.1 below). The **only** place draft data is ever served. |

The mint route (Plan B, admin-authenticated), path verbatim:

```text
POST /api/v1/admin/atlas/versions/{version_id}/preview-token
```

It mints one short-lived preview capability for one version and one locale;
Plan A's document records it because the preview payload endpoint consumes
what it mints.

Route order matters and is implemented as written: the literal `/atlas/preview`
is registered **before** the `/atlas/{locale}` parameter route, so `preview` is
never captured as a locale.

`Method` and `Technology` are published CMS entities (spec §5.2) but have **no
public route of their own in v1** — they exist to be Atlas nodes; only the
payload carries them.

## Active-route payload (spec §10.2)

Top-level fields: `contractVersion`, `locale`, `version`, `nodeTypes`,
`relationTypes`, `groups`, `nodes`, `relations`.

`version` (object): `id`, `revision`, `publishedAt`, `nodeCount`,
`relationCount`, `layoutRevision`.

`nodeTypes[]`: `key`, `label`, `semanticRole`, `visualRole`, `allowAsRoot`,
`allowChildren`, `filterVisible`.

`relationTypes[]`: `key`, `label`, `inverseLabel`, `directed`, `hierarchyRole`,
`visualPriority`.

`groups[]`: `key`, `label`, `description`, `nodeKeys`.

`nodes[]`: `key`, `type`, `label`, `summary`, `accessibleLabel`, `importance`,
`mobileOverviewPriority`, `aliases`, `canonical`, `position`.

`canonical` (object, present only when a canonical record resolved for this
locale — spec §10.3): `family`, `id`, `slug`, `title`, `routeFamily`, `href`.
`href` is the exact-locale site-relative path from `ROUTE_FAMILY_MAP`
(`apps/api/record_resolver.py`); there is no public route for the
`method`/`technology` families in v1 (spec §5.2), so those families never
appear as `routeFamily` values on the wire.

`position` (object): `x`, `y`, `z` — published layout values, rounded to 3
decimals, in scene units; every visible node has one.

`relations[]`: `key`, `type`, `source`, `target`, `directed`, `weight`,
`hierarchy`, `inverseLabel`, `explanation`.

### Field rules (spec §10.3)

| Rule | Detail |
|---|---|
| Naming | camelCase on the wire, snake_case in storage — the compact-overview field is `mobile_overview_priority` in the model and `mobileOverviewPriority` on the wire, and those are the only two spellings that exist anywhere |
| Omission | `null`/empty optional fields are omitted (`exclude_none=True`), never sent as empty strings |
| Ordering | `nodes` ordered by `(-importance, public_key)`; `relations` by `visual_priority DESC, key`; `groups` by `sort_order, key`; catalogs by `sort_order, key` — deterministic and locale-independent |
| Identity | `key` values are the stable public keys of spec §5.3; the relation `key` is **composed, never stored**: `{source.public_key}~{relation_type.key}~{target.public_key}`, with source/target ordered by `public_key` ascending when `directed` is `false` so the identity does not depend on insertion order |
| Key grammar | The public-key grammar is `[a-z0-9._~-]+`; relation keys additionally carry the `~` separators of the composition above. Keys are URL-safe without percent-encoding (the legacy `GraphEdge` `->`/`:` composition is deliberately not reused) |
| Visibility | Only `visible` nodes and relations are served; a hidden node of the active version stays hidden |
| Mobile | `mobileOverviewPriority` ∈ `auto` \| `featured` \| `hidden` |
| Weight | `weight` is an integer 0–100 |
| Directions | `directed`, `hierarchy` and `inverseLabel` are per relation; `hierarchy` is derived from the relation type, never authored per relation |

### Sizes (spec §10.4)

| Budget | Ceiling |
|---|---|
| Nodes served | 80 (`100` triggers the `SCALE_NODES` warning) |
| Relations served | 150 (`250` triggers `SCALE_RELATIONS`) |
| Runtime payload | ≤ 60 KB gzip |

## ETag and conditional requests (spec §10.5)

Every `200` from `/api/atlas/{locale}` carries:

```
ETag: "<version.id>-<16 hex>"
Cache-Control: public, max-age=60
```

The validator is `projection_etag()` (`apps/atlas/projection.py`):
`"<version.id>-"` followed by the **first 16 hex characters of the SHA-256 over
the canonical JSON of this locale projection**, quoted in the HTTP header.
Two consecutive requests over an unchanged active version return the identical
ETag (pinned by test).

**304 contract.** An `If-None-Match` whose quoted (or `W/`-weakened — the
comparison strips an optional `W/` prefix, per
`_atlas_header_candidates()` in `apps/api/api.py`) validator equals the
**freshly recomputed** one answers `304 Not Modified` with **no body** and the
same `ETag`/`Cache-Control` headers. Comparison always happens after a fresh
`public_atlas_payload()` recompute: an active version that fails its own
contract check never earns a 304, so the short-circuit fires only when the
current payload is provably servable. The real 304 path does **not** do the
heavy serialisation — the projection/ETag recompute precedes it and the body
is skipped entirely (pinned by
`tests/test_atlas_public_api.py::test_the_304_path_avoids_rebuilding_the_projection`).
No other `If-None-Match` semantics are implemented: there is no `*`-matches-anything
rule here, and a malformed header can only produce candidates that cannot equal
the literal validator, so it never earns a 304.

## Error behaviour (spec §10.6)

Every error is the I08 envelope (`atlas_not_found` / `atlas_inval` /
`atlas_unauthorized` / `atlas_forbidden`).

| Condition | Response |
|---|---|
| Unknown `locale` (`anything but en`/`fa`) | `404` envelope, `code: "atlas_not_found"` |
| No active version | `404` envelope, `code: "atlas_not_found"` |
| Active version exists but fails its own contract check (a state validation should have prevented) | `500` envelope, `code: "atlas_inval"` — fail-closed, no partial payload ever reaches the wire |
| Unsupported method | `405` by the router |

No endpoint returns draft data, counts, or ids of unpublished entities under
any error path.

## Issue-code vocabulary (frozen, Task 12)

Validation reports only these codes — `BLOCKING_CODES` (18) then
`WARNING_CODES` (11), in this exact order, plus the plan's
`DIRECTION_NOT_OVERRIDABLE` (which has no §20.1 row at all; it is a real,
tested blocker). Frozen against the live emission sites in
`apps/atlas/validation.py` and pinned by `tests/test_validation_report.py`.

Blocking (18): `DANGLING_NODE_HIDDEN_RELATION`, `DANGLING_RELATION_ENDPOINT`,
`CANONICAL_SOURCE_MISSING`, `CANONICAL_SOURCE_UNPUBLISHED`,
`MISSING_LOCALE_PROJECTION`, `AMBIGUOUS_CANONICAL_REF`, `NODE_TYPE_INACTIVE`,
`RELATION_TYPE_INACTIVE`, `RELATION_TYPE_NOT_ALLOWED`, `HIERARCHY_CYCLE`,
`SELF_LOOP_FORBIDDEN`, `DUPLICATE_PUBLIC_KEY`, `DUPLICATE_RELATION`,
`INVALID_PIN`, `MISSING_LAYOUT`, `GROUP_LOCALE_MISSING`,
`PAYLOAD_CONTRACT_INVALID`, `DIRECTION_NOT_OVERRIDABLE`.

Warnings (11): `ISOLATED_NODE`, `NO_INBOUND_RELATIONS`, `NO_OUTBOUND_RELATIONS`,
`HIGH_DEGREE_HUB`, `SUMMARY_MISSING`, `UNUSED_NODE_TYPE`,
`UNUSED_RELATION_TYPE`, `OVERLAPPING_PINS`, `SCALE_NODES`, `SCALE_RELATIONS`,
`SINGLE_LEVEL_HIERARCHY`.

## Draft preview contract (spec §10.10, §10.10.1)

The repository's pre-existing share preview carries its credential in a **path
segment** (`/preview/share/<token>/`). That design is deliberately rejected
for the Atlas: a credential in a path or query reaches access logs, analytics,
`Referer` and browser history. There was no pre-existing `Authorization`
convention for public API reads, so this endpoint **establishes** one:

```
GET /api/atlas/preview?locale=<en|fa>
Authorization: Bearer <atlas-preview capability>
```

The credential travels **only** in the `Authorization` header — never a path
segment, never the query string, never a body field. The `?locale=` selector
stays in the query string because it is not a secret and the response must be
an exact-locale projection; it must equal the token's scope (a mismatch is
`403`) and is never silently inferred from the token.

The capability itself is a short-lived, read-only, purpose-bound,
version- and locale-scoped HMAC token minted by
`apps/atlas/preview_tokens.py` (`build_atlas_preview_token` /
`parse_atlas_preview_token`), signed over the message
`preview:atlas-preview:<version_id>:<locale>:<exp>` with the shared
content-preview secret handling (`PREVIEW_SHARE_SECRET` falling back to
`SECRET_KEY`). The secret is backend-only; TTL is chosen at minting time
(default 600 s — ten minutes) and the expiry inside the signed message is
authoritative afterwards.

### Response headers

A `200` preview response carries exactly this non-caching, non-indexing,
non-referering header set:

```
Cache-Control: no-store
Pragma: no-cache
X-Robots-Tag: noindex, nofollow
Referrer-Policy: no-referrer
```

### Credential status matrix (spec §10.10.1, as implemented)

| Credential state | Status | `code` |
|---|---|---|
| Missing, non-`Bearer`, or empty credential | `401` | `atlas_unauthorized` |
| Unparseable — bytes that verify to nothing (garbage, or a token from a *different message shape*) | `401` | `atlas_unauthorized` |
| Parses but `purpose != "atlas-preview"` | `403` | `atlas_forbidden` |
| Parses but expired | `403` | `atlas_forbidden` |
| Parses but `?locale=` does not equal the token's locale scope | `403` | `atlas_forbidden` |
| Parses, in scope, but the referenced version does not exist | `403` | `atlas_forbidden` |
| Valid capability | `200` with the §10.2 payload for the token's version and locale | — |

**The explicit rule: a bad credential is `never 404`.** A `404` would confirm
to a probe that a draft exists; `401`/`403` keep the existence question
unanswerable. `401` is for credentials that cannot even be authenticated
(absent, non-Bearer, unparseable/garbage); `403` is for credentials that verify
but fail authorisation (expired, wrong purpose, wrong locale, unknown version).

**Plan-vs-implementation deviation, recorded honestly.** The plan's original
matrix treated a foreign four-segment article-family token
(`article.<pk>.<exp>.<sig>`) as a "verified but wrong scope" case and expected
`403`. Under this HMAC primitive that is wrong: the foreign token is signed
over a **different message shape** (no locale segment), so its signature is
cryptographically indistinguishable from garbage and the honest status is
`401`. The spec's authoritative matrix stands as the default — its 401/
`unparseable` tier genuinely covers this case — but note that a hypothetical
implementation where the shapes *were* comparable would answer `403`; the
difference is the message-shape reality, not a relaxation of any gate. The
pinned case lives in `tests/test_atlas_public_api.py` (foreign token → `401`;
an Atlas-family token with a wrong purpose → `403`).

### Token transport ruling (spec §10.10)

The token reaches the browser in the URL **fragment** (`#token=…`), which
browsers never send to a server, never put in `Referer`, and never write to
access logs. The preview page is a static shell (Plan C): read the token from
`location.hash`, hold it in memory only, strip it with `history.replaceState()`
**before** any fetch, then call this endpoint with the `Authorization` header.
It is never written to `localStorage`, `sessionStorage`, cookies, IndexedDB or
any telemetry surface. The preview routes are `noindex, nofollow`, absent from
the sitemap, and never canonicalised.

## Provenance of every statement above

| Statement | Source |
|---|---|
| Field names, shape | spec §10.2 (names quoted verbatim, not paraphrased) |
| Field rules, omission, ordering, key grammar | spec §10.3, §5.3 |
| Sizes | spec §10.4 |
| ETag formula, 304 contract, `W/` weakening | spec §10.5 + `projection_etag()`, `_atlas_header_candidates()` as implemented |
| Error table | spec §10.6 |
| Preview contract, header set, status matrix, never-404 rule | spec §10.10, §10.10.1 + `apps/api/api.py`, `apps/atlas/preview_tokens.py` as implemented |
| No `method`/`technology` public route | spec §5.2 |
| Issue-code vocabulary | Task 12's frozen tuples in `apps/atlas/validation.py` |
| Preflight report format | Task 1 (above, kept verbatim per ruling R7) |
