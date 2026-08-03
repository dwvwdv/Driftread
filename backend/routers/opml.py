import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from defusedxml.common import DefusedXmlException
from defusedxml.ElementTree import fromstring as safe_fromstring
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import Response
from supabase import Client

from auth import AuthUser, get_current_user
from database import get_client
from models import OpmlImportResult
from rss_parser import fetch_and_parse
from services.feed_discovery import DiscoveryError, validate_fetch_url

router = APIRouter(prefix="/me", tags=["me"])

MAX_OPML_BYTES = 5 * 1024 * 1024
MAX_OPML_OUTLINES = 200


def _collect_outlines(node: ET.Element) -> list[ET.Element]:
    """Recursively collect outline elements that look like feeds."""
    out: list[ET.Element] = []
    for child in node.findall("outline"):
        if child.get("xmlUrl"):
            out.append(child)
        out.extend(_collect_outlines(child))
    return out


@router.post("/import/opml", response_model=OpmlImportResult)
async def import_opml(
    file: UploadFile = File(...),
    user: AuthUser = Depends(get_current_user),
    db: Client = Depends(get_client),
) -> OpmlImportResult:
    raw = await file.read()
    if len(raw) > MAX_OPML_BYTES:
        raise HTTPException(status_code=413, detail="OPML file too large")
    try:
        # defusedxml rejects DTD entity declarations and external references,
        # closing the billion-laughs / quadratic-blowup and XXE holes that
        # plain xml.etree.ElementTree.fromstring leaves open for this
        # authenticated-but-untrusted upload.
        root = safe_fromstring(raw)
    except (ET.ParseError, DefusedXmlException) as e:
        raise HTTPException(status_code=400, detail=f"Invalid OPML: {e}")

    body = root.find("body")
    if body is None:
        raise HTTPException(status_code=400, detail="OPML missing <body>")

    all_outlines = _collect_outlines(body)
    outlines = all_outlines[:MAX_OPML_OUTLINES]

    imported = 0
    subscribed = 0
    failed: list[str] = []
    skipped = len(all_outlines) - len(outlines)
    if skipped:
        failed.append(
            f"{skipped} outline(s) skipped: import is limited to {MAX_OPML_OUTLINES} feeds per file"
        )

    for outline in outlines:
        raw_url = outline.get("xmlUrl")
        if not raw_url:
            continue
        try:
            feed_url = await validate_fetch_url(raw_url)
            parsed = await fetch_and_parse(feed_url)
        except DiscoveryError as e:
            failed.append(f"{raw_url}: {e}")
            continue
        except Exception:
            failed.append(f"{raw_url}: failed to fetch")
            continue

        title = outline.get("title") or outline.get("text") or parsed.title
        feed_data = {
            "title": title,
            "url": feed_url,
            "description": parsed.description,
            "website_url": parsed.website_url or outline.get("htmlUrl"),
            "language": parsed.language,
            # Articles aren't upserted here — an import of up to
            # MAX_OPML_OUTLINES feeds would take far too long inside one
            # request. Mark each feed due so the scheduler fetches it next pass.
            "next_fetch_at": datetime.now(timezone.utc).isoformat(),
        }
        result = db.table("feeds").upsert(feed_data, on_conflict="url").execute()
        if not result.data:
            failed.append(f"{feed_url}: upsert returned no data")
            continue
        feed_id = result.data[0]["id"]
        imported += 1

        sub = db.table("user_feeds").upsert(
            {"user_id": user.id, "feed_id": feed_id},
            on_conflict="user_id,feed_id",
        ).execute()
        if sub.data:
            subscribed += 1

    return OpmlImportResult(imported=imported, subscribed=subscribed, failed=failed)


@router.get("/export/opml")
async def export_opml(
    user: AuthUser = Depends(get_current_user),
    db: Client = Depends(get_client),
) -> Response:
    rows = (
        db.table("user_feeds")
        .select("feeds(title,url,website_url)")
        .eq("user_id", user.id)
        .execute()
    )

    opml = ET.Element("opml", version="2.0")
    head = ET.SubElement(opml, "head")
    ET.SubElement(head, "title").text = "Driftread Subscriptions"
    ET.SubElement(head, "dateCreated").text = datetime.now(timezone.utc).isoformat()
    body = ET.SubElement(opml, "body")

    for row in rows.data:
        feed = row.get("feeds") or {}
        if not feed.get("url"):
            continue
        ET.SubElement(
            body,
            "outline",
            type="rss",
            text=feed.get("title") or "",
            title=feed.get("title") or "",
            xmlUrl=feed["url"],
            htmlUrl=feed.get("website_url") or "",
        )

    xml_bytes = ET.tostring(opml, encoding="utf-8", xml_declaration=True)
    return Response(
        content=xml_bytes,
        media_type="text/x-opml",
        headers={"Content-Disposition": 'attachment; filename="driftread.opml"'},
    )
