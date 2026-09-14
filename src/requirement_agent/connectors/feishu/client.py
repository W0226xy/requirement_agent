import asyncio
from dataclasses import dataclass
from time import monotonic

import httpx
from pydantic import ValidationError

from requirement_agent.connectors.feishu.models import FeishuTokenResponse
from requirement_agent.shared.errors import FeishuAPIError


@dataclass(frozen=True)
class _CachedToken:
    value: str
    expires_at: float


class FeishuOpenAPIClient:
    _tokens: dict[tuple[str, str], _CachedToken] = {}
    _token_lock = asyncio.Lock()

    def __init__(
        self,
        *,
        app_id: str,
        app_secret: str,
        base_url: str,
        timeout_seconds: float,
        max_download_size: int,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._app_id = app_id
        self._app_secret = app_secret
        self._base_url = base_url.rstrip("/")
        self._max_download_size = max_download_size
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds),
            follow_redirects=False,
        )

    async def __aenter__(self) -> "FeishuOpenAPIClient":
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object,
    ) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def download_resource(
        self,
        *,
        message_id: str,
        resource_key: str,
        resource_type: str,
    ) -> tuple[bytes, str]:
        token = await self._tenant_access_token()
        url = (
            f"{self._base_url}/open-apis/im/v1/messages/{message_id}/resources/"
            f"{resource_key}"
        )
        try:
            async with self._client.stream(
                "GET",
                url,
                params={"type": resource_type},
                headers={"Authorization": f"Bearer {token}"},
            ) as response:
                if response.status_code >= 300:
                    raise FeishuAPIError(
                        f"Feishu attachment request failed with status {response.status_code}"
                    )
                content_type = response.headers.get(
                    "content-type", "application/octet-stream"
                ).split(";", maxsplit=1)[0]
                chunks: list[bytes] = []
                size = 0
                async for chunk in response.aiter_bytes():
                    remaining = self._max_download_size + 1 - size
                    chunks.append(chunk[:remaining])
                    size += min(len(chunk), remaining)
                    if size > self._max_download_size:
                        break
        except FeishuAPIError:
            raise
        except httpx.HTTPError as exc:
            raise FeishuAPIError("Feishu attachment request failed") from exc
        return b"".join(chunks), content_type

    async def _tenant_access_token(self) -> str:
        cache_key = (self._base_url, self._app_id)
        cached = self._tokens.get(cache_key)
        now = monotonic()
        if cached is not None and cached.expires_at > now:
            return cached.value

        async with self._token_lock:
            cached = self._tokens.get(cache_key)
            now = monotonic()
            if cached is not None and cached.expires_at > now:
                return cached.value
            token, expires_in = await self._request_tenant_access_token()
            refresh_margin = min(60, max(1, expires_in // 10))
            self._tokens[cache_key] = _CachedToken(
                value=token,
                expires_at=now + expires_in - refresh_margin,
            )
            return token

    async def _request_tenant_access_token(self) -> tuple[str, int]:
        try:
            response = await self._client.post(
                f"{self._base_url}/open-apis/auth/v3/tenant_access_token/internal",
                json={"app_id": self._app_id, "app_secret": self._app_secret},
            )
            if response.status_code >= 300:
                raise FeishuAPIError(
                    f"Feishu token request failed with status {response.status_code}"
                )
            payload = FeishuTokenResponse.model_validate(response.json())
        except FeishuAPIError:
            raise
        except (httpx.HTTPError, ValueError, ValidationError):
            raise FeishuAPIError("Feishu token request failed") from None
        if (
            payload.code != 0
            or payload.tenant_access_token is None
            or payload.expire is None
        ):
            raise FeishuAPIError("Feishu token request was rejected")
        return payload.tenant_access_token, payload.expire
