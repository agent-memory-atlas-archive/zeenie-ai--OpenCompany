/**
 * "Hire now": a starter (hire/starters.json) hired in one click, from its
 * chip under the composer or its card in Settings > Plugins, with no setup
 * screen. Its skills go into the library first (a new hire gets the
 * library's skills that are on), then its own setup is hired as it stands,
 * asking first (hirePayload's starterHirePayload), and the new employee
 * lands like any hire.
 *
 * It shares the one hire slot with the setup screen, so the two never
 * overlap; `busy` is true while either is hiring.
 */

import { useCallback } from 'react';
import { useQueryClient, type QueryClient } from '@tanstack/react-query';
import { useWebSocketActions, type WebSocketActions } from '@/contexts/WebSocketContext';
import { fetchFolderSkills, folderSkillsQueryKey } from '@/hooks/useFolderSkills';
import { USER_SKILLS_QUERY_KEY, fetchUserSkills } from '@/hooks/useUserSkills';
import { EMPLOYEES_QUERY_KEY } from '../data/employees';
import type { EmployeeSummary } from '../data/schemas';
import { DISCOVER_SKILL_FOLDER, useSkillActions } from '../data/skills';
import type { Starter } from '../hire/templates';
import { pillToast } from '../ui/pillToast';
import { beginHire, endHire, setComposerInput, useDraftStore } from './draftStore';
import { starterHirePayload } from './hirePayload';
import { HIRE_TIMEOUT_MS, hiredEmployee, hireFailureMessage, welcomeHire, type HireResponse } from './useHire';

type Send = WebSocketActions['sendRequest'];

/** Put a starter's skills in the library, switching on any that are off. */
async function addStarterSkills(
  starter: Starter,
  send: Send,
  queryClient: QueryClient,
  skills: ReturnType<typeof useSkillActions>,
): Promise<void> {
  const [library, builtIns] = await Promise.all([
    queryClient.fetchQuery({ queryKey: USER_SKILLS_QUERY_KEY, queryFn: () => fetchUserSkills(send) }),
    queryClient.ensureQueryData({
      queryKey: folderSkillsQueryKey(DISCOVER_SKILL_FOLDER),
      queryFn: () => fetchFolderSkills(send, DISCOVER_SKILL_FOLDER),
    }),
  ]);
  for (const name of starter.skills) {
    const row = library.find((candidate) => candidate.name === name);
    if (row) {
      if (!row.is_active) await skills.setOn(name, true);
      continue;
    }
    const skill = builtIns.find((candidate) => candidate.skillName === name);
    if (!skill) throw new Error(`${starter.label} needs a skill that isn't available`);
    await skills.add(skill);
  }
}

export function useStarterHire() {
  const { sendRequest } = useWebSocketActions();
  const queryClient = useQueryClient();
  const skills = useSkillActions();
  const busy = useDraftStore((s) => s.hiring);

  /** Resolves true once the starter is on the team. */
  const hire = useCallback(
    async (starter: Starter): Promise<boolean> => {
      const team = queryClient.getQueryData<EmployeeSummary[]>(EMPLOYEES_QUERY_KEY) ?? [];
      const base = starterHirePayload(starter, { idempotencyKey: '', taken: team.map((employee) => employee.name) });
      const key = beginHire(`starter:${JSON.stringify(base)}`);
      if (!key) return false;
      try {
        await addStarterSkills(starter, sendRequest, queryClient, skills);
      } catch (error) {
        endHire();
        pillToast(error instanceof Error ? error.message : `Couldn't add ${starter.label}'s skills`, { tone: 'error' });
        return false;
      }
      try {
        const response = await sendRequest<HireResponse>('hire_employee', { ...base, idempotency_key: key }, HIRE_TIMEOUT_MS);
        const employee = hiredEmployee(response);
        endHire(true);
        // The composer held this starter's job, and it has been hired.
        if (useDraftStore.getState().input.trim() === starter.job) setComposerInput('');
        welcomeHire(queryClient, employee, response);
        return true;
      } catch (error) {
        endHire();
        pillToast(hireFailureMessage(error), { tone: 'error' });
        return false;
      }
    },
    [queryClient, sendRequest, skills],
  );

  return { hire, busy };
}
