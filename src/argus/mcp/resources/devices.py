"""``argus://devices`` resources (configuration-derived; never connects)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from mcp.server.mcpserver.exceptions import ResourceNotFoundError

from argus.exceptions import UTFError
from argus.mcp.context import ServerContext
from argus.mcp.schemas import DeviceDetail, DeviceSummary

if TYPE_CHECKING:
    from mcp.server import MCPServer


def register_device_resources(server: MCPServer, ctx: ServerContext) -> None:
    from argus.mcp.resources import JSON, dump

    @server.resource(
        "argus://devices",
        name="argus-devices",
        title="Device inventory",
        mime_type=JSON,
        description="Configured devices with adapter, platform, capabilities and lease state.",
    )
    def devices_index() -> str:
        items = [DeviceSummary.from_info(d).model_dump() for d in ctx.service.list_devices()]
        return dump({"items": items, "total": len(items)})

    @server.resource(
        "argus://devices/{device_name}",
        name="argus-device",
        title="Device details",
        mime_type=JSON,
        description="One device's configuration-derived details (secrets redacted).",
    )
    def device_detail(device_name: str) -> str:
        try:
            info = ctx.service.get_device(device_name)
        except UTFError as exc:
            raise ResourceNotFoundError(str(exc)) from exc
        return dump(DeviceDetail.from_info(info).model_dump())
