"""GOV-C2-119 — deterministic domain services (no framework imports, no LLM).

OpenDatasetAvailabilityService: normalizes an already-retrieved per-source catalogue metadata lookup into a
canonical availability signal (metadata present, data obtainable, licence/format/endpoint/update_date
presence), determines a per-source availability status (available / metadata_missing / data_unavailable)
against the seeded dataset taxonomy, reconciles availability across the permitted sources for one dataset
type (distinguishing "metadata missing" from "data unavailable", detecting cross-source disagreements,
synthesizing coverage caveats + retrieval provenance), and composes a cited AvailabilityBrief record.

Everything here is deterministic and auditable (schema-defined normalization + set membership + cross-source
comparison + keyed caveat composition) — there is **no LLM** (no model in config/agent.yaml, no LLM
dependency in pyproject, no LLM call anywhere in src/). Records are keyed by an opaque, non-reversible
``item_id`` surrogate; a raw catalogue record identifier / any resident PII is never carried into the brief,
and the S-3 output gate re-redacts anything that leaks. Seeded source allowlist / dataset taxonomy /
metadata schema are overridable by CoE (a change-controlled engineer MR + specialist review) without
touching node logic.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

# Two SEPARATE concerns — do not conflate them:
#   (1) PRIVACY (opaque_id): every caller identifier (entry_id / dataset_ref) is UNCONDITIONALLY tokenized
#       to a deterministic, non-reversible opaque surrogate so a resident/staff name or free-text label
#       (even a bare ``Alice`` / ``Taro.Yamada`` / ``TaroYamada``, no spaces/symbols) can never reach a
#       citation or the availability brief. Tokenizing is a privacy measure — it does NOT assert the value
#       is authorized/verifiable. Surrogates are one-way hashes; graph state never stores a surrogate→raw
#       rejoin map, so the opaque ID is non-linkable back to the person.
#   (2) PROVENANCE (resolve_provenance): a caller ``source`` becomes a grounded CITATION only when it is
#       resolvable against the authorized provenance registry (names a trusted catalogue system of record).
#       Any other free text (a name, ``unknown``, a fabricated value, or a caller value merely SHAPED like a
#       surrogate ``src:1a2b3c4d``) is NOT verifiable provenance → it yields NO citation → S-3 blocks the
#       brief as CITATION_INCOMPLETE (fail-closed). "Tokenized" is never sufficient for a citation.
# Tokenization is UNCONDITIONAL (no syntactic passthrough): a caller value merely *shaped* like a surrogate
# (``item:deadbeef``) is re-hashed, never trusted, so it can never forge an internal join key. Identifiers /
# provenance are resolved exactly once at S-1 (pre_process); downstream trusts that resolution verbatim.
_SAFE_TOKEN = re.compile(r"^[a-z0-9_\-]{1,64}$")

# Authorized provenance registry: the open-data catalogue / government-API systems of record a service
# operator trusts as verifiable data sources. A caller ``source`` is accepted as a grounded citation ONLY
# when its leading namespace names one of these (the "trusted context" = the source allowlist). This is the
# deploying org's / CoE's registry — overridable without touching node logic; it is a SEMANTIC allowlist of
# authorized catalogue systems, not a syntactic character class.
AUTHORIZED_PROVENANCE_SYSTEMS = frozenset(
    {
        "govcatalog",
        "gov_catalog",
        "data_go_jp",
        "datagojp",
        "digital_agency",
        "digitalagency",
        "opendata_catalog",
        "opendatacatalog",
        "municipal_catalog",
        "municipalcatalog",
        "city_catalog",
        "prefecture_catalog",
        "catalog",
        "catalogue",
        "data_catalog",
        "datacatalog",
        "ckan",
        "socrata",
        "arcgis_hub",
        "arcgishub",
        "geospatial",
        "gsi",
        "registry",
        "opendata_registry",
        "gov_api",
        "govapi",
        "e_stat",
        "estat",
        "prefecture_portal",
        "city_portal",
        "authorized_catalog",
        "authorized_feed",
        "system_of_record",
        "sor",
    }
)

# ── seeded dataset taxonomy: Digital Agency 推奨データセット (standard dataset types), CoE-calibratable ──
DATASET_TAXONOMY: dict[str, str] = {
    "public_facilities": "公共施設一覧 (public facility list)",
    "evacuation_areas": "指定緊急避難場所・避難所 (designated evacuation areas / shelters)",
    "tourism": "観光情報 (tourism information)",
    "events": "イベント情報 (event information)",
    "support_systems": "子育て・支援制度情報 (support-system information)",
    "aed": "AED設置箇所 (AED locations)",
    "public_toilets": "公衆トイレ (public toilets)",
    "wifi": "公衆無線LANアクセスポイント (public Wi-Fi access points)",
    "medical_institutions": "医療機関一覧 (medical institution list)",
    "childcare_facilities": "保育施設・幼稚園 (childcare / kindergarten facilities)",
    "unknown": "対象外・分類不能の標準データセット種別 (out-of-taxonomy dataset type)",
}

# ── seeded metadata schema: the availability-metadata fields a catalogue entry should carry ──
# Presence of licence + endpoint + update_date distinguishes "available" from "metadata_missing".
_REQUIRED_METADATA_FIELDS = ("licence", "endpoint_ref", "update_date")

# Availability statuses, ordered so the caveats surface the least-obtainable first in a brief.
AVAILABILITY_STATUSES = ("data_unavailable", "metadata_missing", "available")
_STATUS_RANK = {"data_unavailable": 3, "metadata_missing": 2, "available": 1}

# A catalogue update_date older than this (days) is flagged as a potential-staleness caveat.
_STALE_DAYS = 730


def _sha8(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]


def opaque_id(value: Any, prefix: str) -> str:
    """PRIVACY tokenize a caller identifier to a deterministic, non-reversible opaque surrogate
    ``<prefix>:<sha8>``.

    Caller identifiers are **always** tokenized — no syntactic passthrough — so a name/label (with or
    without spaces) can never survive into a citation or the brief, and a caller value merely *shaped* like a
    surrogate (``item:deadbeef``) is re-hashed rather than trusted (it can never forge an internal join key).
    Same input → same surrogate (records / citations / summary stay joinable within one invocation). This is
    a privacy measure only; it makes no claim that the identifier is authorized, and no surrogate→raw rejoin
    map is ever kept.
    """
    return f"{prefix}:{_sha8(str(value or '').strip())}"


def resolve_provenance(value: Any) -> str | None:
    """Resolve a **raw** caller ``source`` to a grounded, privacy-tokenized CITATION — or ``None``.

    Provenance validation (separate from privacy) and the **single** resolution point (S-1 / pre_process).
    A citation is emitted **only** when the source names an authorized catalogue system of record
    (``<authorized-namespace>[:<ref>]``). Any other value — a name, ``unknown``, a fabricated value, **or a
    value that merely looks like a surrogate (``src:1a2b3c4d``)** — is not verifiable provenance and returns
    ``None`` so the S-3 gate blocks the brief as CITATION_INCOMPLETE (fail-closed). When authorized, the raw
    label is never used verbatim: the citation is a privacy hash (``src:<sha8>``) of the authorized
    reference. No synthetic provenance is fabricated.

    ★ Forged-surrogate defence: there is **no format-based passthrough**. A caller-supplied ``src:<hex>`` has
    namespace ``src`` (not an authorized catalogue system of record), so it resolves to ``None`` — it is
    dropped here at S-1 and can never reach a citation. Because provenance is resolved exactly once (here),
    the produced ``src:<sha8>`` is the trusted citation downstream and is **never** fed back through this
    function (which would, correctly, reject it), so no forged value can imitate an internal surrogate.
    """
    text = str(value or "").strip()
    if not text:
        return None
    namespace = text.split(":", 1)[0].strip().lower()
    if namespace not in AUTHORIZED_PROVENANCE_SYSTEMS:
        return None  # unverifiable / forged-surrogate / non-allow-listed provenance → fail-closed (no citation)
    return "src:" + _sha8(text)


def _bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "yes", "1", "y", "available", "present"}
    if value is None:
        return default
    return bool(value)


def _clean_str(value: Any) -> str:
    return str(value).strip() if value not in (None, "") else ""


def _dataset_type(value: Any) -> str:
    key = str(value or "").strip().lower()
    return key if key in DATASET_TAXONOMY and key != "unknown" else "unknown"


def _stale_days(update_date: str) -> int | None:
    """Days elapsed since an ISO ``YYYY-MM-DD`` update_date, or None if unparseable. Deterministic:
    compares against a fixed reference epoch (no wall-clock, so tests are stable)."""
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", update_date or "")
    if not m:
        return None
    from datetime import date

    try:
        d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None
    # Reference epoch = the proposal date; freshness is expressed relative to it (deterministic).
    ref = date(2026, 7, 28)
    return (ref - d).days


class OpenDatasetAvailabilityService:
    """Deterministic normalization, availability determination, cross-source reconciliation, and brief
    composition for standard open-dataset availability retrieval."""

    # ── normalization + per-source availability ─────────────────────────────
    @staticmethod
    def normalize(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Validate + canonicalize the supplied per-source catalogue metadata lookups into a signal set.
        Rows without an ``entry_id`` (or ``id``) are dropped.

        The raw catalogue record identifier is reduced to an opaque ``item_id`` surrogate; ``source`` was
        already resolved to a grounded citation (``src:<sha8>``) or ``None`` by pre_process (S-1), the single
        provenance-resolution point — a forged / non-allow-listed source was dropped there. normalize trusts
        that value verbatim; it never re-resolves and never fabricates provenance.
        """
        out: list[dict[str, Any]] = []
        for raw in entries or []:
            if not isinstance(raw, dict):
                continue
            raw_id = _clean_str(raw.get("entry_id") or raw.get("id"))
            if not raw_id:
                continue
            item_id = opaque_id(raw_id, "item")
            source = raw.get("source")  # already resolved (src:<sha8> or None) at S-1

            dataset_type = _dataset_type(raw.get("dataset_type"))
            metadata_present = _bool(raw.get("metadata_present"), default=False)
            data_available = _bool(raw.get("data_available"), default=False)
            publisher = _clean_str(raw.get("publisher"))
            update_date = _clean_str(raw.get("update_date"))
            licence = _clean_str(raw.get("licence"))
            data_format = _clean_str(raw.get("format"))
            endpoint_ref = _clean_str(raw.get("endpoint") or raw.get("endpoint_ref"))

            status, missing_fields = OpenDatasetAvailabilityService._availability_status(
                metadata_present, data_available, licence, endpoint_ref, update_date
            )

            out.append(
                {
                    "item_id": item_id,
                    "dataset_type": dataset_type,
                    "availability_status": status,
                    "metadata_present": metadata_present,
                    "data_available": data_available,
                    "publisher": publisher,
                    "update_date": update_date,
                    "licence": licence,
                    "format": data_format,
                    "endpoint_ref": endpoint_ref,
                    "missing_metadata_fields": missing_fields,
                    "source": source,
                }
            )
        return out

    @staticmethod
    def _availability_status(
        metadata_present: bool, data_available: bool, licence: str, endpoint_ref: str, update_date: str
    ) -> tuple[str, list[str]]:
        """Deterministic per-source availability status + which required metadata fields are missing.

        - no catalogue entry            → ``data_unavailable`` (the dataset is not registered / not provided)
        - entry present, data not obtainable OR key metadata missing → ``metadata_missing``
        - entry present, obtainable, all key metadata present        → ``available``
        """
        values = {"licence": licence, "endpoint_ref": endpoint_ref, "update_date": update_date}
        missing = [f for f in _REQUIRED_METADATA_FIELDS if not values[f]]
        if not metadata_present:
            return "data_unavailable", missing
        if missing or not data_available:
            return "metadata_missing", missing
        return "available", missing

    # ── cross-source reconciliation (the Agent value) ────────────────────────
    @staticmethod
    def reconcile(normalized: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Multi-source cited reconciliation, per catalogue lookup, using the other lookups of the SAME
        ``dataset_type`` as corroboration.

        Distinguishes "metadata missing" from "data unavailable", detects cross-source disagreements
        (publisher / update_date / licence / format), flags single-source lookups and potential staleness,
        and synthesizes ``coverage_caveats`` + ``retrieval_provenance``. Deterministic set/threshold
        comparison only — no free-text semantic interpretation.
        """
        # group by dataset_type for cross-source comparison
        groups: dict[str, list[dict[str, Any]]] = {}
        for e in normalized:
            groups.setdefault(e["dataset_type"], []).append(e)

        reconciled: list[dict[str, Any]] = []
        for entry in normalized:
            peers = [p for p in groups[entry["dataset_type"]] if p["item_id"] != entry["item_id"]]
            caveats = OpenDatasetAvailabilityService._caveats(entry, peers)
            reconciled.append(
                {
                    "item_id": entry["item_id"],
                    "dataset_type": entry["dataset_type"],
                    "dataset_type_label": DATASET_TAXONOMY.get(entry["dataset_type"], ""),
                    "availability_status": entry["availability_status"],
                    "publisher": entry["publisher"] or None,
                    "update_date": entry["update_date"] or None,
                    "licence": entry["licence"] or None,
                    "format": entry["format"] or None,
                    "endpoint_ref": entry["endpoint_ref"] or None,
                    "coverage_caveats": caveats,
                    "source_count_for_type": len(peers) + 1,
                    "retrieval_provenance": {
                        "reconciled_across_sources": len(peers) + 1,
                        "citation": entry["source"],
                    },
                    "status_kind": "needs_review",
                    "citation": entry["source"],
                }
            )
        return reconciled

    @staticmethod
    def _caveats(entry: dict[str, Any], peers: list[dict[str, Any]]) -> list[str]:
        """Deterministic coverage-caveat synthesis for one lookup given its same-type peers."""
        caveats: list[str] = []
        status = entry["availability_status"]

        if status == "data_unavailable":
            caveats.append(
                "data_unavailable: no catalogue entry / data not obtainable — distinct from "
                "'metadata missing'; the dataset appears not to be published (verify with owner)"
            )
        elif status == "metadata_missing":
            if entry["missing_metadata_fields"]:
                caveats.append(
                    "metadata_missing: catalogue entry present but missing "
                    f"{sorted(entry['missing_metadata_fields'])} — distinct from 'data unavailable'"
                )
            if entry["metadata_present"] and not entry["data_available"]:
                caveats.append("obtainability_unconfirmed: entry registered but data not confirmed obtainable")

        # cross-source disagreement caveats (multi-source reconciliation)
        for field in ("publisher", "update_date", "licence", "format"):
            mine = entry[field]
            others = {p[field] for p in peers if p[field]}
            if mine and others and any(o != mine for o in others):
                caveats.append(
                    f"source_disagreement: '{field}' differs across permitted sources for this "
                    "dataset type — reconcile before relying on it"
                )

        # single-source corroboration caveat
        if not peers:
            caveats.append("single_source: no cross-source corroboration among the permitted sources")

        # staleness caveat
        if entry["update_date"]:
            days = _stale_days(entry["update_date"])
            if days is not None and days > _STALE_DAYS:
                caveats.append(
                    f"potential_staleness: update_date is ~{days}d old (> {_STALE_DAYS}d) — "
                    "confirm freshness with the data owner"
                )
        return caveats

    # ── availability summary ─────────────────────────────────────────────────
    @staticmethod
    def availability_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
        """Municipality-level rollup: record count, availability-status distribution, dataset types covered,
        and the count of records carrying a cross-source disagreement caveat."""
        distribution: dict[str, int] = {}
        for r in records:
            distribution[r["availability_status"]] = distribution.get(r["availability_status"], 0) + 1
        dataset_types = sorted({r["dataset_type"] for r in records})
        disagreements = [
            r["item_id"] for r in records if any(c.startswith("source_disagreement") for c in r["coverage_caveats"])
        ]
        needs_attention = [
            r["item_id"] for r in records if r["availability_status"] in ("data_unavailable", "metadata_missing")
        ]
        return {
            "total_records": len(records),
            "availability_distribution": distribution,
            "dataset_types_covered": dataset_types,
            "records_with_source_disagreement": disagreements,
            "records_needing_attention": needs_attention,
        }

    @staticmethod
    def priority_rank(record: dict[str, Any]) -> int:
        """Ordering key: least-obtainable (data_unavailable) first so the brief surfaces gaps up front."""
        return _STATUS_RANK.get(record["availability_status"], 0)
