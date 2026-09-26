"""The frozen Atlas wire-contract version — one constant, one home (spec §10.2).

Spec §10.2's response example opens with ``"contractVersion": "atlas01-1.0.0"``,
and the plan's File Map gives that literal a single owner so the payload
(Task 14), the endpoint (Task 15) and the OpenAPI snapshot (Task 19) can never
disagree: the frontend's generated types mirror this string, so a second
spelling anywhere would be a second source of truth for a value that may only
move in a deliberate, contract-visible release.
"""

from __future__ import annotations

#: The Atlas payload contract version both repositories mirror (spec §10.2).
#: Serve it, pin it, compare it — never spell it out again.
ATLAS_CONTRACT_VERSION = "atlas01-1.0.0"
