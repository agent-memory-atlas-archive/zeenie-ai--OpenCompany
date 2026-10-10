"""Offline checks on the gateway's pins; no gateway needed.

- LiteLLM's version and the price-list commit agree across Terraform, the
  local stack and CI.
- The pinned price list prices the default model of every provider the
  gateway serves, so OpenCompany's defaults are never refused as unpriced.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import httpx
import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
TERRAFORM = (REPO / "gateway/infra/terraform/main.tf").read_text(encoding="utf-8")
COMPOSE = yaml.safe_load((REPO / "gateway/local/docker-compose.yml").read_text(encoding="utf-8"))
CI = yaml.safe_load((REPO / ".github/workflows/gateway-ci.yml").read_text(encoding="utf-8"))
LITELLM = COMPOSE["services"]["litellm"]

# Providers the gateway serves: OpenCompany provider id (server/config/
# llm_defaults.json) -> the prefix of its keys in LiteLLM's price list.
SERVED_PROVIDERS = {
    "openai": "",
    "anthropic": "",
    "gemini": "gemini/",
    "groq": "groq/",
    "cerebras": "cerebras/",
    "xai": "xai/",
    "kimi": "moonshot/",
    "mistral": "mistral/",
}


def _terraform_string(name: str) -> str:
    match = re.search(rf'^\s*{name}\s*=\s*"([^"]+)"', TERRAFORM, re.MULTILINE)
    assert match, f"{name} not found in gateway/infra/terraform/main.tf"
    return match.group(1)


def test_pins_agree() -> None:
    version = _terraform_string("litellm_version")
    module = re.search(r'source\s*=\s*"BerriAI/litellm/aws"\s*\n\s*version\s*=\s*"([^"]+)"', TERRAFORM)
    assert module and module.group(1) == version, "module version and local.litellm_version differ in main.tf"
    assert LITELLM["image"].endswith(f":v{version}"), LITELLM["image"]
    assert CI["env"]["LITELLM_VERSION"] == version, CI["env"]
    assert LITELLM["environment"]["LITELLM_MODEL_COST_MAP_URL"] == _terraform_string("LITELLM_MODEL_COST_MAP_URL")


def test_served_default_models_are_priced() -> None:
    url = LITELLM["environment"]["LITELLM_MODEL_COST_MAP_URL"]
    assert re.search(r"/[0-9a-f]{40}/", url), "the price list must be pinned to a commit"
    try:
        prices = httpx.get(url, timeout=60).raise_for_status().json()
    except httpx.HTTPError as exc:
        if os.environ.get("CI"):
            raise
        pytest.skip(f"price list unreachable: {exc}")
    providers = json.loads((REPO / "server/config/llm_defaults.json").read_text(encoding="utf-8"))["providers"]
    unpriced = []
    for provider, prefix in SERVED_PROVIDERS.items():
        key = prefix + providers[provider]["default_model"]
        entry = prices.get(key) or {}
        if "input_cost_per_token" not in entry and "output_cost_per_token" not in entry:
            unpriced.append(key)
    assert not unpriced, f"served default models with no price in the pinned list: {unpriced}"
