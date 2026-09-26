import logging
import uuid

from fastapi import HTTPException

from db import database
from models import complaint, RoleType, State, transaction, user
from services.s3 import S3Service
from services.ses import SESService
from services.wise import WiseService
from utils.helpers import decode_photo

s3 = S3Service()
ses = SESService()
wise = WiseService()

logger = logging.getLogger(__name__)


class ComplaintManager:
    @staticmethod
    async def get_complaints(user):
        q = complaint.select()
        if user["role"] == RoleType.complainer:
            q = q.where(complaint.c.complainer_id == user["id"])
        elif user["role"] == RoleType.approver:
            q = q.where(complaint.c.status == State.pending)
        return [ComplaintManager._present(c) for c in await database.fetch_all(q)]

    @staticmethod
    async def create_complaint(complaint_data, user):
        complaint_data["complainer_id"] = user["id"]
        encoded_photo = complaint_data.pop("encoded_photo")
        extension = complaint_data.pop("extension")
        photo, content_type = decode_photo(encoded_photo, extension)
        name = f"{uuid.uuid4()}.{extension}"
        complaint_data["photo_url"] = await s3.upload_photo(photo, name, content_type)

        # External calls happen outside the DB transaction; if a later step
        # fails, the earlier ones are undone so nothing is left orphaned.
        try:
            transaction_data = await ComplaintManager.issue_transaction(
                complaint_data["amount"],
                f"{user['first_name']} {user['last_name']}",
                user["iban"],
            )
        except Exception:
            await ComplaintManager._undo(s3.delete_photo, name)
            raise
        try:
            async with database.transaction():
                id_ = await database.execute(
                    complaint.insert().values(**complaint_data)
                )
                await database.execute(
                    transaction.insert().values(**transaction_data, complaint_id=id_)
                )
        except Exception:
            await ComplaintManager._undo(
                wise.cancel_transfer, transaction_data["transfer_id"]
            )
            await ComplaintManager._undo(s3.delete_photo, name)
            raise
        return ComplaintManager._present(
            await database.fetch_one(complaint.select().where(complaint.c.id == id_))
        )

    @staticmethod
    async def delete(complaint_id):
        async with database.transaction():
            complaint_do = await ComplaintManager._get_for_update(complaint_id)
            if complaint_do["status"] == State.pending:
                # Don't leave an unfunded transfer behind at Wise
                transaction_do = await ComplaintManager._get_transaction(complaint_id)
                if transaction_do:
                    await wise.cancel_transfer(transaction_do["transfer_id"])
            # The transaction row is kept as a payment record; its
            # complaint_id is set to NULL by the foreign key.
            await database.execute(
                complaint.delete().where(complaint.c.id == complaint_id)
            )

    @staticmethod
    async def approve(id_):
        # The row lock is held while calling Wise so the same complaint
        # cannot be approved or rejected twice concurrently. If funding
        # fails, the status update is rolled back and stays pending.
        async with database.transaction():
            complaint_do = await ComplaintManager._get_pending_for_update(id_)
            transaction_do = await ComplaintManager._get_transaction(id_)
            if not transaction_do:
                raise HTTPException(409, "Complaint has no payment transaction")
            await wise.fund_transfer(transaction_do["transfer_id"])
            await database.execute(
                complaint.update()
                .where(complaint.c.id == id_)
                .values(status=State.approved)
            )
        # Sent after commit: the payment has gone out, so an email failure
        # must not undo the approval.
        complainer = await database.fetch_one(
            user.select().where(user.c.id == complaint_do["complainer_id"])
        )
        try:
            await ses.send_mail(
                "Your complaint is approved",
                [complainer["email"]],
                "Congrats! Your complaint is approved. Please check your bank account after 2 business days to verify the claimed amount is there.\nKind regards!",
            )
        except Exception:
            logger.exception("Failed to send approval email for complaint %s", id_)

    @staticmethod
    async def reject(id_):
        async with database.transaction():
            await ComplaintManager._get_pending_for_update(id_)
            transaction_do = await ComplaintManager._get_transaction(id_)
            if transaction_do:
                await wise.cancel_transfer(transaction_do["transfer_id"])
            await database.execute(
                complaint.update()
                .where(complaint.c.id == id_)
                .values(status=State.rejected)
            )

    @staticmethod
    async def issue_transaction(amount, full_name, iban):
        quote_id = await wise.create_quote(amount)
        recipient_id = await wise.create_recipient_account(full_name, iban)
        transfer_id = await wise.create_transfer(recipient_id, quote_id)
        return {
            "quote_id": quote_id,
            "transfer_id": transfer_id,
            "target_account_id": str(recipient_id),
            "amount": amount,
        }

    @staticmethod
    def _present(complaint_do):
        # Photos are private in S3; hand out a short-lived link instead
        data = {key: complaint_do[key] for key in complaint_do.keys()}
        data["photo_url"] = s3.presigned_url(data["photo_url"])
        return data

    @staticmethod
    async def _undo(action, *args):
        try:
            await action(*args)
        except Exception:
            logger.exception("Cleanup %s%s failed", action.__name__, args)

    @staticmethod
    async def _get_for_update(id_):
        complaint_do = await database.fetch_one(
            complaint.select().where(complaint.c.id == id_).with_for_update()
        )
        if not complaint_do:
            raise HTTPException(404, "Complaint not found")
        return complaint_do

    @staticmethod
    async def _get_pending_for_update(id_):
        complaint_do = await ComplaintManager._get_for_update(id_)
        if complaint_do["status"] != State.pending:
            raise HTTPException(
                409, f"Complaint is already {complaint_do['status'].value.lower()}"
            )
        return complaint_do

    @staticmethod
    async def _get_transaction(complaint_id):
        return await database.fetch_one(
            transaction.select().where(transaction.c.complaint_id == complaint_id)
        )
