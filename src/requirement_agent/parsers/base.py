from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ParsedAttachment:
    parsed_text: str | None = None
    ocr_text: str | None = None


class AttachmentParser(Protocol):
    def supports(self, file_type: str) -> bool:
        ...

    async def parse(self, content: bytes) -> ParsedAttachment:
        ...


class OcrEngine(Protocol):
    def extract_text(self, content: bytes) -> list[str]:
        ...

