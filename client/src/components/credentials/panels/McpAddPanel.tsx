/**
 * McpAddPanel — the MCP server card's page (kind `mcpAdd`): the custom
 * connector form (AddConnectorForm), the same one the Connectors page's Add
 * opens. A saved connector then shows on its own page (`onOpen`); Cancel,
 * or an OAuth sign-in left to finish in its own tab, goes back to the list.
 */

import { AddConnectorForm } from '../AddConnectorForm';
import type { CredentialPanelProps } from '../PanelRenderer';

export default function McpAddPanel({ config, onLeave, onOpen }: CredentialPanelProps) {
  return (
    <div className="flex flex-col gap-4 p-5">
      {config.instructions && <p className="m-0 text-sm text-muted-foreground">{config.instructions}</p>}
      <AddConnectorForm close={() => onLeave?.()} onAdded={(ref) => onOpen?.(ref)} />
    </div>
  );
}
