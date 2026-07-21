import json
from dataclasses import dataclass
from typing import Any

from workhub.confirmations.repository import PendingActionRepository
from workhub.domain import ToolDefinition, ToolDescriptor
from workhub.feishu.transport import FeishuTransport
from workhub.mcp.manager import MCPManager
from workhub.mcp.repository import McpRepository
from workhub.runtime.tools import RuntimeTool, RuntimeToolContext


@dataclass(frozen=True, slots=True)
class McpRuntimeTool:
    descriptor: ToolDescriptor
    context: RuntimeToolContext
    manager: MCPManager
    actions: PendingActionRepository
    transport: FeishuTransport

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=self.descriptor.model_name,
            description=self.descriptor.description or self.descriptor.original_name,
            input_schema=self.descriptor.input_schema,
        )

    async def execute(self, arguments: dict[str, Any]) -> str:
        if self.descriptor.effect == "allow":
            result = await self.manager.call(self.descriptor, arguments, self.context.actor)
            return result.as_json()
        if self.descriptor.effect == "deny":
            return json.dumps({"error": {"code": "mcp_tool_denied"}})
        summary = confirmation_summary(self.descriptor, arguments)
        created = await self.actions.create(
            self.context.actor,
            self.context.session_id,
            turn_id=self.context.turn_id,
            conversation_id=self.context.message.address.conversation_id,
            mcp_client_key=self.descriptor.client_key,
            tool_name=self.descriptor.model_name,
            arguments=arguments,
            confirmation_summary=summary,
        )
        try:
            card_id = await self.transport.send_card(
                self.context.message.address,
                pending_card(summary, created.token),
            )
            await self.actions.set_card_message(created.action.action_id, card_id)
        except BaseException:
            await self.actions.cancel_delivery_failure(
                created.action.action_id, "confirmation_card_delivery_failed"
            )
            raise
        return json.dumps(
            {
                "status": "confirmation_required",
                "action_id": str(created.action.action_id),
                "expires_at": created.action.expires_at,
            },
            separators=(",", ":"),
        )


class McpRuntimeToolProvider:
    def __init__(
        self,
        repository: McpRepository,
        manager: MCPManager,
        actions: PendingActionRepository,
        transport: FeishuTransport,
    ) -> None:
        self.repository = repository
        self.manager = manager
        self.actions = actions
        self.transport = transport

    async def snapshot(self, context: RuntimeToolContext) -> tuple[RuntimeTool, ...]:
        descriptors = await self.repository.list_tools()
        return tuple(
            McpRuntimeTool(item, context, self.manager, self.actions, self.transport)
            for item in descriptors
            if item.allowlisted and item.effect != "deny"
        )


def confirmation_summary(descriptor: ToolDescriptor, arguments: dict[str, Any]) -> dict[str, Any]:
    fields = [
        {"label": key.replace("_", " "), "value": _display_value(arguments[key])}
        for key in sorted(arguments)
    ]
    return {
        "title": descriptor.description or descriptor.original_name,
        "tool": descriptor.original_name,
        "fields": fields,
    }


def pending_card(summary: dict[str, Any], token: str) -> dict[str, Any]:
    field_lines = "\n".join(
        f"**{item['label']}**: {item['value']}" for item in summary.get("fields", [])
    )
    return {
        "config": {"wide_screen_mode": True},
        "header": {"title": {"tag": "plain_text", "content": "请确认操作"}},
        "elements": [
            {"tag": "markdown", "content": f"{summary.get('title', '业务操作')}\n\n{field_lines}"},
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "确认提交"},
                        "type": "primary",
                        "value": {"token": token, "decision": "confirm"},
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "取消"},
                        "value": {"token": token, "decision": "cancel"},
                    },
                ],
            },
        ],
    }


def terminal_card(status: str, summary: dict[str, Any]) -> dict[str, Any]:
    labels = {
        "executing": "正在提交",
        "succeeded": "提交成功",
        "failed": "提交失败",
        "cancelled": "已取消",
        "expired": "已过期",
    }
    return {
        "header": {"title": {"tag": "plain_text", "content": labels.get(status, status)}},
        "elements": [{"tag": "markdown", "content": str(summary.get("title", "业务操作"))}],
    }


def _display_value(value: Any) -> str:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return str(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
