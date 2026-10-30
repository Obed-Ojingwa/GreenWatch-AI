"""Serves locally stored files via short-lived HMAC-signed URLs (development / single-node only).

With the Supabase backend, signed URLs point straight at Supabase and this route is unused.
"""
import mimetypes

from fastapi import APIRouter, Response

from app.core.errors import NotFound, Unauthorized
from app.services.storage import LocalStorage, get_storage

router = APIRouter(prefix="/api", tags=["files"])


@router.get("/files/{path:path}")
def get_file(path: str, exp: int, sig: str):
    st = get_storage()
    if not isinstance(st, LocalStorage):
        raise NotFound("Not found.")
    if not st.verify(path, exp, sig):
        raise Unauthorized("Link expired or invalid.")
    data = st.download_bytes(path)
    ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
    return Response(content=data, media_type=ctype, headers={"Cache-Control": "private, max-age=300"})
