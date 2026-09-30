/** One app-level host for browsing, connecting and managing credentials. */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ArrowLeft, ExternalLink, ShieldCheck } from 'lucide-react';
import { toast } from 'sonner';
import { useShellMode } from '@/app/ShellModeSwitch';
import { FEATURED_AI_PROVIDERS } from '@/components/onboarding/aiProviderLinks';
import Modal from '@/components/ui/Modal';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { useShellDialogsStore, type CredentialsIntent, type CredentialsOptions } from '@/stores/shellDialogsStore';
import CredentialsBrowser from './CredentialsBrowser';
import { isConnected, useCredentialsCatalogue } from './catalogue';
import { rehydrateProvider } from './catalogueAdapter';
import PanelRenderer from './PanelRenderer';

interface Props { visible: boolean; onClose: () => void }

/** Each explicit open starts fresh navigation; switching mode does not. */
export default function CredentialsModal(props: Props) {
  const options = useShellDialogsStore((s) => s.credentialsOptions);
  const requestId = useShellDialogsStore((s) => s.credentialsRequestId);
  return <CredentialsSession key={requestId} {...props} options={options} />;
}

function CredentialsSession({ visible, onClose, options }: Props & { options: CredentialsOptions }) {
  const view = useCredentialsCatalogue();
  const showTechnicalSections = useShellMode() === 'dev';
  const [browsing, setBrowsing] = useState(!options.providerId);
  const [selection, setSelection] = useState<{ id: string; intent: CredentialsIntent } | null>(
    options.providerId ? { id: options.providerId, intent: options.intent ?? 'manage' } : null,
  );
  const browserOpener = useRef<HTMLElement | null>(null);
  const providerOpener = useRef<HTMLElement | null>(null);
  const sessionOpener = useRef<HTMLElement | null>(null);
  const provider = view.providers.find((p) => p.id === selection?.id) ?? null;
  const config = useMemo(() => provider ? rehydrateProvider(provider) : null, [provider]);
  const featured = FEATURED_AI_PROVIDERS.find((p) => p.id === provider?.id);
  const connected = provider ? isConnected(provider) : false;
  const seen = useRef({ id: selection?.id, known: false, connected: false });

  const closeProvider = useCallback(() => {
    if (browsing) setSelection(null);
    else onClose();
  }, [browsing, onClose]);

  useEffect(() => {
    const before = seen.current;
    seen.current = { id: selection?.id, known: visible && provider !== null, connected };
    if (!visible || !provider || before.id !== selection?.id || !before.known || before.connected || !connected) return;
    toast.success(`${provider.name} is connected`);
    if (selection?.intent === 'connect') {
      if (options.intent === 'connect') onClose();
      else closeProvider();
    }
  }, [visible, provider, selection, connected, options.intent, onClose, closeProvider]);

  const allProviders = () => { setBrowsing(true); setSelection(null); };
  const aiSetup = options.categoryId === 'ai' && options.intent === 'connect';
  const hasData = Boolean(view.catalogue.data) && !view.isLoading;

  return (
    <>
      <Modal
        isOpen={visible && browsing}
        onClose={onClose}
        title={aiSetup ? 'Connect an AI model' : 'Connectors'}
        titleIcon={<ShieldCheck className="size-4" />}
        motion="spring"
        maxWidth="min(1040px, calc(100vw - 2rem))"
        maxHeight="min(760px, calc(100dvh - 2rem))"
        onOpenAutoFocus={() => {
          browserOpener.current = document.activeElement as HTMLElement | null;
          sessionOpener.current ??= browserOpener.current;
        }}
        onCloseAutoFocus={(event) => {
          if (sessionOpener.current?.isConnected) {
            event.preventDefault();
            sessionOpener.current.focus();
          }
        }}
      >
        {aiSetup && (
          <div className="px-5 pt-5 sm:px-8">
            <p className="text-sm text-fg-muted">
              Your employees need an AI model to think. Pick a provider, then connect with its API key,
              or choose a model that runs on this computer.
            </p>
          </div>
        )}
        <CredentialsBrowser catalogue={view} initialCategory={options.categoryId}
          onConnect={(id, intent = 'connect') => setSelection({ id, intent })} />
      </Modal>

      <Modal
        isOpen={visible && selection !== null}
        onClose={closeProvider}
        title={provider ? `${selection?.intent === 'manage' ? 'Manage' : 'Connect'} ${provider.name}` : 'Connector'}
        titleIcon={null}
        motion="spring"
        maxWidth="min(640px, calc(100vw - 2rem))"
        maxHeight="min(800px, calc(100dvh - 2rem))"
        autoHeight
        onOpenAutoFocus={() => {
          providerOpener.current = document.activeElement as HTMLElement | null;
          sessionOpener.current ??= providerOpener.current;
        }}
        onCloseAutoFocus={(event) => {
          // Closing both layers returns to the screen, not to a disappearing card.
          event.preventDefault();
          if ((!visible && browsing) || (browsing && providerOpener.current === sessionOpener.current)) return;
          if (providerOpener.current?.isConnected) providerOpener.current.focus();
        }}
      >
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2 px-5 pt-4">
          <Button variant="quiet" size="sm" onClick={allProviders} className="-ml-2 gap-1">
            <ArrowLeft aria-hidden className="size-4" />
            {options.categoryId === 'ai' ? 'All AI models' : 'All connectors'}
          </Button>
          {featured && (
            <a href={featured.keyUrl} target="_blank" rel="noopener noreferrer"
              className="ml-auto inline-flex items-center gap-1 text-sm text-fg-default underline-offset-4 hover:underline">
              Get a key from {provider?.name}<ExternalLink aria-hidden className="size-3.5" />
            </a>
          )}
        </div>
        {!hasData ? (
          view.isError ? <CatalogueError onRetry={() => void view.refetch()} /> : (
            <div className="space-y-3 p-5" role="status" aria-label="Loading connector">
              <Skeleton className="h-6 w-48" /><Skeleton className="h-24 w-full" />
            </div>
          )
        ) : !provider ? (
          <div className="p-5">
            <Alert>
              <AlertTitle>Connector unavailable</AlertTitle>
              <AlertDescription>This connector is missing or disabled. Choose another connector from the catalogue.</AlertDescription>
            </Alert>
          </div>
        ) : (
          <>
            <div className="space-y-2 px-5 pt-3 text-sm text-fg-muted">
              {(provider.description || featured?.hint) && <p>{provider.description || featured?.hint}</p>}
              <div className="flex flex-wrap items-center gap-2">
                {provider.publisher && <span>by {provider.publisher}</span>}
                {provider.verified && <Badge variant="outline">Verified</Badge>}
                <Badge variant={connected ? 'success' : 'secondary'}>{connected ? 'Connected' : 'Not connected'}</Badge>
                {connected && provider.account_label && <span>{provider.account_label}</span>}
                {provider.runs_locally && <span>Runs on this computer</span>}
              </div>
            </div>
            <PanelRenderer config={config} visible={visible} showTechnicalSections={showTechnicalSections} />
          </>
        )}
      </Modal>
    </>
  );
}

function CatalogueError({ onRetry }: { onRetry: () => void }) {
  return (
    <div className="p-5">
      <Alert variant="destructive">
        <AlertTitle>Couldn't reach the credentials server</AlertTitle>
        <AlertDescription>Check your connection and try again.</AlertDescription>
      </Alert>
      <Button variant="outline" onClick={onRetry} className="mt-3">Try again</Button>
    </div>
  );
}
