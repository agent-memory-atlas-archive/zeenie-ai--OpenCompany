import { QueryClient } from '@tanstack/react-query';
import { GC_TIME, STALE_TIME } from './queryConfig';

/**
 * Single app-wide QueryClient. Cache lives across route changes and modal
 * open/close cycles, which is what makes warm-start persistence (e.g. the
 * credentials catalogue) actually work. Exported as a module singleton so
 * imperative code (Zustand actions, workflowApi mutations) can invalidate
 * queries without needing React context.
 *
 * Defaults match the WS-push architecture: data is considered fresh for
 * STALE_TIME.SHORT by default and components trust the cache on mount.
 * The broadcast bridge in WebSocketContext keeps the cache in sync with
 * server state, so refetch-on-mount is unnecessary overhead.
 *
 * See docs-internal/credentials_scaling/architecture.md for cache shape.
 */
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
      refetchOnReconnect: false,
      refetchOnMount: false,
      retry: 1,
      staleTime: STALE_TIME.SHORT,
      gcTime: GC_TIME.DEFAULT,
    },
  },
});

// Per-prefix query defaults. Use setQueryDefaults so the options apply
// to ANY query that matches the prefix regardless of how it entered the
// cache -- including queries hydrated by PersistQueryClientProvider
// from localStorage, which would otherwise inherit the global default
// gcTime: 5min and get evicted after that interval. Nodespec /
// nodegroups data is immutable per backend deploy (the persistor's
// __APP_VERSION__ buster handles cross-deploy invalidation), so we keep
// it forever in memory. Without this, slice-subscribed consumers
// (useNodeSpec via useSyncExternalStore) read `undefined` after the
// 5-minute window and every canvas node loses its icon and handles.
queryClient.setQueryDefaults(['nodeSpec'], {
  staleTime: STALE_TIME.FOREVER,
  gcTime: GC_TIME.FOREVER,
});
queryClient.setQueryDefaults(['nodeGroups'], {
  staleTime: STALE_TIME.FOREVER,
  gcTime: GC_TIME.FOREVER,
});

// Credential values are NOT persisted (removed from the queryPersist.ts
// whitelist: they hold decrypted API keys, which must never reach
// localStorage). The in-memory entry still needs FOREVER: the per-call
// FOREVER staleTime did not prevent gcTime: 5min eviction, so credential
// panel form fields silently went blank after idle. Anchor the contract
// at the prefix root.
queryClient.setQueryDefaults(['credentialValues'], {
  staleTime: STALE_TIME.FOREVER,
  gcTime: GC_TIME.FOREVER,
});

// Skill content is immutable per backend deploy (DB seeded from SKILL.md
// once); Master Skill editor reads via fetchQuery and consumers depend
// on the cache surviving panel close/reopen cycles. Not persisted to
// localStorage -- in-memory FOREVER is sufficient.
queryClient.setQueryDefaults(['skillContent'], {
  staleTime: STALE_TIME.FOREVER,
  gcTime: GC_TIME.FOREVER,
});

// credentialCatalogue is intentionally NOT given a setQueryDefaults
// override. It has its own IDB warm-start path in useCatalogueQuery.ts
// and the per-call `gcTime: 10 * 60_000` there is a deliberate bounded-
// memory cap (IDB hydrate restores it on next mount).
