# Atlas Payload Contract

Contract notes for the Knowledge Atlas v1 subsystem. This file is created in
Plan A Task 1 with the preflight report format, and expanded by Task 17.

## Preflight report — `atlas_preflight` (v1)

`manage.py atlas_preflight` is a **read-only** audit of bilingual pairing for
Atlas candidate records. It runs no writes and never modifies content data.

- **Candidates**: published records (`status="published"`) of every model in the
  `--models` allow-list (default `profile,researchtopic`; keys match
  `Model._meta.model_name`).
- **Pairing rule**: a candidate is paired when its `translation_key` is non-null
  and a record of the same model with the same `translation_key` exists in the
  other locale (`en` ⇄ `fa`). A row with `translation_key=null`, or whose
  counterpart is missing, is **blocking**.

### JSON report shape (`--json <path>`)

```json
{
  "generated_at": "<ISO-8601 timestamp>",
  "candidates": [
    {
      "model": "researchtopic",
      "locale": "en",
      "pk": 1,
      "slug": "example",
      "translation_key": "…uuid… | null",
      "partner_locale_present": true
    }
  ],
  "blocking": [
    { "…same entry shape, repeated for every unpaired candidate…": "" }
  ]
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
