/**
 * Drafts waiting for the owner that no reply in view made (design handoff
 * chat, "StandaloneApprovalRow"): replies to customers the employee drafted
 * in its own work, or a draft whose reply is out of view. Oldest first,
 * after the conversation.
 */

import { ApprovalCard } from './ApprovalCard';

export function StandaloneApprovals({ ids }: { ids: readonly string[] }) {
  if (ids.length === 0) return null;
  return (
    <section aria-label="Drafts waiting for you" className="flex flex-col gap-3">
      {ids.map((id) => (
        <ApprovalCard key={id} approvalId={id} />
      ))}
    </section>
  );
}

export default StandaloneApprovals;
