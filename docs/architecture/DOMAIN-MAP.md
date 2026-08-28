# Domain Map

| App | Responsibility |
|---|---|
| `api` | Public/admin Ninja routers and response schemas |
| `content` | Profiles, content lifecycle, preview, timeline/graph-related content |
| `composition` | Page/home composition structures |
| `media` | Upload metadata, validation, storage, and public delivery |
| `siteconfig` | Site and profile configuration |
| `security` | Staff authentication, MFA, middleware, decorators, audit/security controls |
| `users` | User model/domain |
| `health` | Dependency-aware health response |
| `rebuild` | Guarded rebuild-trigger integration |

This inventory reflects copied source structure. Detailed ownership must be refined through tests and accepted ADRs before large refactors.
