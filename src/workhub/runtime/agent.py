import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any, Literal, TypedDict
from uuid import UUID

from langgraph.graph import END, START, StateGraph

from workhub.audit import redact_audit_value
from workhub.context import ScrollBuilder, ScrollRepository, deterministic_headline, parse_headline
from workhub.domain import ActorContext, ChatMessage, ChatResponse, FeishuMessage
from workhub.domain.context import ScrollWindow
from workhub.feishu.transport import FeishuTransport
from workhub.providers.openai import ChatProvider
from workhub.runtime.prompt import SYSTEM_PROMPT, actor_prompt
from workhub.runtime.tools import RuntimeTool, RuntimeToolContext, RuntimeToolProvider

ProviderFactory = Callable[[], Awaitable[ChatProvider]]


class RuntimeState(TypedDict):
    messages: list[ChatMessage]
    tools: tuple[RuntimeTool, ...]
    actor: ActorContext
    session_id: UUID
    turn_id: UUID
    user_text: str
    iterations: int
    last_response: ChatResponse | None


class AgentRuntime:
    def __init__(
        self,
        repository: ScrollRepository,
        scroll: ScrollBuilder,
        provider_factory: ProviderFactory,
        tool_provider: RuntimeToolProvider,
        transport: FeishuTransport,
        *,
        max_iterations: int,
        total_timeout_seconds: float,
        tool_timeout_seconds: float,
        context_token_budget: int,
    ) -> None:
        self.repository = repository
        self.scroll = scroll
        self.provider_factory = provider_factory
        self.tool_provider = tool_provider
        self.transport = transport
        self.max_iterations = max_iterations
        self.total_timeout_seconds = total_timeout_seconds
        self.tool_timeout_seconds = tool_timeout_seconds
        self.context_token_budget = context_token_budget
        self._locks: dict[UUID, asyncio.Lock] = {}
        self._locks_guard = asyncio.Lock()
        graph = StateGraph(RuntimeState)
        graph.add_node("model", self._model_node)
        graph.add_node("tools", self._tool_node)
        graph.add_edge(START, "model")
        graph.add_conditional_edges("model", self._next_node, {"tools": "tools", "end": END})
        graph.add_edge("tools", "model")
        self._graph = graph.compile()

    async def handle_message(
        self, message: FeishuMessage, actor: ActorContext, session_id: UUID
    ) -> None:
        lock = await self._lock(actor.employee_id)
        async with lock:
            try:
                reply = await asyncio.wait_for(
                    self._run(message, actor, session_id), self.total_timeout_seconds
                )
            except Exception:
                reply = "对话处理失败。请稍后重试。"
            await self.transport.reply_text(message.message_id, reply)

    async def _run(self, message: FeishuMessage, actor: ActorContext, session_id: UUID) -> str:
        turn = await self.repository.start_turn(
            actor,
            session_id,
            kind="normal",
            first_event_type="user_message",
            first_text=message.text,
        )
        try:
            window = await self.scroll.build(
                actor, session_id, token_budget=self.context_token_budget
            )
            tools = await self.tool_provider.snapshot(
                RuntimeToolContext(
                    actor=actor,
                    session_id=session_id,
                    turn_id=turn.turn_id,
                    message=message,
                )
            )
            state: RuntimeState = {
                "messages": _messages(window, actor),
                "tools": tools,
                "actor": actor,
                "session_id": session_id,
                "turn_id": turn.turn_id,
                "user_text": message.text,
                "iterations": 0,
                "last_response": None,
            }
            result = await self._graph.ainvoke(
                state, {"recursion_limit": self.max_iterations * 2 + 2}
            )
            response = result["last_response"]
            if response is None:
                raise RuntimeError("Agent did not return a response")
            reply, headline = _final_output(response.text, message.text)
            await self.repository.complete_turn(actor, session_id, turn.turn_id, headline=headline)
            return reply
        except BaseException as exc:
            await self.repository.append_event(
                actor,
                session_id,
                turn.turn_id,
                event_type="runtime_error",
                payload={
                    "error": {
                        "code": "agent_run_failed",
                        "type": type(exc).__name__,
                    }
                },
            )
            await self.repository.complete_turn(
                actor,
                session_id,
                turn.turn_id,
                headline=deterministic_headline(message.text),
                status="failed",
            )
            raise

    async def _model_node(self, state: RuntimeState) -> dict[str, Any]:
        iteration = state["iterations"] + 1
        if iteration > self.max_iterations:
            raise RuntimeError("Agent iteration limit exceeded")
        provider = await self.provider_factory()
        response = await provider.complete(
            state["messages"], [tool.definition for tool in state["tools"]]
        )
        message = ChatMessage(
            role="assistant", content=response.text, tool_calls=response.tool_calls
        )
        await self.repository.append_event(
            state["actor"],
            state["session_id"],
            state["turn_id"],
            event_type="agent_message",
            text=response.text,
            payload={"tool_names": [call.name for call in response.tool_calls]},
        )
        return {
            "messages": [*state["messages"], message],
            "iterations": iteration,
            "last_response": response,
        }

    async def _tool_node(self, state: RuntimeState) -> dict[str, Any]:
        response = state["last_response"]
        assert response is not None
        tools = {tool.definition.name: tool for tool in state["tools"]}
        messages = list(state["messages"])
        for call in response.tool_calls:
            await self.repository.append_event(
                state["actor"],
                state["session_id"],
                state["turn_id"],
                event_type="tool_call",
                tool_call_id=call.call_id,
                payload=redact_audit_value({"tool_name": call.name, "arguments": call.arguments}),
            )
            tool = tools.get(call.name)
            if tool is None:
                result = json.dumps({"error": {"code": "unknown_tool"}})
            else:
                try:
                    result = await asyncio.wait_for(
                        tool.execute(call.arguments), self.tool_timeout_seconds
                    )
                except Exception as exc:
                    result = json.dumps(
                        {"error": {"code": "tool_failed", "type": type(exc).__name__}}
                    )
            await self.repository.append_event(
                state["actor"],
                state["session_id"],
                state["turn_id"],
                event_type="tool_result",
                text=result,
                tool_call_id=call.call_id,
            )
            messages.append(ChatMessage(role="tool", content=result, tool_call_id=call.call_id))
        return {"messages": messages}

    def _next_node(self, state: RuntimeState) -> Literal["tools", "end"]:
        response = state["last_response"]
        return "tools" if response is not None and response.tool_calls else "end"

    async def _lock(self, employee_id: UUID) -> asyncio.Lock:
        async with self._locks_guard:
            return self._locks.setdefault(employee_id, asyncio.Lock())


