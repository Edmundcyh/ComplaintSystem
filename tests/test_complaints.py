import base64
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest
import sqlalchemy as sa
from asyncpg.exceptions import DataError

from tests.conftest import PNG, complaint_body


def get_row(engine, sql, **params):
    with engine.begin() as conn:
        return conn.execute(sa.text(sql), params).mappings().all()


# --- create ---------------------------------------------------------------


def test_create_complaint(client, engine, fakes, make_user, create_complaint):
    user = make_user()

    complaint = create_complaint(user, amount="12.30")

    assert complaint["status"] == "Pending"
    assert complaint["amount"] == 12.3  # still a JSON number
    assert complaint["photo_url"].endswith("?signed")
    [(key, (data, content_type))] = fakes.s3.uploaded.items()
    assert data == PNG and content_type == "image/png" and key.endswith(".png")
    assert fakes.wise.quotes == [Decimal("12.30")]
    assert fakes.wise.recipients == [("Jane Doe", "DE89370400440532013000")]
    [tx] = get_row(engine, "SELECT * FROM transactions")
    assert tx["complaint_id"] == complaint["id"]
    assert tx["amount"] == Decimal("12.30")
    assert tx["transfer_id"] == 3_000_000_000


def test_only_complainers_can_create(client, make_user):
    for role in ("approver", "admin"):
        user = make_user(role)
        resp = client.post(
            "/complaints/", json=complaint_body(), headers=user["headers"]
        )
        assert resp.status_code == 403


def test_create_requires_login(client):
    resp = client.post("/complaints/", json=complaint_body())
    assert resp.status_code in (401, 403)


def test_invalid_amounts_are_rejected(client, make_user):
    user = make_user()
    for amount in (-1, 0, "10.555", 10**9):
        resp = client.post(
            "/complaints/", json=complaint_body(amount=amount), headers=user["headers"]
        )
        assert resp.status_code == 422, amount


def test_photo_validation(client, make_user):
    user = make_user()

    def post(**overrides):
        return client.post(
            "/complaints/", json=complaint_body(**overrides), headers=user["headers"]
        )

    assert post(extension="exe").status_code == 422
    assert post(extension="png/../../x").status_code == 422
    assert post(encoded_photo="not base64!").status_code == 400
    not_image = base64.b64encode(b"just some text").decode()
    assert post(encoded_photo=not_image).status_code == 400
    # PNG bytes sent as a jpg
    assert post(extension="jpg").status_code == 400
    # Bodies over the 8 MB request limit are refused before parsing
    assert post(encoded_photo="A" * (9 * 1024 * 1024)).status_code == 413
    # Extension is normalised
    assert post(extension=".PNG").status_code == 200


def test_text_limits_and_validation_errors(client, make_user):
    user = make_user()
    resp = client.post(
        "/complaints/",
        json=complaint_body(description="x" * 5001, extension="exe"),
        headers=user["headers"],
    )
    assert resp.status_code == 422
    assert {e["loc"][-1] for e in resp.json()["detail"]} == {"description", "extension"}
    # Submitted values are not echoed back
    assert all("input" not in e for e in resp.json()["detail"])


def test_wise_failure_removes_uploaded_photo(client, engine, fakes, make_user):
    user = make_user()
    fakes.wise.fail.add("create_transfer")

    resp = client.post("/complaints/", json=complaint_body(), headers=user["headers"])

    assert resp.status_code == 502
    assert fakes.s3.deleted == list(fakes.s3.uploaded)
    assert get_row(engine, "SELECT id FROM complaints") == []


def test_db_failure_cancels_transfer_and_removes_photo(
    client, engine, fakes, make_user
):
    user = make_user()
    fakes.wise.transfer_id_override = "not-a-number"  # makes the INSERT fail

    with pytest.raises(DataError):
        client.post("/complaints/", json=complaint_body(), headers=user["headers"])

    assert fakes.wise.cancelled == ["not-a-number"]
    assert fakes.s3.deleted == list(fakes.s3.uploaded)
    assert get_row(engine, "SELECT id FROM complaints") == []
    assert get_row(engine, "SELECT id FROM transactions") == []


