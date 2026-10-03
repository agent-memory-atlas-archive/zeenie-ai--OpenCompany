"""The skill phases the backend sends must be the words the canvas reads.

``services/skill_runtime.py`` puts the phase in an agent node's
``node_status`` data, and ``client/src/lib/agentPhases.ts`` is what the
canvas compares it with. They drifted once: the editor checked
``loading_skills`` while the backend sent ``loading_skill``, so the skill
edge never lit up while a skill loaded.

Cross-language, so it cannot be a type. This test reads the client file.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from services.skill_runtime import SKILL_LOADED_PHASE, SKILL_LOADING_PHASE

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
AGENT_PHASES = REPO_ROOT / "client" / "src" / "lib" / "agentPhases.ts"


def _client_phases() -> dict[str, str]:
    source = AGENT_PHASES.read_text(encoding="utf-8")
    return dict(re.findall(r"(\w+):\s*'([^']+)'", source))


def test_the_client_reads_the_skill_phases_the_backend_sends():
    phases = _client_phases()
    assert phases.get("loadingSkill") == SKILL_LOADING_PHASE
    assert phases.get("skillLoaded") == SKILL_LOADED_PHASE
