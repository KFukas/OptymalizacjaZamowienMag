"""Klient HTTP do WMS z tokenem usługowym (client_credentials).

mcp-wms NIE ma dostępu do bazy ani do sekretu JWT. Dostaje od WMS token z ograniczonymi scope'ami
(decyduje o nich WMS) i przy każdym wywołaniu przekazuje atrybucję klienta MCP w `X-Acting-Principal`.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import httpx

SAFE_ERROR_KEYS = ("error", "reason", "details", "fields")


@dataclass
class WmsResponse:
    status: int
    body: dict[str, Any]

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300


class WmsUnavailable(Exception):
    pass


class WmsClient:
    def __init__(
        self,
        base_url: str,
        client_secret: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._http = httpx.AsyncClient(base_url=base_url.rstrip("/"), transport=transport, timeout=timeout)
        self._secret = client_secret
        self._token: str | None = None
        self._token_exp = 0.0
        self.scopes: frozenset[str] = frozenset()

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _ensure_token(self, force: bool = False) -> str:
        if self._token and not force and time.monotonic() < self._token_exp:
            return self._token
        try:
            r = await self._http.post(
                "/auth/token",
                json={
                    "grant_type": "client_credentials",
                    "client_id": "mcp-wms",
                    "client_secret": self._secret,
                },
            )
        except httpx.HTTPError as exc:
            raise WmsUnavailable("token") from exc
        if r.status_code != 200:
            raise WmsUnavailable("token_rejected")
        data = r.json()
        self._token = data["access_token"]
        self.scopes = frozenset(data.get("scopes", []))
        self._token_exp = time.monotonic() + max(30, int(data.get("expires_in", 300)) - 60)
        return self._token

    async def request(
        self, method: str, path: str, *, acting: str, params: dict | None = None, json: dict | None = None
    ) -> WmsResponse:
        for attempt in (0, 1):
            token = await self._ensure_token(force=attempt == 1)
            try:
                r = await self._http.request(
                    method,
                    path,
                    params={k: v for k, v in (params or {}).items() if v is not None},
                    json=json,
                    headers={"Authorization": f"Bearer {token}", "X-Acting-Principal": acting},
                )
            except httpx.HTTPError as exc:
                raise WmsUnavailable(path) from exc
            if r.status_code == 401 and attempt == 0:
                continue  # token wygasł — jedno odświeżenie
            try:
                body = r.json()
            except ValueError:
                body = {}
            if not isinstance(body, dict):
                body = {"items": body}
            if r.status_code >= 400:
                body = {k: body[k] for k in SAFE_ERROR_KEYS if k in body} or {
                    "error": "UPSTREAM_ERROR",
                    "reason": "wms_error",
                }
            return WmsResponse(r.status_code, body)
        raise WmsUnavailable("auth")

    async def audit(self, entry: dict[str, Any]) -> bool:
        try:
            r = await self.request("POST", "/internal/audit", acting=str(entry.get("actor", "?")), json=entry)
        except WmsUnavailable:
            return False
        return r.ok
