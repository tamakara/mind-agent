import json
from typing import Any, Literal

from workhub.audit import redact_audit_value
from workhub.confirmations.repository import ActionClaim, PendingActionRepository
from workhub.context import ScrollRepository
from workhub.domain import ActorContext, PendingAction
from workhub.domain.feishu import FeishuCardAction
from workhub.feishu.transport import FeishuTransport
from workhub.mcp.manager import MCPManager
from workhub.mcp.repository import McpRepository
from workhub.mcp.runtime import terminal_card


class ConfirmationService:
    def __init__(
        self,
        actions: PendingActionRepository,
        tools: McpRepository,
        manager: MCPManager,
        scroll: ScrollRepository,
        transport: FeishuTransport,
    ) -> None:
        self.actions = actions
        self.tools = tools
        self.manager = manager
        self.scroll = scroll
        self.transport = transport

    async def handle_card_action(self, callback: FeishuCardAction) -> None:
        claim = await self.actions.validate_and_claim(callback)
        if not claim.claimed:
            await self._refresh_card(claim.action)
            return
        if callback.decision == "cancel":
            await self._record_cancel(claim)
            await self._refresh_card(claim.action.model_copy(update={"status": "cancelled"}))
            return
        await self._execute(claim.action, claim.actor)

    async def recover(self) -> None:
        await self.actions.expire_pending()
        for action in await self.actions.list_executing():
            try:
                actor = await self.actions.actor_for(action)
                await self._execute(action, actor)
            except Exception:
                await self.actions.finish(
                    action.action_id,
                    status="failed",
                    error_code="mcp_recovery_failed",
                )

    async def _record_cancel(self, claim: ActionClaim) -> None:
        turn = await self.scroll.start_turn(
            claim.actor,
            claim.action.session_id,
            kind="confirmation",
            first_event_type="confirmation_cancelled",
            first_text="取消操作",
            pending_action_id=claim.action.action_id,
        )
        await self.scroll.complete_turn(
            claim.actor,
            claim.action.session_id,
            turn.turn_id,
            headline="取消申请",
        )

    async def _execute(self, action: PendingAction, actor: ActorContext) -> None:
        turn = await self.scroll.start_turn(
            actor,
            action.session_id,
            kind="confirmation",
            first_event_type="confirmation_accepted",
            first_text="确认提交",
            pending_action_id=action.action_id,
        )
        status: Literal["succeeded", "failed"] = "failed"
        headline = "确认提交失败"
        text = "操作提交失败。请重新发起。"
        error_code: str | None = None
        result_summary: dict[str, Any] | None = None
        try:
            descriptor = await self.tools.get_tool(action.tool_name)
            if descriptor is None or not descriptor.allowlisted or descriptor.effect == "deny":
                error_code = "mcp_tool_denied"
                raise RuntimeError(error_code)
            await self.scroll.append_event(
                actor,
                action.session_id,
                turn.turn_id,
                event_type="tool_call",
                payload={
                    "tool_name": descriptor.model_name,
                    "args_hash": action.args_sha256,
                    "idempotency_key_hash": action.idempotency_key[-12:],
                },
            )
            result = await self.manager.call(
                descriptor,
                action.canonical_arguments,
                actor,
                idempotency_key=action.idempotency_key,
            )
            result_summary = {
                "is_error": result.is_error,
                "content": redact_audit_value(result.content),
            }
            if result.is_error:
                error_code = "mcp_business_error"
                raise RuntimeError(error_code)
            status = "succeeded"
            headline = "确认并提交申请"
            text = _result_text(result.content)
        except Exception as exc:
            error_code = error_code or getattr(exc, "code", "mcp_execution_failed")
        final = await self.actions.finish(
            action.action_id,
            status=status,
            result_summary=result_summary,
            error_code=error_code,
        )
        await self.scroll.append_event(
            actor,
            action.session_id,
            turn.turn_id,
            event_type="tool_result",
            text=json.dumps(
                {
                    "status": final.status,
                    "result": final.result_summary,
                    "error_code": final.error_code,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        )
        await self.scroll.append_event(
            actor,
            action.session_id,
            turn.turn_id,
            event_type="confirmation_reply",
            text=text,
        )
        await self.scroll.complete_turn(
            actor,
            action.session_id,
            turn.turn_id,
            headline=headline,
            status="completed" if status == "succeeded" else "failed",
        )
        await self._refresh_card(final)
        address = await self.actions.address_for(final)
        await self.transport.send_text(address, text)

    async def _refresh_card(self, action: PendingAction) -> None:
        if action.card_message_id:
            await self.transport.update_card(
                action.card_message_id,
                terminal_card(action.status, action.confirmation_summary),
            )


def _result_text(content: list[dict[str, Any]]) -> str:
    for item in content:
        text = item.get("text")
        if isinstance(text, str) and text.strip():
            return text.strip()
    return "操作已成功提交。"
