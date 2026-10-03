import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.body_limit import RequestBodyLimitMiddleware

from db import database
from resources.routes import api_router

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)

origins = ["http://localhost", "http://localhost:4200"]

# Room for a 5 MB photo, which is about 6.7 MB once base64-encoded
MAX_REQUEST_BYTES = 8 * 1024 * 1024


@asynccontextmanager
async def lifespan(app: FastAPI):
    await database.connect()
    yield
    await database.disconnect()


# strict_content_type=False: a JSON body sent without a Content-Type header is
# still parsed, as before the FastAPI upgrade. FastAPI's strict default guards
# against cross-site requests made with the user's cookies; this API uses
# bearer tokens, which another site can't make the browser send.
app = FastAPI(lifespan=lifespan, strict_content_type=False)
app.include_router(api_router)
# Added before CORS so CORS stays outermost and 413 responses get CORS headers
app.add_middleware(RequestBodyLimitMiddleware, max_body_size=MAX_REQUEST_BYTES)
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request, exc):
    # FastAPI's default response, minus the submitted values ("input"),
    # which would echo passwords and whole photos back to the client
    errors = [{k: v for k, v in err.items() if k != "input"} for err in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": jsonable_encoder(errors)})
