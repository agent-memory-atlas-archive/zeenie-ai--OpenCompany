/** Shared role colours for catalogue tiles and employee avatars. */
export const COLOR_ROLES = ['agent', 'model', 'tool', 'trigger', 'workflow'] as const;
export type ColorRole = (typeof COLOR_ROLES)[number];

export const AVATAR_CLASS: Record<ColorRole, string> = {
  agent: 'bg-node-agent-fill border-node-agent-edge text-node-agent-ink',
  model: 'bg-node-model-fill border-node-model-edge text-node-model-ink',
  tool: 'bg-node-tool-fill border-node-tool-edge text-node-tool-ink',
  trigger: 'bg-node-trigger-fill border-node-trigger-edge text-node-trigger-ink',
  workflow: 'bg-node-workflow-fill border-node-workflow-edge text-node-workflow-ink',
};

/** An avatar's letter: the first letter of the name, "?" when empty. */
export function initialOf(name: string): string {
  const letter = Array.from(name.trim())[0];
  return letter ? letter.toLocaleUpperCase() : '?';
}
