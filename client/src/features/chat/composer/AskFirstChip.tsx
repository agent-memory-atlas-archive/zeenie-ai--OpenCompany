/**
 * Ask first, beside the message box (design handoff chat, "Composer"): while
 * it is on, anything the employee would send to someone waits for the
 * owner's OK on a card in the chat. It changes the workflow's live rule
 * (`set_ask_first`), with no restart. Turning it off asks first, since from
 * then on messages go without a card to stop them.
 */

import { ShieldCheck } from 'lucide-react';
import { useState } from 'react';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { Toggle } from '@/components/ui/toggle';
import { cn } from '@/lib/utils';

export function AskFirstChip({
  on,
  name,
  onChange,
  disabled = false,
  compact = false,
}: {
  on: boolean;
  name: string;
  onChange: (next: boolean) => void;
  disabled?: boolean;
  compact?: boolean;
}) {
  const [confirming, setConfirming] = useState(false);
  return (
    <>
      <Toggle
        variant="chips"
        size="sm"
        pressed={on}
        disabled={disabled}
        onPressedChange={(next) => (next ? onChange(true) : setConfirming(true))}
        aria-label="Ask first"
        title={on ? `${name} asks you before sending anything` : `${name} sends without asking`}
        className={cn(
          'gap-1.25 data-[state=on]:border-action-config-border data-[state=on]:bg-action-config-soft data-[state=on]:text-action-config-ink',
          compact && 'h-6 px-2',
        )}
      >
        <ShieldCheck aria-hidden className="size-3.25" strokeWidth={2} />
        Ask first
      </Toggle>
      <AlertDialog open={confirming} onOpenChange={setConfirming}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Let {name} send without asking?</AlertDialogTitle>
            <AlertDialogDescription>
              Messages, emails and invites {name} writes will go out straight away, without a card for you to check first.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Keep asking</AlertDialogCancel>
            <AlertDialogAction onClick={() => onChange(false)}>Turn off Ask first</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}

export default AskFirstChip;
