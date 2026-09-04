from pathlib import Path

from requirement_agent.shared.errors import FileTooLargeError, UnsupportedFileError

ALLOWED_FILE_SIGNATURES = {
    "application/pdf": (".pdf",),
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": (".docx",),
    "image/jpeg": (".jpg", ".jpeg"),
    "image/png": (".png",),
}


def sanitize_file_name(file_name: str) -> str:
    safe_name = Path(file_name.replace("\x00", "")).name
    if not safe_name or safe_name in {".", ".."}:
        raise UnsupportedFileError("file name is invalid")
    return safe_name[:512]


def validate_file(
    file_name: str,
    file_type: str,
    content: bytes,
    max_size: int,
) -> str:
    if len(content) > max_size:
        raise FileTooLargeError(f"file exceeds maximum size of {max_size} bytes")
    if not content:
        raise UnsupportedFileError("empty files are not supported")

    safe_name = sanitize_file_name(file_name)
    allowed_extensions = ALLOWED_FILE_SIGNATURES.get(file_type)
    if allowed_extensions is None or Path(safe_name).suffix.lower() not in allowed_extensions:
        raise UnsupportedFileError(f"unsupported file type: {file_type}")

    valid_signature = (
        (file_type == "application/pdf" and content.startswith(b"%PDF-"))
        or (file_type.endswith("wordprocessingml.document") and content.startswith(b"PK"))
        or (file_type == "image/png" and content.startswith(b"\x89PNG\r\n\x1a\n"))
        or (file_type == "image/jpeg" and content.startswith(b"\xff\xd8\xff"))
    )
    if not valid_signature:
        raise UnsupportedFileError("file content does not match its declared type")
    return safe_name

