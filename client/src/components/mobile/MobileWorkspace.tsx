import { useCallback, useEffect, useRef, useState } from 'react';
import type { PointerEvent } from 'react';
import { ArrowLeft, Home, Play, RotateCw, Smartphone, Square } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { buildApiUrl } from '@/config/api';
import { mobilePath, mobilePoint, mobileRequest, type Doctor, type Geometry, type Invocation, type MobileStatus } from './api';

const fieldClass = 'min-w-0 rounded border border-border-default bg-bg-input px-2 py-1.5 text-sm text-fg-default outline-none focus-visible:ring-2 focus-visible:ring-ring';
const finished = new Set(['completed', 'success', 'succeeded', 'failed', 'cancelled', 'canceled']);
const taskState = (task: Invocation | null) => task?.status ?? task?.state ?? '';
const describe = (value: unknown): string => typeof value === 'string' ? value : value == null ? '' : JSON.stringify(value);

export default function MobileWorkspace({ workflowId, nodes, visible = true }: {
  workflowId?: string | null; nodes: { node_id: string; label: string }[]; visible?: boolean;
}) {
  const [selected, setSelected] = useState('');
  const nodeId = nodes.some((node) => node.node_id === selected) ? selected : nodes[0]?.node_id;
  if (!workflowId || workflowId === 'new') return <Empty message="Save this workflow before opening its mobile workspace." />;
  if (!nodeId) return <Empty message="Add a Mobile Agent node to this workflow to use its phone here." />;
  return <div className="flex min-h-0 flex-1 flex-col gap-2">
    {nodes.length > 1 && <select className={fieldClass} aria-label="Mobile agent" value={nodeId} onChange={(event) => setSelected(event.target.value)}>
      {nodes.map((node) => <option key={node.node_id} value={node.node_id}>{node.label}</option>)}
    </select>}
    <MobileSession key={`${workflowId}:${nodeId}`} workflowId={workflowId} nodeId={nodeId} visible={visible} />
  </div>;
}

function Empty({ message }: { message: string }) {
  return <div className="m-auto flex max-w-80 flex-col items-center gap-3 p-6 text-center text-sm text-fg-muted"><Smartphone aria-hidden className="size-6" />{message}</div>;
}

