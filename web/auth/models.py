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
