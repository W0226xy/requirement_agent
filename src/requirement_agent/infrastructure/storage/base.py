from typing import Protocol


class ObjectStorage(Protocol):
    async def put(self, object_name: str, content: bytes, content_type: str) -> None:
        ...

    async def get(self, object_name: str) -> bytes:
        ...

