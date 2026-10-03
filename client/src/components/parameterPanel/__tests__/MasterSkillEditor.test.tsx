/**
 * Creating or deleting a user skill saves the Master Skill node's row
 * directly, and that save replaces the whole row. It used to send the
 * folder as `skillFolder`, a key nothing reads, so a node set to any folder
 * but "assistant" lost its folder on every create or delete.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { renderWithProviders } from '../../../test/providers';

const sendRequest = vi.hoisted(() => vi.fn());

vi.mock('../../../contexts/WebSocketContext', () => ({
  useWebSocket: () => ({ sendRequest }),
  useNodeStatus: () => undefined,
}));
vi.mock('../../../hooks/useFolderSkills', () => ({
  useFolderSkills: () => ({ data: [], isLoading: false }),
}));
vi.mock('../../../hooks/useUserSkills', () => ({
  USER_SKILLS_QUERY_KEY: ['userSkills'],
  useUserSkillsQuery: () => ({
    data: [{ name: 'triage', display_name: 'Triage', description: 'Sort the inbox', instructions: 'Sort it', icon: '', color: '#6366F1' }],
    isLoading: false,
  }),
}));
vi.mock('../../../hooks/useNodeAllowlist', () => ({
  useNodeAllowlist: () => ({ isSkillFolderDisabled: () => false }),
}));
vi.mock('../../../assets/icons', () => ({ NodeIcon: () => null }));

import MasterSkillEditor from '../MasterSkillEditor';

beforeEach(() => {
  sendRequest.mockReset();
  sendRequest.mockImplementation(async (type: string) => (
    type === 'list_skill_folders'
      ? { success: true, folders: [{ name: 'coding', skill_count: 0 }] }
      : { success: true }
  ));
});

describe('MasterSkillEditor', () => {
  it('keeps the folder in the row it saves after deleting a user skill', async () => {
    renderWithProviders(
      <MasterSkillEditor
        nodeId="7:masterSkill:1"
        skillFolder="coding"
        skillsConfig={{ triage: { enabled: true, instructions: 'Sort it', isCustomized: false } }}
        onConfigChange={vi.fn()}
      />,
    );

    fireEvent.click(await screen.findByRole('button', { name: 'Delete' }));
    const dialog = await screen.findByRole('alertdialog');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Delete' }));

    await waitFor(() => expect(sendRequest).toHaveBeenCalledWith('save_node_parameters', {
      node_id: '7:masterSkill:1',
      parameters: { skills_config: {}, skill_folder: 'coding' },
    }));
  });
});
