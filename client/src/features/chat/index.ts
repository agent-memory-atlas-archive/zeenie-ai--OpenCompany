/**
 * The chat shared by Home's employee page and Dev's console Chat pane
 * (docs-internal/chat_protocol.md). Import from here, not from the folders
 * inside.
 */

export { ChatPane } from './ChatPane';
export type { ChatHost, ChatHostKind, ChatPaneHandle, ChatPersona, ComposerMode, NotifyTone } from './host';
export { useClearChat } from './data/send';
export { useLaneRun } from './data/runs';
export { useChatThread, type ThreadScope } from './data/thread';
export { CHAT_MARKDOWN_COMPONENTS } from './markdown/components';
export { SafeImage } from './markdown/SafeImage';
export { isWorkspaceImage } from './markdown/workspaceImage';
