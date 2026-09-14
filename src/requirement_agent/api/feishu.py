import hashlib
import json
import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, Request, status
from pydantic import ValidationError

from requirement_agent.application.ingestion.dispatcher import (
    FeishuEventDispatcher,
    get_task_dispatcher,
)
from requirement_agent.connectors.feishu.models import (
    FeishuEventRequest,
    FeishuURLVerificationRequest,
)
from requirement_agent.shared.config import Settings
from requirement_agent.shared.errors import (
    FeishuCallbackAuthenticationError,
    FeishuCallbackPayloadError,
    FeishuEncryptedCallbackError,
    TaskDispatchError,
)

router = APIRouter(prefix="/api/v1/connectors/feishu", tags=["connectors"])


def get_feishu_event_dispatcher() -> FeishuEventDispatcher:
    return get_task_dispatcher()


@router.post("/events", status_code=status.HTTP_200_OK)
async def receive_feishu_event(
    request: Request,
    dispatcher: Annotated[
        FeishuEventDispatcher,
        Depends(get_feishu_event_dispatcher),
    ],
) -> dict[str, object]:
    body = await request.body()
    if len(body) > 1_000_000:
        raise FeishuCallbackPayloadError("invalid Feishu callback payload")
    settings: Settings = request.app.state.settings
    _validate_signature(request, body, settings)

    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FeishuCallbackPayloadError("invalid Feishu callback payload") from exc
    if not isinstance(payload, dict):
        raise FeishuCallbackPayloadError("invalid Feishu callback payload")
    if "encrypt" in payload:
        raise FeishuEncryptedCallbackError(
            "encrypted Feishu callbacks are not supported"
        )

    if payload.get("type") == "url_verification":
        challenge = _parse_challenge(payload)
        _validate_token(challenge.token, settings)
        return {"challenge": challenge.challenge}

    event = _parse_event(payload)
    _validate_token(event.header.token, settings)
    if event.header.app_id != settings.feishu_app_id:
        raise FeishuCallbackAuthenticationError(
            "Feishu callback authentication failed"
        )

    sanitized = event.model_copy(
        update={"header": event.header.model_copy(update={"token": None})}
    )
    try:
        dispatcher.dispatch_feishu_event(
            sanitized.model_dump(mode="json", by_alias=True, exclude_none=True)
        )
    except Exception as exc:
        raise TaskDispatchError("Feishu event could not be queued") from exc
    return {"code": 0}


def _parse_challenge(payload: dict[str, object]) -> FeishuURLVerificationRequest:
    try:
        return FeishuURLVerificationRequest.model_validate(payload)
    except ValidationError as exc:
        raise FeishuCallbackPayloadError("invalid Feishu callback payload") from exc


def _parse_event(payload: dict[str, object]) -> FeishuEventRequest:
    try:
        return FeishuEventRequest.model_validate(payload)
    except ValidationError as exc:
        raise FeishuCallbackPayloadError("invalid Feishu callback payload") from exc


def _validate_token(token: str | None, settings: Settings) -> None:
    expected = settings.feishu_verification_token.get_secret_value()
    if not expected or token is None or not secrets.compare_digest(token, expected):
        raise FeishuCallbackAuthenticationError(
            "Feishu callback authentication failed"
        )


def _validate_signature(request: Request, body: bytes, settings: Settings) -> None:
    encrypt_key = settings.feishu_encrypt_key.get_secret_value()
    timestamp = request.headers.get("X-Lark-Request-Timestamp")
    nonce = request.headers.get("X-Lark-Request-Nonce")
    signature = request.headers.get("X-Lark-Signature")
    supplied = any((timestamp, nonce, signature))
    if not encrypt_key:
        if supplied:
            raise FeishuCallbackAuthenticationError(
                "Feishu callback authentication failed"
            )
        return
    if timestamp is None or nonce is None or signature is None:
        raise FeishuCallbackAuthenticationError(
            "Feishu callback authentication failed"
        )
    signed = timestamp.encode() + nonce.encode() + encrypt_key.encode() + body
    expected = hashlib.sha256(signed).hexdigest()
    if not secrets.compare_digest(signature, expected):
        raise FeishuCallbackAuthenticationError(
            "Feishu callback authentication failed"
        )
