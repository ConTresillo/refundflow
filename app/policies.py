"""Deterministic policy corpus + retrieval, with no LLM involved.

Public sources (all real, offical, and publicly readable):

* :mod:`app.policies` exposes ``load_registry()`` plus pure helpers
  ``split_text`` / ``make_documents`` / ``retrieve``. ``retrieve`` is a pure
  function: given the same query it always returns the same ranking, and it
  never makes network calls.

Citation bookkeeping: each policy document carries ``source_url`` and
``source_name`` so every downstream decision can cite its source verbatim.
"""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
POLICY_DIR = HERE / "policies"

# Public, reputable sources. URLs are fetched verbatim from the live pages
# (before this module ships) and registered in the grounded-citations ledger.
REGISTRY = {
    "stripe-refund": {
        "id": "stripe-refund",
        "title": "Stripe Refund and Cancel Payments Policy",
        "source_name": "Stripe Documentation",
        "source_url": "https://docs.stripe.com/refunds",
        "summary": (
            "Rules for cancelling and refunding payments, refund destinations, "
            "limits, why refunds fail, and timing of refunds."
        ),
        "excerpts": [
            "You can cancel a payment before it is completed at no cost. Or you can refund all or part of a payment after it succeeds, which might incur a fee.",
            "Refunds can only be sent back to the original payment method used in a charge; they cannot be sent to a different destination.",
            "You cannot refund a total greater than the original charge amount.",
            "A refund can fail if the customer's bank or card issuer cannot process it, for example a closed bank account or a problem with the card.",
            "Your customer sees the refund as a credit approximately five to ten business days later, depending on the card network and issuing bank.",
        ],
    },
    "github-tos": {
        "id": "github-tos",
        "title": "GitHub Terms of Service",
        "source_name": "GitHub Docs",
        "source_url": "https://docs.github.com/en/github/site-policy/github-terms-of-service",
        "summary": (
            "Account terms, acceptable use, ownership of user-generated content, "
            "and payment terms including non-refundable charges."
        ),
        "excerpts": [
            "You must be a human to create an Account. Accounts registered by bots or other automated methods are not permitted.",
            "You own Your Content. You grant GitHub and its Affiliates the right to store, host, archive, parse, display, and make copies of Your Content as necessary to provide, develop, and improve the Service, including by training AI Features.",
            "You agree that you will not under any circumstances violate our Acceptable Use Policies or Community Guidelines.",
            "Payment Based on Plan ... the Service is billed in advance on a monthly or yearly basis respectively and is non-refundable.",
        ],
    },
}


def load_registry() -> dict[str, dict]:
    """Return the policy registry. JSON files in ``policies/`` are authoritative."""
    reg = dict(REGISTRY)
    for path in sorted(POLICY_DIR.glob("*.json")):
        key = path.stem
        if key in reg:
            msg = f"duplicate policy key {key!r} in registry.json and {path.name}"
            raise ValueError(msg)
        reg[key] = json.loads(path.read_text(encoding="utf-8"))
    return reg


def make_documents(registry: dict[str, dict]) -> list[dict]:
    """Flatten every policy excerpt into one 'document' chunk."""
    docs: list[dict] = []
    for policy_id, policy in registry.items():
        for excerpt in policy.get("excerpts", []):
            docs.append(
                {
                    "policy_id": policy_id,
                    "title": policy.get("title", policy_id),
                    "source_name": policy.get("source_name", ""),
                    "source_url": policy.get("source_url", ""),
                    "chunk": excerpt,
                }
            )
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


def retrieve(query: str, documents: list[dict], top_k: int = 3) -> list[dict]:
    """Pure, deterministic retrieval by token-overlap scoring.

    No LLM, no randomness, no network calls. The result is always the same
    for the same (query, documents) input.
    """
    query_tokens = {w.lower() for w in query.split() if w.strip()}
    scored: list[tuple[int, dict]] = []
    for doc in documents:
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
            "title": doc["title"],
            "source_name": doc["source_name"],
            "source_url": doc["source_url"],
            "chunk": doc["chunk"],
            "score": overlap,
        }
        for overlap, doc in top
    ]
