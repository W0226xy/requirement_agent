import pytest

from requirement_agent.application.ingestion.files import sanitize_file_name, validate_file
from requirement_agent.shared.errors import FileTooLargeError, UnsupportedFileError


def test_file_name_is_reduced_to_base_name() -> None:
    assert sanitize_file_name("../../requirement.pdf") == "requirement.pdf"


def test_file_signature_must_match_declared_type() -> None:
    with pytest.raises(UnsupportedFileError):
        validate_file("requirement.pdf", "application/pdf", b"not-a-pdf", 100)


def test_file_size_is_limited() -> None:
    with pytest.raises(FileTooLargeError):
        validate_file("requirement.pdf", "application/pdf", b"%PDF-large", 5)

