from typing import List

from fastapi import APIRouter, Depends
from starlette.requests import Request

from managers.auth import oauth2_scheme, is_complainer, is_admin, is_approver
from managers.complaint import ComplaintManager
from resources.params import Id
from schemas.request.complaint import ComplaintIn
from schemas.response.complaint import ComplaintOut
from services.s3 import S3Service, get_s3_service
from services.ses import SESService, get_ses_service
from services.wise import WiseService, get_wise_service

router = APIRouter(tags=["Complaints"])


@router.get(
    "/complaints/",
    dependencies=[Depends(oauth2_scheme)],
    response_model=List[ComplaintOut],
)
async def get_complaints(request: Request, s3: S3Service = Depends(get_s3_service)):
    user = request.state.user
    return await ComplaintManager.get_complaints(user, s3)


@router.post(
    "/complaints/",
    dependencies=[Depends(oauth2_scheme), Depends(is_complainer)],
    response_model=ComplaintOut,
)  # response model is what data to feedback to user
async def create_complaint(
    request: Request,
    complaint: ComplaintIn,  # Complaint In schema
    s3: S3Service = Depends(get_s3_service),
    wise: WiseService = Depends(get_wise_service),
):
    user = request.state.user
    return await ComplaintManager.create_complaint(
        complaint.model_dump(), user, s3, wise
    )


@router.delete(
    "/complaints/{complaint_id}/",
    dependencies=[Depends(oauth2_scheme), Depends(is_admin)],
    status_code=204,
)
async def delete_complaint(
    complaint_id: Id,
    wise: WiseService = Depends(get_wise_service),
    s3: S3Service = Depends(get_s3_service),
):
    await ComplaintManager.delete(complaint_id, wise, s3)


@router.put(
    "/complaints/{complaint_id}/approve",
    dependencies=[Depends(oauth2_scheme), Depends(is_approver)],
    status_code=204,
)
async def approve_complaint(
    complaint_id: Id,
    wise: WiseService = Depends(get_wise_service),
    ses: SESService = Depends(get_ses_service),
):
    await ComplaintManager.approve(complaint_id, wise, ses)


@router.put(
    "/complaints/{complaint_id}/reject",
    dependencies=[Depends(oauth2_scheme), Depends(is_approver)],
    status_code=204,
)
async def reject_complaint(
    complaint_id: Id, wise: WiseService = Depends(get_wise_service)
):
    await ComplaintManager.reject(complaint_id, wise)
