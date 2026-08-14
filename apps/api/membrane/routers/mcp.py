"""MCP tool-poisoning scanner endpoints.

Scan a server's advertised tool schemas before an agent is permitted to
connect. One poisoned tool description propagates to every agent that installs
it, so this is a supply-chain control: scanning the registry protects the whole
ecosystem at once rather than one deployment at a time.
"""

from __future__ import annotations

import json

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from .. import audit
from ..db import get_db
from ..events import bus
from ..layers import mcp_scan
from ..models import Layer, Verdict
from ..schemas import McpScanRequest

router = APIRouter(prefix="/v1/mcp", tags=["mcp"])


async def _persist(db: AsyncSession, report: mcp_scan.ScanReport,
                   session_id: str | None) -> None:
    audit_session = session_id or f"mcpscan:{report.server}"
    await audit.touch_session(db, audit_session, subject="mcp-scanner")
    await audit.record(
        db, session_id=audit_session, layer=Layer.MCP_SCAN,
        verdict=report.verdict, reason=report.reason,
        source_ref=f"mcp:{report.server}",
        signals={"tools_scanned": len(report.tools),
                 "poisoned": report.poisoned_tools},
    )
    for tool_report in report.tools:
        if tool_report.verdict is Verdict.PASS:
            continue
        for finding in tool_report.findings:
            await audit.record(
                db, session_id=audit_session, layer=Layer.MCP_SCAN,
                verdict=tool_report.verdict,
                reason=f"{tool_report.tool}: {finding.reason}",
                span_hash_value=finding.span_hash,
                source_ref=f"mcp:{report.server}",
                signals=finding.to_dict(),
            )
            await audit.promote_to_corpus(
                db, span_hash_value=finding.span_hash, family="tool_poisoning",
                signals=finding.to_dict(),
            )

    await bus.publish("mcp.scanned", {
        "session_id": audit_session,
        "server": report.server,
        "verdict": report.verdict.value,
        "reason": report.reason,
        "poisoned_tools": report.poisoned_tools,
        "tools_scanned": len(report.tools),
    })


@router.post("/scan")
async def scan(body: McpScanRequest, db: AsyncSession = Depends(get_db)) -> dict:
    """Audit a list of tool definitions supplied directly."""
    if not body.tools:
        raise HTTPException(422, "provide at least one tool definition")
    report = mcp_scan.scan_server(body.server, body.tools)
    await _persist(db, report, body.session_id)
    return report.to_dict()


@router.post("/scan-url")
async def scan_url(payload: dict, request: Request,
                   db: AsyncSession = Depends(get_db)) -> dict:
    """Fetch a `tools/list` response from a URL and audit it.

    Accepts either a bare list, `{"tools": [...]}`, or a full JSON-RPC envelope
    with the tools under `result.tools`.
    """
    url = payload.get("url")
    if not isinstance(url, str) or "://" not in url:
        raise HTTPException(422, "provide a 'url'")

    client: httpx.AsyncClient | None = getattr(request.app.state, "http_client", None)
    owned = client is None
    http = client or httpx.AsyncClient()
    try:
        response = await http.get(url, timeout=10.0,
                                  headers={"accept": "application/json"})
        response.raise_for_status()
        document = response.json()
    except Exception as exc:
        raise HTTPException(502, f"could not fetch tool list: {exc}") from exc
    finally:
        if owned:
            await http.aclose()

    if isinstance(document, list):
        tools = document
    elif isinstance(document, dict):
        tools = (document.get("tools")
                 or (document.get("result") or {}).get("tools")
                 or [])
    else:
        tools = []
    if not tools:
        raise HTTPException(422, "no tool definitions found at that URL")

    report = mcp_scan.scan_server(payload.get("server") or url, tools)
    await _persist(db, report, payload.get("session_id"))
    return report.to_dict()


@router.post("/scan-file")
async def scan_file(payload: dict, db: AsyncSession = Depends(get_db)) -> dict:
    """Audit a pasted `tools/list` JSON document."""
    document = payload.get("document")
    if isinstance(document, str):
        try:
            document = json.loads(document)
        except ValueError as exc:
            raise HTTPException(422, f"document is not valid JSON: {exc}") from exc
    if isinstance(document, dict):
        tools = (document.get("tools")
                 or (document.get("result") or {}).get("tools") or [])
    elif isinstance(document, list):
        tools = document
    else:
        raise HTTPException(422, "provide a 'document'")
    if not tools:
        raise HTTPException(422, "no tool definitions found in that document")

    report = mcp_scan.scan_server(payload.get("server") or "pasted-document", tools)
    await _persist(db, report, payload.get("session_id"))
    return report.to_dict()
