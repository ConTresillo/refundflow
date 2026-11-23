import pytest

from app.dev_sessions import DevelopmentSessionStore


def test_development_session_rotation_expiry_and_revocation():
    now = [10.0]
    store = DevelopmentSessionStore(ttl_seconds=5, clock=lambda: now[0])
    anonymous = store.create()

    authenticated = store.rotate(anonymous.session_id, "alice.chen")

    assert store.get(anonymous.session_id) is None
    assert store.get(authenticated.session_id) == authenticated
    assert authenticated.subject_id == "alice.chen"
    now[0] = 16.0
    assert store.get(authenticated.session_id) is None


def test_development_session_store_bounds_capacity_and_validates_configuration():
    with pytest.raises(ValueError):
        DevelopmentSessionStore(ttl_seconds=0)
    with pytest.raises(ValueError):
        DevelopmentSessionStore(max_sessions=0)

    store = DevelopmentSessionStore(max_sessions=1)
    first = store.create()
    second = store.create()

    assert store.get(first.session_id) is None
    assert store.get(second.session_id) == second


def test_concurrent_session_rotation_consumes_old_session_exactly_once():
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    store = DevelopmentSessionStore()
    anonymous = store.create()
    start = Barrier(2)

    def rotate(subject_id):
        start.wait()
        return store.rotate(anonymous.session_id, subject_id)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(rotate, ("alice.chen", "bob.patel")))

    created = [session for session in results if session is not None]
    assert len(created) == 1
    assert store.get(created[0].session_id) == created[0]
