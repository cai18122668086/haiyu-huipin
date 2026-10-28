from enum import StrEnum


class AdminRole(StrEnum):
    SUPER_ADMIN = "super_admin"
    ADMIN = "admin"


class AdminStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"


class UserStatus(StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    DELETED = "deleted"


class WorkspaceStatus(StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    DELETED = "deleted"


class MembershipRole(StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"
    VIEWER = "viewer"


class MembershipStatus(StrEnum):
    ACTIVE = "active"
    INVITED = "invited"
    SUSPENDED = "suspended"


class AssetMediaKind(StrEnum):
    IMAGE = "image"
    MASK = "mask"
    JSON = "json"
    ARCHIVE = "archive"


class AssetOriginKind(StrEnum):
    UPLOAD = "upload"
    GENERATED = "generated"
    IMPORTED = "imported"
    DERIVED = "derived"


class AssetLifecycleStatus(StrEnum):
    PENDING = "pending"
    READY = "ready"
    QUARANTINED = "quarantined"
    FAILED = "failed"
    DELETED = "deleted"


class JobType(StrEnum):
    LOCAL_EDIT = "local_edit"
    PRODUCT_IMAGE = "product_image"
    REFERENCE_REMIX = "reference_remix"
    MODEL_POSE_TRANSFER = "model_pose_transfer"
    CREATIVE_IMAGE = "creative_image"
    DETAIL_PACK = "detail_pack"
    DOMESTIC_DETAIL_REVIEW = "domestic_detail_review"
    AMAZON_VISUAL_SUITE = "amazon_visual_suite"
    DOMESTIC_MAIN_IMAGE = "domestic_main_image"


class JobStatus(StrEnum):
    DRAFT = "draft"
    QUEUED = "queued"
    PLANNING = "planning"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    PROCESSING = "processing"
    AWAITING_FINAL_REVIEW = "awaiting_final_review"
    CANCELLING = "cancelling"
    PARTIAL = "partial"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_JOB_STATUSES = {
    JobStatus.PARTIAL,
    JobStatus.COMPLETED,
    JobStatus.FAILED,
    JobStatus.CANCELLED,
}

JOB_TRANSITIONS: dict[JobStatus, set[JobStatus]] = {
    JobStatus.DRAFT: {JobStatus.QUEUED, JobStatus.CANCELLED},
    JobStatus.QUEUED: {
        JobStatus.PLANNING,
        JobStatus.PROCESSING,
        JobStatus.CANCELLING,
        JobStatus.CANCELLED,
        JobStatus.FAILED,
    },
    JobStatus.PLANNING: {
        JobStatus.AWAITING_CONFIRMATION,
        JobStatus.PROCESSING,
        JobStatus.CANCELLING,
        JobStatus.FAILED,
    },
    JobStatus.AWAITING_CONFIRMATION: {
        JobStatus.PLANNING,
        JobStatus.PROCESSING,
        JobStatus.CANCELLING,
        JobStatus.CANCELLED,
        JobStatus.FAILED,
    },
    JobStatus.PROCESSING: {
        JobStatus.AWAITING_FINAL_REVIEW,
        JobStatus.CANCELLING,
        JobStatus.PARTIAL,
        JobStatus.COMPLETED,
        JobStatus.FAILED,
    },
    JobStatus.AWAITING_FINAL_REVIEW: {
        JobStatus.PROCESSING,
        JobStatus.PARTIAL,
        JobStatus.COMPLETED,
        JobStatus.CANCELLING,
        JobStatus.FAILED,
    },
    JobStatus.CANCELLING: {JobStatus.CANCELLED},
}


class EcommerceAccountStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    DISABLED = "disabled"


class EcommerceLoginState(StrEnum):
    UNKNOWN = "unknown"
    READY = "ready"
    NEEDS_LOGIN = "needs_login"


class EcommerceLoginSessionStatus(StrEnum):
    ACTIVE = "active"
    COMPLETED = "completed"
    EXPIRED = "expired"
    CANCELLED = "cancelled"
    FAILED = "failed"


class CrawlTaskStatus(StrEnum):
    CREATED = "created"
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_SESSION = "waiting_session"
    CANCELLING = "cancelling"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    RETRY_SCHEDULED = "retry_scheduled"


CRAWL_TASK_TERMINAL_STATUSES = {
    CrawlTaskStatus.SUCCEEDED,
    CrawlTaskStatus.FAILED,
    CrawlTaskStatus.CANCELLED,
}

CRAWL_TASK_TRANSITIONS: dict[CrawlTaskStatus, set[CrawlTaskStatus]] = {
    CrawlTaskStatus.CREATED: {CrawlTaskStatus.QUEUED, CrawlTaskStatus.CANCELLED},
    CrawlTaskStatus.QUEUED: {
        CrawlTaskStatus.RUNNING,
        CrawlTaskStatus.CANCELLING,
        CrawlTaskStatus.CANCELLED,
        CrawlTaskStatus.FAILED,
    },
    CrawlTaskStatus.RUNNING: {
        CrawlTaskStatus.WAITING_SESSION,
        CrawlTaskStatus.CANCELLING,
        CrawlTaskStatus.SUCCEEDED,
        CrawlTaskStatus.FAILED,
        CrawlTaskStatus.CANCELLED,
    },
    CrawlTaskStatus.WAITING_SESSION: {
        CrawlTaskStatus.QUEUED,
        CrawlTaskStatus.CANCELLING,
        CrawlTaskStatus.FAILED,
        CrawlTaskStatus.CANCELLED,
    },
    CrawlTaskStatus.CANCELLING: {CrawlTaskStatus.CANCELLED},
    CrawlTaskStatus.FAILED: {CrawlTaskStatus.RETRY_SCHEDULED},
    CrawlTaskStatus.CANCELLED: {CrawlTaskStatus.RETRY_SCHEDULED},
    CrawlTaskStatus.RETRY_SCHEDULED: {CrawlTaskStatus.QUEUED},
}


def can_transition_crawl_task(
    current: CrawlTaskStatus, target: CrawlTaskStatus
) -> bool:
    return target in CRAWL_TASK_TRANSITIONS.get(current, set())


class JobItemStatus(StrEnum):
    PENDING = "pending"
    QUEUED = "queued"
    RUNNING = "running"
    RETRY_WAIT = "retry_wait"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"
    REUSED = "reused"


class AttemptStatus(StrEnum):
    CLAIMED = "claimed"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    OUTCOME_UNKNOWN = "outcome_unknown"
    CANCELLED = "cancelled"


class PlanValidationStatus(StrEnum):
    PENDING = "pending"
    VALID = "valid"
    INVALID = "invalid"


class UsageCompleteness(StrEnum):
    NONE = "none"
    PARTIAL = "partial"
    COMPLETE = "complete"


class IdempotencyStatus(StrEnum):
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


class OutboxStatus(StrEnum):
    PENDING = "pending"
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    FAILED = "failed"


class CreditReservationStatus(StrEnum):
    ACTIVE = "active"
    SETTLED = "settled"
    RELEASED = "released"


class RetryScope(StrEnum):
    FULL = "full"
    FAILED = "failed"
    MISSING = "missing"
    SELECTED = "selected"


class JobAssetDirection(StrEnum):
    INPUT = "input"
    INTERMEDIATE = "intermediate"
    OUTPUT = "output"


def can_transition(current: JobStatus, target: JobStatus) -> bool:
    return target in JOB_TRANSITIONS.get(current, set())
