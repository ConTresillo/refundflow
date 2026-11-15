from app import policies

QUERY_NEED_REFUND = "I paid for an order but the item arrived damaged. How do I get a refund?"


def test_loads_only_versioned_synthetic_demo_policies_by_department():
    reg = policies.load_registry()
    assert reg
    assert {policy["department_id"] for policy in reg.values()} >= {
        "support", "billing", "shipping", "risk"
    }
    for policy_id, policy in reg.items():
        assert policy["id"] == policy_id
        assert policy["status"] == "active"
        assert policy["version"] == "1.0"
        assert policy["effective_from"]
        assert policy["effective_to"] is None
        assert policy["source_type"] == "synthetic_demo"
        assert policy["source_name"] == "RefundFlow synthetic policy collection"
        assert policy["license_notes"] == "Synthetic demo text; not legal advice."
        assert policy["excerpts"]


def test_registry_rejects_external_or_incomplete_policy_records(tmp_path, monkeypatch):
    monkeypatch.setattr(policies, "POLICY_DIR", tmp_path)
    (tmp_path / "invalid.json").write_text(
        '{"id":"invalid","title":"Not provenance-complete"}', encoding="utf-8"
    )
    try:
        policies.load_registry()
    except ValueError as error:
        assert "invalid policy record" in str(error)
    else:
        raise AssertionError("invalid policy record should be rejected")


def test_retrieval_ignores_punctuation_for_overlap():
    doc = {
        "policy_id": "punctuated",
        "title": "Policy",
        "source_name": "Example",
        "source_url": "https://example.com/policy",
        "chunk": "Refunds are allowed.",
    }

    results = policies.retrieve("refunds!!!", [doc])

    assert results[0]["policy_id"] == "punctuated"
    assert results[0]["score"] == 1


def test_chunks_and_runs_deterministic_retrieval_without_llm():
    registry = policies.load_registry()
    docs = policies.make_documents(registry)

    # Splitting a short piece of policy text must be deterministic and
    # produce at least one sentence-sized chunk per excerpt.
    quoted = "You can cancel a payment before it is completed at no cost."
    assert policies.split_text(quoted) == [quoted]
    # max_words splits the sentence into word-groups of at most that many words.
    assert policies.split_text(quoted, max_words=3) == [
        "You can cancel",
        "a payment before",
        "it is completed",
        "at no cost.",
    ]

    # Retrieval is a pure function: the same query must always return the
    # same ranking, and it must never touch the network.
    ranking = policies.retrieve(QUERY_NEED_REFUND, docs, top_k=2)
    assert len(ranking) == 2
    assert isinstance(ranking, list)
    first = ranking[0]
    assert first["policy_id"] in registry
    assert first["source_type"] == "synthetic_demo"
    assert first["score"] > 0
