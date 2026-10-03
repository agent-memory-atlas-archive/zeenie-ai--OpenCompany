/**
 * Lets canvas node components ask whether the canvas may be edited now.
 *
 * Dashboard provides its `guardCanvasEdit`: false (after telling the user
 * why) while the workflow runs, true otherwise. Node components use it for
 * edits they start themselves, such as a double-click rename, so those obey
 * the same lock as F2, the context menu, drop and paste. Outside the editor
 * nothing provides it and every edit is allowed.
 */

import { createContext, useContext } from 'react';

export type CanvasEditGuard = () => boolean;

const allowAll: CanvasEditGuard = () => true;

export const CanvasEditGuardContext = createContext<CanvasEditGuard>(allowAll);

export const useCanvasEditGuard = (): CanvasEditGuard => useContext(CanvasEditGuardContext);
