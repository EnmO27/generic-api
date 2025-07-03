from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

class ForwardedProtoMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Si la cabecera X-Forwarded-Proto indica https, ajusta el esquema
        forwarded_proto = request.headers.get("x-forwarded-proto")
        if forwarded_proto:
            request.scope["scheme"] = forwarded_proto
        response = await call_next(request)
        return response