"""FastAPI Dependency Injection for Authentication and Tenant Context."""

import uuid
from typing import AsyncGenerator, Optional
from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal, set_tenant_context
from app.models.identity import User
from app.models.organization import Membership


async def get_current_user(
    x_user_id: Optional[str] = Header(None, alias="X-User-ID"),
    authorization: Optional[str] = Header(None, alias="Authorization"),
) -> User:
    """Authenticate the current user.
    
    Accepts X-User-ID header (for tests/demo) or Bearer token.
    Fails closed if user is missing or inactive.
    """
    user_id_raw = x_user_id
    if not user_id_raw and authorization and authorization.startswith("Bearer "):
        # For demo simplicity, tokens formatted as Bearer <user_id> or standalone tokens
        user_id_raw = authorization.split("Bearer ", 1)[1].strip()

    if not user_id_raw:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication credentials were not provided",
        )

    try:
        user_uuid = uuid.UUID(user_id_raw)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token format",
        )

    async with AsyncSessionLocal() as session:
        stmt = select(User).where(User.id == user_uuid, User.is_active == True)
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive",
        )

    return user


async def get_user_session(
    current_user: User = Depends(get_current_user),
) -> AsyncGenerator[AsyncSession, None]:
    """Provide session in User Mode (current_user_id set, current_org_id unset).
    
    Used for listing user's memberships and registering new organizations.
    """
    async with AsyncSessionLocal() as session:
        # Establish User Mode context
        await session.execute(
            text("SELECT set_config('app.current_user_id', :uid, true)"),
            {"uid": str(current_user.id)},
        )
        await session.execute(
            text("SELECT set_config('app.current_org_id', '', true)")
        )
        try:
            yield session
        finally:
            await session.close()


async def get_tenant_session(
    x_organization_id: str = Header(..., alias="X-Organization-ID"),
    current_user: User = Depends(get_current_user),
) -> AsyncGenerator[AsyncSession, None]:
    """Provide session in Tenant Mode with RLS context strictly bound.
    
    Verifies the user's active membership in the target organization before
    binding the transaction-local app.current_org_id. Fails closed with 403
    if the user is not an active member.
    """
    try:
        org_uuid = uuid.UUID(x_organization_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid organization ID format",
        )

    async with AsyncSessionLocal() as session:
        # 1. Authorize: Set user context so user can query their own membership under RLS
        await session.execute(
            text("SELECT set_config('app.current_user_id', :uid, true)"),
            {"uid": str(current_user.id)},
        )
        await session.execute(
            text("SELECT set_config('app.current_org_id', '', true)")
        )

        stmt = select(Membership).where(
            Membership.organization_id == org_uuid,
            Membership.user_id == current_user.id,
            Membership.status == "active",
        )
        res = await session.execute(stmt)
        membership = res.scalar_one_or_none()

        if not membership:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Forbidden: User is not an active member of this organization",
            )

        # 2. Bind transaction-local tenant context
        await set_tenant_context(session, organization_id=org_uuid, user_id=current_user.id)

        try:
            yield session
        finally:
            await session.close()
