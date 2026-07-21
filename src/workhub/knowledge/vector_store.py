import asyncio
from pathlib import Path
from typing import Any, cast

import chromadb

from workhub.domain.knowledge import KnowledgeChunk


class KnowledgeVectorStore:
    def __init__(self, path: Path) -> None:
        self._client = chromadb.PersistentClient(path=path)

    async def write_generation(
        self,
        index_key: str,
        chunks: list[KnowledgeChunk],
        embeddings: list[list[float]],
    ) -> None:
        if len(chunks) != len(embeddings):
            raise ValueError("Chunk and embedding counts do not match")

        def write() -> None:
            collection = self._client.get_or_create_collection(index_key)
            if chunks:
                collection.add(
                    ids=[f"chunk_{index}" for index in range(len(chunks))],
                    embeddings=cast(Any, embeddings),
                    documents=[chunk.content for chunk in chunks],
                    metadatas=[
                        {
                            "chunk_no": index,
                            "heading_path": chunk.heading_path or "",
                            "line_start": chunk.line_start,
                            "line_end": chunk.line_end,
                        }
                        for index, chunk in enumerate(chunks)
                    ],
                )

        await asyncio.to_thread(write)

    async def query(
        self, index_key: str, query_embedding: list[float], *, limit: int
    ) -> list[dict[str, Any]]:
        def run() -> list[dict[str, Any]]:
            collection = self._client.get_collection(index_key)
            result = collection.query(
                query_embeddings=cast(Any, [query_embedding]),
                n_results=limit,
                include=["documents", "metadatas", "distances"],
            )
            documents = (result.get("documents") or [[]])[0]
            metadatas = (result.get("metadatas") or [[]])[0]
            distances = (result.get("distances") or [[]])[0]
            return [
                {
                    "document": document,
                    "metadata": metadata or {},
                    "distance": float(distance),
                }
                for document, metadata, distance in zip(
                    documents, metadatas, distances, strict=True
                )
            ]

        return await asyncio.to_thread(run)

    async def delete(self, index_key: str) -> None:
        def run() -> None:
            try:
                self._client.delete_collection(index_key)
            except Exception as exc:
                if type(exc).__name__ != "NotFoundError":
                    raise

        await asyncio.to_thread(run)

    async def exists(self, index_key: str) -> bool:
        def run() -> bool:
            try:
                self._client.get_collection(index_key)
            except Exception as exc:
                if type(exc).__name__ == "NotFoundError":
                    return False
                raise
            return True

        return await asyncio.to_thread(run)

    async def list_keys(self) -> set[str]:
        return await asyncio.to_thread(
            lambda: {collection.name for collection in self._client.list_collections()}
        )