function MobileSession({ workflowId, nodeId, visible }: { workflowId: string; nodeId: string; visible: boolean }) {
  const path = mobilePath(workflowId, nodeId);
  const [viewerId] = useState(() => crypto.randomUUID());
  const [status, setStatus] = useState<MobileStatus | null>(null);
  const [doctor, setDoctor] = useState<Doctor | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState('');
  const [accepted, setAccepted] = useState(false);
  const [prompt, setPrompt] = useState('');
  const [text, setText] = useState('');
  const [task, setTask] = useState<Invocation | null>(null);
  const [submission, setSubmission] = useState<string | null>(() => {
    try { return sessionStorage.getItem(`mobile-task:${workflowId}:${nodeId}`); } catch { return null; }
  });
  const [epoch, setEpoch] = useState<number | null>(null);
  const [leaseOwner, setLeaseOwner] = useState<string | null>(null);
  const [videoError, setVideoError] = useState('');
  const [videoAttempt, setVideoAttempt] = useState(0);
  const [live, setLive] = useState(false);
  const [pageVisible, setPageVisible] = useState(!document.hidden);
  const canvas = useRef<HTMLCanvasElement>(null);
  const surface = useRef<HTMLDivElement>(null);
  const active = useRef(true);
  const operation = useRef(false);
  const leaseEpoch = useRef<number | null>(null);
  const viewActive = useRef(visible && pageVisible);
  viewActive.current = visible && pageVisible;
  const pendingSubmission = useRef<{ id: string; prompt: string } | null>(null);
  const pointer = useRef<{ x: number; y: number; at: number; geometry: Geometry } | null>(null);
  const held = status?.control_state === 'human' && leaseOwner !== null && status.controller === leaseOwner && epoch === status.epoch;
  const hasTask = !!submission && !finished.has(taskState(task));
  const running = status?.running === true;
  const refresh = useCallback(async (signal?: AbortSignal) => {
    const next = await mobileRequest<MobileStatus>(`${path}/status`, undefined, signal);
    if (active.current) setStatus(next);
  }, [path]);

  useEffect(() => {
    active.current = true;
    const changed = () => setPageVisible(!document.hidden);
    document.addEventListener('visibilitychange', changed);
    return () => { active.current = false; document.removeEventListener('visibilitychange', changed); };
  }, []);
  useEffect(() => {
    if (!visible || !pageVisible) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try { await refresh(controller.signal); }
      catch (cause) { if (!controller.signal.aborted && active.current) setError(describe(cause instanceof Error ? cause.message : cause)); }
      if (!controller.signal.aborted) timer = setTimeout(() => void poll(), 2000);
    };
    void poll();
    void mobileRequest<Doctor>(`${path}/doctor`, undefined, controller.signal).then((value) => {
      if (!controller.signal.aborted) setDoctor(value);
    }).catch((cause) => { if (!controller.signal.aborted) setError(cause.message); });
    return () => { controller.abort(); clearTimeout(timer); };
  }, [visible, pageVisible, refresh, path]);
  useEffect(() => {
    if (!submission || !visible || !pageVisible) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const next = await mobileRequest<Invocation>(`${path}/tasks/${encodeURIComponent(submission)}`, undefined, controller.signal);
        if (!controller.signal.aborted) setTask(next);
        if (!finished.has(taskState(next)) && !controller.signal.aborted) timer = setTimeout(() => void poll(), 1500);
      } catch (cause) {
        if (!controller.signal.aborted) { setError(cause instanceof Error ? cause.message : 'Could not read this task.'); timer = setTimeout(() => void poll(), 4000); }
      }
    };
    void poll();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [submission, visible, pageVisible, path]);
  useEffect(() => {
    if (!running || !visible || !pageVisible || !canvas.current) return;
    let disposed = false;
    let disconnect: (() => void) | undefined;
    setVideoError(''); setLive(false);
    const target = canvas.current;
    void import('./video').then(({ connectMobileVideo }) => {
      if (!disposed) disconnect = connectMobileVideo({ workflowId, nodeId, viewerId, canvas: target,
        onError: setVideoError, onLive: () => setLive(true) });
    }).catch(() => { if (!disposed) setVideoError('Could not load the video player. Reconnect to try again.'); });
    return () => { disposed = true; disconnect?.(); };
  }, [running, visible, pageVisible, workflowId, nodeId, viewerId, videoAttempt]);
  useEffect(() => {
    if (status?.setup !== 'ready') return;
    const controller = new AbortController();
    void mobileRequest<Doctor>(`${path}/doctor`, undefined, controller.signal).then((value) => {
      if (!controller.signal.aborted) setDoctor(value);
    }).catch(() => {});
    return () => controller.abort();
  }, [status?.setup, path]);
  // Hiding the view relinquishes only this grant, never a later control session.
  useEffect(() => {
    if (!visible || !pageVisible) return;
    return () => {
      const releasedEpoch = leaseEpoch.current;
      leaseEpoch.current = null;
      if (releasedEpoch !== null) void mobileRequest(`${path}/release`, { viewer_id: viewerId, epoch: releasedEpoch, resume: false }).catch(() => {});
    };
  }, [visible, pageVisible, path, viewerId]);

  const perform = async (label: string, action: () => Promise<void>) => {
    if (operation.current) return;
    operation.current = true; setBusy(label); setError('');
    try { await action(); if (active.current) await refresh(); }
    catch (cause) { if (active.current) setError(cause instanceof Error ? cause.message : 'That action failed.'); }
    finally { operation.current = false; if (active.current) setBusy(''); }
  };
  const input = async (action: string, parameters: Record<string, unknown>, geometry = status?.geometry) => {
    if (!held || epoch === null || !geometry) return;
    await mobileRequest(`${path}/input`, { viewer_id: viewerId, epoch, operation_id: crypto.randomUUID(), operation: action, parameters, geometry });
  };
  const point = (event: PointerEvent<HTMLDivElement>) => {
    const rect = surface.current?.getBoundingClientRect();
    const geometry = status?.geometry;
    if (!rect || !geometry) return null;
    return mobilePoint(event.clientX - rect.left, event.clientY - rect.top, rect.width, rect.height, geometry);
  };
  const down = (event: PointerEvent<HTMLDivElement>) => {
    if (!held || busy || !live || videoError) return;
    const start = point(event);
    if (!start || !status?.geometry) return;
    pointer.current = { ...start, at: performance.now(), geometry: { ...status.geometry } };
    event.currentTarget.setPointerCapture(event.pointerId);
  };
  const up = (event: PointerEvent<HTMLDivElement>) => {
    const start = pointer.current; pointer.current = null;
    const end = point(event);
    if (!start || !end || !held || !live || videoError) return;
    const duration = Math.max(100, Math.min(2000, Math.round(performance.now() - start.at)));
    void perform('input', () => Math.hypot(end.x - start.x, end.y - start.y) < 12
      ? input('tap', { x: end.x, y: end.y }, start.geometry)
      : input('swipe', { x: start.x, y: start.y, end_x: end.x, end_y: end.y, duration }, start.geometry));
  };
  const submit = () => perform('task', async () => {
    const clean = prompt.trim();
    if (!clean || hasTask) return;
    // A lost response retries the same task; changing the text makes a new submission.
    const attempt = pendingSubmission.current?.prompt === clean ? pendingSubmission.current : { id: crypto.randomUUID(), prompt: clean };
    pendingSubmission.current = attempt;
    const response = await mobileRequest<Invocation>(`${path}/tasks`, { submission_id: attempt.id, prompt: clean });
    if (!active.current) return;
    setSubmission(attempt.id); setTask(response); setPrompt(''); pendingSubmission.current = null;
    try { sessionStorage.setItem(`mobile-task:${workflowId}:${nodeId}`, attempt.id); } catch { /* optional recovery */ }
  });
  const installed = doctor?.adb && doctor?.emulator && doctor?.image && doctor?.engine && doctor?.video;
  const setupActive = typeof status?.setup === 'string' && status.setup.startsWith('installing_');
  return <div className="flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto">
    <div className="flex flex-wrap items-center gap-2">
      <Smartphone aria-hidden className="size-4" />
      <span className="flex-1 text-sm font-medium">Shared phone</span>
      <span role="status" className="text-xs text-fg-muted">{busy ? `${busy}…` : running ? status.control_state : status ? 'Stopped' : 'Connecting…'}</span>
      <Button size="icon-sm" variant="ghost" aria-label="Refresh mobile status" onClick={() => void perform('refresh', async () => { setDoctor(await mobileRequest(`${path}/doctor`)); })}><RotateCw /></Button>
    </div>
    {error && <p role="alert" className="m-0 rounded border border-destructive/30 bg-destructive/10 p-2 text-sm text-destructive">{error}</p>}
    {status?.setup_error && <p role="alert" className="m-0 text-sm text-destructive">{status.setup_error}</p>}
    {doctor?.supported === false && <p className="m-0 text-sm text-fg-muted">This host cannot run the mobile runtime. Android setup currently requires Windows x64 with virtualization available.</p>}
    {doctor && !installed && !running && <section aria-label="Mobile setup" className="space-y-2 rounded border border-border-default bg-bg-panel p-3">
      <h3 className="m-0 text-sm font-semibold">Set up your shared Android phone</h3>
      <p className="m-0 text-xs text-fg-muted">Download the Android tools, emulator image and mobile agent runtime. Apps and sign-ins stay on this phone between tasks.</p>
      <ul className="m-0 list-none space-y-1 p-0 text-xs text-fg-muted">{(['adb', 'emulator', 'image', 'engine', 'video'] as const).map((key) => <li key={key}>{key === 'adb' ? 'Android tools' : key === 'engine' ? 'Agent runtime' : key === 'video' ? 'Live video' : key === 'image' ? 'Android image' : 'Emulator'}: {doctor[key] ? 'Ready' : 'Needed'}</li>)}</ul>
      <label className="flex items-start gap-2 text-xs"><input type="checkbox" checked={accepted} onChange={(event) => setAccepted(event.target.checked)} aria-label="Accept Android SDK license terms" />
        <span>I accept the <a className="underline" href="https://developer.android.com/studio/terms" target="_blank" rel="noreferrer">Android SDK license terms</a> and want to download the required components.</span></label>
      <Button size="sm" disabled={!accepted || !!busy || setupActive || doctor.supported === false} onClick={() => void perform('setup', async () => {
        await mobileRequest(`${path}/setup`, { licenses_accepted: true }); setDoctor(await mobileRequest(`${path}/doctor`));
      })}>Set up phone</Button>
      {setupActive && <p role="status" className="m-0 break-words text-xs text-fg-muted">{status?.setup === 'installing_engine' ? 'Installing the agent runtime…' : 'Installing Android tools and preparing your phone…'}</p>}
    </section>}
    {doctor?.acceleration && <details className="rounded border border-border-default px-3 py-2 text-xs text-fg-muted">
      <summary className="cursor-pointer font-medium">Host readiness: hardware acceleration</summary>
      <p className="mb-0 mt-2 whitespace-pre-wrap break-words">{doctor.acceleration}</p>
      <p className="mb-0 mt-2">If acceleration is unavailable on Windows, enable virtualization in your computer�s firmware and Windows Hypervisor Platform in Windows Features, then restart Windows and refresh mobile status.</p>
    </details>}
    <div className="flex flex-wrap gap-2">
      <Button size="sm" variant="outline" disabled={!!busy || setupActive || !status || doctor?.supported === false || (!running && !installed)} onClick={() => void perform(running ? 'stopping' : 'starting', async () => { await mobileRequest(`${path}/${running ? 'stop' : 'start'}`, {}); setEpoch(null); })}>
        {running ? <Square className="size-3.5" /> : <Play className="size-3.5" />}{running ? 'Stop phone' : 'Start phone'}
      </Button>
      {running && (held ? <>
        <Button size="sm" onClick={() => void perform('releasing', async () => { await mobileRequest(`${path}/release`, { viewer_id: viewerId, epoch, resume: true }); leaseEpoch.current = null; setEpoch(null); })} disabled={!!busy}>Hand back & resume</Button>
        <Button size="sm" variant="outline" onClick={() => void perform('releasing', async () => { await mobileRequest(`${path}/release`, { viewer_id: viewerId, epoch, resume: false }); leaseEpoch.current = null; setEpoch(null); })} disabled={!!busy}>Release control</Button>
      </> : <Button size="sm" variant="outline" disabled={!!busy} onClick={() => void perform('taking control', async () => { const claim = await mobileRequest<{ epoch: number; owner: string }>(`${path}/takeover`, { viewer_id: viewerId });
          if (!active.current || !viewActive.current) { await mobileRequest(`${path}/release`, { viewer_id: viewerId, epoch: claim.epoch, resume: false }); return; }
          leaseEpoch.current = claim.epoch; setEpoch(claim.epoch); setLeaseOwner(claim.owner); })}>Take control</Button>)}
    </div>
    {running && <>
      <div ref={surface} className={`relative flex min-h-64 flex-1 touch-none items-center justify-center overflow-hidden rounded border border-border-default bg-bg-canvas ${held ? 'cursor-crosshair' : ''}`} onPointerDown={down} onPointerUp={up} onPointerCancel={() => { pointer.current = null; }} aria-label={held ? 'Phone screen: tap or drag to interact' : 'Phone screen, view only'}>
        <canvas ref={canvas} className="absolute h-full w-full object-contain" />
        {!live && !videoError && <p className="pointer-events-none z-10 text-sm text-fg-muted">Connecting live view…</p>}
      </div>
      {videoError && <div className="flex items-center gap-2"><p role="alert" className="m-0 flex-1 text-xs text-fg-muted">{videoError}</p><Button size="sm" variant="outline" onClick={() => setVideoAttempt((value) => value + 1)}>Reconnect</Button></div>}
      {held && <div className="flex flex-wrap gap-1">
        <Button size="icon-sm" variant="outline" aria-label="Phone Back" disabled={!!busy} onClick={() => void perform('input', () => input('key', { key: 'back' }))}><ArrowLeft /></Button>
        <Button size="icon-sm" variant="outline" aria-label="Phone Home" disabled={!!busy} onClick={() => void perform('input', () => input('key', { key: 'home' }))}><Home /></Button>
        <input className={`${fieldClass} flex-1`} aria-label="Text to type on phone" value={text} onChange={(event) => setText(event.target.value)} placeholder="Type on phone" />
        <Button size="sm" variant="outline" disabled={!text || !!busy} onClick={() => void perform('input', async () => { await input('text', { text }); setText(''); })}>Type</Button>
      </div>}
      {held && <label className="flex items-center gap-2 text-xs text-fg-muted">Install APK (up to 256 MB)
        <input type="file" accept=".apk" aria-label="Install APK" disabled={!!busy} className="min-w-0 flex-1 text-xs" onChange={(event) => {
          const file = event.target.files?.[0]; event.target.value = '';
          if (!file) return;
          if (!file.name.toLowerCase().endsWith('.apk') || file.size > 256 * 1024 * 1024) { setError('Choose an APK file no larger than 256 MB.'); return; }
          void perform('installing APK', async () => {
            const query = new URLSearchParams({ viewer_id: viewerId, epoch: String(epoch), operation_id: crypto.randomUUID(), filename: file.name });
            const response = await fetch(buildApiUrl(`${path}/apk?${query}`), { method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/vnd.android.package-archive' }, body: file });
            if (!response.ok) { const body = await response.json().catch(() => null); throw new Error(typeof body?.detail === 'string' ? body.detail : 'APK installation failed.'); }
          });
        }} />
      </label>}
    </>}
    <form className="mt-auto flex shrink-0 flex-col gap-2 border-t border-border-default pt-3" onSubmit={(event) => { event.preventDefault(); void submit(); }}>
      <label htmlFor={`task-${nodeId}`} className="text-sm font-medium">Give this mobile agent a task</label>
      <textarea id={`task-${nodeId}`} className={`${fieldClass} resize-y`} rows={2} maxLength={12000} value={prompt} onChange={(event) => setPrompt(event.target.value)} placeholder="What should it do on the phone?" />
      <p className="m-0 text-xs text-fg-muted">Runs in this workflow. The phone is shared; tasks wait while another employee or person controls it.</p>
      <div className="flex items-center gap-2"><Button size="sm" type="submit" disabled={!prompt.trim() || !!busy || hasTask}>Run task</Button>
        {hasTask && <Button size="sm" type="button" variant="outline" disabled={!!busy} onClick={() => void perform('cancelling', async () => { setTask(await mobileRequest(`${path}/tasks/${encodeURIComponent(submission!)}/cancel`, {})); })}>Cancel task</Button>}
        {task && <span role="status" className="text-xs text-fg-muted">{taskState(task) || 'Submitted'}</span>}
      </div>
      {task?.error != null && <p role="alert" className="m-0 break-words text-sm text-destructive">{describe(task.error)}</p>}
      {task?.result != null && <pre className="m-0 max-h-48 overflow-auto whitespace-pre-wrap rounded bg-bg-panel p-2 text-xs">{describe(task.result)}</pre>}
    </form>
  </div>;
}
