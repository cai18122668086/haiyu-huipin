from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.mutable import MutableDict, MutableList
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base
from .domain import (
    AdminRole,
    AdminStatus,
    AssetLifecycleStatus,
    AssetMediaKind,
    AssetOriginKind,
    AttemptStatus,
    CrawlTaskStatus,
    CreditReservationStatus,
    EcommerceAccountStatus,
    EcommerceLoginState,
    IdempotencyStatus,
    JobItemStatus,
    JobStatus,
    JobType,
    MembershipRole,
    MembershipStatus,
    OutboxStatus,
    PlanValidationStatus,
    UsageCompleteness,
    UserStatus,
    WorkspaceStatus,
)

IDENTITY_TYPE = BigInteger().with_variant(Integer(), "sqlite")


def json_type() -> JSON:
    return JSON().with_variant(postgresql.JSONB(), "postgresql")


def utc_now() -> datetime:
    return datetime.now(UTC)


RETENTION_DAYS = 30


def retention_deadline() -> datetime:
    return utc_now() + timedelta(days=RETENTION_DAYS)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        server_default=text("CURRENT_TIMESTAMP"),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        server_default=text("CURRENT_TIMESTAMP"),
        nullable=False,
    )


class UserRecord(TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("auth_provider", "auth_subject", name="uq_users_auth"),
        Index("uq_users_phone_number", "phone_number", unique=True),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    auth_provider: Mapped[str] = mapped_column(String(32), nullable=False)
    auth_subject: Mapped[str] = mapped_column(String(255), nullable=False)
    email_normalized: Mapped[str | None] = mapped_column(String(320))
    phone_number: Mapped[str | None] = mapped_column(String(24))
    password_hash: Mapped[str | None] = mapped_column(String(255))
    password_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(
        String(16), default=UserStatus.ACTIVE.value, nullable=False
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ProcessingConsentRecord(Base):
    __tablename__ = "processing_consents"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    policy_version: Mapped[str] = mapped_column(String(40), nullable=False)
    notice_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    notice: Mapped[dict] = mapped_column(json_type(), nullable=False)
    choices: Mapped[dict] = mapped_column(json_type(), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    accepted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AdminUserRecord(TimestampMixin, Base):
    __tablename__ = "admin_users"
    __table_args__ = (
        CheckConstraint(
            "role IN ('super_admin', 'admin')",
            name="ck_admin_users_role",
        ),
        CheckConstraint(
            "status IN ('active', 'disabled')",
            name="ck_admin_users_status",
        ),
    )

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), primary_key=True
    )
    role: Mapped[str] = mapped_column(
        String(24), default=AdminRole.ADMIN.value, nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(16), default=AdminStatus.ACTIVE.value, nullable=False, index=True
    )
    permissions: Mapped[list[str]] = mapped_column(
        json_type(), default=list, server_default=text("'[]'"), nullable=False
    )
    created_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    updated_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )


class WorkspaceRecord(TimestampMixin, Base):
    __tablename__ = "workspaces"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), default=WorkspaceStatus.ACTIVE.value, nullable=False, index=True
    )
    created_by_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WorkspaceMemberRecord(TimestampMixin, Base):
    __tablename__ = "workspace_members"

    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[str] = mapped_column(
        String(16), default=MembershipRole.MEMBER.value, nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(16), default=MembershipStatus.ACTIVE.value, nullable=False
    )


class WorkspaceLimitRecord(TimestampMixin, Base):
    __tablename__ = "workspace_limits"

    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True
    )
    storage_quota_bytes: Mapped[int] = mapped_column(
        BigInteger, default=10 * 1024 * 1024 * 1024, nullable=False
    )
    max_active_jobs: Mapped[int] = mapped_column(Integer, default=3000, nullable=False)
    max_concurrent_jobs: Mapped[int] = mapped_column(
        Integer, default=3000, nullable=False
    )
    daily_provider_cost_limit_micros: Mapped[int] = mapped_column(
        BigInteger, default=10_000_000_000_000, nullable=False
    )
    monthly_provider_cost_limit_micros: Mapped[int] = mapped_column(
        BigInteger, default=10_000_000_000_000, nullable=False
    )
    max_upload_bytes: Mapped[int] = mapped_column(
        BigInteger, default=20 * 1024 * 1024, nullable=False
    )


class AssetRecord(Base):
    __tablename__ = "assets"
    __table_args__ = (
        CheckConstraint("byte_size > 0", name="ck_assets_byte_size_positive"),
        CheckConstraint("width IS NULL OR width > 0", name="ck_assets_width_positive"),
        CheckConstraint(
            "height IS NULL OR height > 0", name="ck_assets_height_positive"
        ),
        UniqueConstraint("workspace_id", "id", name="uq_assets_workspace_id"),
        UniqueConstraint(
            "storage_provider",
            "storage_bucket",
            "object_key",
            "object_version",
            name="uq_assets_storage_object",
        ),
        Index(
            "ix_assets_workspace_created",
            "workspace_id",
            text("created_at DESC"),
            text("id DESC"),
        ),
        Index("ix_assets_retention_due", "retention_class", "expires_at"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False
    )
    media_kind: Mapped[str] = mapped_column(
        String(16), default=AssetMediaKind.IMAGE.value, nullable=False
    )
    origin_kind: Mapped[str] = mapped_column(
        String(16), default=AssetOriginKind.UPLOAD.value, nullable=False
    )
    lifecycle_status: Mapped[str] = mapped_column(
        String(16), default=AssetLifecycleStatus.PENDING.value, nullable=False
    )
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(64), nullable=False)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_provider: Mapped[str] = mapped_column(String(16), nullable=False)
    storage_bucket: Mapped[str] = mapped_column(String(128), nullable=False)
    object_key: Mapped[str] = mapped_column(String(512), nullable=False)
    object_version: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    etag: Mapped[str | None] = mapped_column(String(255))
    asset_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        MutableDict.as_mutable(json_type()),
        default=dict,
        server_default=text("'{}'"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        server_default=text("CURRENT_TIMESTAMP"),
        nullable=False,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retention_class: Mapped[str] = mapped_column(
        String(24),
        default="user_30d",
        server_default=text("'user_30d'"),
        nullable=False,
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=retention_deadline
    )
    purge_attempts: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"), nullable=False
    )
    purge_next_retry_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    purge_last_error: Mapped[str | None] = mapped_column(String(120))
    purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Compatibility aliases used only by the Phase 1 migration/backfill.
    @property
    def filename(self) -> str:
        return self.original_filename