# --- list -----------------------------------------------------------------


def test_list_is_scoped_by_role(client, make_user, create_complaint):
    alice, bob = make_user(), make_user()
    approver, admin = make_user("approver"), make_user("admin")
    a1 = create_complaint(alice)
    a2 = create_complaint(alice)
    b1 = create_complaint(bob)
    client.put(f"/complaints/{a2['id']}/reject", headers=approver["headers"])

    def ids(user):
        resp = client.get("/complaints/", headers=user["headers"])
        assert resp.status_code == 200
        return sorted(c["id"] for c in resp.json())

    assert ids(alice) == [a1["id"], a2["id"]]
    assert ids(bob) == [b1["id"]]
    assert ids(approver) == [a1["id"], b1["id"]]  # pending only
    assert ids(admin) == [a1["id"], a2["id"], b1["id"]]


def test_legacy_rows_are_still_listed(client, engine, make_user):
    # Rows created before input validation existed may break today's rules
    user, admin = make_user(), make_user("admin")
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO complaints (title, description, photo_url, amount,"
                " complainer_id) VALUES ('old', 'd', 'https://b/old.png', -5, :id)"
            ),
            {"id": user["id"]},
        )

    resp = client.get("/complaints/", headers=admin["headers"])

    assert resp.status_code == 200
    assert resp.json()[0]["amount"] == -5.0


# --- approve --------------------------------------------------------------


def test_approve_funds_and_emails_complainer(
    client, engine, fakes, make_user, create_complaint
):
    user, approver = make_user(), make_user("approver")
    complaint = create_complaint(user)
    [tx] = get_row(engine, "SELECT transfer_id FROM transactions")

    resp = client.put(
        f"/complaints/{complaint['id']}/approve", headers=approver["headers"]
    )

    assert resp.status_code == 204
    assert fakes.wise.funded == [tx["transfer_id"]]
    assert fakes.ses.sent == [("Your complaint is approved", [user["email"]])]
    [row] = get_row(engine, "SELECT status FROM complaints")
    assert row["status"] == "approved"


def test_approve_twice_is_rejected(client, fakes, make_user, create_complaint):
    user, approver = make_user(), make_user("approver")
    complaint = create_complaint(user)
    url = f"/complaints/{complaint['id']}/approve"

    assert client.put(url, headers=approver["headers"]).status_code == 204
    resp = client.put(url, headers=approver["headers"])

    assert resp.status_code == 409
    assert resp.json()["detail"] == "Complaint is already approved"
    assert len(fakes.wise.funded) == 1


def test_concurrent_approvals_fund_once(client, fakes, make_user, create_complaint):
    user, approver = make_user(), make_user("approver")
    complaint = create_complaint(user)
    url = f"/complaints/{complaint['id']}/approve"
    fakes.wise.fund_delay = 0.3  # keep the first approval in flight

    with ThreadPoolExecutor(2) as pool:
        codes = sorted(
            pool.map(
                lambda _: client.put(url, headers=approver["headers"]).status_code,
                range(2),
            )
        )

    assert codes == [204, 409]
    assert len(fakes.wise.funded) == 1


def test_failed_funding_keeps_complaint_pending(
    client, engine, fakes, make_user, create_complaint
):
    user, approver = make_user(), make_user("approver")
    complaint = create_complaint(user)
    fakes.wise.fail.add("fund_transfer")

    resp = client.put(
        f"/complaints/{complaint['id']}/approve", headers=approver["headers"]
    )

    assert resp.status_code == 502
    [row] = get_row(engine, "SELECT status FROM complaints")
    assert row["status"] == "pending"
    assert fakes.ses.sent == []


def test_email_failure_keeps_approval(
    client, engine, fakes, make_user, create_complaint, caplog
):
    user, approver = make_user(), make_user("approver")
    complaint = create_complaint(user)
    fakes.ses.fail = True

    resp = client.put(
        f"/complaints/{complaint['id']}/approve", headers=approver["headers"]
    )

    assert resp.status_code == 204
    [row] = get_row(engine, "SELECT status FROM complaints")
    assert row["status"] == "approved"
    assert "Failed to send approval email" in caplog.text


