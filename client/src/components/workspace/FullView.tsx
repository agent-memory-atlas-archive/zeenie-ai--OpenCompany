import { useEffect, useRef, useState, type ReactNode } from 'react';
import { Maximize2, Minimize2 } from 'lucide-react';
import { Button } from '@/components/ui/button';

/** Fullscreen the existing viewer; never remount its canvas or connection. */
export function FullView({ label, children }: { label: string; children: ReactNode }) {
  const root = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const [full, setFull] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => {
    const element = root.current;
    let wasFull = false;
    const changed = () => {
      const next = document.fullscreenElement === element;
      setFull(next);
      if (wasFull && !next) button.current?.focus();
      wasFull = next;
    };
    document.addEventListener('fullscreenchange', changed);
    return () => {
      document.removeEventListener('fullscreenchange', changed);
      if (document.fullscreenElement === element) void document.exitFullscreen().catch(() => {});
    };
  }, []);
  const toggle = async () => {
    setError('');
    try {
      if (document.fullscreenElement === root.current) await document.exitFullscreen();
      else if (root.current?.requestFullscreen) await root.current.requestFullscreen();
      else setError('Full view is unavailable in this browser. Use the Workspace expand button instead.');
    } catch {
      setError('Could not open full view. Try again or use the Workspace expand button.');
    }
  };
  return <div ref={root} data-full-view={full} className={`group/fullview flex min-h-0 min-w-0 flex-1 flex-col gap-2 bg-bg-app ${full ? 'h-screen w-screen overflow-hidden p-3' : ''}`}>
    <div className="flex shrink-0 items-center justify-end gap-2">
      {full && <span className="mr-auto text-sm font-medium">{label} <span className="text-xs font-normal text-fg-muted">· Esc to return</span></span>}
      <Button ref={button} size="sm" variant="outline" aria-label={full ? `Exit ${label.toLowerCase()} full view` : `${label} full view`} aria-pressed={full} onClick={() => void toggle()}>
        {full ? <Minimize2 className="size-4" /> : <Maximize2 className="size-4" />}{full ? 'Exit full view' : 'Full view'}
      </Button>
    </div>
    {error && <p role="alert" className="m-0 text-xs text-destructive">{error}</p>}
    {children}
  </div>;
}
