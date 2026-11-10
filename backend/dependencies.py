"""
This file is the actual security boundary of the whole app.

get_current_user()  -> decodes the JWT sent by the client and loads the
                        matching User row. Rejects with 401 if the token
                        is missing/invalid/expired.

require_role(*roles) -> a dependency FACTORY. Call it with the roles that
                        are allowed to hit a given route, e.g.
                        require_role("librarian") or
                        require_role("student", "librarian").
                        It rejects with 403 if the logged-in user's role
                        isn't in the allowed list.

Every protected route in routers/ depends on one of these. This is what
stops a student from being able to call an admin endpoint directly
(e.g. via curl/Postman) even if they never see the admin page in the UI -
the frontend redirect alone would NOT be enough.
"""
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import ExpiredSignatureError, JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import decode_access_token
from database import get_db
from models import RoleEnum, User

# tokenUrl just tells the auto-generated docs (/docs) where to get a token.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = decode_access_token(token)
        user_id: str = payload.get("sub")
        if user_id is None:
            raise credentials_exception
    except ExpiredSignatureError:
        # Distinct message so the client can redirect to the login page
        # instead of showing "wrong username or password".
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired. Please log in again.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except JWTError:
        raise credentials_exception

    result = await db.execute(select(User).where(User.id == int(user_id)))
    user = result.scalar_one_or_none()
    if user is None:
        raise credentials_exception
    # Legacy faculty rows are no longer a supported account type — a token
    # issued before the role was dropped must not keep working.
    if user.role == RoleEnum.faculty:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account type is no longer supported",
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Your account has been deactivated. Contact the librarian.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def require_role(*allowed_roles: str):
    """
    Usage in a route:
        current_user: User = Depends(require_role("librarian"))
    or:
        current_user: User = Depends(require_role("student", "librarian"))
    """
    async def role_checker(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role.value not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to perform this action",
            )
        return current_user

    return role_checker

