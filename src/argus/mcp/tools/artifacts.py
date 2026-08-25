"""Artifact tools: list a run's files, fetch one safely and bounded."""

from __future__ import annotations

import io
from typing import TYPE_CHECKING, Annotated

from mcp.server.mcpserver import Image
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import Field

from argus.mcp.context import ServerContext
from argus.mcp.errors import guarded
from argus.mcp.pagination import paginate
from argus.mcp.schemas import ArtifactContentView, ArtifactItem, ArtifactList

READ_ONLY = ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False)

_TEXT_MIME_PREFIXES = ("text/",)
_TEXT_MIMES = {"application/json", "application/xml", "text/html", "text/xml"}
_IMAGE_MIMES = {"image/png", "image/jpeg", "image/gif", "image/webp"}

if TYPE_CHECKING:
    from mcp.server import MCPServer


def register_artifact_tools(server: MCPServer, ctx: ServerContext) -> None:
    @server.tool(
        name="argus_list_artifacts",
        annotations=READ_ONLY,
        description=(
            "List the files a run produced (screenshots actual/expected/diff, device logs, "
            "instrumentation state, metadata, reports) with id, kind, MIME type, size and "
            "owning test. Read-only; returns metadata only. Filter by test_id or kind; "
            "paginated. Then fetch one with argus_get_artifact."
        ),
    )
    @guarded("argus_list_artifacts")
    def argus_list_artifacts(
        run_id: Annotated[str, Field(min_length=1)],
        test_id: Annotated[str | None, Field(description="Only this test's artifacts.")] = None,
        kind: Annotated[
            str | None,
            Field(description="screenshot | reference | diff | log | instrumentation | "
            "metadata | report | image | file"),
        ] = None,
        limit: Annotated[int | None, Field(ge=1)] = None,
        cursor: Annotated[str | None, Field(description="next_cursor from a previous page")] = None,
    ) -> ArtifactList:
        infos = ctx.service.list_artifacts(run_id, test_id=test_id)
        if kind:
            infos = [i for i in infos if i.kind == kind]
        page = paginate(infos, cursor=cursor, limit=ctx.bounded_limit(limit))
        return ArtifactList(
            run_id=run_id,
            items=[ArtifactItem.from_info(i) for i in page.items],
            total=page.total,
            truncated=page.truncated,
            next_cursor=page.next_cursor,
        )

    @server.tool(
        name="argus_get_artifact",
        annotations=READ_ONLY,
        structured_output=False,
        description=(
            "Fetch one artifact of a run by artifact_id (from argus_list_artifacts). "
            "Images are returned as MCP image content (downscaled if larger than the "
            "server's limit); text/JSON/XML/HTML as text, truncated to max_bytes with "
            "truncated=true in the metadata; other binaries are described but not "
            "returned. Access is restricted to the run's own results directory. Read-only."
        ),
    )
    @guarded("argus_get_artifact")
    def argus_get_artifact(
        run_id: Annotated[str, Field(min_length=1)],
        artifact_id: Annotated[str, Field(min_length=1, description="From argus_list_artifacts.")],
        max_bytes: Annotated[
            int | None, Field(ge=256, description="Cap for text content (bounded by server).")
        ] = None,
    ) -> CallToolResult:
        limits = ctx.limits
        cap = min(max_bytes or limits.max_log_bytes, limits.max_artifact_bytes)
        content = ctx.service.read_artifact(
            run_id, artifact_id, max_bytes=limits.max_artifact_bytes
        )
        info = content.info
        mime = info.mime_type
        blocks: list = []
        delivery = "omitted"
        note: str | None = None
        returned = 0
        truncated = content.truncated

        if mime in _IMAGE_MIMES:
            if content.truncated:
                note = "Image exceeds max_artifact_bytes and was not returned."
            else:
                data, note = bound_image(content.data, mime, limits.max_screenshot_dimension)
                image = Image(data=data, format=mime.removeprefix("image/"))
                blocks.append(image.to_image_content())
                delivery = "image"
                returned = len(data)
        elif mime in _TEXT_MIMES or mime.startswith(_TEXT_MIME_PREFIXES):
            data = content.data[:cap]
            truncated = truncated or len(content.data) > cap
            text = data.decode("utf-8", errors="replace")
            blocks.append(TextContent(type="text", text=text))
            delivery = "json" if mime == "application/json" else "text"
            returned = len(data)
            if truncated:
                note = f"Truncated to {cap} bytes of {info.size}; raise max_bytes for more."
        else:
            note = f"{mime} content is not returned inline; open it from results_dir."

        view = ArtifactContentView(
            run_id=run_id,
            artifact_id=artifact_id,
            kind=info.kind,
            mime_type=mime,
            size=info.size,
            returned_bytes=returned,
            truncated=truncated,
            delivery=delivery,
            note=note,
        )
        header = TextContent(type="text", text=view.model_dump_json())
        return CallToolResult(
            content=[header, *blocks], structured_content=view.model_dump(mode="json")
        )


def bound_image(data: bytes, mime: str, max_dimension: int) -> tuple[bytes, str | None]:
    """Downscale an encoded image whose longest side exceeds ``max_dimension``."""
    from PIL import Image as PILImage

    with PILImage.open(io.BytesIO(data)) as image:
        if max(image.size) <= max_dimension:
            return data, None
        original = image.size
        resized = image.copy()
        resized.thumbnail((max_dimension, max_dimension))
        buffer = io.BytesIO()
        resized.save(buffer, format="JPEG" if mime == "image/jpeg" else "PNG")
        return (
            buffer.getvalue(),
            f"Downscaled from {original[0]}x{original[1]} to {resized.width}x{resized.height}.",
        )
