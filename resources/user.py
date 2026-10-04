from typing import Optional, List

from fastapi import APIRouter, Depends
from starlette.requests import Request

from managers.auth import oauth2_scheme, is_admin
from managers.user import UserManager
from models import RoleType
from resources.params import Id
from schemas.response.user import UserOut

router = APIRouter(tags=["Users"])


@router.get(
    "/users/",
    dependencies=[Depends(oauth2_scheme), Depends(is_admin)],
    response_model=List[UserOut],
)
async def get_user(email: Optional[str] = None):
    if email:
        return await UserManager.get_user_by_email(email)
    return await UserManager.get_all_users()


@router.put(
    "/users/{user_id}/make-admin",
    dependencies=[Depends(oauth2_scheme), Depends(is_admin)],
    status_code=204,
)
async def make_admin(request: Request, user_id: Id):
    await UserManager.change_role(RoleType.admin, user_id, request.state.user)


@router.put(
    "/users/{user_id}/make-approver",
    dependencies=[Depends(oauth2_scheme), Depends(is_admin)],
    status_code=204,
)
async def make_approver(request: Request, user_id: Id):
    await UserManager.change_role(RoleType.approver, user_id, request.state.user)
