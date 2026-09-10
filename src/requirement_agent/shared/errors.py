class ApplicationError(Exception):
    code = "APPLICATION_ERROR"


class ConnectorVerificationError(ApplicationError):
    code = "CONNECTOR_VERIFICATION_FAILED"


class FeishuCallbackAuthenticationError(ApplicationError):
    code = "FEISHU_CALLBACK_AUTHENTICATION_FAILED"


class FeishuCallbackPayloadError(ApplicationError):
    code = "FEISHU_CALLBACK_PAYLOAD_INVALID"


class FeishuEncryptedCallbackError(ApplicationError):
    code = "FEISHU_ENCRYPTED_CALLBACK_UNSUPPORTED"


class FeishuAPIError(ApplicationError):
    code = "FEISHU_API_ERROR"


class IdempotencyConflictError(ApplicationError):
    code = "IDEMPOTENCY_CONFLICT"


class SourceNotFoundError(ApplicationError):
    code = "SOURCE_NOT_FOUND"


class ConversationNotFoundError(ApplicationError):
    code = "CONVERSATION_NOT_FOUND"


class UnsupportedFileError(ApplicationError):
    code = "UNSUPPORTED_FILE"


class FileTooLargeError(ApplicationError):
    code = "FILE_TOO_LARGE"


class ObjectStorageError(ApplicationError):
    code = "OBJECT_STORAGE_ERROR"


class AttachmentProcessingError(ApplicationError):
    code = "ATTACHMENT_PROCESSING_ERROR"


class TaskDispatchError(ApplicationError):
    code = "TASK_DISPATCH_ERROR"


class LLMServiceError(ApplicationError):
    code = "LLM_SERVICE_ERROR"


class StructuredOutputError(ApplicationError):
    code = "STRUCTURED_OUTPUT_INVALID"


class AnalysisNotFoundError(ApplicationError):
    code = "ANALYSIS_NOT_FOUND"


class CandidateScopeError(ApplicationError):
    code = "CANDIDATE_SCOPE_VIOLATION"


class ReviewTaskNotFoundError(ApplicationError):
    code = "REVIEW_TASK_NOT_FOUND"


class RequirementNotFoundError(ApplicationError):
    code = "REQUIREMENT_NOT_FOUND"


class ReviewStateError(ApplicationError):
    code = "INVALID_REVIEW_STATE"


class VersionOperationError(ApplicationError):
    code = "INVALID_VERSION_OPERATION"


class PermissionDeniedError(ApplicationError):
    code = "PERMISSION_DENIED"
