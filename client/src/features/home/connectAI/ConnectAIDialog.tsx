/**
 * "Connect an AI model", for an owner who has never made an API key (after
 * onboarding's Connect your AI step). It lists the featured AI providers
 * (components/onboarding/aiProviderLinks.ts: a hint and the page where the
 * key is made), each with its name, mark and whether it is connected from
 * the credential catalogue. Picking one opens that provider's own panel,
 * compact, as the Connect dialog does, with the link to its key page; the
 * dialog closes once that provider connects. Every other AI model (one on
 * this computer, another company) is in Settings > Connectors > AI.
 *
 * Opened through homeStore's openConnectAI: when a setup finds no AI model,
 * after a hire that cannot start without one, and from an employee's main
 * action (on their page, or in the Workspace header).
 */

import { ArrowLeft, Check, ExternalLink, ShieldCheck } from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { FEATURED_AI_PROVIDERS } from '@/components/onboarding/aiProviderLinks';
import PanelRenderer from '@/components/credentials/PanelRenderer';
import { rehydrateProvider } from '@/components/credentials/catalogueAdapter';
import { Button } from '@/components/ui/button';
import Modal from '@/components/ui/Modal';
import { Skeleton } from '@/components/ui/skeleton';
import { isConnected, useHomeCatalogue } from '../data/connectors';
import { useHomeStore } from '../state/homeStore';
import { pillToast } from '../ui/pillToast';
import { AppMark } from '../ui/primitives';

export function ConnectAIDialog() {
  const open = useHomeStore((s) => s.connectAIOpen);
  const closeConnectAI = useHomeStore((s) => s.closeConnectAI);
  const openSettings = useHomeStore((s) => s.openSettings);
  const { data } = useHomeCatalogue();
  const [pickedId, setPickedId] = useState<string | null>(null);

  const featured = useMemo(
    () =>
      FEATURED_AI_PROVIDERS.flatMap((entry) => {
        const provider = data?.providers.find((candidate) => candidate.id === entry.id);
        return provider ? [{ ...entry, provider }] : [];
      }),
    [data],
  );
  const picked = featured.find((entry) => entry.id === pickedId) ?? null;
  const pickedProvider = picked?.provider ?? null;
  const config = useMemo(() => (pickedProvider ? rehydrateProvider(pickedProvider) : null), [pickedProvider]);

  const close = useCallback(() => {
    setPickedId(null);
    closeConnectAI();
  }, [closeConnectAI]);

  // Only the picked provider going from not connected to connected counts.
  const connected = pickedProvider ? isConnected(pickedProvider) : false;
  const name = pickedProvider?.name ?? '';
  const seen = useRef({ id: pickedId, connected });
  useEffect(() => {
    const before = seen.current;
    seen.current = { id: pickedId, connected };
    if (!pickedId || before.id !== pickedId || before.connected || !connected) return;
    pillToast(`${name} is connected`);
    close();
  }, [pickedId, connected, name, close]);

  const seeAll = () => {
    close();
    openSettings('connectors', 'ai');
  };

  return (
    <Modal
      isOpen={open}
      onClose={close}
      title={picked ? `Connect ${picked.provider.name}` : 'Connect an AI model'}
      titleIcon={null}
      motion="spring"
      maxWidth="min(560px, calc(100vw - 2rem))"
      maxHeight="min(720px, calc(100vh - 2rem))"
      autoHeight
      className="rounded-panel bg-bg-panel shadow-dialog"
    >
      {picked ? (
        <div className="flex flex-col">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 px-5 pt-4">
            <Button
              variant="quiet"
              size="sm"
              onClick={() => setPickedId(null)}
              className="-ml-2 gap-1 text-row text-fg-muted"
            >
              <ArrowLeft aria-hidden />
              All AI models
            </Button>
            <span className="ml-auto text-sm text-fg-muted">
              No key yet?{' '}
              <a
                href={picked.keyUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1 font-medium text-fg-default underline-offset-4 hover:underline"
              >
                Get one from {picked.provider.name}
                <ExternalLink aria-hidden className="size-3.5" />
              </a>
            </span>
          </div>
          <PanelRenderer config={config} visible variant="compact" />
        </div>
      ) : (
        <div className="flex flex-col gap-4 px-6 pt-5 pb-6">
          <p className="m-0 text-base text-pretty text-fg-muted">
            Your employees need an AI model to think. Pick one, then paste the key they give you. It takes about a
            minute.
          </p>
          {data ? (
            <div className="grid grid-cols-[repeat(auto-fit,minmax(140px,1fr))] gap-2.5">
              {featured.map(({ id, hint, provider }) => (
                <Button
                  key={id}
                  variant="quiet"
                  onClick={() => setPickedId(id)}
                  className="h-auto flex-col gap-1.5 rounded-card border-border-default bg-bg-elevated px-3 py-4 whitespace-normal hover:border-border-strong"
                >
                  <AppMark name={provider.name} iconRef={provider.icon_ref} size="lg" />
                  <span className="text-base font-semibold text-fg-default">{provider.name}</span>
                  <span className="text-xs font-normal text-fg-muted">{hint}</span>
                  {isConnected(provider) && (
                    <span className="flex items-center gap-1 text-xs font-medium text-status-working-ink">
                      <Check aria-hidden className="size-3" strokeWidth={2.5} />
                      Connected
                    </span>
                  )}
                </Button>
              ))}
            </div>
          ) : (
            <div className="grid grid-cols-3 gap-2.5">
              {FEATURED_AI_PROVIDERS.map((entry) => (
                <Skeleton key={entry.id} className="h-30 rounded-card" />
              ))}
            </div>
          )}
          <div className="flex flex-wrap items-center gap-x-3 gap-y-2 border-t border-border-default pt-4">
            <span className="text-sm text-fg-muted">A model that runs on this computer, or another company?</span>
            <Button
              variant="quiet"
              size="sm"
              onClick={seeAll}
              className="ml-auto h-8 border-border-strong px-3.5 font-semibold text-fg-default"
            >
              See all AI models
            </Button>
          </div>
          <p className="m-0 flex items-center gap-1.5 text-xs text-fg-faint">
            <ShieldCheck aria-hidden className="size-3.5 shrink-0" />
            Your key is stored encrypted, and is only sent to the company you pick.
          </p>
        </div>
      )}
    </Modal>
  );
}

export default ConnectAIDialog;
