from typing import Any, Literal, Protocol
from uuid import UUID

from workhub.audit import AuditEvent, AuditWriter
from workhub.domain import ActorContext, FeishuMessage
from workhub.domain.feishu import FeishuCardAction
from workhub.employees import IdentityRepository
from workhub.feishu.conversion import convert_card_event, convert_message_event
from workhub.feishu.transport import FeishuTransport


class AgentMessageHandler(Protocol):
    async def handle_message(
        self, message: FeishuMessage, actor: ActorContext, session_id: UUID
    ) -> None: ...


class CardActionHandler(Protocol):
    async def handle_card_action(self, action: FeishuCardAction) -> None: ...


class UnavailableAgentHandler:
    def __init__(self, transport: FeishuTransport) -> None:
        self.transport = transport

    async def handle_message(
        self, message: FeishuMessage, actor: ActorContext, session_id: UUID
    ) -> None:
        del actor, session_id
        await self.transport.reply_text(message.message_id, "对话服务尚未启用。请稍后再试。")


class IgnoreCardActionHandler:
    async def handle_card_action(self, action: FeishuCardAction) -> None:
        del action


class FeishuEventRouter:
    def __init__(
        self,
        identities: IdentityRepository,
        transport: FeishuTransport,
        audit: AuditWriter,
        *,
        message_handler: AgentMessageHandler | None = None,
        card_handler: CardActionHandler | None = None,
    ) -> None:
        self.identities = identities
        self.transport = transport
        self.audit = audit
        self.message_handler = message_handler or UnavailableAgentHandler(transport)
        self.card_handler = card_handler or IgnoreCardActionHandler()

    async def handle_message_event(
        self, event: Any
    ) -> Literal["processed", "duplicate", "ignored", "rejected"]:
        converted = convert_message_event(event)
        if not converted.app_id or not converted.event_id:
            return "rejected"
        if not await self.identities.claim_event(
            app_id=converted.app_id, event_id=converted.event_id
        ):
            return "duplicate"
        if converted.status == "group":
            await self._audit_rejection(converted.event_id, "unsupported_group")
            return "ignored"
        if converted.status in {"non_text", "empty"}:
            await self._audit_rejection(converted.event_id, f"unsupported_{converted.status}")
            if converted.message_id:
                await self.transport.reply_text(
                    converted.message_id, "目前只支持一对一私聊中的非空文本消息。"
                )
            return "rejected"
        if converted.status != "supported" or converted.message is None:
            await self._audit_rejection(converted.event_id, "invalid_message")
            return "rejected"
        message = converted.message
        resolution = await self.identities.resolve_or_register(
            app_id=message.address.app_id,
            platform_user_id=message.sender.platform_user_id,
            display_name=message.sender.display_name,
        )
        if resolution.status == "unbound":
            await self.transport.reply_text(
                message.message_id, "当前飞书身份尚未绑定员工。请联系管理员完成绑定。"
            )
            return "rejected"
        if resolution.status == "disabled":
            await self.transport.reply_text(message.message_id, "当前员工账号已停用。")
            return "rejected"
        assert resolution.actor is not None and resolution.session_id is not None
        await self.message_handler.handle_message(message, resolution.actor, resolution.session_id)
        return "processed"

    async def handle_card_event(self, event: Any) -> Literal["processed", "duplicate", "rejected"]:
        action = convert_card_event(event)
        if action is None:
            return "rejected"
        if not await self.identities.claim_event(app_id=action.app_id, event_id=action.event_id):
            return "duplicate"
        await self.card_handler.handle_card_action(action)
        return "processed"

    async def _audit_rejection(self, event_id: str, error_code: str) -> None:
        await self.audit.write(
            AuditEvent(
                event_type="feishu.message_rejected",
                subject_type="channel_event",
                subject_id=event_id,
                summary={},
                error_code=error_code,
            )
        )
