/**
 * The hire composer's label rule, kept apart from the component so the
 * component file exports components only (react-refresh) and the rule is
 * testable on its own. Which Enter sends is lib/composerKeys.ts.
 */

export function createLabel(working: boolean, refining: boolean): string {
  if (working) return 'Creating…';
  return refining ? 'Update' : 'Create employee';
}
