from starlette.middleware.body_limit import RequestBodyLimitMiddleware
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .errors import CONTENT_TOO_LARGE, ErrorResponse

CONTENT_TOO_LARGE_RESPONSE = JSONResponse(
    ErrorResponse(detail=CONTENT_TOO_LARGE).model_dump(),
    status_code=413,
)


class TypedRequestBodyLimitMiddleware:
    def __init__(self, app: ASGIApp, max_body_size: int) -> None:
        self._body_limit = RequestBodyLimitMiddleware(
            app,
            max_body_size=max_body_size,
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        replacing_response = False
        response_body_sent = False

        async def send_typed_413(message: Message) -> None:
            nonlocal replacing_response, response_body_sent

            if message["type"] == "http.response.start" and message["status"] == 413:
                replacing_response = True
                message["headers"] = CONTENT_TOO_LARGE_RESPONSE.raw_headers.copy()
                await send(message)
                return

            if replacing_response and message["type"] == "http.response.body":
                if not response_body_sent:
                    response_body_sent = True
                    await send(
                        {
                            "type": "http.response.body",
                            "body": CONTENT_TOO_LARGE_RESPONSE.body,
                            "more_body": False,
                        }
                    )
                return
            await send(message)

        await self._body_limit(scope, receive, send_typed_413)
