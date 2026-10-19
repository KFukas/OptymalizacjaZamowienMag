"""Audyt append-only z łańcuchem hashy (tamper-evident).

hash_n = SHA-256(kanoniczny JSON pól wpisu n, w tym prev_hash = hash_{n-1}).
Serializacja dopisywania: wiersz `audit_head` blokowany `FOR UPDATE` (PostgreSQL) + blokada procesu
(SQLite ma i tak jednego pisarza). UNIQUE(prev_hash) uniemożliwia rozwidlenie łańcucha.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from wms.models import GENESIS_HASH, AuditHead, AuditLog
from wms.security import canonical_json, sha256_hex, utcnow

MAX_ARGS_CHARS = 4000
_lock = threading.RLock()


def entry_hash(
    *,
    ts: datetime,
    source: str,
    actor: str,
    actor_type: str,
    action: str,
    target: str,
    args: str,
    decision: str,
    outcome: str,
    reason: str,
    prev_hash: str,
) -> str:
    return sha256_hex(
        canonical_json(
            {
                "ts": ts.isoformat(timespec="microseconds"),
                "source": source,
                "actor": actor,
                "actor_type": actor_type,
                "action": action,
                "target": target,
                "args": args,
                "decision": decision,
                "outcome": outcome,
                "reason": reason,
                "prev_hash": prev_hash,
            }
        )
    )


def _hash_of(row: AuditLog) -> str:
    return entry_hash(
        ts=row.ts,
        source=row.source,
        actor=row.actor,
        actor_type=row.actor_type,
        action=row.action,
        target=row.target,
        args=row.args,
        decision=row.decision,
        outcome=row.outcome,
        reason=row.reason,
        prev_hash=row.prev_hash,
    )


def _serialize_args(args: Any) -> str:
    text = canonical_json(args if args is not None else {})
    if len(text) > MAX_ARGS_CHARS:
        text = canonical_json({"_truncated": True, "sha256": sha256_hex(text), "head": text[:1000]})
    return text


def append(
    session: Session,
    *,
    actor: str,
    actor_type: str,
    action: str,
    target: str = "",
    args: Any = None,
    decision: str = "allow",
    outcome: str = "ok",
    reason: str = "",
    source: str = "wms",
) -> AuditLog:
    """Dopisuje wpis w bieżącej transakcji (atomowo ze zmianą biznesową)."""
    with _lock:
        head = session.execute(
            select(AuditHead).where(AuditHead.id == 1).with_for_update()
        ).scalar_one_or_none()
        if head is None:
            head = AuditHead(id=1, last_hash=GENESIS_HASH, count=0)
            session.add(head)
            session.flush()
        row = AuditLog(
            ts=utcnow(),
            source=source[:16],
            actor=actor[:96],
            actor_type=actor_type[:16],
            action=action[:64],
            target=target[:96],
            args=_serialize_args(args),
            decision=decision,
            outcome=outcome[:16],
            reason=reason[:128],
            prev_hash=head.last_hash,
        )
        row.hash = _hash_of(row)
        session.add(row)
        head.last_hash = row.hash
        head.count += 1
        session.flush()
        return row


@dataclass
class ChainVerification:
    ok: bool
    checked: int
    first_broken_seq: int | None = None
    problem: str | None = None
    head_matches: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "checked": self.checked,
            "first_broken_seq": self.first_broken_seq,
            "problem": self.problem,
            "head_matches": self.head_matches,
        }


def verify_chain(session: Session) -> ChainVerification:
    prev = GENESIS_HASH
    checked = 0
    for row in session.execute(select(AuditLog).order_by(AuditLog.seq)).scalars().yield_per(1000):
        checked += 1
        if row.prev_hash != prev:
            return ChainVerification(False, checked, row.seq, "prev_hash_mismatch")
        if _hash_of(row) != row.hash:
            return ChainVerification(False, checked, row.seq, "hash_mismatch")
        prev = row.hash
    head = session.get(AuditHead, 1)
    head_ok = (
        head is None
        and checked == 0
        or (head is not None and head.last_hash == prev and head.count == checked)
    )
    if not head_ok:
        # Usunięcie wpisów z końca łańcucha wykrywamy przez niezgodność z nagłówkiem.
        return ChainVerification(False, checked, None, "head_mismatch", head_matches=False)
    return ChainVerification(True, checked)