def test_approve_unknown_complaint(client, make_user):
    approver = make_user("approver")
    resp = client.put("/complaints/999/approve", headers=approver["headers"])
    assert resp.status_code == 404


def test_approve_without_transaction(client, engine, make_user):
    user, approver = make_user(), make_user("approver")
    with engine.begin() as conn:
        complaint_id = conn.execute(
            sa.text(
                "INSERT INTO complaints (title, description, photo_url, amount,"
                " complainer_id) VALUES ('old', 'd', 'https://b/old.png', 5, :id)"
                " RETURNING id"
            ),
            {"id": user["id"]},
        ).scalar_one()

    resp = client.put(
        f"/complaints/{complaint_id}/approve", headers=approver["headers"]
    )

    assert resp.status_code == 409


def test_only_approvers_can_approve(client, make_user, create_complaint):
    user, admin = make_user(), make_user("admin")
    complaint = create_complaint(user)
    for who in (user, admin):
        resp = client.put(
            f"/complaints/{complaint['id']}/approve", headers=who["headers"]
        )
        assert resp.status_code == 403


# --- reject ---------------------------------------------------------------


def test_reject_cancels_transfer(client, engine, fakes, make_user, create_complaint):
    user, approver = make_user(), make_user("approver")
    complaint = create_complaint(user)
    [tx] = get_row(engine, "SELECT transfer_id FROM transactions")

    resp = client.put(
        f"/complaints/{complaint['id']}/reject", headers=approver["headers"]
    )

    assert resp.status_code == 204
    assert fakes.wise.cancelled == [tx["transfer_id"]]
    [row] = get_row(engine, "SELECT status FROM complaints")
    assert row["status"] == "rejected"


def test_cannot_change_a_decided_complaint(client, fakes, make_user, create_complaint):
    user, approver = make_user(), make_user("approver")
    approved = create_complaint(user)
    rejected = create_complaint(user)
    client.put(f"/complaints/{approved['id']}/approve", headers=approver["headers"])
    client.put(f"/complaints/{rejected['id']}/reject", headers=approver["headers"])

    for complaint in (approved, rejected):
        for action in ("approve", "reject"):
            resp = client.put(
                f"/complaints/{complaint['id']}/{action}", headers=approver["headers"]
            )
            assert resp.status_code == 409
    assert len(fakes.wise.funded) == 1
    assert len(fakes.wise.cancelled) == 1


def test_failed_cancel_keeps_complaint_pending(
    client, engine, fakes, make_user, create_complaint
):
    user, approver = make_user(), make_user("approver")
    complaint = create_complaint(user)
    fakes.wise.fail.add("cancel_transfer")

    resp = client.put(
        f"/complaints/{complaint['id']}/reject", headers=approver["headers"]
    )

    assert resp.status_code == 502
    [row] = get_row(engine, "SELECT status FROM complaints")
    assert row["status"] == "pending"


# --- delete ---------------------------------------------------------------


def test_delete_pending_cancels_and_keeps_payment_record(
    client, engine, fakes, make_user, create_complaint
):
    user, admin = make_user(), make_user("admin")
    complaint = create_complaint(user)
    [tx] = get_row(engine, "SELECT transfer_id FROM transactions")

    resp = client.delete(f"/complaints/{complaint['id']}/", headers=admin["headers"])

    assert resp.status_code == 204
    assert fakes.wise.cancelled == [tx["transfer_id"]]
    assert get_row(engine, "SELECT id FROM complaints") == []
    [kept] = get_row(engine, "SELECT transfer_id, complaint_id FROM transactions")
    assert kept["transfer_id"] == tx["transfer_id"]
    assert kept["complaint_id"] is None


