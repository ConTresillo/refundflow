"""Department-tagged synthetic refund policy records for the local demo.

Records are authored solely for RefundFlow; they are not external policy advice.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
POLICY_DIR = HERE / "policies"
REQUIRED_POLICY_FIELDS = {
    "id", "department_id", "version", "title", "status", "effective_from",
    "effective_to", "source_type", "source_name", "source_url", "provenance",
    "license_notes", "excerpts",
}

# Legacy real-world excerpts are deliberately retained in this excluded
# compatibility registry, but the product corpus uses curated synthetic JSON.
REGISTRY = {}


def compute_content_hash(record: dict) -> str:
    """Hash canonical policy fields while excluding any derived hash field."""
    canonical = {key: value for key, value in record.items() if key != "content_hash"}
    normalized = json.dumps(
        canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()



def load_registry(directory: str | Path | None = None) -> dict[str, dict]:
    """Load validated, department-tagged policy records from local JSON files."""
    policy_dir = Path(directory) if directory is not None else POLICY_DIR
    if not policy_dir.is_dir():
        raise FileNotFoundError(f"policy source directory does not exist: {policy_dir}")
    reg: dict[str, dict] = {}
    for path in sorted(policy_dir.glob("*.json")):
        policy = json.loads(path.read_text(encoding="utf-8"))
        missing = REQUIRED_POLICY_FIELDS - policy.keys()
        policy_id = policy.get("id")
        if missing or not isinstance(policy_id, str) or not policy_id.strip():
            raise ValueError(
                f"invalid policy record in {path.name}: missing {sorted(missing)} or id"
            )
        if policy["source_type"] != "synthetic_demo":
            raise ValueError(f"invalid policy record in {path.name}: source must be synthetic_demo")
        if policy_id != path.stem or policy_id in reg:
            raise ValueError(f"invalid policy record in {path.name}: id must be unique and match filename")
        if not isinstance(policy["excerpts"], list) or not policy["excerpts"] or not all(
            isinstance(item, str) and item.strip() for item in policy["excerpts"]
        ):
            raise ValueError(f"invalid policy record in {path.name}: excerpts must be non-empty strings")
        normalized = json.dumps(policy, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        policy["content_hash"] = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        reg[policy_id] = policy
    return reg


def make_documents(registry: dict[str, dict]) -> list[dict]:
    """Flatten excerpts while preserving policy provenance and scope."""
    docs: list[dict] = []
    for policy_id, policy in registry.items():
        for excerpt in policy.get("excerpts", []):
            docs.append({
                "policy_id": policy_id,
                "department_id": policy["department_id"],
                "version": policy["version"],
                "status": policy["status"],
                "effective_from": policy["effective_from"],
                "effective_to": policy["effective_to"],
                "source_type": policy["source_type"],
                "content_hash": policy["content_hash"],
                "title": policy["title"],
                "source_name": policy["source_name"],
                "source_url": policy["source_url"],
                "chunk": excerpt,
            })
    return docs

def split_text(text: str, max_words: int | None = None) -> list[str]:
    """Deterministic chunking. Without ``max_words`` the whole text is one chunk.

    With ``max_words`` it is broken into word-groups of at most ``max_words``
    words (sentence boundary characters are preserved inside the groups).
    """
    if max_words is None or max_words <= 0:
        return [text]
    words = text.split()
    return [
        " ".join(words[index : index + max_words])
        for index in range(0, len(words), max_words)
    ]


def retrieve(
    query: str,
    documents: list[dict],
    top_k: int = 3,
    *,
    departments: set[str] | None = None,
    policy_ids: set[str] | None = None,
    as_of: str | None = None,
) -> list[dict]:
    """Pure, deterministic retrieval by token-overlap scoring.

    Optional department, policy-ID, and as-of filters constrain evidence before
    ranking. Dates use ISO-8601 calendar-date strings from validated records.
    No LLM, randomness, or network calls are involved.
    """
    query_tokens = {
        w.lower().strip(".,;:!?()[]{}\"'") for w in query.split() if w.strip()
    }
    scored: list[tuple[int, dict]] = []
    for doc in documents:
        if departments is not None and doc.get("department_id") not in departments:
            continue
        if policy_ids is not None and doc.get("policy_id") not in policy_ids:
            continue
        if as_of is not None and (
            doc.get("status") != "active"
            or doc.get("effective_from", "") > as_of
            or (doc.get("effective_to") is not None and doc["effective_to"] < as_of)
        ):
            continue
        chunk_tokens = {
            w.lower().strip(".,;:!?()[]{}\"'") for w in doc["chunk"].split()
        }
        overlap = len(query_tokens & chunk_tokens)
        if overlap > 0:
            scored.append((overlap, doc))
    # Highest score first; ties broken by policy_id for determinism.
    scored.sort(key=lambda item: (-item[0], item[1]["policy_id"]))
    top = [(overlap, doc) for overlap, doc in scored[:top_k]]
    return [
        {
            "policy_id": doc["policy_id"],
            "department_id": doc.get("department_id", ""),
            "version": doc.get("version", ""),
            "status": doc.get("status", "active"),
            "effective_from": doc.get("effective_from", ""),
            "effective_to": doc.get("effective_to"),
            "source_type": doc.get("source_type", ""),
            "content_hash": doc.get("content_hash", ""),
            "title": doc["title"],
            "source_name": doc["source_name"],
            "source_url": doc["source_url"],
            "chunk": doc["chunk"],
            "score": overlap,
        }
        for overlap, doc in top
    ]
