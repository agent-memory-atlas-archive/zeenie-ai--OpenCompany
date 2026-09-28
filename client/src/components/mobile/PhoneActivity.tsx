import { useEffect, useState } from 'react';

const seconds = (from: unknown, to: number) => typeof from === 'number' ? Math.max(0, Math.floor(to - from)) : 0;

export function PhoneActivity({ task }: { task?: Record<string, unknown> | null }) {
  const [now, setNow] = useState(Date.now() / 1000);
  const ended = typeof task?.finished_at === 'number';
  useEffect(() => {
    if (!task?.run_id || ended) return;
    const timer = setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => clearInterval(timer);
  }, [task?.run_id, ended]);
  if (!task?.run_id) return null;
  const end = ended ? Number(task.finished_at) : now;
  const quiet = seconds(task.updated_at, end);
  const events = Array.isArray(task.activity) ? task.activity as { at: number; message: string; step: number }[] : [];
  return <details open className="max-h-44 shrink-0 overflow-y-auto border-t border-border-default text-xs group-data-[full-view=true]/fullview:hidden" aria-label="Android activity">
    <summary className="cursor-pointer p-2 font-medium">AI activity · {ended ? String(task.status) : String(task.phase || 'Preparing phone')} · {seconds(task.started_at, end)}s total</summary>
    <div className="space-y-1 px-2 pb-2">
      <p className="m-0 text-fg-muted">{String(task.provider || '')} {String(task.model || '')} · Step {Number(task.steps || 0)} / {Number(task.max_steps || 0)}</p>
      {!ended && <p className="m-0" role="status">Current phase: {seconds(task.phase_started_at, now)}s · Last activity: {quiet}s ago</p>}
      {!ended && quiet >= 30 && <p className="m-0 text-amber-600">No new activity for {quiet}s. The last reported phase is shown above. This may be a slow request; it does not confirm the task is stuck. Use “Use phone” to take control.</p>}
      {ended && task.error_code != null && <p className="m-0 text-destructive">Stopped during {String(task.phase || 'startup')} · {String(task.error_code)}</p>}
      <ol className="m-0 list-none space-y-1 p-0" aria-label="Recent Android steps">
        {[...events].reverse().map((item, index) => <li key={`${item.at}-${index}`}><span className="text-fg-muted">+{seconds(task.started_at, item.at)}s · </span>{item.message}</li>)}
      </ol>
    </div>
  </details>;
}
