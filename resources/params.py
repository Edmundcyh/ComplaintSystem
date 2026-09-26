from typing import Annotated

from fastapi import Path

# ids are 32-bit INTEGER columns; bigger numbers would make PostgreSQL error
Id = Annotated[int, Path(gt=0, le=2_147_483_647)]
