"""Device tools: inventory, status, screenshots."""

from __future__ import annotations

import io
from typing import TYPE_CHECKING, Annotated, Any

from mcp.server.mcpserver import Image
from mcp.types import TextContent, ToolAnnotations
from pydantic import BaseModel, Field

from argus.mcp.context import ServerContext
from argus.mcp.errors import InvalidArgumentError, guarded
from argus.mcp.pagination import paginate
from argus.mcp.schemas import DeviceDetail, DeviceList, DeviceSummary
from argus.models.common import Region

READ_ONLY = ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False)
PROBE = ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=True)

if TYPE_CHECKING:
    from mcp.server import MCPServer


class ScreenshotRegion(BaseModel):
    """Crop rectangle in screenshot pixels."""

    x: int = Field(ge=0)
    y: int = Field(ge=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)


def register_device_tools(server: MCPServer, ctx: ServerContext) -> None:
    @server.tool(
        name="argus_list_devices",
        annotations=READ_ONLY,
        description=(
            "Inventory of configured devices: name, adapter type, platform, capabilities "
            "(screenshot, tap, keyboard, logs, instrumentation, …), whether it is fully "
            "configured, and whether a run currently holds it. Read-only and instant — it "
            "does NOT connect to devices; use argus_get_device with probe=true for live "
            "health."
        ),
    )
    @guarded("argus_list_devices")
    def argus_list_devices(
        limit: Annotated[int | None, Field(ge=1)] = None,
        cursor: Annotated[str | None, Field(description="next_cursor from a previous page")] = None,
    ) -> DeviceList:
        page = paginate(ctx.service.list_devices(), cursor=cursor, limit=ctx.bounded_limit(limit))
        return DeviceList(
            items=[DeviceSummary.from_info(d) for d in page.items],
            total=page.total,
            truncated=page.truncated,
            next_cursor=page.next_cursor,
        )

    @server.tool(
        name="argus_get_device",
        annotations=PROBE,
        description=(
            "Details for one device: capabilities, instrumentation, redacted adapter "
            "options, and current lease. With probe=true it also connects and reports "
            "health, screen size and whether the app is running (takes a short exclusive "
            "lease; fails if a run holds the device). Read-only; probe=false is instant."
        ),
    )
    @guarded("argus_get_device")
    def argus_get_device(
        device: Annotated[str, Field(min_length=1, description="Configured device name.")],
        probe: Annotated[bool, Field(description="Connect and run a health check.")] = False,
    ) -> DeviceDetail:
        info = ctx.service.get_device(device, probe=probe)
        return DeviceDetail.from_info(info, probed=probe)

    @server.tool(
        name="argus_capture_screenshot",
        annotations=PROBE,
        structured_output=False,
        description=(
            "Capture the device's current screen and return it as an MCP image (plus a "
            "one-line text summary). Connects to the device (short exclusive lease; fails "
            "while a run holds it) but sends no input. Optional region crop (screenshot "
            "pixels), png/jpeg, quality, and max_dimension (large screens are downscaled "
            "to bound the payload; the summary states the scale). Use it to look at the "
            "screen before writing or debugging a visual test."
        ),
    )
    @guarded("argus_capture_screenshot")
    def argus_capture_screenshot(
        device: Annotated[str, Field(min_length=1, description="Configured device name.")],
        region: Annotated[ScreenshotRegion | None, Field(description="Crop rectangle.")] = None,
        format: Annotated[str, Field(description="png or jpeg.")] = "png",
        quality: Annotated[int, Field(ge=1, le=100, description="JPEG quality.")] = 80,
        max_dimension: Annotated[
            int | None,
            Field(ge=64, description="Downscale so width and height fit (bounded by server)."),
        ] = None,
    ) -> list[Any]:
        fmt = format.lower()
        if fmt == "jpg":
            fmt = "jpeg"
        if fmt not in ("png", "jpeg"):
            raise InvalidArgumentError(
                f"Unsupported format {format!r}.", remediation="Use 'png' or 'jpeg'."
            )
        image = ctx.service.capture_screenshot(device)
        original = image.size
        if region is not None:
            crop = Region(**region.model_dump())
            if crop.right > image.width or crop.bottom > image.height:
                raise InvalidArgumentError(
                    f"Region {crop.as_tuple()} exceeds the {image.width}x{image.height} screen.",
                    remediation="Use a region inside the screenshot bounds.",
                )
            image = image.crop((crop.x, crop.y, crop.right, crop.bottom))
        cap = ctx.limits.max_screenshot_dimension
        bound = min(max_dimension or cap, cap)
        scale = 1.0
        if max(image.size) > bound:
            scale = bound / max(image.size)
            image = image.copy()
            image.thumbnail((bound, bound))
        data = encode_image(image, fmt, quality)
        summary = (
            f"device={device} screen={original[0]}x{original[1]} "
            f"returned={image.width}x{image.height} scale={scale:.3f} "
            f"format={fmt} bytes={len(data)}"
            + (f" region={region.model_dump()}" if region else "")
        )
        return [TextContent(type="text", text=summary), Image(data=data, format=fmt)]


def encode_image(image: Any, fmt: str, quality: int) -> bytes:
    buffer = io.BytesIO()
    if fmt == "jpeg":
        image.convert("RGB").save(buffer, format="JPEG", quality=quality, optimize=True)
    else:
        image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()
