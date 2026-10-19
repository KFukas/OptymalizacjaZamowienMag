"""Wczytanie i walidacja config/policy.yaml (sam plik polityki też ma ścisły schemat)."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PrincipalCfg(_Strict):
    id: Annotated[str, Field(pattern=r"^[a-z0-9-]{3,32}$")]
    token_env: Annotated[str, Field(pattern=r"^[A-Z0-9_]{3,64}$")]
    role: str


class RoleCfg(_Strict):
    description: str = ""
    allow_tools: list[str] = []


class RateCfg(_Strict):
    calls: Annotated[int, Field(ge=1)]
    per_seconds: Annotated[int, Field(ge=1)]


class LimitsCfg(_Strict):
    max_argument_bytes: int = 32768
    max_result_bytes: int = 200000


class Policy(_Strict):
    version: Literal[1]
    default_decision: Literal["deny"]
    principals: list[PrincipalCfg]
    roles: dict[str, RoleCfg]
    rate_limits: dict[str, RateCfg] = {}
    limits: LimitsCfg = LimitsCfg()

    def rate_for(self, tool: str) -> RateCfg:
        return (
            self.rate_limits.get(tool) or self.rate_limits.get("default") or RateCfg(calls=60, per_seconds=60)
        )

    def token_index(self, environ: dict[str, str] | None = None) -> dict[str, PrincipalCfg]:
        """sha256(token) -> principal. Tokeny czytane ze środowiska; puste tokeny są pomijane."""
        env = environ if environ is not None else dict(os.environ)
        index: dict[str, PrincipalCfg] = {}
        for p in self.principals:
            token = env.get(p.token_env, "")
            if len(token) >= 16:  # zbyt krótkie tokeny ignorujemy (nie da się ich uznać za sekret)
                index[hashlib.sha256(token.encode()).hexdigest()] = p
        return index


def load_policy(path: str | Path) -> Policy:
    with open(path, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    policy = Policy.model_validate(raw)
    for p in policy.principals:
        if p.role not in policy.roles:
            raise ValueError(f"principal {p.id}: nieznana rola {p.role}")
    return policy
