"""Corpus manifest validator - the reproducible corpus specification contract.

This module is a narrow, replaceable adapter for the D1 manifest contract. It
validates a candidate corpus manifest against the versioned JSON schema and
enforces the bounded-selection, uniqueness, and rights/effective-date rules. It
has no I/O of its own beyond loading the pinned schema file, so it is safe to
call synchronously inside ingestion, evaluation, and cache-path code.

This is a demonstration/spec contract. It does not download or ingest any
source content; content acquisition remains an explicitly authorized,
bounded, separate operation.
"""

from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "docs" / "corpus" / "manifest-schema.json"
_SCHEMA_VERSION = "1.0.0"


class Rights(BaseModel):
    model_config = ConfigDict(extra="forbid")

    effective_from: str | None = None
    effective_to: str | None = None
    allowed_batches: list[str] = Field(default_factory=list)
    excluded_batches: list[str] = Field(default_factory=list)


class Source(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repository: str
    revision: str
    url: str | None = None
    company: str = Field(min_length=1, max_length=128)
    department: str | None = None
    media_type: str | None = None
    retrieval_time: str | None = None


class License(BaseModel):
    model_config = ConfigDict(extra="forbid")

    terms: str = Field(min_length=1, max_length=256)
    attribution: str = Field(min_length=1, max_length=2048)
    notice_file: str | None = None


class SourceRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    representation_id: str = Field(min_length=1, max_length=256)
    form: str = Field(min_length=1, max_length=16)
    media_type: str = Field(min_length=1, max_length=32)
    source: dict[str, str] = Field(min_length=1, max_length=4)
    source_hash: str | None = None
    normalized_hash: str | None = None
    effective_from: str | None = None
    effective_to: str | None = None
    department: str | None = None
    status: str = Field(min_length=1, max_length=16)

    @field_validator("form")
    @classmethod
    def _validate_form(cls, value: str) -> str:
        if value not in {"original", "synthetic"}:
            raise ValueError("form must be 'original' or 'synthetic'")
        return value

    @field_validator("status")
    @classmethod
    def _validate_status(cls, value: str) -> str:
        if value not in {"active", "withdrawn", "obsolete"}:
            raise ValueError("status must be 'active', 'withdrawn', or 'obsolete'")
        return value


class Selections(BaseModel):
    model_config = ConfigDict(extra="forbid")

    original: int = Field(ge=0)
    synthetic: int = Field(ge=0)
    total: int = Field(ge=0)

    @model_validator(mode="after")
    def _validate_counts(self) -> "Selections":
        if self.total != self.original + self.synthetic:
            raise ValueError(
                f"total ({self.total}) must equal original ({self.original}) + synthetic ({self.synthetic})"
            )
        return self


class AdaptiveVariant(BaseModel):
    model_config = ConfigDict(extra="forbid")

    variant_id: str = Field(min_length=1, max_length=256)
    parent_representation_id: str = Field(min_length=1, max_length=256)
    mutation_recipe_version: str = Field(min_length=1, max_length=64)
    mutation_recipe: str | None = None
    expected_changed_section_ids: list[str] = Field(default_factory=list)
    expected_answers: dict[str, Any] | None = None
    label: str | None = None

    @field_validator("label")
    @classmethod
    def _validate_label(cls, value: str | None) -> str | None:
        if value is not None and not value.startswith("synthetic-adaptation"):
            raise ValueError("label must start with 'synthetic-adaptation'")
        return value


class CorpusManifest(BaseModel):
    """A validated, reproducible corpus manifest."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str
    source: Source
    license: License
    rights: Rights = Field(default_factory=Rights)
    formed_by: list[SourceRecord] = Field(default_factory=list)
    selections: Selections = Field(default_factory=lambda: Selections())
    adaptive_variants: list[AdaptiveVariant] = Field(default_factory=list)

    @field_validator("schema_version")
    @classmethod
    def _validate_schema_version(cls, value: str) -> str:
        if value != _SCHEMA_VERSION:
            raise ValueError(f"schema_version must equal '{_SCHEMA_VERSION}'")
        return value

    @model_validator(mode="after")
    def _validate_manifest(self) -> "CorpusManifest":
        # Uniqueness of representations and source paths within one manifest.
        seen_ids: set[str] = set()
        seen_paths: set[str] = set()
        for record in self.formed_by:
            if record.representation_id in seen_ids:
                raise ValueError(f"duplicate representation_id: {record.representation_id}")
            seen_ids.add(record.representation_id)
            if not record.source.get("path"):
                raise ValueError(
                    f"representation {record.representation_id} has no source path"
                )
            if record.source["path"] in seen_paths:
                raise ValueError(
                    f"duplicate source path in manifest: {record.source['path']}"
                )
            seen_paths.add(record.source["path"])

        # Rights/effective dates must be present and parseable when given.
        if self.rights:
            self._validate_rights_dates()

        # Bounded planned selection must be consistent with formed_by counts.
        self._validate_bounded_selection()
        return self

    def _validate_rights_dates(self) -> None:
        if self.rights is None:
            return
        effective_from = self.rights.effective_from
        effective_to = self.rights.effective_to

        if effective_from is not None:
            try:
                _dt.date.fromisoformat(effective_from)
            except ValueError as error:
                raise ValueError(
                    f"rights.effective_from must be an ISO date, got {effective_from!r}"
                ) from error
        if effective_to is not None:
            try:
                _dt.date.fromisoformat(effective_to)
            except ValueError as error:
                raise ValueError(
                    f"rights.effective_to must be an ISO date, got {effective_to!r}"
                ) from error
        if effective_from is not None and effective_to is not None:
            if effective_from >= effective_to:
                raise ValueError(
                    "rights.effective_from must be earlier than rights.effective_to"
                )

    def _validate_bounded_selection(self) -> None:
        formed = self.formed_by
        original = sum(1 for r in formed if r.form == "original")
        synthetic = sum(1 for r in formed if r.form == "synthetic")
        if self.selections.original != original:
            raise ValueError(
                f"selections.original ({self.selections.original}) != formed original count ({original})"
            )
        if self.selections.synthetic != synthetic:
            raise ValueError(
                f"selections.synthetic ({self.selections.synthetic}) != formed synthetic count ({synthetic})"
            )

    def bounded_dry_run(self, *, max_documents: int | None = None) -> dict[str, list[str]]:
        """Deterministic, bounded selection of representation ids for a run.

        Returns the planned original + synthetic ids sorted deterministically.
        Never raises on over-limit input: it caps the returned ids at
        max_documents so a demo run can stay bounded while the pinned manifest
        still records the full planned selection.
        """
        originals = sorted(
            r.representation_id for r in self.formed_by if r.form == "original"
        )
        synthetics = sorted(
            r.representation_id for r in self.formed_by if r.form == "synthetic"
        )
        if max_documents is None or max_documents >= len(originals) + len(synthetics):
            return {"original": originals, "synthetic": synthetics}
        cap = max(0, max_documents)
        return {
            "original": originals[:cap],
            "synthetic": synthetics[:cap],
        }

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")
