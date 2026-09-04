from functools import lru_cache

from requirement_agent.parsers.base import AttachmentParser
from requirement_agent.parsers.document import DocxParser, PdfParser
from requirement_agent.parsers.image import ImageParser, get_ocr_engine
from requirement_agent.shared.errors import UnsupportedFileError


class ParserRegistry:
    def __init__(self, parsers: list[AttachmentParser]) -> None:
        self._parsers = parsers

    def for_file_type(self, file_type: str) -> AttachmentParser:
        for parser in self._parsers:
            if parser.supports(file_type):
                return parser
        raise UnsupportedFileError(f"unsupported file type: {file_type}")


@lru_cache
def get_parser_registry() -> ParserRegistry:
    return ParserRegistry([PdfParser(), DocxParser(), ImageParser(get_ocr_engine())])
