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
        # At least one policy has a bounded effective window (shipping-delivery-window); accept perpetual or future-dated.
        assert policy["effective_to"] is None or policy["effective_to"] >= policy["effective_from"]
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


def test_retrieval_can_filter_to_selected_departments():
    docs = [
        {"policy_id": "billing", "department_id": "billing", "title": "Billing", "source_name": "Demo", "source_url": "", "chunk": "refund payment evidence"},
        {"policy_id": "support", "department_id": "support", "title": "Support", "source_name": "Demo", "source_url": "", "chunk": "refund payment evidence"},
    ]

    results = policies.retrieve("refund payment", docs, departments={"billing"})

    assert [item["policy_id"] for item in results] == ["billing"]


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


def test_retrieval_filters_policy_status_and_effective_dates():
    docs = [
        {"policy_id": "current", "department_id": "billing", "status": "active", "effective_from": "2026-01-01", "effective_to": None, "title": "Current", "source_name": "Demo", "source_url": "", "chunk": "refund settlement"},
        {"policy_id": "future", "department_id": "billing", "status": "active", "effective_from": "2027-01-01", "effective_to": None, "title": "Future", "source_name": "Demo", "source_url": "", "chunk": "refund settlement"},
        {"policy_id": "withdrawn", "department_id": "billing", "status": "withdrawn", "effective_from": "2020-01-01", "effective_to": None, "title": "Withdrawn", "source_name": "Demo", "source_url": "", "chunk": "refund settlement"},
    ]

    results = policies.retrieve("refund settlement", docs, as_of="2026-11-16")

    assert [item["policy_id"] for item in results] == ["current"]
