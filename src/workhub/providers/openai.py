import asyncio
import json
from typing import Any, Protocol

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from pydantic import SecretStr

from workhub.domain.providers import ChatMessage, ChatResponse, ToolCall, ToolDefinition


class ChatProvider(Protocol):
    async def complete(
        self, messages: list[ChatMessage], tools: list[ToolDefinition] | None = None
    ) -> ChatResponse: ...


class EmbeddingProvider(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class OpenAICompatibleChatProvider:
    def __init__(self, *, base_url: str, api_key: str, model: str, timeout_seconds: float) -> None:
        self._client = ChatOpenAI(
            base_url=base_url,
            api_key=SecretStr(api_key),
            model=model,
            timeout=timeout_seconds,
            max_retries=0,
            temperature=0,
        )

    async def complete(
        self, messages: list[ChatMessage], tools: list[ToolDefinition] | None = None
    ) -> ChatResponse:
        client: Any = self._client
        if tools:
            definitions = [
                {
                    "type": "function",
                    "function": {
                        "name": item.name,
                        "description": item.description,
                        "parameters": item.input_schema,
                    },
                }
                for item in tools
            ]
            client = client.bind_tools(definitions)
        response = await client.ainvoke([_message(item) for item in messages])
        text = response.content if isinstance(response.content, str) else ""
        calls = tuple(
            ToolCall(
                call_id=str(item.get("id", "")),
                name=str(item.get("name", "")),
                arguments=_arguments(item.get("args")),
            )
            for item in response.tool_calls
        )
        return ChatResponse(text=text, tool_calls=calls)


class OpenAICompatibleEmbeddingProvider:
    def __init__(self, *, base_url: str, api_key: str, model: str, timeout_seconds: float) -> None:
        self._client = OpenAIEmbeddings(
            base_url=base_url,
            api_key=SecretStr(api_key),
            model=model,
            timeout=timeout_seconds,
            max_retries=0,
            check_embedding_ctx_length=False,
        )

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return await self._client.aembed_documents(texts)


async def test_chat_connection(provider: ChatProvider, timeout_seconds: float) -> None:
    await asyncio.wait_for(
        provider.complete([ChatMessage(role="user", content="Reply with OK.")]),
        timeout=timeout_seconds,
    )


async def test_embedding_connection(provider: EmbeddingProvider, timeout_seconds: float) -> None:
    vectors = await asyncio.wait_for(provider.embed(["connection test"]), timeout_seconds)
    if len(vectors) != 1 or not vectors[0]:
        raise RuntimeError("Embedding provider returned an empty vector")


def _message(message: ChatMessage) -> Any:
    if message.role == "system":
        return SystemMessage(content=message.content)
    if message.role == "user":
        return HumanMessage(content=message.content)
    if message.role == "tool":
        return ToolMessage(content=message.content, tool_call_id=message.tool_call_id or "")
    return AIMessage(
        content=message.content,
        tool_calls=[
            {"id": call.call_id, "name": call.name, "args": call.arguments}
            for call in message.tool_calls
        ],
    )


def _arguments(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        parsed = json.loads(value)
        if isinstance(parsed, dict):
            return parsed
    raise ValueError("Provider returned invalid tool arguments")
