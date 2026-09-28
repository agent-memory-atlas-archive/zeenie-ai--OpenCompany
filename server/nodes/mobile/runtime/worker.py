"""One trusted mobile-use task. No device clients: every action crosses the broker."""

from __future__ import annotations
import asyncio
import base64
import json
import os
import re
import sys
import urllib.request
import uuid
from io import BytesIO

WIRE = sys.stdout


def emit(kind: str, **data) -> None:
    WIRE.write(json.dumps({"v": 1, "type": kind, **data}, default=str) + "\n")
    WIRE.flush()


class Controller:
    def __init__(self, config: dict):
        self.config = config
        self.geometry = None

    def read_sync(self, operation: str, **params):
        payload = {
            "operation": operation,
            "parameters": params,
            "epoch": self.config["epoch"],
            "operation_id": str(uuid.uuid4()),
            "geometry": self.geometry,
        }
        req = urllib.request.Request(
            self.config["broker_url"],
            json.dumps(payload).encode(),
            {"Content-Type": "application/json", "Authorization": f"Bearer {self.config['capability']}"},
        )
        with urllib.request.urlopen(req, timeout=35) as response:
            result = json.load(response)
        if not result.get("success"):
            raise RuntimeError(result.get("error", "Device command failed"))
        return result["result"]

    async def read(self, operation: str, **params):
        return await asyncio.to_thread(self.read_sync, operation, **params)

    async def get_screen_data(self):
        from minitap.mobile_use.controllers.device_controller import ScreenDataResponse

        result = await self.read("observe")
        self.geometry = result.pop("geometry")
        return ScreenDataResponse(**result)

    async def screenshot(self):
        return (await self.get_screen_data()).base64

    async def get_ui_hierarchy(self):
        return (await self.get_screen_data()).elements

    async def tap(self, coords, long_press=False, long_press_duration=1000):
        from minitap.mobile_use.controllers.types import TapOutput

        await self.read("tap", x=coords.x, y=coords.y, duration=long_press_duration if long_press else 0)
        return TapOutput(error=None)

    async def swipe(self, start, end, duration=400):
        await self.read("swipe", x=start.x, y=start.y, end_x=end.x, end_y=end.y, duration=duration)
        return None

    async def input_text(self, text):
        return await self.read("text", text=text)

    async def launch_app(self, package_or_bundle_id):
        return await self.read("launch", package=package_or_bundle_id)

    async def terminate_app(self, package_or_bundle_id):
        return await self.read("terminate", package=package_or_bundle_id)

    async def open_url(self, url):
        return await self.read("url", url=url)

    async def press_back(self):
        return await self.read("key", key="back")

    async def press_home(self):
        return await self.read("key", key="home")

    async def press_enter(self):
        return await self.read("key", key="enter")

    async def erase_text(self, nb_chars=None):
        return await self.read("erase", count=nb_chars or 50)

    async def cleanup(self):
        pass  # broker owns the persistent device

    def find_element(self, ui_hierarchy, resource_id=None, text=None, index=0):
        from minitap.mobile_use.controllers.types import Bounds

        matches = [
            e
            for e in ui_hierarchy
            if (resource_id and e.get("resource-id") == resource_id)
            or (text and (e.get("text") == text or e.get("accessibilityText") == text))
        ]
        if index < 0 or index >= len(matches):
            return None, None, "Element not found"
        element = matches[index]
        points = re.fullmatch(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", element.get("bounds", ""))
        bounds = Bounds(**dict(zip(("x1", "y1", "x2", "y2"), map(int, points.groups())))) if points else None
        return element, bounds, None

    def get_compressed_b64_screenshot(self, image_base64, quality=50):
        from PIL import Image

        output = BytesIO()
        Image.open(BytesIO(base64.b64decode(image_base64))).convert("RGB").save(output, "JPEG", quality=quality)
        return base64.b64encode(output.getvalue()).decode()

    async def start_video_recording(self, max_duration_seconds=900):
        raise RuntimeError("Recording is not enabled; use Workspace preview")

    async def stop_video_recording(self):
        raise RuntimeError("Recording is not enabled")


async def main(config: dict) -> None:
    sys.stdout = sys.stderr  # upstream diagnostics can never corrupt the protocol
    os.environ.update(config.pop("model_env"))
    os.environ["MOBILE_USE_TELEMETRY_ENABLED"] = "false"
    os.environ["PYTHON_DOTENV_DISABLED"] = "1"
    sys.path.insert(0, config["source"])
    from minitap.mobile_use.config import LLM, LLMWithFallback, LLMConfig, LLMConfigUtils
    from minitap.mobile_use.context import DeviceContext, DevicePlatform
    from minitap.mobile_use.sdk.agent import Agent
    from minitap.mobile_use.sdk.builders.agent_config_builder import AgentConfigBuilder
    from minitap.mobile_use.sdk.types.task import AgentProfile, TaskRequest
    # Only this isolated engine process uses LangChain; the server does not.
    from langchain_core.callbacks import BaseCallbackHandler

    class Progress(BaseCallbackHandler):
        def on_chain_start(self, serialized, inputs, *, metadata=None, **kwargs):
            step = (metadata or {}).get("langgraph_step")
            if isinstance(step, int):
                emit("progress", steps=step + 1)

        def on_llm_end(self, response, **kwargs):
            output = response.llm_output or {}
            usage = output.get("token_usage") or output.get("usage") or {}
            if usage:
                emit("usage", usage=usage)

    model = LLM(provider=config["provider"], model=config["model"])
    stage = LLMWithFallback(provider=model.provider, model=model.model, fallback=model)
    profile = AgentProfile(
        name="opencompany",
        llm_config=LLMConfig(
            planner=stage,
            orchestrator=stage,
            contextor=stage,
            cortex=stage,
            executor=stage,
            utils=LLMConfigUtils(outputter=stage, hopper=stage),
        ),
    )
    agent_config = (
        AgentConfigBuilder()
        .with_default_profile(profile)
        .for_device(DevicePlatform(config["platform"]), config["serial"])
        .with_graph_config_callbacks([Progress()])
        .build()
    )
    agent_config.local_controller = Controller(config)
    agent_config.local_device_context = DeviceContext(
        host_platform="WINDOWS" if sys.platform == "win32" else "MACOS" if sys.platform == "darwin" else "LINUX",
        mobile_platform=DevicePlatform(config["platform"]),
        device_id=config["serial"],
        device_width=config["width"],
        device_height=config["height"],
    )
    agent = Agent(config=agent_config)
    await agent.init()
    emit("ready")
    try:
        result = await asyncio.wait_for(
            agent.run_task(request=TaskRequest(goal=config["prompt"], max_steps=config["max_steps"], record_trace=False)),
            config["timeout_s"],
        )
        emit("completed", result=result)
    finally:
        await agent.clean()


if __name__ == "__main__":
    try:
        asyncio.run(main(json.loads(sys.stdin.readline())))
    except BaseException as exc:
        # Provider errors may echo credentials; expose only an actionable category.
        # Prompts/screenshots in upstream stderr are not persisted.
        kind = type(exc).__name__
        message = {
            "AuthenticationError": "The selected model rejected its credentials.",
            "RateLimitError": "The selected model is rate limited. Try again later.",
            "TimeoutError": "The mobile task exceeded its active time budget.",
            "GraphRecursionError": "The mobile task exhausted its step budget.",
            "HTTPError": "Device control changed or the device connection failed.",
        }.get(kind, f"Mobile engine failed ({kind}).")
        emit("failed", error=message)
        sys.exit(1)