class EcommerceAccountRecord(TimestampMixin, Base):
    """Workspace-owned ecommerce identity with one isolated browser profile."""

    __tablename__ = "ecommerce_accounts"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "platform",
            "account_key",
            name="uq_ecommerce_accounts_workspace_platform_key",
        ),
        UniqueConstraint("profile_id", name="uq_ecommerce_accounts_profile"),
        UniqueConstraint("profile_path", name="uq_ecommerce_accounts_profile_path"),
        Index("ix_ecommerce_accounts_workspace_status", "workspace_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    created_by: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    platform: Mapped[str] = mapped_column(String(24), nullable=False)
    account_key: Mapped[str] = mapped_column(String(128), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(120))
    profile_id: Mapped[str] = mapped_column(String(128), nullable=False)
    profile_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    # Opaque id of the outbound Worker that owns this account's browser
    # profile. Browser paths and credentials never cross the control plane.
    worker_id: Mapped[str | None] = mapped_column(String(40), index=True)
    status: Mapped[str] = mapped_column(
        String(16), default=EcommerceAccountStatus.ACTIVE.value, nullable=False
    )
    login_state: Mapped[str] = mapped_column(
        String(24), default=EcommerceLoginState.UNKNOWN.value, nullable=False
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EcommerceLoginSessionRecord(Base):
    """Durable, workspace-scoped lease for a human-assisted login flow."""

    __tablename__ = "ecommerce_login_sessions"
    __table_args__ = (
        Index(
            "ix_ecommerce_login_sessions_account_status",
            "account_id",
            "status",
            "expires_at",
        ),
        Index(
            "ix_ecommerce_login_sessions_workspace_created",
            "workspace_id",
            "started_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    account_id: Mapped[str] = mapped_column(
        ForeignKey("ecommerce_accounts.id", ondelete="CASCADE"), nullable=False
    )
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    started_by: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(24), default="active", server_default=text("'active'"), nullable=False
    )
    transport: Mapped[str] = mapped_column(String(24), nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        server_default=text("CURRENT_TIMESTAMP"),
        nullable=False,
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(64))
    # When transport is ``remote_worker`` these fields bind the login lease to
    # one outbound worker.  The control plane stores only opaque identifiers
    # and coarse state; browser profiles and credentials remain local.
    worker_id: Mapped[str | None] = mapped_column(String(40), index=True)
    state: Mapped[str] = mapped_column(
        String(32), default="queued", server_default=text("'queued'"), nullable=False
    )
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_frame_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EcommerceWorkerRecord(TimestampMixin, Base):
    """A registered outbound-only local browser execution agent."""

    __tablename__ = "ecommerce_workers"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "workspace_id",
            "device_id",
            name="uq_ecommerce_workers_user_workspace_device",
        ),
        Index("ix_ecommerce_workers_workspace_status", "workspace_id", "status"),
        Index("ix_ecommerce_workers_last_seen", "last_seen_at"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    device_id: Mapped[str] = mapped_column(String(128), nullable=False)
    protocol_version: Mapped[str] = mapped_column(String(32), nullable=False)
    agent_version: Mapped[str] = mapped_column(String(64), nullable=False)
    capabilities: Mapped[list[str]] = mapped_column(
        MutableList.as_mutable(json_type()), default=list, nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(16), default="offline", server_default=text("'offline'"), nullable=False
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    session_token_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    session_token_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    # Long-lived, hashed recovery credential used only to mint a new short
    # session token after a Worker has been offline past the session TTL.
    recovery_token_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    recovery_token_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )


class EcommerceWorkerEnrollmentRecord(Base):
    """One-time user-scoped token used to register a local worker."""

    __tablename__ = "ecommerce_worker_enrollments"
    __table_args__ = (Index("ix_ecommerce_worker_enrollments_expires", "expires_at"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    device_id: Mapped[str] = mapped_column(String(128), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        server_default=text("CURRENT_TIMESTAMP"),
        nullable=False,
    )


class CrawlTaskRecord(TimestampMixin, Base):
    __tablename__ = "crawl_tasks"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "idempotency_key", name="uq_crawl_tasks_idempotency"
        ),
        Index("ix_crawl_tasks_workspace_created", "workspace_id", "created_at", "id"),
        Index("ix_crawl_tasks_status_queued", "status", "queued_at"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    created_by: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    platform: Mapped[str] = mapped_column(String(24), nullable=False)
    source_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    canonical_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), default=CrawlTaskStatus.CREATED.value, nullable=False
    )
    stage: Mapped[str | None] = mapped_column(String(64))
    progress: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message_safe: Mapped[str | None] = mapped_column(Text)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    account_id: Mapped[str | None] = mapped_column(
        ForeignKey("ecommerce_accounts.id", ondelete="SET NULL")
    )
    profile_id: Mapped[str | None] = mapped_column(String(128))
    result_snapshot_id: Mapped[str | None] = mapped_column(String(40))
    request_payload: Mapped[dict[str, Any]] = mapped_column(
        MutableDict.as_mutable(json_type()), default=dict, nullable=False
    )
    result_payload: Mapped[dict[str, Any] | None] = mapped_column(json_type())
    state_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cancel_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    queued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CrawlProfileLeaseRecord(TimestampMixin, Base):
    """Cross-process mutex for one persistent ecommerce browser profile."""

    __tablename__ = "crawl_profile_leases"
    __table_args__ = (
        UniqueConstraint("profile_id", name="uq_crawl_profile_leases_profile"),
        UniqueConstraint("task_id", name="uq_crawl_profile_leases_task"),
        Index("ix_crawl_profile_leases_expires", "expires_at"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    profile_id: Mapped[str] = mapped_column(String(128), nullable=False)
    platform: Mapped[str] = mapped_column(String(24), nullable=False)
    task_id: Mapped[str] = mapped_column(
        ForeignKey("crawl_tasks.id", ondelete="CASCADE"), nullable=False
    )
    owner_id: Mapped[str] = mapped_column(String(128), nullable=False)
    fencing_token: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    acquired_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    heartbeat_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class CrawlAttemptRecord(Base):
    __tablename__ = "crawl_attempts"
    __table_args__ = (
        UniqueConstraint("task_id", "attempt_no", name="uq_crawl_attempt_task_no"),
        Index("ix_crawl_attempts_task_created", "task_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    task_id: Mapped[str] = mapped_column(
        ForeignKey("crawl_tasks.id", ondelete="CASCADE"), nullable=False
    )
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    worker_id: Mapped[str] = mapped_column(String(128), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message_safe: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        server_default=text("CURRENT_TIMESTAMP"),
        nullable=False,
    )


class CrawlEventRecord(Base):
    __tablename__ = "crawl_events"
    __table_args__ = (
        Index("ix_crawl_events_task_created", "task_id", "created_at", "id"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    task_id: Mapped[str] = mapped_column(
        ForeignKey("crawl_tasks.id", ondelete="CASCADE"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(32))
    to_status: Mapped[str | None] = mapped_column(String(32))
    message: Mapped[str] = mapped_column(String(500), nullable=False)
    details: Mapped[dict[str, Any] | None] = mapped_column(json_type())
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        server_default=text("CURRENT_TIMESTAMP"),
        nullable=False,
    )


class ProductSnapshotRecord(TimestampMixin, Base):
    __tablename__ = "product_snapshots"
    __table_args__ = (
        UniqueConstraint("task_id", name="uq_product_snapshots_task"),
        Index("ix_product_snapshots_workspace_created", "workspace_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False
    )
    task_id: Mapped[str] = mapped_column(
        ForeignKey("crawl_tasks.id", ondelete="CASCADE"), nullable=False
    )
    schema_version: Mapped[str] = mapped_column(
        String(24), default="product_snapshot.v1", nullable=False
    )
    platform: Mapped[str] = mapped_column(String(24), nullable=False)
    product_id: Mapped[str | None] = mapped_column(String(128))
    canonical_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    adapter_version: Mapped[str] = mapped_column(
        String(64), default="ecommerce-adapter.v1", nullable=False
    )
    parser_version: Mapped[str] = mapped_column(
        String(64), default="ecommerce-normalizer.v1", nullable=False
    )
    title: Mapped[str | None] = mapped_column(String(500))
    price: Mapped[str | None] = mapped_column(String(128))
    shop_name: Mapped[str | None] = mapped_column(String(300))
    sales: Mapped[str | None] = mapped_column(String(128))
    payload: Mapped[dict[str, Any]] = mapped_column(
        MutableDict.as_mutable(json_type()), default=dict, nullable=False
    )
    warnings: Mapped[list[str]] = mapped_column(
        json_type(), default=list, server_default=text("'[]'"), nullable=False
    )


class ProductAssetRecord(Base):
    __tablename__ = "product_assets"
    __table_args__ = (
        UniqueConstraint(
            "snapshot_id",
            "role",
            "ordinal",
            name="uq_product_assets_snapshot_role_ordinal",
        ),
        Index("ix_product_assets_snapshot", "snapshot_id", "role", "ordinal"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("product_snapshots.id", ondelete="CASCADE"), nullable=False
    )
    asset_id: Mapped[str] = mapped_column(
        ForeignKey("assets.id", ondelete="RESTRICT"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(24), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    source_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    source: Mapped[str | None] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        server_default=text("CURRENT_TIMESTAMP"),
        nullable=False,
    )


class JobRecord(TimestampMixin, Base):
    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint("progress >= 0 AND progress <= 100", name="ck_jobs_progress"),
        CheckConstraint(
            "expected_item_count >= 0", name="ck_jobs_expected_count_nonnegative"
        ),
        CheckConstraint(
            "succeeded_item_count >= 0 AND failed_item_count >= 0",
            name="ck_jobs_result_counts_nonnegative",
        ),
        CheckConstraint(
            "succeeded_item_count + failed_item_count <= expected_item_count",
            name="ck_jobs_result_counts_bounded",
        ),
        CheckConstraint(
            "actual_cost_micros IS NULL OR actual_cost_micros >= 0",
            name="ck_jobs_actual_cost_nonnegative",
        ),
        CheckConstraint(
            "api_request_count >= 0",
            name="ck_jobs_api_request_count_nonnegative",
        ),
        CheckConstraint(
            """
            (
                status IN ('partial', 'completed', 'failed', 'cancelled')
                AND finished_at IS NOT NULL
                AND (
                    (
                        status = 'completed'
                        AND succeeded_item_count = expected_item_count
                        AND failed_item_count = 0
                    )
                    OR (
                        status = 'partial'
                        AND succeeded_item_count > 0
                        AND succeeded_item_count < expected_item_count
                    )
                    OR (status = 'failed' AND succeeded_item_count = 0)
                    OR status = 'cancelled'
                )
            )
            OR (
                status NOT IN ('partial', 'completed', 'failed', 'cancelled')
                AND finished_at IS NULL
            )
            """,
            name="ck_jobs_terminal_consistency",
        ),
        UniqueConstraint("workspace_id", "id", name="uq_jobs_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "root_job_id"],
            ["jobs.workspace_id", "jobs.id"],
            name="fk_jobs_workspace_root_job",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "retry_of_job_id"],
            ["jobs.workspace_id", "jobs.id"],
            name="fk_jobs_workspace_retry_of_job",
        ),
        Index("ix_jobs_workspace_created", "workspace_id", "created_at", "id"),
        Index(
            "ix_jobs_workspace_status_created",
            "workspace_id",
            "status",
            text("created_at DESC"),
            text("id DESC"),
        ),
        Index(
            "ix_jobs_workspace_type_status_created",
            "workspace_id",
            "type",
            "status",
            "created_at",
            "id",
        ),
        Index("ix_jobs_history_due", "history_expires_at", "history_deleted_at"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False
    )
    type: Mapped[str] = mapped_column(
        String(32), default=JobType.LOCAL_EDIT.value, nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(32), default=JobStatus.DRAFT.value, nullable=False
    )
    stage: Mapped[str | None] = mapped_column(String(64))
    display_name: Mapped[str | None] = mapped_column(String(160))
    progress: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    configuration: Mapped[dict[str, Any]] = mapped_column(
        MutableDict.as_mutable(json_type()),
        default=dict,
        nullable=False,
    )
    configuration_schema: Mapped[str] = mapped_column(
        String(32), default="v1", nullable=False
    )
    prompt_version: Mapped[str] = mapped_column(
        String(64), default="phase1.1-mock", nullable=False
    )
    strategy_version: Mapped[str] = mapped_column(
        String(64), default="phase1.1-mock", nullable=False
    )
    release_version: Mapped[str] = mapped_column(
        String(64), default="phase1.1", nullable=False
    )
    root_job_id: Mapped[str | None] = mapped_column(
        ForeignKey("jobs.id"), nullable=True, index=True
    )
    retry_of_job_id: Mapped[str | None] = mapped_column(
        ForeignKey("jobs.id"), nullable=True, index=True
    )
    retry_scope: Mapped[str | None] = mapped_column(String(16))
    state_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    expected_item_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    succeeded_item_count: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    failed_item_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    error_details: Mapped[dict[str, Any] | None] = mapped_column(json_type())
    estimated_cost_min_micros: Mapped[int | None] = mapped_column(BigInteger)
    estimated_cost_max_micros: Mapped[int | None] = mapped_column(BigInteger)
    actual_cost_micros: Mapped[int | None] = mapped_column(BigInteger)
    currency: Mapped[str | None] = mapped_column(String(3))
    estimated_credits: Mapped[int | None] = mapped_column(BigInteger)
    actual_credits: Mapped[int | None] = mapped_column(BigInteger)
    usage_completeness: Mapped[str] = mapped_column(
        String(16), default=UsageCompleteness.NONE.value, nullable=False
    )
    api_request_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cancel_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    cancel_reason: Mapped[str | None] = mapped_column(Text)
    queued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    history_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=retention_deadline
    )
    history_deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class JobItemRecord(TimestampMixin, Base):
    __tablename__ = "job_items"
    __table_args__ = (
        UniqueConstraint("job_id", "item_key", name="uq_job_items_key"),
        UniqueConstraint("job_id", "ordinal", name="uq_job_items_ordinal"),
        UniqueConstraint(
            "workspace_id", "job_id", "id", name="uq_job_items_workspace_job_id"
        ),
        ForeignKeyConstraint(
            ["workspace_id", "job_id"],
            ["jobs.workspace_id", "jobs.id"],
            ondelete="CASCADE",
        ),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(40), nullable=False)
    job_id: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    item_key: Mapped[str] = mapped_column(String(64), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(24), default=JobItemStatus.PENDING.value, nullable=False
    )
    stage: Mapped[str | None] = mapped_column(String(64))
    quality: Mapped[str | None] = mapped_column(String(16))
    configuration: Mapped[dict[str, Any]] = mapped_column(
        MutableDict.as_mutable(json_type()),
        default=dict,
        server_default=text("'{}'"),
        nullable=False,
    )
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    source_job_item_id: Mapped[str | None] = mapped_column(
        ForeignKey("job_items.id"), nullable=True
    )
    result_source: Mapped[str | None] = mapped_column(String(16))
    state_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    error_details: Mapped[dict[str, Any] | None] = mapped_column(json_type())
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class JobAssetRecord(Base):
    __tablename__ = "job_assets"
    __table_args__ = (
        CheckConstraint("ordinal >= 0", name="ck_job_assets_ordinal"),
        Index(
            "uq_job_assets_role_ordinal",
            "job_id",
            "job_item_id",
            "direction",
            "role",
            "ordinal",
            unique=True,
            postgresql_nulls_not_distinct=True,
        ),
        ForeignKeyConstraint(
            ["workspace_id", "job_id"],
            ["jobs.workspace_id", "jobs.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "asset_id"],
            ["assets.workspace_id", "assets.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "job_id", "job_item_id"],
            ["job_items.workspace_id", "job_items.job_id", "job_items.id"],
            name="fk_job_assets_workspace_job_item",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[int] = mapped_column(IDENTITY_TYPE, primary_key=True, autoincrement=True)
    workspace_id: Mapped[str] = mapped_column(String(40), nullable=False)
    job_id: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    job_item_id: Mapped[str | None] = mapped_column(
        ForeignKey("job_items.id", ondelete="CASCADE")
    )
    asset_id: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    direction: Mapped[str] = mapped_column(String(16), nullable=False)
    role: Mapped[str] = mapped_column(String(40), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_deliverable: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    validation: Mapped[dict[str, Any]] = mapped_column(
        MutableDict.as_mutable(json_type()),
        default=dict,
        server_default=text("'{}'"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        server_default=text("CURRENT_TIMESTAMP"),
        nullable=False,
    )


class JobPlanRecord(Base):
    __tablename__ = "job_plans"
    __table_args__ = (
        UniqueConstraint("job_id", "revision", name="uq_job_plans_revision"),
        ForeignKeyConstraint(
            ["workspace_id", "job_id"],
            ["jobs.workspace_id", "jobs.id"],
            ondelete="CASCADE",
        ),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(40), nullable=False)
    job_id: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    schema_version: Mapped[str] = mapped_column(
        String(64), default="v1", nullable=False
    )
    payload: Mapped[dict[str, Any]] = mapped_column(json_type(), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    validation_status: Mapped[str] = mapped_column(
        String(16), default=PlanValidationStatus.PENDING.value, nullable=False
    )
    validation_errors: Mapped[list[dict[str, Any]]] = mapped_column(
        json_type(), default=list, server_default=text("'[]'"), nullable=False
    )
    validator_version: Mapped[str] = mapped_column(
        String(64), default="phase1.1", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        server_default=text("CURRENT_TIMESTAMP"),
        nullable=False,
    )
    validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class JobAttemptRecord(Base):
    __tablename__ = "job_attempts"
    __table_args__ = (
        CheckConstraint(
            "attempt_no > 0",
            name="ck_job_attempts_attempt_no_positive",
        ),
        UniqueConstraint("job_item_id", "attempt_no", name="uq_job_attempt_no"),
        Index("ix_attempt_provider_idem", "provider", "provider_idempotency_key"),
        UniqueConstraint(
            "workspace_id",
            "job_id",
            "job_item_id",
            "id",
            name="uq_job_attempts_workspace_job_item_id",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "job_id"],
            ["jobs.workspace_id", "jobs.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "job_id", "job_item_id"],
            ["job_items.workspace_id", "job_items.job_id", "job_items.id"],
            name="fk_job_attempts_workspace_job_item",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(40), nullable=False)
    job_id: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    job_item_id: Mapped[str] = mapped_column(
        ForeignKey("job_items.id", ondelete="CASCADE"), nullable=False, index=True
    )
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(24), default=AttemptStatus.CLAIMED.value, nullable=False
    )
    lease_token: Mapped[str] = mapped_column(String(64), nullable=False)
    leased_by: Mapped[str] = mapped_column(String(128), nullable=False)
    lease_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(32), default="mock", nullable=False)
    operation: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_request_id: Mapped[str | None] = mapped_column(String(255), unique=True)
    provider_idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    error_details: Mapped[dict[str, Any] | None] = mapped_column(json_type())
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        server_default=text("CURRENT_TIMESTAMP"),
        nullable=False,
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AmazonSuiteReviewAttemptRecord(TimestampMixin, Base):
    __tablename__ = "amazon_suite_review_attempts"
    __table_args__ = (
        CheckConstraint(
            "review_round > 0",
            name="ck_amazon_suite_review_round_positive",
        ),
        CheckConstraint(
            "actual_cost_micros IS NULL OR actual_cost_micros >= 0",
            name="ck_amazon_suite_review_actual_cost_nonnegative",
        ),
        CheckConstraint(
            "estimated_cost_micros IS NULL OR estimated_cost_micros >= 0",
            name="ck_amazon_suite_review_estimated_cost_nonnegative",
        ),
        UniqueConstraint(
            "job_id",
            "review_round",
            "output_asset_set_hash",
            name="uq_amazon_suite_review_job_round_output",
        ),
        UniqueConstraint(
            "provider",
            "provider_idempotency_key",
            name="uq_amazon_suite_review_provider_idem",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "job_id"],
            ["jobs.workspace_id", "jobs.id"],
            name="fk_amazon_suite_review_workspace_job",
            ondelete="CASCADE",
        ),
        Index(
            "ix_amazon_suite_review_job_status_lease",
            "workspace_id",
            "job_id",
            "status",
            "lease_expires_at",
        ),
        Index(
            "ix_amazon_suite_review_provider_request",
            "provider",
            "provider_request_id",
        ),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(40), nullable=False)
    job_id: Mapped[str] = mapped_column(String(40), nullable=False)
    review_round: Mapped[int] = mapped_column(Integer, nullable=False)
    output_asset_set_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(
        String(24), default="claimed", server_default=text("'claimed'"), nullable=False
    )
    lease_token: Mapped[str] = mapped_column(String(64), nullable=False)
    leased_by: Mapped[str] = mapped_column(String(128), nullable=False)
    lease_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    provider_request_id: Mapped[str | None] = mapped_column(String(255))
    raw_usage: Mapped[dict[str, Any]] = mapped_column(
        MutableDict.as_mutable(json_type()),
        default=dict,
        server_default=text("'{}'"),
        nullable=False,
    )
    normalized_usage: Mapped[dict[str, Any]] = mapped_column(
        MutableDict.as_mutable(json_type()),
        default=dict,
        server_default=text("'{}'"),
        nullable=False,
    )
    actual_cost_micros: Mapped[int | None] = mapped_column(BigInteger)
    estimated_cost_micros: Mapped[int | None] = mapped_column(BigInteger)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    provider_call_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UsageEventRecord(Base):
    __tablename__ = "usage_events"
    __table_args__ = (
        CheckConstraint(
            "estimated_cost_micros IS NULL OR estimated_cost_micros >= 0",
            name="ck_usage_estimated_cost",
        ),
        CheckConstraint(
            "actual_cost_micros IS NULL OR actual_cost_micros >= 0",
            name="ck_usage_actual_cost",
        ),
        UniqueConstraint("attempt_id", name="uq_usage_events_attempt_id"),
        ForeignKeyConstraint(
            ["workspace_id", "job_id", "job_item_id", "attempt_id"],
            [
                "job_attempts.workspace_id",
                "job_attempts.job_id",
                "job_attempts.job_item_id",
                "job_attempts.id",
            ],
            name="fk_usage_events_workspace_job_item_attempt",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[int] = mapped_column(IDENTITY_TYPE, primary_key=True, autoincrement=True)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id"), nullable=False, index=True
    )
    job_id: Mapped[str] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    job_item_id: Mapped[str] = mapped_column(
        ForeignKey("job_items.id", ondelete="CASCADE"), nullable=False
    )
    attempt_id: Mapped[str] = mapped_column(
        ForeignKey("job_attempts.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    operation: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_request_id: Mapped[str | None] = mapped_column(String(255))
    input_tokens: Mapped[int | None] = mapped_column(BigInteger)
    output_tokens: Mapped[int | None] = mapped_column(BigInteger)
    cached_tokens: Mapped[int | None] = mapped_column(BigInteger)
    input_image_count: Mapped[int | None] = mapped_column(Integer)
    output_image_count: Mapped[int | None] = mapped_column(Integer)
    quality: Mapped[str | None] = mapped_column(String(16))
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    usage_reported: Mapped[bool] = mapped_column(Boolean, nullable=False)
    raw_usage: Mapped[dict[str, Any]] = mapped_column(
        json_type(), default=dict, server_default=text("'{}'"), nullable=False
    )
    pricing_version: Mapped[str] = mapped_column(String(64), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    estimated_cost_micros: Mapped[int | None] = mapped_column(BigInteger)
    actual_cost_micros: Mapped[int | None] = mapped_column(BigInteger)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        server_default=text("CURRENT_TIMESTAMP"),
        nullable=False,
    )


class IdempotencyRequestRecord(TimestampMixin, Base):
    __tablename__ = "idempotency_requests"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "method",
            "route_template",
            "idempotency_key",
            name="uq_idempotency_scope",
        ),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    actor_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    method: Mapped[str] = mapped_column(String(10), nullable=False)
    route_template: Mapped[str] = mapped_column(String(255), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), default=IdempotencyStatus.IN_PROGRESS.value, nullable=False
    )
    resource_type: Mapped[str | None] = mapped_column(String(32))
    resource_id: Mapped[str | None] = mapped_column(String(40))
    response_status: Mapped[int | None] = mapped_column(Integer)
    response_body: Mapped[dict[str, Any] | None] = mapped_column(json_type())
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class OutboxEventRecord(Base):
    __tablename__ = "outbox_events"
    __table_args__ = (
        Index(
            "ix_outbox_events_claim",
            "available_at",
            "created_at",
            postgresql_where=text("status IN ('pending', 'failed')"),
            sqlite_where=text("status IN ('pending', 'failed')"),
        ),
        Index(
            "ix_outbox_events_reclaim",
            "locked_at",
            "created_at",
            postgresql_where=text("status = 'publishing' AND locked_at IS NOT NULL"),
            sqlite_where=text("status = 'publishing' AND locked_at IS NOT NULL"),
        ),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    aggregate_type: Mapped[str] = mapped_column(String(32), nullable=False)
    aggregate_id: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(json_type(), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), default=OutboxStatus.PENDING.value, nullable=False
    )
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_by: Mapped[str | None] = mapped_column(String(128))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        server_default=text("CURRENT_TIMESTAMP"),
        nullable=False,
    )


class WorkspaceCreditAccountRecord(TimestampMixin, Base):
    __tablename__ = "workspace_credit_accounts"
    __table_args__ = (
        CheckConstraint("balance_credits >= 0", name="ck_credit_balance_nonnegative"),
        CheckConstraint("reserved_credits >= 0", name="ck_credit_reserved_nonnegative"),
        CheckConstraint(
            "reserved_credits <= balance_credits", name="ck_credit_reserved_bounded"
        ),
    )

    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True
    )
    balance_credits: Mapped[int] = mapped_column(
        BigInteger, default=10_000, nullable=False
    )
    reserved_credits: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    lots_initialized: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )


class CreditReservationRecord(TimestampMixin, Base):
    __tablename__ = "credit_reservations"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "id",
            name="uq_credit_reservations_workspace_id",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "job_id"],
            ["jobs.workspace_id", "jobs.id"],
            name="fk_credit_reservations_workspace_job",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    job_id: Mapped[str] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    credit_allocations: Mapped[dict[str, int] | None] = mapped_column(
        json_type(), nullable=True
    )
    pricing_snapshot: Mapped[dict[str, Any] | None] = mapped_column(
        json_type(), nullable=True
    )
    reserved_credits: Mapped[int] = mapped_column(BigInteger, nullable=False)
    settled_credits: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), default=CreditReservationStatus.ACTIVE.value, nullable=False
    )


class CreditLedgerEntryRecord(Base):
    __tablename__ = "credit_ledger_entries"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "reservation_id"],
            ["credit_reservations.workspace_id", "credit_reservations.id"],
            name="fk_credit_ledger_workspace_reservation",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "job_id"],
            ["jobs.workspace_id", "jobs.id"],
            name="fk_credit_ledger_workspace_job",
        ),
        Index(
            "uq_credit_ledger_settlement_reservation",
            "reservation_id",
            unique=True,
            postgresql_where=text(
                "entry_type = 'settle' AND reservation_id IS NOT NULL"
            ),
            sqlite_where=text("entry_type = 'settle' AND reservation_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(IDENTITY_TYPE, primary_key=True, autoincrement=True)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    reservation_id: Mapped[str | None] = mapped_column(
        ForeignKey("credit_reservations.id", ondelete="SET NULL")
    )
    job_id: Mapped[str | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="SET NULL")
    )
    description: Mapped[str | None] = mapped_column(String(200))
    entry_type: Mapped[str] = mapped_column(String(24), nullable=False)
    delta_balance_credits: Mapped[int] = mapped_column(BigInteger, nullable=False)
    delta_reserved_credits: Mapped[int] = mapped_column(BigInteger, nullable=False)
    balance_after_credits: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reserved_after_credits: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        server_default=text("CURRENT_TIMESTAMP"),
        nullable=False,
    )


class SupportCaseRecord(TimestampMixin, Base):
    __tablename__ = "support_cases"
    __table_args__ = (
        UniqueConstraint("workspace_id", "request_key", name="uq_support_case_request"),
        CheckConstraint(
            "status IN ('open','in_progress','resolved')", name="ck_support_case_status"
        ),
    )
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="RESTRICT"), index=True
    )
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    job_id: Mapped[str | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="RESTRICT"), index=True
    )
    request_key: Mapped[str] = mapped_column(String(128))
    issue: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), default="open")
    resolution: Mapped[str] = mapped_column(Text, default="")


