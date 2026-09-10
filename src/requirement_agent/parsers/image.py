import asyncio
import logging
from collections.abc import Mapping, Sequence
from functools import lru_cache
from io import BytesIO
from typing import Protocol, cast

from PIL import Image

from requirement_agent.parsers.base import OcrEngine, ParsedAttachment
from requirement_agent.shared.errors import AttachmentProcessingError

IMAGE_TYPES = {"image/jpeg", "image/png"}
logger = logging.getLogger(__name__)


class PaddlePipeline(Protocol):
    def predict(self, *, input: object) -> Sequence[object]:
        ...


class PaddleOcrEngine:
    def __init__(self) -> None:
        self._engine: object | None = None

    def extract_text(self, content: bytes) -> list[str]:
        try:
            import numpy as np
            from paddleocr import PaddleOCR

            if self._engine is None:
                self._engine = PaddleOCR(
                    enable_mkldnn=False,
                    use_doc_orientation_classify=False,
                    use_doc_unwarping=False,
                    use_textline_orientation=False,
                )
            image = np.asarray(Image.open(BytesIO(content)).convert("RGB"))
            pipeline = cast(PaddlePipeline, self._engine)
            results = pipeline.predict(input=image)
            texts: list[str] = []
            for result in results:
                payload = getattr(result, "json", result)
                if callable(payload):
                    payload = payload()
                if isinstance(payload, Mapping):
                    body = payload.get("res", payload)
                    if isinstance(body, Mapping):
                        values = body.get("rec_texts", [])
                        if isinstance(values, Sequence) and not isinstance(values, str | bytes):
                            texts.extend(str(value) for value in values)
            return texts
        except Exception as exc:
            logger.exception("PaddleOCR failed while parsing an image")
            raise AttachmentProcessingError("failed to run PaddleOCR") from exc


class ImageParser:
    def __init__(self, engine: OcrEngine) -> None:
        self._engine = engine

    def supports(self, file_type: str) -> bool:
        return file_type in IMAGE_TYPES

    async def parse(self, content: bytes) -> ParsedAttachment:
        try:
            text_parts = await asyncio.to_thread(self._engine.extract_text, content)
            return ParsedAttachment(ocr_text="\n".join(text_parts).strip())
        except AttachmentProcessingError:
            raise
        except Exception as exc:
            raise AttachmentProcessingError("failed to parse image") from exc


@lru_cache
def get_ocr_engine() -> PaddleOcrEngine:
    return PaddleOcrEngine()
