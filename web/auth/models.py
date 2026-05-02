from __future__ import annotations

from pydantic import BaseModel, Field

# ── Shared ───────────────────────────────────────────────────────────────


class SuccessResponse(BaseModel):
    success: bool = True


# ── Auth: Claim / Logout / Me ────────────────────────────────────────────


class ClaimRequest(BaseModel):
    code: str = Field(max_length=50)


class ClaimResponse(BaseModel):
    success: bool
    redirect: str


class LoginRequest(BaseModel):
    display_name: str = Field(max_length=200)
    password: str = Field(min_length=1, max_length=200)


class LoginResponse(BaseModel):
    success: bool
    redirect: str


class SetPasswordRequest(BaseModel):
    password: str = Field(min_length=8, max_length=200)
    current_password: str | None = Field(default=None, max_length=200)


class AppInfo(BaseModel):
    id: str
    name: str
    description: str
    route_prefix: str


class MeResponse(BaseModel):
    user_id: str
    display_name: str
    is_admin: bool
    apps: list[AppInfo]


# ── Admin: Users ─────────────────────────────────────────────────────────


class UserInfo(BaseModel):
    id: str
    display_name: str
    is_active: bool
    is_admin: bool
    created_at: str
    last_seen_at: str
    apps: list[str]


class UserListResponse(BaseModel):
    users: list[UserInfo]


class PatchUserRequest(BaseModel):
    is_active: bool | None = None
    is_admin: bool | None = None


class CreateUserRequest(BaseModel):
    display_name: str = Field(max_length=200)
    app_permissions: list[str] = Field(max_length=20)
    is_admin: bool = False


class CreateUserResponse(BaseModel):
    user_id: str
    display_name: str
    password: str


class ResetPasswordResponse(BaseModel):
    user_id: str
    display_name: str
    password: str


# ── Admin: Sessions ──────────────────────────────────────────────────────


class SessionInfo(BaseModel):
    token_prefix: str
    device_label: str | None
    created_at: str
    last_seen_at: str


# ── Companies ────────────────────────────────────────────────────────────


class CompanyInfo(BaseModel):
    id: str
    legal_name: str
    display_name: str
    slug: str
    primary_timezone: str
    is_active: bool
    created_at: str


class CompanyUserInfo(BaseModel):
    user_id: str
    display_name: str
    role: str
    joined_at: str


class CompanyListResponse(BaseModel):
    companies: list[CompanyInfo]


# ── Company Signup (IR #2) ────────────────────────────────────────────────


class CompanySignupInviteRequest(BaseModel):
    proposed_company_name: str = Field(max_length=200)
    admin_email: str = Field(max_length=200)


class CompanySignupInviteResponse(BaseModel):
    token: str
    link: str


class CompanySignupInviteInfo(BaseModel):
    token: str
    proposed_company_name: str
    admin_email: str
    created_at: str
    expires_at: str
    claimed_at: str | None


class CompanySignupInviteListResponse(BaseModel):
    invites: list[CompanySignupInviteInfo]


class CompanyClaimRequest(BaseModel):
    token: str
    display_name: str = Field(max_length=200)
    password: str = Field(min_length=8, max_length=200)
    legal_name: str = Field(max_length=200)
    company_display_name: str = Field(max_length=200)
    timezone: str = Field(default="America/Chicago", max_length=80)
    address: str | None = Field(default=None, max_length=400)
    phone: str | None = Field(default=None, max_length=40)
    website: str | None = Field(default=None, max_length=400)


class CompanyClaimResponse(BaseModel):
    success: bool
    company_id: str
    redirect: str


# ── Employee Invite (IR #2) ───────────────────────────────────────────────


class EmployeeInviteRequest(BaseModel):
    display_name: str = Field(max_length=200)
    role: str
    app_permissions: list[str] = Field(max_length=20)


class EmployeeInviteResponse(BaseModel):
    code: str
    link: str


class SessionListResponse(BaseModel):
    sessions: list[SessionInfo]


class DeleteSessionsResponse(BaseModel):
    success: bool
    deleted_count: int


# ── Admin: Invites ───────────────────────────────────────────────────────


class InviteCreateRequest(BaseModel):
    display_name: str = Field(max_length=200)
    app_permissions: list[str] = Field(max_length=20)


class InviteCreateResponse(BaseModel):
    code: str
    link: str


class InviteInfo(BaseModel):
    id: str
    display_name: str
    status: str
    app_permissions: list[str]
    created_at: str
    claimed_at: str | None
    claimed_by: str | None


class InviteListResponse(BaseModel):
    invites: list[InviteInfo]


# ── Admin: Apps ──────────────────────────────────────────────────────────


class GrantAppRequest(BaseModel):
    app_id: str


class AppCreateRequest(BaseModel):
    id: str = Field(max_length=50)
    name: str = Field(max_length=200)
    description: str = Field(max_length=500)
    route_prefix: str = Field(max_length=100)


class PatchAppRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    is_active: bool | None = None


class AppFullInfo(BaseModel):
    id: str
    name: str
    description: str
    route_prefix: str
    is_active: bool
    created_at: str


class AppListResponse(BaseModel):
    apps: list[AppFullInfo]


# ── Projects (IR-1) ──────────────────────────────────────────────────────


