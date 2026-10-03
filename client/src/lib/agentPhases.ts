/**
 * Skill phases an agent node's status carries in `data.phase`.
 *
 * The backend sends them in `node_status` (server/services/skill_runtime.py),
 * and WebSocketContext writes the same words when an `agent_capability` event
 * reports a skill loading or loaded. Read and write them through these
 * constants: the canvas once checked `loading_skills` while both writers sent
 * `loading_skill`, so the skill edge never lit up.
 * server/tests/test_agent_phase_names.py keeps them equal to the backend's.
 */
export const AGENT_PHASE = {
  loadingSkill: 'loading_skill',
  skillLoaded: 'skill_loaded',
} as const;
