# Complaint System

A FastAPI backend where customers file complaints (with a photo and the
amount they want refunded), approvers approve or reject them, and approved
refunds are paid out through [Wise](https://wise.com).

- **Complainers** register, file complaints and see their own complaints.
- **Approvers** see pending complaints and approve or reject them.
- **Admins** see everything, delete complaints and manage user roles.

When a complaint is filed, its photo is stored privately in S3 and a Wise
transfer for the refund is prepared. Approving funds that transfer and emails
the complainer through Amazon SES; rejecting cancels it.

## Requirements

- Python 3.11 or newer
- PostgreSQL
- An AWS account (S3 bucket + SES) and a Wise API token for payments

## Setup

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt    # requirements-dev.txt for tests/black

cp .env.example .env               # then fill in the values
alembic upgrade head               # create/upgrade the database tables
```

All settings are described in [`.env.example`](.env.example). Generate
`SECRET_KEY` with something like
`python -c "import secrets; print(secrets.token_urlsafe(48))"`.

## Running

```bash
uvicorn main:app --reload
```

Interactive API docs are at http://localhost:8000/docs.

## Creating an admin

Run from the project root:

```bash
python -m commands.create_super_user -f First -l Last -e admin@example.com \
    -p "+49 123 456" -i DE89370400440532013000 -pa "a-strong-password"
```

Admins can then promote other users with `PUT /users/{id}/make-approver` or
`PUT /users/{id}/make-admin`.

## API

| Method | Path | Who | |
| --- | --- | --- | --- |
| POST | `/register/` | anyone | Create a complainer account, returns a token |
| POST | `/login/` | anyone | Returns a token and the user's role |
| GET | `/complaints/` | any user | Own complaints / pending ones / all, by role |
| POST | `/complaints/` | complainer | File a complaint |
| PUT | `/complaints/{id}/approve` | approver | Pay the refund and email the complainer |
| PUT | `/complaints/{id}/reject` | approver | Cancel the refund |
| DELETE | `/complaints/{id}/` | admin | Delete (cancels the refund if still pending) |
| GET | `/users/?email=` | admin | List users, optionally by email |
| PUT | `/users/{id}/make-admin`, `/users/{id}/make-approver` | admin | Change a role |

Send the token as `Authorization: Bearer <token>`; tokens last 2 hours.

A complaint needs `title`, `description`, `amount` (more than 0, at most 2
decimals), `encoded_photo` (base64, up to 5 MB) and `extension` (`jpg`,
`jpeg`, `png` or `webp`, matching the photo). Only pending complaints can be
approved or rejected; anything else returns `409`.

`photo_url` in responses is a presigned S3 link that expires after one hour,
so fetch complaints again rather than storing it. (Photos uploaded before this
change were public and stay that way.)

## Tests

The tests need a PostgreSQL database they are allowed to wipe. They never use
the database from `.env`; point them at a separate one:

```bash
createdb complaints_test
export TEST_DATABASE_URL=postgresql://user:password@localhost:5432/complaints_test
pip install -r requirements-dev.txt
pytest
```

S3, SES and Wise are replaced with fakes, so no credentials or network access
are needed. The same checks (`black --check .` and `pytest` on Python
3.11–3.13) run on GitHub Actions for every push and pull request.

## Notes on Wise

- Use `https://api.wise-sandbox.com` while testing; the old
  `api.sandbox.transferwise.tech` host has been retired.
- Wise's documentation says transfers can't be funded through the API with a
  personal token for EU/UK profiles (PSD2/SCA). Check this against your
  account before relying on automatic payouts in production.
