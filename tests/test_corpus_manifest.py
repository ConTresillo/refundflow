"""Behavior-focused tests for the reproducible corpus manifest validator (D1).

Verifies the pinned-source resolver, rights/effective-date enforcement,
uniqueness of representations and source paths, selections consistency, and the
deterministic bounded dry-run, without downloading or ingesting source content.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.corpus_manifest import (
    CorpusManifest,
    Selections,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
_VALID_MANIFEST = json.loads((FIXTURES / "corpus_manifest.json").read_text(encoding="utf-8"))


def _manifest(**overrides) -> CorpusManifest:
    data = copy.deepcopy(_VALID_MANIFEST)
    data.update(overrides)
    return CorpusManifest.model_validate(data)


@pytest.fixture()
def manifest() -> CorpusManifest:
    return _manifest()


# ------------------------------------------------------------------ shape
def test_valid_manifest_loads(manifest: CorpusManifest) -> None:
    assert manifest.schema_version == "1.0.0"
    assert manifest.source.repository == "https://gitlab.com/gitlab-com/content-sites/handbook.git"
    assert manifest.source.revision
    assert manifest.source.company == "GitLab"
    assert manifest.license.terms == "CC BY-SA 4.0"
    assert len(manifest.formed_by) == 5
    assert manifest.selections == Selections(original=3, synthetic=2, total=5)


def test_source_is_pinned(manifest: CorpusManifest) -> None:
    assert manifest.source.revision == _VALID_MANIFEST["source"]["revision"]
    assert manifest.source.url == _VALID_MANIFEST["source"]["url"]


# ------------------------------------------------------------------ rights
def test_unknown_rights_field_rejected() -> None:
    bad = copy.deepcopy(_VALID_MANIFEST)
    bad["rights"]["limitation"] = "unknown field"
    with pytest.raises(ValidationError):
        CorpusManifest.model_validate(bad)


def test_unparsable_rights_effective_from_rejected() -> None:
    bad = copy.deepcopy(_VALID_MANIFEST)
    bad["rights"]["effective_from"] = "not-a-date"
    with pytest.raises(ValueError, match="effective_from must be an ISO date"):
        CorpusManifest.model_validate(bad)


def test_effective_from_after_effective_to_rejected() -> None:
    bad = copy.deepcopy(_VALID_MANIFEST)
    bad["rights"]["effective_from"] = "2027-01-01"
    bad["rights"]["effective_to"] = "2026-10-01"
    with pytest.raises(ValueError, match="effective_from must be earlier"):
        CorpusManifest.model_validate(bad)


# ----------------------------------------------------------------- uniqueness
def test_duplicate_representation_id_rejected() -> None:
    formed = copy.deepcopy(_VALID_MANIFEST["formed_by"])
    formed.append(dict(formed[0]))
    bad = copy.deepcopy(_VALID_MANIFEST)
    bad["formed_by"] = formed
    with pytest.raises(ValueError, match="duplicate representation_id"):
        CorpusManifest.model_validate(bad)


def test_duplicate_source_path_rejected() -> None:
    formed = copy.deepcopy(_VALID_MANIFEST["formed_by"])
    # Deliberately reuse an already-seen source path with a new representation id.
    duplicate = copy.deepcopy(formed[1])
    duplicate["representation_id"] = "gitlab-handbook-support-path-dup"
    formed.append(duplicate)
    bad = copy.deepcopy(_VALID_MANIFEST)
    bad["formed_by"] = formed
    with pytest.raises(ValueError, match="duplicate source path"):
        CorpusManifest.model_validate(bad)


def test_missing_source_path_rejected() -> None:
    formed = copy.deepcopy(_VALID_MANIFEST["formed_by"])
    formed[0]["source"].pop("path")
    bad = copy.deepcopy(_VALID_MANIFEST)
    bad["formed_by"] = formed
    with pytest.raises(ValueError, match="no source path"):
        CorpusManifest.model_validate(bad)


# --------------------------------------------------------------- selections
def test_selections_mismatch_rejected() -> None:
    import pprint

    # formed_by has 3 originals; selections still claims 3 but the manifest also
    # carries an extra original without updating the declared count.
    formed = copy.deepcopy(_VALID_MANIFEST["formed_by"])
    extra_original = {
        "representation_id": "gitlab-handbook-support-extra",
        "form": "original",
        "media_type": "text/markdown",
        "source": {
            "repository": "https://gitlab.com/gitlab-com/content-sites/handbook.git",
            "revision": "7f3c9a1e4b2d5f6a7c8d9e0f1a2b3c4d5e6f7a8b",
            "path": "doc/support/extra-original.md",
        },
        "source_hash": None,
        "normalized_hash": None,
        "effective_from": "2026-11-01",
        "status": "active",
    }
    formed.append(extra_original)
    bad = copy.deepcopy(_VALID_MANIFEST)
    bad["formed_by"] = formed
    print("DEBUG formed[0] source:", pprint.pformat(formed[0]["source"]))
    with pytest.raises(ValueError, match="selections.original"):
        CorpusManifest.model_validate(bad)


def test_selections_total_mismatch_rejected() -> None:
    bad = copy.deepcopy(_VALID_MANIFEST)
    bad["selections"]["total"] = 50
    with pytest.raises(ValueError, match="total"):
        CorpusManifest.model_validate(bad)


# --------------------------------------------------------------- dry-run
def test_bounded_dry_run_returns_ids(manifest: CorpusManifest) -> None:
    result = manifest.bounded_dry_run()
    assert set(result["original"]) == {
        "gitlab-handbook-about-index",
        "gitlab-handbook-support",
        "gitlab-handbook-support-revoked",
    }
    assert set(result["synthetic"]) == {
        "gitlab-handbook-support-refund-process",
        "gitlab-handbook-support-returns",
    }


def test_bounded_dry_run_caps_at_max_documents(manifest: CorpusManifest) -> None:
    result = manifest.bounded_dry_run(max_documents=1)
    assert result["original"] == ["gitlab-handbook-about-index"]
    # synthetics are also capped at the same document budget.
    assert result["synthetic"] == ["gitlab-handbook-support-refund-process"]


def test_bounded_dry_run_returns_lists(manifest: CorpusManifest) -> None:
    result = manifest.bounded_dry_run()
    assert all(isinstance(v, list) for v in result.values())
