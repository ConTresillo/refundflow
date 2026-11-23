import json
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field
from starlette.requests import Request

from app.dev_sessions import DevelopmentSessionStore


class GroupRef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    name: str = Field(min_length=1, max_length=100)


class DemoIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    subject_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    display_name: str = Field(min_length=1, max_length=100)
    tenant_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    tenant_name: str = Field(min_length=1, max_length=100)
    groups: tuple[GroupRef, ...]
    roles: tuple[str, ...]


class IdentityPort(Protocol):
    def authenticate(self, request: Request) -> DemoIdentity | None: ...


class DevelopmentIdentityAdapter:
    """Resolve only server-side sessions and fixed local development identities."""

    def __init__(
        self,
        identities: tuple[DemoIdentity, ...],
        session_store: DevelopmentSessionStore,
        session_cookie: str,
    ) -> None:
        self._identities = {identity.subject_id: identity for identity in identities}
        self._session_store = session_store
        self._session_cookie = session_cookie

    @property
    def identities(self) -> tuple[DemoIdentity, ...]:
        return tuple(self._identities.values())

    def for_subject(self, subject_id: str) -> DemoIdentity | None:
        return self._identities.get(subject_id)

    def resolve_subject(self, subject_id: str | None) -> DemoIdentity | None:
        if not isinstance(subject_id, str):
            return None
        return self.for_subject(subject_id)

    def authenticate(self, request: Request) -> DemoIdentity | None:
        session = self._session_store.get(request.cookies.get(self._session_cookie))
        return self.resolve_subject(session.subject_id if session is not None else None)


def load_seeded_identities(path: Path | None = None) -> tuple[DemoIdentity, ...]:
    source = path or Path(__file__).with_name("dev_identities.json")
    raw = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise TypeError("development identity fixture must be a JSON list")
    identities = tuple(DemoIdentity.model_validate(item) for item in raw)
    subjects = [identity.subject_id for identity in identities]
    if not identities or len(subjects) != len(set(subjects)):
        raise ValueError("development identity subjects must be non-empty and unique")
    return identities