def test_delete_removes_photo(client, engine, fakes, make_user, create_complaint):
    user, admin = make_user(), make_user("admin")
    complaint = create_complaint(user)
    [key] = fakes.s3.uploaded

    resp = client.delete(f"/complaints/{complaint['id']}/", headers=admin["headers"])

    assert resp.status_code == 204
    assert fakes.s3.deleted == [key]


def test_delete_keeps_complaint_when_photo_removal_fails(
    client, engine, fakes, make_user, create_complaint
):
    user, admin = make_user(), make_user("admin")
    complaint = create_complaint(user)
    fakes.s3.fail_delete = True

    resp = client.delete(f"/complaints/{complaint['id']}/", headers=admin["headers"])

    assert resp.status_code == 502
    assert [c["id"] for c in get_row(engine, "SELECT id FROM complaints")] == [
        complaint["id"]
    ]
    # A retry works once S3 is back (the transfer is already cancelled)
    fakes.s3.fail_delete = False
    resp = client.delete(f"/complaints/{complaint['id']}/", headers=admin["headers"])
    assert resp.status_code == 204
    assert get_row(engine, "SELECT id FROM complaints") == []


def test_delete_approved_does_not_cancel(client, fakes, make_user, create_complaint):
    user, approver, admin = make_user(), make_user("approver"), make_user("admin")
    complaint = create_complaint(user)
    client.put(f"/complaints/{complaint['id']}/approve", headers=approver["headers"])

    resp = client.delete(f"/complaints/{complaint['id']}/", headers=admin["headers"])

    assert resp.status_code == 204
    assert fakes.wise.cancelled == []


def test_delete_unknown_and_permissions(client, make_user, create_complaint):
    user, admin = make_user(), make_user("admin")
    complaint = create_complaint(user)

    assert (
        client.delete("/complaints/999/", headers=admin["headers"]).status_code == 404
    )
    resp = client.delete(f"/complaints/{complaint['id']}/", headers=user["headers"])
    assert resp.status_code == 403


def test_ids_outside_the_database_range(client, make_user):
    approver, admin = make_user("approver"), make_user("admin")
    too_big = 2**31
    assert (
        client.put(
            f"/complaints/{too_big}/approve", headers=approver["headers"]
        ).status_code
        == 422
    )
    assert (
        client.delete(f"/complaints/{too_big}/", headers=admin["headers"]).status_code
        == 422
    )
    assert (
        client.put(f"/users/{too_big}/make-admin", headers=admin["headers"]).status_code
        == 422
    )


# --- Wise state reconciliation ---------------------------------------------


def test_approve_retry_after_lost_funding_response(
    client, engine, fakes, make_user, create_complaint
):
    # Wise funded the transfer but the answer never arrived
    user, approver = make_user(), make_user("approver")
    complaint = create_complaint(user)
    url = f"/complaints/{complaint['id']}/approve"
    fakes.wise.fund_response_lost = True

    assert client.put(url, headers=approver["headers"]).status_code == 502
    [row] = get_row(engine, "SELECT status FROM complaints")
    assert row["status"] == "pending"

    # Retrying records the approval without paying a second time
    assert client.put(url, headers=approver["headers"]).status_code == 204
    [row] = get_row(engine, "SELECT status FROM complaints")
    assert row["status"] == "approved"
    assert len(fakes.wise.funded) == 1
    assert fakes.ses.sent == [("Your complaint is approved", [user["email"]])]


def test_paid_complaint_cannot_be_rejected_or_deleted(
    client, fakes, make_user, create_complaint
):
    user, approver, admin = make_user(), make_user("approver"), make_user("admin")
    complaint = create_complaint(user)
    fakes.wise.fund_response_lost = True
    client.put(f"/complaints/{complaint['id']}/approve", headers=approver["headers"])

    resp = client.put(
        f"/complaints/{complaint['id']}/reject", headers=approver["headers"]
    )
    assert resp.status_code == 409
    assert "approve the complaint instead" in resp.json()["detail"]
    resp = client.delete(f"/complaints/{complaint['id']}/", headers=admin["headers"])
    assert resp.status_code == 409
    assert fakes.wise.cancelled == []


