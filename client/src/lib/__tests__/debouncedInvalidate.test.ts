import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { QueryClient } from '@tanstack/react-query';
import { makeDebouncedInvalidator } from '../debouncedInvalidate';

let client: QueryClient;

beforeEach(() => {
  vi.useFakeTimers();
  client = new QueryClient();
  client.setQueryData(['team'], ['a']);
  client.setQueryData(['team', 'detail', 'a'], { name: 'Maya' });
});

afterEach(() => {
  client.clear();
  vi.clearAllTimers();
  vi.useRealTimers();
});

describe('makeDebouncedInvalidator', () => {
  it('keeps prefix invalidation for existing callers', () => {
    const invalidate = makeDebouncedInvalidator(['team'], 300);
    invalidate(client);
    vi.advanceTimersByTime(300);
    expect(client.getQueryState(['team'])?.isInvalidated).toBe(true);
    expect(client.getQueryState(['team', 'detail', 'a'])?.isInvalidated).toBe(true);
  });

  it('coalesces an exact-list refresh without invalidating unrelated details', () => {
    const invalidate = makeDebouncedInvalidator(['team'], 300, true);
    invalidate(client);
    vi.advanceTimersByTime(200);
    invalidate(client);
    vi.advanceTimersByTime(299);
    expect(client.getQueryState(['team'])?.isInvalidated).toBe(false);
    vi.advanceTimersByTime(1);
    expect(client.getQueryState(['team'])?.isInvalidated).toBe(true);
    expect(client.getQueryState(['team', 'detail', 'a'])?.isInvalidated).toBe(false);
  });
});
