"""Map local authentication identities to production ActorRef snapshots."""

from __future__ import annotations

from uuid import UUID, uuid5

from auth import AuthSession
from production.models import ActorRef, ActorType


LOCAL_ACTOR_NAMESPACE = UUID("66e7f10e-5ae7-4b87-9bb0-42622e75b761")


def actor_from_auth_session(session: AuthSession) -> ActorRef:
    """Build a stable local Actor without treating it as an Employee."""

    identity = "|".join(
        (
            session.organization_name.strip().casefold(),
            session.department_name.strip().casefold(),
            session.username.strip().casefold(),
        )
    )
    return ActorRef(
        actor_type=ActorType.LOCAL_USER,
        display_name=session.username,
        uid=uuid5(LOCAL_ACTOR_NAMESPACE, identity),
    )
