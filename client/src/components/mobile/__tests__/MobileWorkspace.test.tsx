import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import MobileWorkspace from '../MobileWorkspace';
import { mobilePoint, mobileRequest } from '../api';

const { connectVideo } = vi.hoisted(() => ({ connectVideo: vi.fn(() => () => {}) }));
vi.mock('../video', () => ({ connectMobileVideo: connectVideo }));
const fetchMock = vi.fn();
const nodes = [{ node_id: 'flow:mobile_agent:1', label: 'Phone assistant' }];
const ready = { supported: true, adb: true, emulator: true, image: true, engine: true, video: true, acceleration: 'WHPX is installed and usable.' };
let snapshot: Record<string, unknown>;
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

beforeEach(() => {
  sessionStorage.clear();
  connectVideo.mockClear();
  snapshot = { running: false, control_state: 'idle', controller: null, epoch: 0, geometry: { width: 1080, height: 1920, rotation: 0 } };
  vi.stubGlobal('fetch', fetchMock);
  fetchMock.mockReset().mockImplementation(async (url: string) => json(url.endsWith('/doctor') ? ready : snapshot));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe('Mobile Workspace', () => {
  it('keeps the phone connected and preserves a task draft when secondary panels collapse', async () => {
    snapshot.running = true;
    render(<MobileWorkspace workflowId="flow" nodes={nodes} />);
    await screen.findByText('Ready');
    await waitFor(() => expect(connectVideo).toHaveBeenCalledOnce());
    const canvas = screen.getByLabelText('Phone screen, view only').querySelector('canvas');
    const summary = screen.getByText('Ask AI to use the phone', { selector: 'summary' });
    expect(screen.getByRole('textbox', { name: 'Ask AI to use the phone' })).not.toBeVisible();
    await userEvent.click(summary);
    await userEvent.type(screen.getByRole('textbox', { name: 'Ask AI to use the phone' }), 'Open Settings');
    await userEvent.click(summary);
    await userEvent.click(screen.getByRole('button', { name: 'Phone controls' }));
    expect(screen.getByRole('button', { name: 'Stop phone' })).toBeVisible();
    await userEvent.click(screen.getByRole('button', { name: 'Phone controls' }));
    await userEvent.click(summary);
    expect(screen.getByRole('textbox', { name: 'Ask AI to use the phone' })).toHaveValue('Open Settings');
    expect(screen.getByLabelText('Phone screen, view only').querySelector('canvas')).toBe(canvas);
    expect(connectVideo).toHaveBeenCalledOnce();
  });
  it('clears a failed status check when the phone reconnects', async () => {
    let polls = 0;
    snapshot.running = true;
    fetchMock.mockImplementation(async (url: string) => {
      if (url.endsWith('/status') && polls++ === 0) throw new Error('Start the device before using it');
      return json(url.endsWith('/doctor') ? ready : snapshot);
    });
    render(<MobileWorkspace workflowId="flow" nodes={nodes} />);
    await screen.findByText(/Retrying automatically/);
    await waitFor(() => expect(screen.queryByText(/Retrying automatically/)).toBeNull(), { timeout: 4000 });
    expect(screen.getByText('Ready')).toBeInTheDocument();
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('explains how to start and disables AI tasks while the phone is off', async () => {
    render(<MobileWorkspace workflowId="flow" nodes={nodes} />);
    await screen.findByText('Phone is off');
    await userEvent.click(screen.getByText('Ask AI to use the phone', { selector: 'summary' }));
    await userEvent.type(screen.getByRole('textbox', { name: 'Ask AI to use the phone' }), 'Open Settings');
    expect(screen.getByRole('button', { name: 'Run task' })).toBeDisabled();
  });
  it('requires a saved workflow and an explicitly discovered Mobile node', () => {
    const view = render(<MobileWorkspace nodes={nodes} />);
    expect(screen.getByText(/Save this workflow/)).toBeInTheDocument();
    view.rerender(<MobileWorkspace workflowId="flow" nodes={[]} />);
    expect(screen.getByText(/Add a Mobile Agent/)).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('only reads status on open; never starts or installs automatically', async () => {
    render(<MobileWorkspace workflowId="flow" nodes={nodes} />);
    await screen.findByRole('button', { name: 'Start phone' });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    await userEvent.click(screen.getByRole('button', { name: 'Phone controls' }));
    expect(fetchMock.mock.calls.every(([, options]) => options.method === 'GET')).toBe(true);
    expect(screen.getByText('Help & diagnostics')).toBeInTheDocument();
    expect(screen.getByText('WHPX is installed and usable.')).toBeInTheDocument();
  });

  it('requires explicit license acceptance before setup', async () => {
    fetchMock.mockImplementation(async (url: string) => json(url.endsWith('/doctor') ? { supported: true } : snapshot));
    render(<MobileWorkspace workflowId="flow" nodes={nodes} />);
    const setup = await screen.findByRole('button', { name: 'Set up phone' });
    expect(setup).toBeDisabled();
    await userEvent.click(screen.getByRole('checkbox', { name: 'Accept Android SDK license terms' }));
    await userEvent.click(setup);
    await waitFor(() => expect(fetchMock.mock.calls.some(([url, options]) => url.endsWith('/setup') && JSON.parse(options.body).licenses_accepted === true)).toBe(true));
  });

  it('shows active setup and bounded activity even when all tools are ready', async () => {
    const now = Date.now() / 1000;
    snapshot = { ...snapshot, setup: 'installing_device', setup_progress: {
      message: 'Downloading Android image', started_at: now - 90, updated_at: now - 10,
      events: Array.from({ length: 25 }, (_, index) => ({ at: now - 25 + index, message: `Step ${index}` })),
    } };
    render(<MobileWorkspace workflowId="flow" nodes={nodes} />);
    await screen.findByText('Setting up');
    expect(screen.getByRole('region', { name: 'Mobile setup' })).toBeInTheDocument();
    expect(screen.getByText('Downloading Android image')).toBeInTheDocument();
    expect(screen.getByText(/Elapsed: 1m/)).toBeInTheDocument();
    expect(screen.getByText(/Last update: \d+s/)).toBeInTheDocument();
    expect(screen.queryByText(/— Step 0$/)).toBeNull();
    expect(screen.getByText(/Step 24$/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Set up phone' })).toBeDisabled();
  });

  it('retains completed setup activity without install controls', async () => {
    const now = Date.now() / 1000;
    snapshot = { ...snapshot, setup: 'ready', setup_progress: { message: 'Phone is ready', started_at: now - 120, finished_at: now - 30, updated_at: now - 30, events: [{ at: now - 30, message: 'Installation finished' }] } };
    render(<MobileWorkspace workflowId="flow" nodes={nodes} />);
    await screen.findByText('Setup complete');
    expect(screen.getByText('Elapsed: 1m 30s')).toBeInTheDocument();
    expect(screen.getByText(/Installation finished/)).toBeInTheDocument();
    expect(screen.queryByRole('checkbox', { name: 'Accept Android SDK license terms' })).toBeNull();
  });

  it('keeps interrupted setup visible after reopening and offers an explicit retry', async () => {
    snapshot = { ...snapshot, setup: 'interrupted', setup_error: 'Setup stopped when the host restarted.' };
    render(<MobileWorkspace workflowId="flow" nodes={nodes} />);
    await screen.findByText('Setup stopped when the host restarted.');
    const retry = screen.getByRole('button', { name: 'Retry setup' });
    expect(retry).toBeDisabled();
    await userEvent.click(screen.getByRole('checkbox', { name: 'Accept Android SDK license terms' }));
    expect(retry).toBeEnabled();
  });

  it('submits only to the displayed workflow and retries a lost response with the same id', async () => {
    snapshot.running = true;
    let attempts = 0;
    fetchMock.mockImplementation(async (url: string, options: RequestInit) => {
      if (url.endsWith('/tasks') && options.method === 'POST') {
        attempts++;
        if (attempts === 1) throw new Error('Connection dropped');
        return json({ status: 'queued', invocation_id: 'inv-1' });
      }
      if (url.includes('/tasks/')) return json({ status: 'completed', result: 'Done' });
      return json(url.endsWith('/doctor') ? ready : snapshot);
    });
    render(<MobileWorkspace workflowId="home-flow" nodes={nodes} />);
    await userEvent.click(screen.getByText('Ask AI to use the phone', { selector: 'summary' }));
    await userEvent.type(screen.getByRole('textbox', { name: 'Ask AI to use the phone' }), 'Open settings');
    await userEvent.click(screen.getByRole('button', { name: 'Run task' }));
    await screen.findByText('Connection dropped');
    await userEvent.click(screen.getByRole('button', { name: 'Run task' }));
    await screen.findByText('Done', { selector: 'pre' });
    const requests = fetchMock.mock.calls.filter(([url, options]) => url.endsWith('/tasks') && options.method === 'POST');
    expect(requests).toHaveLength(2);
    expect(requests[0][0]).toBe('/api/mobile/home-flow/flow%3Amobile_agent%3A1/tasks');
    expect(JSON.parse(requests[0][1].body)).toEqual(JSON.parse(requests[1][1].body));
    expect(JSON.parse(requests[0][1].body)).toMatchObject({ prompt: 'Open settings' });
  });

  it('unlocks input only after an opaque lease grant matches status and sends epoch', async () => {
    snapshot.running = true;
    fetchMock.mockImplementation(async (url: string) => {
      if (url.endsWith('/takeover')) {
        snapshot = { ...snapshot, controller: 'viewer:server-hash', control_state: 'human', epoch: 7 };
        return json({ owner: 'viewer:server-hash', epoch: 7 });
      }
      return json(url.endsWith('/doctor') ? ready : snapshot);
    });
    const view = render(<MobileWorkspace workflowId="flow" nodes={nodes} />);
    expect(screen.queryByRole('button', { name: 'Phone Home' })).toBeNull();
    await userEvent.click(await screen.findByRole('button', { name: 'Use phone' }));
    await userEvent.click(await screen.findByRole('button', { name: 'Phone Home' }));
    await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => url.endsWith('/input'))).toBe(true));
    const request = fetchMock.mock.calls.find(([url]) => url.endsWith('/input'))!;
    expect(JSON.parse(request[1].body)).toMatchObject({ epoch: 7, operation: 'key', parameters: { key: 'home' } });
    await userEvent.click(screen.getByRole('button', { name: 'Phone controls' }));
    expect(screen.getByLabelText('Install APK')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Install APK'), { target: { files: [new File(['bad'], 'not-an-app.txt')] } });
    expect(screen.getByText('Choose an APK file no larger than 256 MB.')).toBeInTheDocument();
    view.rerender(<MobileWorkspace workflowId="flow" nodes={nodes} visible={false} />);
    await waitFor(() => expect(fetchMock.mock.calls.some(([url, options]) => url.endsWith('/release') && JSON.parse(options.body).epoch === 7)).toBe(true));
  });
});

describe('mobile transport boundaries', () => {
  it('maps aspect-fit coordinates and refuses letterbox input', () => {
    const geometry = { width: 100, height: 200 };
    expect(mobilePoint(100, 100, 200, 200, geometry)).toEqual({ x: 50, y: 100 });
    expect(mobilePoint(5, 100, 200, 200, geometry)).toBeNull();
    expect(mobilePoint(0, 0, 0, 0, geometry)).toBeNull();
  });
  it('surfaces structured device errors', async () => {
    fetchMock.mockResolvedValue(json({ detail: { code: 'stale_lease', message: 'Device control changed.' } }, 409));
    await expect(mobileRequest('/api/mobile/test')).rejects.toThrow('Device control changed.');
  });
});
