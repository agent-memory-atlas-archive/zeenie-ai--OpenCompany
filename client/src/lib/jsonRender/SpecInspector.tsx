/**
 * A developer's view of a generated UI: the spec as JSON, and the patch
 * stream that builds it (one JSON patch per line, as the chat protocol
 * streams it). Render it only in development builds
 * (`import.meta.env.DEV && <SpecInspector ... />`) so release builds drop it.
 */

import { Code } from 'lucide-react';
import { useMemo, useState } from 'react';
import type { Spec } from '@json-render/core';
import { Button } from '@/components/ui/button';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { specToPatches } from './reveal';

const CODE_CLASS =
  'm-0 max-h-70 overflow-auto rounded-row border border-border-default bg-bg-app px-3.5 py-3 font-mono text-2xs leading-normal whitespace-pre text-fg-muted';

export function SpecInspector({ spec, label = 'Layout JSON' }: { spec: Spec; label?: string }) {
  const [open, setOpen] = useState(false);
  const specJson = useMemo(() => JSON.stringify(spec, null, 2), [spec]);
  const patchLines = useMemo(
    () =>
      specToPatches(spec)
        .map((patch) => JSON.stringify(patch))
        .join('\n'),
    [spec],
  );
  return (
    <div className="flex flex-col gap-2">
      <Button
        variant="quiet"
        size="xs"
        onClick={() => setOpen((on) => !on)}
        title="See what the assistant generated"
        aria-expanded={open}
        className="gap-1.5 self-start font-mono text-2xs font-normal text-fg-faint hover:border-border-default hover:bg-transparent hover:text-fg-muted"
      >
        <Code aria-hidden />
        {open ? `Hide ${label}` : label}
      </Button>
      {open && (
        <Tabs defaultValue="spec" className="gap-2">
          <TabsList variant="line" className="h-7">
            <TabsTrigger value="spec" className="font-mono text-2xs">
              spec.json
            </TabsTrigger>
            <TabsTrigger value="patches" className="font-mono text-2xs">
              patches.jsonl
            </TabsTrigger>
          </TabsList>
          <TabsContent value="spec">
            <pre className={CODE_CLASS}>{specJson}</pre>
          </TabsContent>
          <TabsContent value="patches">
            <pre className={CODE_CLASS}>{patchLines}</pre>
          </TabsContent>
        </Tabs>
      )}
    </div>
  );
}