class AuditEventRecord(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(IDENTITY_TYPE, primary_key=True, autoincrement=True)
    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="SET NULL"), index=True
    )
    actor_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_type: Mapped[str | None] = mapped_column(String(32))
    resource_id: Mapped[str | None] = mapped_column(String(40))
    request_id: Mapped[str | None] = mapped_column(String(64))
    details: Mapped[dict[str, Any]] = mapped_column(
        json_type(), default=dict, server_default=text("'{}'"), nullable=False
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        server_default=text("CURRENT_TIMESTAMP"),
        nullable=False,
    )


class AnnouncementRecord(TimestampMixin, Base):
    __tablename__ = "announcements"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft', 'published', 'archived')",
            name="ck_announcements_status",
        ),
        CheckConstraint(
            "kind IN ('info', 'success', 'warning', 'maintenance')",
            name="ck_announcements_kind",
        ),
        CheckConstraint(
            "ends_at IS NULL OR starts_at IS NULL OR ends_at > starts_at",
            name="ck_announcements_valid_window",
        ),
        Index(
            "ix_announcements_public_order",
            "status",
            "is_pinned",
            "published_at",
        ),
        Index("ix_announcements_schedule", "status", "starts_at", "ends_at"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    summary: Mapped[str | None] = mapped_column(String(320))
    content: Mapped[str] = mapped_column(Text, nullable=False)
    image_asset_id: Mapped[str | None] = mapped_column(
        ForeignKey("assets.id", ondelete="SET NULL")
    )
    image_alt: Mapped[str | None] = mapped_column(String(240))
    kind: Mapped[str] = mapped_column(String(16), default="info", nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="draft", nullable=False)
    link_url: Mapped[str | None] = mapped_column(String(2048))
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_pinned: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    updated_by_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )


class InspirationCaseRecord(TimestampMixin, Base):
    __tablename__ = "inspiration_cases"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft', 'published', 'archived')",
            name="ck_inspiration_cases_status",
        ),
        CheckConstraint(
            "workflow_type IN ('reference_remix', 'product_prompt', "
            "'local_edit', 'legacy_unclassified')",
            name="ck_inspiration_cases_workflow_type",
        ),
        CheckConstraint(
            "reference_source IN ('admin_reference', 'result_image', 'none')",
            name="ck_inspiration_cases_reference_source",
        ),
        CheckConstraint(
            "configuration_status IN ('draft', 'ready', 'incomplete', 'archived')",
            name="ck_inspiration_cases_configuration_status",
        ),
        Index(
            "ix_inspiration_cases_public_order",
            "status",
            "is_pinned",
            "sort_order",
            "published_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    slug: Mapped[str | None] = mapped_column(String(120), unique=True)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    role: Mapped[str] = mapped_column(String(80), nullable=False)
    type: Mapped[str] = mapped_column(String(40), nullable=False)
    category: Mapped[str] = mapped_column(String(80), nullable=False)
    platform: Mapped[str] = mapped_column(String(16), nullable=False)
    ratio: Mapped[str] = mapped_column(String(24), nullable=False)
    intent: Mapped[str] = mapped_column(String(300), nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    workflow_type: Mapped[str] = mapped_column(
        String(24), default="legacy_unclassified", nullable=False
    )
    reference_source: Mapped[str] = mapped_column(
        String(20), default="none", nullable=False
    )
    configuration_status: Mapped[str] = mapped_column(
        String(16), default="incomplete", nullable=False
    )
    button_label: Mapped[str] = mapped_column(
        String(80), default="一键做同款", nullable=False
    )
    status: Mapped[str] = mapped_column(String(16), default="draft", nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_pinned: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_by_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    updated_by_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class InspirationCaseAssetRecord(Base):
    __tablename__ = "inspiration_case_assets"
    __table_args__ = (
        UniqueConstraint(
            "case_id", "role", "ordinal", name="uq_inspiration_case_asset_role_ordinal"
        ),
        Index("ix_inspiration_case_assets_case", "case_id", "role", "ordinal"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    case_id: Mapped[str] = mapped_column(
        ForeignKey("inspiration_cases.id", ondelete="CASCADE"), nullable=False
    )
    asset_id: Mapped[str] = mapped_column(
        ForeignKey("assets.id", ondelete="RESTRICT"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(24), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    label: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        server_default=text("CURRENT_TIMESTAMP"),
        nullable=False,
    )


class ReplicaDocumentRecord(Base):
    __tablename__ = "replica_documents"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "kind", "fingerprint", name="uq_replica_document_intent"
        ),
        Index("ix_replica_workspace_created", "workspace_id", "created_at"),
    )
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    created_by: Mapped[str] = mapped_column(String(40), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class CreditPurchaseRecord(TimestampMixin, Base):
    __tablename__ = "credit_purchases"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "created_by_user_id",
            "idempotency_key",
            name="uq_credit_purchase_request",
        ),
        UniqueConstraint(
            "merchant_pid", "gateway_trade_no", name="uq_credit_purchase_gateway_trade"
        ),
        CheckConstraint(
            "amount_fen > 0 AND credits > 0", name="ck_credit_purchase_positive"
        ),
        CheckConstraint(
            "status IN ('pending', 'paid', 'partially_refunded', 'refunded', 'closed')",
            name="ck_credit_purchase_status",
        ),
        Index("ix_credit_purchase_reconcile_due", "status", "reconcile_next_at"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    created_by_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    product_snapshot: Mapped[dict[str, Any] | None] = mapped_column(
        json_type(), nullable=True
    )
    package_id: Mapped[str] = mapped_column(String(40), nullable=False)
    package_name: Mapped[str] = mapped_column(String(100), nullable=False)
    amount_fen: Mapped[int] = mapped_column(BigInteger, nullable=False)
    credits: Mapped[int] = mapped_column(BigInteger, nullable=False)
    merchant_pid: Mapped[str] = mapped_column(String(32), nullable=False)
    signing_key_id: Mapped[str] = mapped_column(String(64), nullable=False)
    checkout_url: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="pending", nullable=False)
    gateway_trade_no: Mapped[str | None] = mapped_column(String(64))
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reconcile_next_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    reconcile_lease_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    reconcile_lease_token: Mapped[str | None] = mapped_column(String(32))
    reconcile_attempts: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0"
    )
    reconcile_last_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reconcile_last_status: Mapped[str | None] = mapped_column(String(40))
    reconcile_last_error: Mapped[str | None] = mapped_column(String(80))
    refunded_amount_fen: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default=text("0"), nullable=False
    )
    ledger_entry_id: Mapped[int | None] = mapped_column(
        ForeignKey("credit_ledger_entries.id", ondelete="RESTRICT"), unique=True
    )


class CreditPaymentReconciliationRecord(Base):
    __tablename__ = "credit_payment_reconciliation"
    id: Mapped[str] = mapped_column(String(20), primary_key=True)
    last_completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    checked_count: Mapped[int] = mapped_column(Integer, default=0)
    confirmed_count: Mapped[int] = mapped_column(Integer, default=0)
    error_count: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(String(80))


class CreditMembershipRecord(TimestampMixin, Base):
    __tablename__ = "credit_memberships"
    __table_args__ = (
        CheckConstraint(
            "months IN (1, 12) AND monthly_credits > 0 AND ends_at > starts_at",
            name="ck_credit_membership_terms",
        ),
    )
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    purchase_id: Mapped[str | None] = mapped_column(
        ForeignKey("credit_purchases.id", ondelete="RESTRICT"),
        nullable=True,
        unique=True,
    )
    plan_id: Mapped[str] = mapped_column(String(20), nullable=False)
    plan_name: Mapped[str] = mapped_column(String(100), nullable=False)
    monthly_credits: Mapped[int] = mapped_column(BigInteger, nullable=False)
    months: Mapped[int] = mapped_column(Integer, nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancellation_reason: Mapped[str | None] = mapped_column(String(200))


class CreditAdminActionRecord(TimestampMixin, Base):
    """Permanent idempotency receipt for manual financial/entitlement actions."""

    __tablename__ = "credit_admin_actions"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "actor_user_id",
            "idempotency_key",
            name="uq_credit_admin_action_request",
        ),
    )
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False
    )
    actor_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    response_body: Mapped[dict[str, Any]] = mapped_column(json_type(), nullable=False)


