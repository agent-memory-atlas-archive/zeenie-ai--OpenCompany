import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import MobileWorkspace from '../MobileWorkspace';
import { mobilePoint, mobileRequest } from '../api';

vi.mock('../video', () => ({ connectMobileVideo: () => () => {} }));
const fetchMock = vi.fn();
const nodes = [{ node_id: 'flow:mobile_agent:1', label: 'Phone assistant' }];
const ready = { supported: true, adb: true, emulator: true, image: true, engine: true, video: true, acceleration: 'WHPX is installed and usable.' };
let snapshot: Record<string, unknown>;
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

beforeEach(() => {
  sessionStorage.clear();
  snapshot = { running: false, control_state: 'idle', controller: null, epoch: 0, geometry: { width: 1080, height: 1920, rotation: 0 } };
  vi.stubGlobal('fetch', fetchMock);
  fetchMock.mockReset().mockImplementation(async (url: string) => json(url.endsWith('/doctor') ? ready : snapshot));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe('Mobile Workspace', () => {
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
    expect(fetchMock.mock.calls.every(([, options]) => options.method === 'GET')).toBe(true);
    expect(screen.getByText('Host readiness: hardware acceleration')).toBeInTheDocument();
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

  it('submits only to the displayed workflow and retries a lost response with the same id', async () => {
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
    await userEvent.type(screen.getByRole('textbox', { name: 'Give this mobile agent a task' }), 'Open settings');
    await userEvent.click(screen.getByRole('button', { name: 'Run task' }));
    await screen.findByText('Connection dropped');
    await userEvent.click(screen.getByRole('button', { name: 'Run task' }));
    await screen.findByText('Done');
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
    await userEvent.click(await screen.findByRole('button', { name: 'Take control' }));
    await userEvent.click(await screen.findByRole('button', { name: 'Phone Home' }));
    await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => url.endsWith('/input'))).toBe(true));
    const request = fetchMock.mock.calls.find(([url]) => url.endsWith('/input'))!;
    expect(JSON.parse(request[1].body)).toMatchObject({ epoch: 7, operation: 'key', parameters: { key: 'home' } });
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
