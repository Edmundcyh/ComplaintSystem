# Complaint System

A FastAPI backend where customers file complaints (with a photo and the
amount they want refunded), approvers approve or reject them, and approved
refunds are paid out through [Wise](https://wise.com).

- **Complainers** register, file complaints and see their own complaints.
- **Approvers** see pending complaints and approve or reject them (never
  their own).
- **Admins** see everything, delete complaints and manage other users' roles.

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

The AWS user needs `s3:PutObject`, `s3:GetObject` and `s3:DeleteObject` on the
bucket, and `ses:SendEmail` for the sender address.

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
| DELETE | `/complaints/{id}/` | admin | Delete the complaint and its photo (cancels the refund if still pending) |
| GET | `/users/?email=` | admin | List users, optionally by email |
| PUT | `/users/{id}/make-admin`, `/users/{id}/make-approver` | admin | Change another user's role |

Send the token as `Authorization: Bearer <token>`; tokens last 2 hours.
Email addresses are case-insensitive: they are stored in lower case and
`Jane@Example.com` logs in to the same account as `jane@example.com`.

Send JSON with `Content-Type: application/json` (a request without a
`Content-Type` header is also read as JSON; other content types get `422`).
Request bodies are limited to 8 MB. A complaint needs `title` (up to 120 characters), `description` (up
to 5000), `amount` (more than 0, at most 2 decimals), `encoded_photo` (base64,
up to 5 MB and 25 megapixels) and `extension` (`jpg`, `jpeg`, `png` or `webp`,
matching the photo). Photos are fully decoded, so cut-off or corrupt files are
rejected; the formats themselves can't reveal every kind of damage (e.g. WebP
image data has no checksum). Only pending complaints can be approved or
rejected; anything else returns `409`.

`photo_url` in responses is a presigned S3 link that expires after one hour,
so fetch complaints again rather than storing it.

## Upgrading an existing installation

- Add `SES_SENDER_EMAIL` to `.env` (the sender used to be hard-coded). Without
  it approvals still work, but no email is sent and an error is logged.
- Give the AWS user the permissions listed under [Setup](#setup).
- Turn on S3 Block Public Access for the bucket. Photos used to be uploaded as
  public, including those of complaints deleted before this version (which
  are no longer referenced anywhere); the app now only uses signed links, so
  nothing needs public access. If the bucket is versioned, add a lifecycle
  rule that expires noncurrent versions, or deleted photos are kept.
- Run `alembic upgrade head`. It converts amounts to exact decimals (rounded
  to 2 places) and stops with a list of rows if any amount is too large
  (100,000,000 or more) or not a number; correct those and run it again. It
  also lowercases all email addresses and stops with a list of accounts if two
  of them differ only by case; remove or change all but one of each first.
- Everyone has to log in again: tokens issued by the old version are no longer
  accepted.
- New passwords are limited to 72 bytes (bcrypt ignores the rest); existing
  longer passwords keep working.

## Tests

The tests need a PostgreSQL database they are allowed to wipe. They never use
the database from `.env`, and refuse to run unless the database name contains
`test`:

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
- When a complaint is filed, creating its transfer is retried a few times if
  Wise times out or is briefly unavailable. Each retry reuses the same
  `customerTransactionId`, so Wise returns the transfer it may already have
  created instead of making a new one. If creating a transfer fails, that id
  is logged so the transfer can be found in Wise. Calls made while a complaint
  is being approved, rejected or deleted are not retried, so a Wise outage
  doesn't hold database locks for long; each Wise request is capped at 10
  seconds.
- Before paying or cancelling, the app asks Wise for the transfer's status.
  This makes retries safe (a payment whose response was lost isn't made
  twice) and replaces transfers that Wise cancelled because they stayed
  unfunded for about two weeks. Transfers in any other unexpected state are
  reported with `409` and need to be handled in Wise.
- Wise's documentation says transfers can't be funded through the API with a
  personal token for EU/UK profiles (PSD2/SCA). Check this against your
  account before relying on automatic payouts in production.
