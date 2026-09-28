/**
 * TanStack Query persistence wiring.
 *
 * Persists the in-memory query cache to localStorage so that on hard
 * refresh the editor paints node specs / catalogues from last session
 * instead of waiting for the WebSocket handshake + bulk fetch.
 *
 * Buster: tied to root package.json version via Vite's __APP_VERSION__
 * compile-time constant (see vite.config.js). Bumping the version
 * automatically purges stale persisted entries on next load.
 *
 * Filter: only successfully resolved queries whose key starts with a
 * prefix in PERSISTED_KEY_PREFIXES (nodeSpec, node groups) are persisted.
 * Those carry data that only changes with a backend deploy. High-frequency
 * / per-session queries (status, parameters) are not persisted. Each
 * persisted prefix also needs a FOREVER setQueryDefaults in queryClient.ts,
 * because hydration applies the client's default options.
 *
 * References:
 *   https://tanstack.com/query/latest/docs/framework/react/plugins/persistQueryClient
 *   https://datatracker.ietf.org/doc/html/rfc5861 (stale-while-revalidate)
 */

import { createSyncStoragePersister } from '@tanstack/query-sync-storage-persister';
import type { Query } from '@tanstack/react-query';
import { BRAND_STORAGE_KEYS, readAndMigrateStorageValue } from './brandStorage';

declare const __APP_VERSION__: string;

const APP_VERSION =
  typeof __APP_VERSION__ !== 'undefined' ? __APP_VERSION__ : '0.0.0';

const STORAGE_KEYS = BRAND_STORAGE_KEYS.queryCache;
const storage = typeof window !== 'undefined' ? window.localStorage : undefined;

// Retain the warm cache across the product rename, then write only the
// canonical OpenCompany key from this point forward.
readAndMigrateStorageValue(storage, STORAGE_KEYS);

export const queryPersister = createSyncStoragePersister({
  storage,
  key: STORAGE_KEYS.canonical,
  // Throttle persistence writes so high-frequency cache mutations do
  // not thrash localStorage. Default is 1000ms which is fine.
});

/**
 * Buster string. Persisted cache from a different version is purged on
 * load. Use the package.json version so a release bump implicitly
 * invalidates everything.
 */
export const queryBuster = APP_VERSION;

/**
 * Persisted entries are valid for 24h. After that the cache is
 * discarded on hydrate and refetched fresh. RFC 5861 SWR window.
 */
export const queryPersistMaxAge = 24 * 60 * 60 * 1000;

/**
 * Filter predicate: persist a query only when its key's first element is
 * in this allowlist AND it resolved successfully. The allowlist names the
 * data that only changes with a backend deploy (node specs, node groups);
 * high-frequency or per-session data stays in memory. The predicate does
 * not read staleTime, so a new prefix here must be paired with a
 * setQueryDefaults entry in queryClient.ts.
 */
const PERSISTED_KEY_PREFIXES = [
  'nodeSpec',
  'nodeGroups',
  // credentialValues was previously persisted here for the cred-panel
  // form-field warm-start. Removed per OWASP HTML5 Security Cheat Sheet
  // / ASVS V9.9: a credentialValues entry contains the decrypted API
  // key value (populated by getStoredApiKey on the WS roundtrip), and
  // localStorage stores it in plaintext readable via DevTools on shared
  // / compromised machines. The in-memory TanStack Query cache (still
  // gcTime: FOREVER, see queryClient.ts) keeps the form populated for
  // the session lifetime; on reload the panel refetches — one
  // roundtrip cost, fine because the modal only opens on user action.
  //
  // credentialCatalogue is also NOT here -- it has its own IDB
  // warm-start in useCatalogueQuery.ts (idb-keyval). skillContent
  // stays in-memory only.
];

export function shouldPersistQuery(query: Query): boolean {
  const key = query.queryKey;
  if (!Array.isArray(key) || typeof key[0] !== 'string') return false;
  if (!PERSISTED_KEY_PREFIXES.includes(key[0])) return false;
  // Only persist successfully resolved queries; do not write error or
  // pending state to disk.
  return query.state.status === 'success';
}
