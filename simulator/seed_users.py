"""Konta demo. To JEDYNE miejsce z domyślnymi danymi logowania demo (nie ma ich w README).

Domyślne wartości można (i w każdym wdrożeniu poza laptopem należy) nadpisać zmiennymi
DEMO_MANAGER_PASSWORD, DEMO_MANAGER_PIN, DEMO_MANAGER2_PASSWORD, DEMO_MANAGER2_PIN, DEMO_OPERATOR_PASSWORD.
Konta są fikcyjne, dane syntetyczne.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from sqlalchemy.orm import Session

from wms.auth import Role
from wms.models import User
from wms.security import hash_secret


@dataclass(frozen=True)
class DemoUser:
    username: str
    display_name: str
    role: Role
    password: str
    pin: str | None


def demo_users() -> list[DemoUser]:
    return [
        DemoUser(
            "kierownik",
            "Kierownik zmiany A (demo)",
            Role.MANAGER,
            os.getenv("DEMO_MANAGER_PASSWORD", "Alfa-Kierownik-2026"),
            os.getenv("DEMO_MANAGER_PIN", "2468"),
        ),
        DemoUser(
            "kierownik2",
            "Kierownik zmiany B (demo)",
            Role.MANAGER,
            os.getenv("DEMO_MANAGER2_PASSWORD", "Alfa-Kierownik2-2026"),
            os.getenv("DEMO_MANAGER2_PIN", "1357"),
        ),
        DemoUser(
            "operator",
            "Operator magazynu (demo)",
            Role.OPERATOR,
            os.getenv("DEMO_OPERATOR_PASSWORD", "Alfa-Operator-2026"),
            None,
        ),
    ]


def seed_users(session: Session, *, iterations: int | None = None) -> list[DemoUser]:
    users = demo_users()
    kwargs = {"iterations": iterations} if iterations else {}
    for u in users:
        existing = session.get(User, u.username)
        if existing is not None:
            session.delete(existing)
            session.flush()
        session.add(
            User(
                username=u.username,
                display_name=u.display_name,
                role=u.role.value,
                password_hash=hash_secret(u.password, **kwargs),
                pin_hash=hash_secret(u.pin, **kwargs) if u.pin else None,
            )
        )
    session.flush()
    return users
