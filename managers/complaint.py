import logging
import uuid

from fastapi import HTTPException

from db import database
from models import complaint, RoleType, State, transaction, user
from services.wise import CANCELLED, FUNDED, UNFUNDED
from utils.helpers import decode_photo

logger = logging.getLogger(__name__)


class ComplaintManager:
    @staticmethod
    async def get_complaints(user, s3):
        q = complaint.select()
        if user["role"] == RoleType.complainer:
            q = q.where(complaint.c.complainer_id == user["id"])
        elif user["role"] == RoleType.approver:
            q = q.where(complaint.c.status == State.pending)
        return [ComplaintManager._present(c, s3) for c in await database.fetch_all(q)]

    @staticmethod
    async def create_complaint(complaint_data, user, s3, wise):
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
                wise,
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
            await database.fetch_one(complaint.select().where(complaint.c.id == id_)),
            s3,
        )

    @staticmethod
    async def delete(complaint_id, wise):
        async with database.transaction():
            complaint_do = await ComplaintManager._get_for_update(complaint_id)
            if complaint_do["status"] == State.pending:
                # Don't leave an unfunded transfer behind at Wise
                await ComplaintManager._cancel_transfer(complaint_id, wise)
            # The transaction row is kept as a payment record; its
            # complaint_id is set to NULL by the foreign key.
            await database.execute(
                complaint.delete().where(complaint.c.id == complaint_id)
            )

    @staticmethod
    async def approve(id_, wise, ses):
        # The row lock is held while calling Wise so the same complaint
        # cannot be approved or rejected twice concurrently. If funding
        # fails, the status update is rolled back and stays pending.
        async with database.transaction():
            complaint_do = await ComplaintManager._get_pending_for_update(id_)
            transaction_do = await ComplaintManager._get_transaction(id_)
            if not transaction_do:
                raise HTTPException(409, "Complaint has no payment transaction")
            complainer = await database.fetch_one(
                user.select().where(user.c.id == complaint_do["complainer_id"])
            )
            transfer_id = transaction_do["transfer_id"]
            # Checked first: a retry after a lost Wise response must not pay
            # again, and an expired transfer has to be replaced
            status = await wise.get_transfer_status(transfer_id)
            if status == CANCELLED:
                # Wise cancels transfers that stay unfunded for about 2 weeks
                transfer_id = await ComplaintManager._replace_transfer(
                    transaction_do, complaint_do, complainer, wise
                )
                status = UNFUNDED
            if status == UNFUNDED:
                await wise.fund_transfer(transfer_id)
            elif status not in FUNDED:
                raise HTTPException(
                    409,
                    f"The refund transfer is '{status}' at Wise and needs manual attention",
                )
            await database.execute(
                complaint.update()
                .where(complaint.c.id == id_)
                .values(status=State.approved)
            )
        # Sent after commit: the payment has gone out, so an email failure
        # must not undo the approval.
        try:
            await ses.send_mail(
                "Your complaint is approved",
                [complainer["email"]],
                "Congrats! Your complaint is approved. Please check your bank account after 2 business days to verify the claimed amount is there.\nKind regards!",
            )
        except Exception:
            logger.exception("Failed to send approval email for complaint %s", id_)

    @staticmethod
    async def reject(id_, wise):
        async with database.transaction():
            await ComplaintManager._get_pending_for_update(id_)
            await ComplaintManager._cancel_transfer(id_, wise)
            await database.execute(
                complaint.update()
                .where(complaint.c.id == id_)
                .values(status=State.rejected)
            )

    @staticmethod
    async def issue_transaction(wise, amount, full_name, iban):
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
    async def _replace_transfer(transaction_do, complaint_do, complainer, wise):
        # If funding the new transfer fails, this update is rolled back and
        # the new, unfunded transfer simply expires at Wise
        new = await ComplaintManager.issue_transaction(
            wise,
            complaint_do["amount"],
            f"{complainer['first_name']} {complainer['last_name']}",
            complainer["iban"],
        )
        await database.execute(
            transaction.update()
            .where(transaction.c.id == transaction_do["id"])
            .values(
                quote_id=new["quote_id"],
                transfer_id=new["transfer_id"],
                target_account_id=new["target_account_id"],
            )
        )
        return new["transfer_id"]

    @staticmethod
    async def _cancel_transfer(complaint_id, wise):
        transaction_do = await ComplaintManager._get_transaction(complaint_id)
        if not transaction_do:
            return
        transfer_id = transaction_do["transfer_id"]
        # Checked first so a retry after a lost response, or a transfer Wise
        # already cancelled, doesn't fail
        status = await wise.get_transfer_status(transfer_id)
        if status == UNFUNDED:
            await wise.cancel_transfer(transfer_id)
        elif status in FUNDED:
            # e.g. an approval whose response was lost
            raise HTTPException(
                409, "The refund has already been paid; approve the complaint instead"
            )
        elif status != CANCELLED:
            raise HTTPException(
                409,
                f"The refund transfer is '{status}' at Wise and needs manual attention",
            )

    @staticmethod
    def _present(complaint_do, s3):
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
