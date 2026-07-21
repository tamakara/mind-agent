import json
from typing import Any, Protocol

import lark_oapi as lark  # type: ignore[import-untyped]
from lark_oapi.api.im.v1 import (  # type: ignore[import-untyped]
    CreateMessageRequest,
    CreateMessageRequestBody,
    PatchMessageRequest,
    PatchMessageRequestBody,
    ReplyMessageRequest,
    ReplyMessageRequestBody,
)

from workhub.domain import ChannelAddress


class FeishuDeliveryError(RuntimeError):
    pass


class FeishuTransport(Protocol):
    async def reply_text(self, message_id: str, text: str) -> str: ...

    async def send_text(self, address: ChannelAddress, text: str) -> str: ...

    async def send_card(self, address: ChannelAddress, card: dict[str, Any]) -> str: ...

    async def update_card(self, message_id: str, card: dict[str, Any]) -> None: ...


class OfficialFeishuTransport:
    def __init__(self, app_id: str, app_secret: str, *, timeout_seconds: float = 10) -> None:
        self.client = (
            lark.Client.builder()
            .app_id(app_id)
            .app_secret(app_secret)
            .timeout(timeout_seconds)
            .build()
        )

    async def reply_text(self, message_id: str, text: str) -> str:
        body = (
            ReplyMessageRequestBody.builder()
            .msg_type("text")
            .content(json.dumps({"text": text}, ensure_ascii=False))
            .build()
        )
        request = ReplyMessageRequest.builder().message_id(message_id).request_body(body).build()
        response = await self.client.im.v1.message.areply(request)
        return _message_id(response)

    async def send_text(self, address: ChannelAddress, text: str) -> str:
        return await self._create(address, "text", {"text": text})

    async def send_card(self, address: ChannelAddress, card: dict[str, Any]) -> str:
        return await self._create(address, "interactive", card)

    async def update_card(self, message_id: str, card: dict[str, Any]) -> None:
        body = PatchMessageRequestBody.builder().content(_json(card)).build()
        request = PatchMessageRequest.builder().message_id(message_id).request_body(body).build()
        response = await self.client.im.v1.message.apatch(request)
        _ensure_success(response)

    async def _create(
        self, address: ChannelAddress, message_type: str, content: dict[str, Any]
    ) -> str:
        body = (
            CreateMessageRequestBody.builder()
            .receive_id(address.conversation_id)
            .msg_type(message_type)
            .content(_json(content))
            .build()
        )
        request = (
            CreateMessageRequest.builder().receive_id_type("chat_id").request_body(body).build()
        )
        response = await self.client.im.v1.message.acreate(request)
        return _message_id(response)


def _ensure_success(response: Any) -> None:
    if not response.success():
        raise FeishuDeliveryError(f"Feishu API request failed with code {response.code}")


def _message_id(response: Any) -> str:
    _ensure_success(response)
    message_id = getattr(getattr(response, "data", None), "message_id", None)
    if not isinstance(message_id, str) or not message_id:
        raise FeishuDeliveryError("Feishu API response did not contain a message ID")
    return message_id


def _json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