class CreditRefundRecord(TimestampMixin, Base):
    """Durable financial intent; retries always reuse the same provider request ID."""

    __tablename__ = "credit_refunds"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "created_by_user_id",
            "idempotency_key",
            name="uq_credit_refund_request",
        ),
        CheckConstraint("amount_fen > 0", name="ck_credit_refund_positive"),
        CheckConstraint(
            "status IN ('requested', 'processing', 'succeeded', 'failed')",
            name="ck_credit_refund_status",
        ),
        Index("ix_credit_refund_purchase", "purchase_id", "created_at"),
    )
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    purchase_id: Mapped[str] = mapped_column(
        ForeignKey("credit_purchases.id", ondelete="RESTRICT"), nullable=False
    )
    created_by_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    amount_fen: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reason: Mapped[str] = mapped_column(String(160), nullable=False)
    policy: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16),
        default="requested",
        server_default=text("'requested'"),
        nullable=False,
    )
    entitlement_snapshot: Mapped[dict[str, Any]] = mapped_column(
        json_type(), nullable=False
    )
    provider_refund_id: Mapped[str | None] = mapped_column(String(128))
    ledger_entry_id: Mapped[int | None] = mapped_column(
        ForeignKey("credit_ledger_entries.id", ondelete="RESTRICT")
    )
    attempted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_retry_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True
    )
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    last_error_code: Mapped[str | None] = mapped_column(String(80))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CreditGrantRecord(TimestampMixin, Base):
    __tablename__ = "credit_grants"
    __table_args__ = (
        UniqueConstraint("workspace_id", "source_key", name="uq_credit_grant_source"),
        CheckConstraint(
            "amount >= 0 AND remaining >= 0 AND held >= 0 "
            "AND held <= remaining AND remaining <= amount",
            name="ck_credit_grant_balance",
        ),
    )
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    source_key: Mapped[str] = mapped_column(String(100), nullable=False)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    remaining: Mapped[int] = mapped_column(BigInteger, nullable=False)
    held: Mapped[int] = mapped_column(BigInteger, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ledger_entry_id: Mapped[int | None] = mapped_column(
        ForeignKey("credit_ledger_entries.id", ondelete="RESTRICT"),
        nullable=True,
        unique=True,
    )