class ProjectCreateRequest(BaseModel):
    project_number: str = Field(max_length=100)
    project_name: str = Field(max_length=200)
    site_address: str = Field(max_length=400)
    timezone: str = Field(default="America/Chicago", max_length=80)
    rain_station_code: str = Field(max_length=50)
    project_start_date: str | None = None
    project_end_date: str | None = None
    re_odot_contact_1: str | None = Field(default=None, max_length=200)
    re_odot_contact_2: str | None = Field(default=None, max_length=200)
    contractor_name: str | None = Field(default=None, max_length=200)
    contract_id: str | None = Field(default=None, max_length=100)
    notes: str | None = Field(default=None, max_length=2000)


class ProjectInfo(BaseModel):
    id: str
    company_id: str
    project_number: str
    project_name: str
    site_address: str
    timezone: str
    rain_station_code: str
    status: str
    auto_weekly_enabled: bool
    last_successful_run_at: str | None
    last_run_status: str | None
    created_at: str


class ProjectListResponse(BaseModel):
    projects: list[ProjectInfo]


class ProjectDetailResponse(BaseModel):
    id: str
    company_id: str
    project_number: str
    project_name: str
    site_address: str
    timezone: str
    rain_station_code: str
    project_start_date: str | None
    project_end_date: str | None
    re_odot_contact_1: str | None
    re_odot_contact_2: str | None
    contractor_name: str | None
    contract_id: str | None
    notes: str | None
    auto_weekly_enabled: bool
    schedule_day_of_week: int
    rain_threshold_inches: float
    notify_on_success: bool
    notify_on_failure: bool
    notification_emails: str | None
    template_review_cadence: str
    auto_pause_on_missed_review: bool
    template_promote_mode: str
    status: str
    active_template_version_id: str | None
    paused_until: str | None
    last_successful_run_at: str | None
    last_run_status: str | None
    last_run_at: str | None
    template_last_reviewed_at: str | None
    last_preview_generated_at: str | None
    archive_zip_path: str | None
    archived_at: str | None
    archived_by_user_id: str | None
    not_document_path: str | None
    not_uploaded_at: str | None
    not_uploaded_by: str | None
    cloud_sync_status: str | None
    created_at: str
    created_by_user_id: str


class ProjectUpdateRequest(BaseModel):
    project_name: str | None = Field(default=None, max_length=200)
    site_address: str | None = Field(default=None, max_length=400)
    timezone: str | None = Field(default=None, max_length=80)
    rain_station_code: str | None = Field(default=None, max_length=50)
    project_start_date: str | None = None
    project_end_date: str | None = None
    re_odot_contact_1: str | None = Field(default=None, max_length=200)
    re_odot_contact_2: str | None = Field(default=None, max_length=200)
    contractor_name: str | None = Field(default=None, max_length=200)
    contract_id: str | None = Field(default=None, max_length=100)
    notes: str | None = Field(default=None, max_length=2000)
    auto_weekly_enabled: bool | None = None
    schedule_day_of_week: int | None = None
    rain_threshold_inches: float | None = None
    notify_on_success: bool | None = None
    notify_on_failure: bool | None = None
    notification_emails: str | None = None
    template_review_cadence: str | None = None
    auto_pause_on_missed_review: bool | None = None
    template_promote_mode: str | None = None
    status: str | None = None
    paused_until: str | None = None


# ── Project Template Versions ────────────────────────────────────────────


class TemplateVersionData(BaseModel):
    """Template data payload — SWPPP form fields stored in template_data column."""

    # Core project fields (mirrors SWPPP form fields from odot_mapping.yaml)
    job_piece: str | None = Field(default=None, max_length=200)
    project_number: str | None = Field(default=None, max_length=100)
    contract_id: str | None = Field(default=None, max_length=100)
    location_description_1: str | None = Field(default=None, max_length=400)
    location_description_2: str | None = Field(default=None, max_length=400)
    re_odot_contact_1: str | None = Field(default=None, max_length=200)
    re_odot_contact_2: str | None = Field(default=None, max_length=200)
    inspection_type: str | None = Field(default=None, max_length=100)
    inspected_by: str | None = Field(default=None, max_length=200)
    reviewed_by: str | None = Field(default=None, max_length=200)
    # Checkbox group defaults — JSON-serializable dict
    checkboxes: dict = Field(default_factory=dict)
    # Any additional SWPPP fields from odot_mapping.yaml
    extra_fields: dict = Field(default_factory=dict)


class TemplateSaveRequest(BaseModel):
    """Request to save a new template version."""

    template_data: TemplateVersionData


class TemplateVersionInfo(BaseModel):
    """Template version metadata (without template_data)."""

    id: str
    project_id: str
    version_number: int
    status: str
    created_at: str
    created_by_user_id: str
    promoted_at: str | None
    promoted_by_user_id: str | None
    superseded_at: str | None


class TemplateVersionDetail(TemplateVersionInfo):
    """Template version with full template_data included."""

    template_data: dict


class TemplateVersionListResponse(BaseModel):
    """Response containing all versions for a project."""

    versions: list[TemplateVersionInfo]
    active_version_id: str | None


class TemplatePromoteModeRequest(BaseModel):
    """Request to update template promote mode (auto or manual)."""

    template_promote_mode: str  # 'auto' or 'manual'


# ── Mailbox Entries (IR-3) ──────────────────────────────────────────────


class MailboxEntryPublic(BaseModel):
    """Public mailbox entry (no auth required)."""

    id: str
    report_date: str
    report_type: str
    generation_mode: str
    file_size_bytes: int | None
    created_at: str


class MailboxProjectView(BaseModel):
    """Public project view for mailbox (no auth required)."""

    project_number: str
    project_name: str
    site_address: str
    entry_count: int
    entries: list[MailboxEntryPublic]
