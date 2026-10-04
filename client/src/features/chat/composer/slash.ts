/**
 * Which slash commands a draft asks for (composer/SlashMenu.tsx). Kept apart
 * from the component so its file exports components only (react-refresh)
 * and the rule is testable on its own.
 */

import type { ChatCommand } from '../data/chatContext';

/** The command a draft starts, lower-cased without its `/`; null when it
 *  is not one word starting with `/`. */
export function slashQuery(text: string): string | null {
  return text.startsWith('/') && !/\s/.test(text) ? text.slice(1).toLowerCase() : null;
}

/** The commands that begin with what was typed. */
export function matchCommands(commands: readonly ChatCommand[], query: string): ChatCommand[] {
  return commands.filter((command) => command.command.slice(1).toLowerCase().startsWith(query));
}
