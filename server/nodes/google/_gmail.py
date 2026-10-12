"""Gmail-specific shared helpers (Wave 11.D.4).

Used by both gmail (send/search/read) and gmail_receive (polling).
Lives here because it's Gmail-internal — not reusable across other
Google Workspace plugins.
"""

from __future__ import annotations

import base64
import re
from email.message import Message
from html.parser import HTMLParser
from typing import Any, Dict, List, Set

from ._base import run_sync


async def poll_gmail_ids(service, query: str, max_results: int = 20) -> Set[str]:
    """Fetch message IDs matching a Gmail query."""
    result = await run_sync(
        lambda: service.users()
        .messages()
        .list(
            userId="me",
            q=query,
            maxResults=max_results,
        )
        .execute()
    )
    return {m.get("id") for m in result.get("messages", []) if m.get("id")}


async def fetch_email_details(service, message_id: str) -> Dict[str, Any]:
    """Fetch full email message + format it for output."""
    result = await run_sync(
        lambda: service.users()
        .messages()
        .get(
            userId="me",
            id=message_id,
            format="full",
            metadataHeaders=["From", "To", "Subject", "Date", "Cc", "Bcc"],
        )
        .execute()
    )
    return format_message(result, include_body=True)


async def mark_email_as_read(service, message_id: str) -> None:
    """Remove the UNREAD label from a message."""
    await run_sync(
        lambda: service.users()
        .messages()
        .modify(
            userId="me",
            id=message_id,
            body={"removeLabelIds": ["UNREAD"]},
        )
        .execute()
    )


def format_message(message: Dict[str, Any], include_body: bool = False) -> Dict[str, Any]:
    """Convert a raw Gmail message payload into a flat, LLM-friendly dict."""
    headers: Dict[str, str] = {}
    payload = message.get("payload", {})
    for header in payload.get("headers", []):
        name = header.get("name", "").lower()
        if name in ("from", "to", "subject", "date", "cc", "bcc"):
            headers[name] = header.get("value", "")

    formatted: Dict[str, Any] = {
        "message_id": message.get("id"),
        "thread_id": message.get("threadId"),
        "from": headers.get("from", ""),
        "to": headers.get("to", ""),
        "cc": headers.get("cc", ""),
        "subject": headers.get("subject", ""),
        "date": headers.get("date", ""),
        "snippet": message.get("snippet", ""),
        "labels": message.get("labelIds", []),
        "size_estimate": message.get("sizeEstimate", 0),
    }

    if include_body:
        formatted["body"], formatted["body_mime_type"] = _extract_body_with_type(payload)
        formatted["body_text"] = readable_message_body(payload)

    attachments = _extract_attachments(payload)
    if attachments:
        formatted["attachments"] = attachments

    return formatted


def _extract_body(payload: Dict[str, Any]) -> str:
    """Extract text body from a (possibly multipart) Gmail payload."""
    return _extract_body_with_type(payload)[0]


def _extract_body_with_type(payload: Dict[str, Any]) -> tuple[str, str]:
    """Keep the established raw-body selection while exposing its MIME type."""
    if payload.get("body", {}).get("data"):
        return _decode_body(payload["body"]["data"]), str(payload.get("mimeType") or "text/plain")

    body = ""
    for part in payload.get("parts", []):
        mime = part.get("mimeType", "")
        data = part.get("body", {}).get("data")
        if mime == "text/plain" and data:
            return _decode_body(data), mime
        if mime == "text/html" and data and not body:
            body = _decode_body(data)
        elif mime.startswith("multipart/"):
            nested, nested_type = _extract_body_with_type(part)
            if nested:
                return nested, nested_type
    return body, "text/html" if body else str(payload.get("mimeType") or "text/plain")


def _decode_body(data: str) -> str:
    # Gmail uses unpadded base64url; accepting padding also supports old fixtures.
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", errors="ignore")


class _ReadableHTML(HTMLParser):
    """A local, tolerant HTML projection; never resolves remote resources."""

    _blocks = frozenset({"p", "div", "br", "li", "tr", "table", "section", "article", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "hr"})

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden: list[str] = []
        self.links: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden.append(tag)
        if self.hidden:
            return
        if tag in self._blocks:
            self.parts.append("\n")
        if tag == "a":
            self.links.append(str(dict(attrs).get("href") or ""))
        if tag == "img":
            alt = str(dict(attrs).get("alt") or "")
            if alt:
                self.parts.append(alt)

    def handle_endtag(self, tag):
        if self.hidden:
            if tag in self.hidden:
                self.hidden = self.hidden[:self.hidden.index(tag)]
            return
        if tag == "a" and self.links:
            href = self.links.pop()
            if href:
                self.parts.append(f" ({href})")
        if tag in self._blocks:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)

    def text(self):
        # Email layout produces many redundant spaces/newlines; content and
        # URLs are retained. Normalization never touches the original body.
        trailing_links = "".join(f" ({href})" for href in reversed(self.links) if href)
        return re.sub(r"\n{3,}", "\n\n", re.sub(r"[^\S\n]+", " ", "".join(self.parts) + trailing_links)).strip()


def html_body_text(body: str) -> str:
    parser = _ReadableHTML()
    parser.feed(body)
    parser.close()
    return parser.text()


def readable_message_body(payload: Dict[str, Any]) -> str:
    """Prefer any non-attachment plain alternative across the entire MIME tree."""
    plain: list[str] = []
    html: list[str] = []

    def visit(part):
        headers = {str(h.get("name", "")).lower(): str(h.get("value", "")) for h in part.get("headers", [])}
        if part.get("filename") or headers.get("content-disposition", "").lower().startswith("attachment"):
            return
        data = (part.get("body") or {}).get("data")
        mime = str(part.get("mimeType") or "").lower()
        if data and mime in {"text/plain", "text/html"}:
            content_type = Message()
            content_type["content-type"] = headers.get("content-type", mime)
            charset = content_type.get_content_charset() or "utf-8"
            decoded = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))
            try:
                text = decoded.decode(charset, errors="replace")
            except LookupError:
                text = decoded.decode("utf-8", errors="replace")
            (plain if mime == "text/plain" else html).append(text)
        for child in part.get("parts", []):
            visit(child)

    visit(payload)
    if plain:
        return plain[0]
    if html:
        return html_body_text(html[0])
    return ""


def _extract_attachments(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Flatten attachment metadata from a multipart payload."""
    attachments: List[Dict[str, Any]] = []
    for part in payload.get("parts", []):
        filename = part.get("filename", "")
        if filename:
            attachments.append(
                {
                    "filename": filename,
                    "mime_type": part.get("mimeType", ""),
                    "size": part.get("body", {}).get("size", 0),
                    "attachment_id": part.get("body", {}).get("attachmentId", ""),
                }
            )
        if part.get("parts"):
            attachments.extend(_extract_attachments(part))
    return attachments
