"""The public side of "Share link": one page, reached with nothing but a
token, showing the transcript it points to.

Not behind the module gate every other conversations route sits behind --
`require_module` demands a signed-in user (`Depends(get_current_user)`), and
whoever opens a share link sent to them has no session on this platform at
all. The token itself is the only credential, verified by
`conversation_share_service.resolve` before anything is read.
"""

from __future__ import annotations

import html
import logging

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from backend.services import conversation_share_service, transcript_service


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/share", tags=["Conversation share links"])

_NOT_FOUND_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<title>Link not available</title></head><body style="font-family:sans-serif;
max-width:640px;margin:64px auto;padding:0 16px;color:#333">
<h1>This link is not available</h1>
<p>It may have expired, been revoked, or never existed. Ask whoever sent it to
you for a new one.</p></body></html>"""


@router.get("/conversation/{token}", response_class=HTMLResponse)
def view_shared_conversation(token: str) -> HTMLResponse:
    resolved = conversation_share_service.resolve(token)
    if not resolved:
        return HTMLResponse(content=_NOT_FOUND_PAGE, status_code=404)

    text = transcript_service.build_transcript_text(
        company_id=resolved["company_id"],
        channel=resolved["channel"],
        external_user_id=resolved["external_user_id"],
        scope=resolved["scope"],
    )
    if text is None:
        return HTMLResponse(content=_NOT_FOUND_PAGE, status_code=404)

    # A plain <pre> block, deliberately: this page is opened by someone with
    # no session and no reason to see this platform's own design, the same way
    # an emailed PDF does not carry a company's internal branding. Escaped
    # because the transcript holds a customer's own typed text.
    body = f"""<!doctype html><html><head><meta charset="utf-8">
<title>Conversation transcript</title></head>
<body style="font-family:monospace;max-width:800px;margin:32px auto;
padding:0 16px;white-space:pre-wrap;word-wrap:break-word;color:#222">
<pre>{html.escape(text)}</pre>
</body></html>"""
    return HTMLResponse(content=body)
