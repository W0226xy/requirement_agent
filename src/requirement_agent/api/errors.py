from fastapi import Request, status
from fastapi.responses import JSONResponse

from requirement_agent.shared.errors import (
    AnalysisNotFoundError,
    ApplicationError,
    CandidateScopeError,
    ConnectorVerificationError,
    ConversationNotFoundError,
    FeishuAPIError,
    FeishuCallbackAuthenticationError,
    FeishuCallbackPayloadError,
    FeishuEncryptedCallbackError,
    FileTooLargeError,
    IdempotencyConflictError,
    LLMServiceError,
    ObjectStorageError,
    PermissionDeniedError,
    RequirementNotFoundError,
    ReviewStateError,
    ReviewTaskNotFoundError,
    SourceNotFoundError,
    StructuredOutputError,
    TaskDispatchError,
    UnsupportedFileError,
    VersionOperationError,
)

ERROR_STATUS = {
    AnalysisNotFoundError: status.HTTP_404_NOT_FOUND,
    ConnectorVerificationError: status.HTTP_400_BAD_REQUEST,
    ConversationNotFoundError: status.HTTP_404_NOT_FOUND,
    FeishuCallbackAuthenticationError: status.HTTP_401_UNAUTHORIZED,
    FeishuCallbackPayloadError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    FeishuEncryptedCallbackError: status.HTTP_400_BAD_REQUEST,
    FeishuAPIError: status.HTTP_502_BAD_GATEWAY,
    CandidateScopeError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    FileTooLargeError: status.HTTP_413_CONTENT_TOO_LARGE,
    IdempotencyConflictError: status.HTTP_409_CONFLICT,
    LLMServiceError: status.HTTP_503_SERVICE_UNAVAILABLE,
    ObjectStorageError: status.HTTP_503_SERVICE_UNAVAILABLE,
    PermissionDeniedError: status.HTTP_403_FORBIDDEN,
    RequirementNotFoundError: status.HTTP_404_NOT_FOUND,
    ReviewStateError: status.HTTP_409_CONFLICT,
    ReviewTaskNotFoundError: status.HTTP_404_NOT_FOUND,
    SourceNotFoundError: status.HTTP_404_NOT_FOUND,
    TaskDispatchError: status.HTTP_503_SERVICE_UNAVAILABLE,
    UnsupportedFileError: status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
    VersionOperationError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    StructuredOutputError: status.HTTP_422_UNPROCESSABLE_CONTENT,
}


async def application_error_handler(
    request: Request,
    exc: Exception,
) -> JSONResponse:
    if not isinstance(exc, ApplicationError):
        raise exc
    status_code = next(
        (
            response_status
            for error_type, response_status in ERROR_STATUS.items()
            if isinstance(exc, error_type)
        ),
        status.HTTP_400_BAD_REQUEST,
    )
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": exc.code,
                "message": str(exc),
                "details": {},
            }
        },
    )
