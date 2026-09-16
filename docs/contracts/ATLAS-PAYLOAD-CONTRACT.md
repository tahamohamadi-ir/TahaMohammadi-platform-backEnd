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