def test_approve_replaces_expired_transfer(
    client, engine, fakes, make_user, create_complaint
):
    user, approver = make_user(), make_user("approver")
    complaint = create_complaint(user, amount="20.00")
    [old] = get_row(engine, "SELECT id, transfer_id FROM transactions")
    fakes.wise.statuses[old["transfer_id"]] = "cancelled"  # expired at Wise

    resp = client.put(
        f"/complaints/{complaint['id']}/approve", headers=approver["headers"]
    )

    assert resp.status_code == 204
    [new] = get_row(engine, "SELECT id, transfer_id, amount FROM transactions")
    assert new["id"] == old["id"]
    assert new["transfer_id"] != old["transfer_id"]
    assert new["amount"] == Decimal("20.00")
    assert fakes.wise.funded == [new["transfer_id"]]
    assert fakes.wise.quotes == [Decimal("20.00"), Decimal("20.00")]


def test_replacement_transfer_is_not_paid_twice(
    client, engine, fakes, make_user, create_complaint
):
    # The expired transfer is replaced, Wise pays the replacement, but the
    # answer is lost; the retry must find the replacement, not make another
    user, approver = make_user(), make_user("approver")
    complaint = create_complaint(user)
    [old] = get_row(engine, "SELECT transfer_id FROM transactions")
    fakes.wise.statuses[old["transfer_id"]] = "cancelled"
    url = f"/complaints/{complaint['id']}/approve"
    fakes.wise.fund_response_lost = True

    assert client.put(url, headers=approver["headers"]).status_code == 502
    fakes.wise.fund_response_lost = False
    assert client.put(url, headers=approver["headers"]).status_code == 204

    assert len(fakes.wise.funded) == 1
    [tx] = get_row(engine, "SELECT transfer_id FROM transactions")
    assert tx["transfer_id"] == fakes.wise.funded[0]


def test_approve_with_unexpected_transfer_state(
    client, engine, fakes, make_user, create_complaint
):
    user, approver = make_user(), make_user("approver")
    complaint = create_complaint(user)
    [tx] = get_row(engine, "SELECT transfer_id FROM transactions")
    fakes.wise.statuses[tx["transfer_id"]] = "bounced_back"

    resp = client.put(
        f"/complaints/{complaint['id']}/approve", headers=approver["headers"]
    )

    assert resp.status_code == 409
    assert "bounced_back" in resp.json()["detail"]
    assert fakes.wise.funded == []


def test_reject_and_delete_when_wise_already_cancelled(
    client, engine, fakes, make_user, create_complaint
):
    user, approver, admin = make_user(), make_user("approver"), make_user("admin")
    rejected = create_complaint(user)
    deleted = create_complaint(user)
    for tx in get_row(engine, "SELECT transfer_id FROM transactions"):
        fakes.wise.statuses[tx["transfer_id"]] = "cancelled"

    resp = client.put(
        f"/complaints/{rejected['id']}/reject", headers=approver["headers"]
    )
    assert resp.status_code == 204
    resp = client.delete(f"/complaints/{deleted['id']}/", headers=admin["headers"])
    assert resp.status_code == 204
    assert fakes.wise.cancelled == []


def test_missing_email_settings_do_not_block_approval(
    client, engine, fakes, make_user, create_complaint, monkeypatch, caplog
):
    import services.ses
    from decouple import UndefinedValueError

    from main import app
    from services.ses import SESService, get_ses_service

    def missing(name, *args, **kwargs):
        raise UndefinedValueError(f"{name} not found")

    monkeypatch.setattr(services.ses, "config", missing)
    app.dependency_overrides[get_ses_service] = SESService
    user, approver = make_user(), make_user("approver")
    complaint = create_complaint(user)

    resp = client.put(
        f"/complaints/{complaint['id']}/approve", headers=approver["headers"]
    )

    assert resp.status_code == 204
    [row] = get_row(engine, "SELECT status FROM complaints")
    assert row["status"] == "approved"
    assert "Failed to send approval email" in caplog.text
