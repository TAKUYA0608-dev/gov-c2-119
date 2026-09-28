"""GOV-C2-119 — S-4 audit trace helper (platform logger + stderr fallback).

Prefers the platform audit logger (`shared.utils.audit_logger.emit_trace_event`); falls back to a
best-effort stderr record in the local stub framework. Never raises and never logs raw catalogue content or
resident PII — counts / availability-status distribution / dataset-type keys / error codes only (no resident
name, no staff free text, no raw caller provenance). S-4 no-persist: the raw record and any PII are not
retained; the audit holds minimised counts + reference data only.
"""

from __future__ import annotations

import json
import sys
from typing import Any

try:
    from shared.utils.audit_logger import emit_trace_event as _platform_emit
except Exception:  # pragma: no cover - import-time environment branch
    _platform_emit = None

_TEMPLATE_ID = "GOV-C2-119"


def emit_trace_event(event_type: str, payload: dict[str, Any], state: dict[str, Any] | None = None) -> None:
    """Emit a domain audit event (best-effort, never raises)."""
    if _platform_emit is not None:
        try:
            _platform_emit(event_type, payload, state)
            return
        except Exception:  # pragma: no cover - defensive
            pass
    record = {
        "template_id": _TEMPLATE_ID,
        "event_type": event_type,
        "payload": payload,
        "session_id": (state or {}).get("session_id"),
    }
    try:
        print(json.dumps(record, ensure_ascii=False), file=sys.stderr)
    except Exception:  # pragma: no cover - defensive
        pass
