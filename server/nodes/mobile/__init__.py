"""Optional Mobile plugin. Import registers capabilities; starts no runtime."""

from services.ws_handler_registry import register_router
from services.plugin.shutdown_hooks import register_shutdown_hook
from services.node_output_schemas import register_output_schema
from ._node import MobileUseAgent, MobileOutput
from ._tool import AndroidTool
from ._router import router

__all__ = ["MobileUseAgent", "MobileOutput", "AndroidTool"]


async def shutdown() -> None:
    import asyncio

    from ._video import shutdown_video
    from ._runtime import peek_runtime

    runtime = peek_runtime()
    if runtime is not None:
        await asyncio.gather(shutdown_video(), runtime.shutdown())
    else:
        await shutdown_video()


register_router(router, name="mobile")
register_shutdown_hook("mobile", shutdown)
register_output_schema("mobile_use_agent", MobileOutput)
register_output_schema("android_tool", MobileOutput)
