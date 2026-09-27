import asyncclick as click
from pydantic import ValidationError

from db import database
from managers.user import UserManager
from models import RoleType
from schemas.request.user import UserRegisterIn


@click.command()
@click.option("-f", "--first_name", type=str, required=True)
@click.option("-l", "--last_name", type=str, required=True)
@click.option("-e", "--email", type=str, required=True)
@click.option("-p", "--phone", type=str, required=True)
@click.option("-i", "--iban", type=str, required=True)
@click.option("-pa", "--password", type=str, required=True)
# @click.option("-r", "--role", type=enum, required=True) # No need role as its something done automatically
async def create_user(first_name, last_name, email, phone, iban, password):
    try:
        user_data = UserRegisterIn(
            first_name=first_name,
            last_name=last_name,
            email=email,
            phone=phone,
            iban=iban,
            password=password,
        ).model_dump()
    except ValidationError as ex:
        # Don't echo the submitted values (the password among them)
        errors = [f"{err['loc'][0]}: {err['msg']}" for err in ex.errors()]
        raise click.UsageError("; ".join(errors))
    user_data["role"] = RoleType.admin
    await database.connect()
    await UserManager.register(user_data)
    await database.disconnect()


if __name__ == "__main__":
    create_user(_anyio_backend="asyncio")