def _messages(window: ScrollWindow, actor: ActorContext) -> list[ChatMessage]:
    messages = [
        ChatMessage(role="system", content=SYSTEM_PROMPT),
        ChatMessage(role="system", content=actor_prompt(actor)),
    ]
    if window.compressed_navigation:
        messages.append(ChatMessage(role="system", content=window.compressed_navigation))
    for turn in window.turns:
        for event in turn.events:
            if event.event_type == "user_message" and event.text is not None:
                messages.append(ChatMessage(role="user", content=event.text))
            elif event.event_type == "agent_message" and event.text is not None:
                messages.append(ChatMessage(role="assistant", content=event.text))
            elif event.event_type == "tool_call":
                tool_name = (event.payload or {}).get("tool_name", "unknown")
                messages.append(
                    ChatMessage(
                        role="assistant",
                        content=f"[tool call: {tool_name}; arguments redacted]",
                    )
                )
            elif event.event_type == "tool_result" and event.text is not None:
                messages.append(
                    ChatMessage(
                        role="system",
                        content=f"[tool result: {event.tool_call_id}] {event.text}",
                    )
                )
    return messages


def _final_output(text: str, user_text: str) -> tuple[str, str]:
    try:
        value = json.loads(text)
    except (TypeError, ValueError):
        return text.strip(), deterministic_headline(user_text)
    if not isinstance(value, dict) or not isinstance(value.get("response"), str):
        return text.strip(), deterministic_headline(user_text)
    response = value["response"].strip()
    return response, parse_headline(value.get("headline"), user_text)
