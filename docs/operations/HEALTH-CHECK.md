# Health Check — `GET /health/`

Readiness endpoint for the Django API. Implemented in `apps/health/views.py` and registered in `config/urls.py`.

## Request

```http
GET /health/ HTTP/1.1
Host: 127.0.0.1:8000
```

No authentication. Safe for load balancers and local smoke checks.

## Responses

### Healthy (database reachable, contact not misconfigured)

HTTP `200`:

```json
{
  "status": "ok",
  "db": "ok",
  "contact": "disabled"
}
```

`contact` may also be `"ok"` or `"unknown"` depending on `SiteSettings` and email configuration.

### Degraded — database error

HTTP `200` (body reports failure; orchestrators should treat `status` / `db`):

```json
{
  "status": "degraded",
  "db": "error",
  "contact": "unknown"
}
```

### Degraded — contact misconfiguration

HTTP `200` when the contact form is enabled but email is not configured:

```json
{
  "status": "degraded",
  "db": "ok",
  "contact": "error",
  "detail": "contact form enabled but EMAIL_HOST not configured"
}
```

## Local verification

With disposable PostgreSQL running and migrations applied:

```powershell
# Terminal 1
uv run python manage.py runserver 127.0.0.1:8000 --settings=config.settings.development

# Terminal 2
curl -s http://127.0.0.1:8000/health/
```

Example observed against disposable PostgreSQL on port 5433 after `migrate` (contact form enabled by `siteconfig.0003` seed migration; email not configured locally):

```json
{"status": "degraded", "db": "ok", "contact": "error", "detail": "contact form enabled but EMAIL_HOST not configured"}
```

`db: "ok"` confirms database connectivity. Configure `EMAIL_HOST` / `CONTACT_FORM_TO` in `.env` to reach `status: "ok"` when contact is enabled.

Example observed against SQLite (no `DATABASE_URL`, before contact seed side effects):

```json
{"status": "ok", "db": "ok", "contact": "disabled"}
```

## Security notes

- No secrets, stack traces, or internal paths in the payload.
- Exempt from audit logging and MFA enforcement (public probe path).
- Not indexed (`NoIndexMiddleware` skips `/health/`).
