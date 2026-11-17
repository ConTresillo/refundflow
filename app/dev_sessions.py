from __future__ import annotations

import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from threading import Lock


@dataclass(frozen=True)
class DevelopmentSession:
    session_id: str
    csrf_token: str
    subject_id: str | None
    expires_at: float


class DevelopmentSessionStore:
    """Ephemeral server-side sessions for the loopback-only demo adapter."""

    def __init__(
        self,
        *,
        ttl_seconds: int = 3600,
        max_sessions: int = 1024,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if ttl_seconds < 1 or max_sessions < 1:
            raise ValueError("session TTL and capacity must be positive")
        self._ttl_seconds = ttl_seconds
        self._max_sessions = max_sessions
        self._clock = clock
        self._sessions: dict[str, DevelopmentSession] = {}
        self._lock = Lock()

    def create(self, subject_id: str | None = None) -> DevelopmentSession:
        now = self._clock()
        with self._lock:
            self._purge_expired(now)
            return self._create_locked(now, subject_id)

    def get(self, session_id: str | None) -> DevelopmentSession | None:
        if not isinstance(session_id, str) or not 1 <= len(session_id) <= 128:
            return None
        now = self._clock()
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return None
            if session.expires_at <= now:
                del self._sessions[session_id]
                return None
            return session

    def rotate(
        self, previous_session_id: str, subject_id: str
    ) -> DevelopmentSession | None:
        now = self._clock()
        with self._lock:
            previous = self._sessions.get(previous_session_id)
            if previous is None or previous.expires_at <= now:
                self._sessions.pop(previous_session_id, None)
                return None
            del self._sessions[previous_session_id]
            self._purge_expired(now)
            return self._create_locked(now, subject_id)

    def revoke(self, session_id: str | None) -> None:
        if not isinstance(session_id, str):
            return
        with self._lock:
            self._sessions.pop(session_id, None)

    def _create_locked(self, now: float, subject_id: str | None) -> DevelopmentSession:
        if len(self._sessions) >= self._max_sessions:
            oldest_id = min(
                self._sessions,
                key=lambda key: self._sessions[key].expires_at,
            )
            del self._sessions[oldest_id]
        session = DevelopmentSession(
            session_id=secrets.token_urlsafe(32),
            csrf_token=secrets.token_urlsafe(32),
            subject_id=subject_id,
            expires_at=now + self._ttl_seconds,
        )
        self._sessions[session.session_id] = session
        return session

    def _purge_expired(self, now: float) -> None:
        expired = [
            session_id
            for session_id, session in self._sessions.items()
            if session.expires_at <= now
        ]
        for session_id in expired:
            del self._sessions[session_id]
