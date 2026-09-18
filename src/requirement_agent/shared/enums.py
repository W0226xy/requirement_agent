from enum import StrEnum


class ChannelType(StrEnum):
    WEB_FORM = "web_form"
    DOCUMENT = "document"
    IMAGE = "image"
    FEISHU = "feishu"


class ProcessingStatus(StrEnum):
    RECEIVED = "received"
    PARSING = "parsing"
    EXTRACTED = "extracted"
    RETRIEVING = "retrieving"
    ANALYZING = "analyzing"
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    RETURNED = "returned"
    VERSIONED = "versioned"
    PARSE_FAILED = "parse_failed"
    EXTRACTION_FAILED = "extraction_failed"
    ANALYSIS_FAILED = "analysis_failed"
    VERSION_FAILED = "version_failed"


class AttachmentParseStatus(StrEnum):
    PENDING = "pending"
    PARSING = "parsing"
    PARSED = "parsed"
    FAILED = "failed"


class AuditActionType(StrEnum):
    SOURCE_RECEIVED = "source_received"
    ATTACHMENT_STORED = "attachment_stored"
    SOURCE_PARSING_STARTED = "source_parsing_started"
    ATTACHMENT_PARSED = "attachment_parsed"
    SOURCE_PARSE_FAILED = "source_parse_failed"
    REVIEW_TASK_CREATED = "review_task_created"
    REVIEW_APPROVED = "review_approved"
    REVIEW_REJECTED = "review_rejected"
    REVIEW_RETURNED = "review_returned"
    REQUIREMENT_VERSION_CREATED = "requirement_version_created"
    AGENT_TOOL_CALLED = "agent_tool_called"


class AuditEntityType(StrEnum):
    SOURCE_RECORD = "source_record"
    SOURCE_ATTACHMENT = "source_attachment"
    ANALYSIS_RESULT = "analysis_result"
    REVIEW_TASK = "review_task"
    REQUIREMENT = "requirement"
    REQUIREMENT_VERSION = "requirement_version"
    CONVERSATION = "conversation"


class ChatMessageStatus(StrEnum):
    SUBMITTED = "submitted"
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class AnalysisType(StrEnum):
    EXTRACTION = "extraction"
    CONFLICT_RISK = "conflict_risk"
    EMBEDDING = "embedding"


class ConflictStatus(StrEnum):
    NONE = "none"
    RELATED = "related"
    DUPLICATE = "duplicate"
    CONTRADICTORY = "contradictory"
    SUPERSEDES = "supersedes"
    DEPENDENCY = "dependency"
    INSUFFICIENT_INFO = "insufficient_info"


class ConflictType(StrEnum):
    RELATED = "related"
    DUPLICATE = "duplicate"
    CONTRADICTORY = "contradictory"
    SUPERSEDES = "supersedes"
    DEPENDENCY = "dependency"


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ProbabilityLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ChangeOperation(StrEnum):
    ADD = "add"
    MODIFY = "modify"
    DELETE = "delete"
    RESTORE = "restore"


class RequirementStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class RequirementChangeType(StrEnum):
    INITIAL = "initial"
    UPDATE = "update"


class FeatureStatus(StrEnum):
    ACTIVE = "active"
    DELETED = "deleted"


class LineageOperationType(StrEnum):
    INTRODUCED = "introduced"
    MODIFIED = "modified"
    DELETED = "deleted"
    RESTORED = "restored"


class ReviewStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    RETURNED = "returned"


class ReviewDecision(StrEnum):
    CREATE = "create"
    MERGE = "merge"
