from app.policy_catalog import PolicyCatalog


def test_catalog_publishes_versions_and_lists_timeline(tmp_path):
    catalog = PolicyCatalog(tmp_path / "catalog.sqlite3")
    first = {"id": "billing-refunds", "department_id": "billing", "version": "1.0", "title": "Refund rules", "status": "active", "effective_from": "2026-01-01", "effective_to": None, "content_hash": "a" * 64}
    second = {**first, "version": "2.0", "content_hash": "b" * 64}

    catalog.publish(first, occurred_at="2026-01-01T00:00:00Z")
    catalog.publish(second, occurred_at="2026-02-01T00:00:00Z")

    assert catalog.current("billing-refunds")["version"] == "2.0"
    assert [(event["version"], event["event_type"]) for event in catalog.timeline()] == [
        ("1.0", "published"), ("2.0", "published")
    ]


def test_catalog_rejects_duplicate_version_without_mutating_state(tmp_path):
    catalog = PolicyCatalog(tmp_path / "catalog.sqlite3")
    record = {"id": "billing-refunds", "department_id": "billing", "version": "1.0", "title": "Refund rules", "status": "active", "effective_from": "2026-01-01", "effective_to": None, "content_hash": "a" * 64}
    catalog.publish(record, occurred_at="2026-01-01T00:00:00Z")

    try:
        catalog.publish(record, occurred_at="2026-01-02T00:00:00Z")
    except ValueError as error:
        assert "already exists" in str(error)
    else:
        raise AssertionError("duplicate version should be rejected")

    assert catalog.current("billing-refunds")["version"] == "1.0"
    assert len(catalog.timeline()) == 1
