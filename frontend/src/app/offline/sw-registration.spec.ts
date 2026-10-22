import { RUNTIME_CONFIG, SW_SCRIPT, registerServiceWorker } from './sw-registration';

function fakeContainer(controller: unknown = null) {
  const listeners: Record<string, () => void> = {};
  return {
    controller,
    register: vi.fn().mockResolvedValue({}),
    addEventListener: vi.fn((type: string, cb: () => void) => (listeners[type] = cb)),
    fire: (type: string) => listeners[type]?.(),
  };
}

describe('registerServiceWorker', () => {
  it('registers the Angular worker at the app root and warms env.js when controlled', async () => {
    const container = fakeContainer({});
    const warm = vi.fn().mockResolvedValue(undefined);
    const ok = await registerServiceWorker({
      container: container as unknown as ServiceWorkerContainer,
      enabled: true,
      warm,
    });
    expect(ok).toBe(true);
    expect(container.register).toHaveBeenCalledWith(SW_SCRIPT, { scope: './' });
    expect(warm).toHaveBeenCalledWith(RUNTIME_CONFIG);
  });

  it('waits for the first controllerchange before warming the runtime config', async () => {
    const container = fakeContainer(null);
    const warm = vi.fn().mockResolvedValue(undefined);
    await registerServiceWorker({
      container: container as unknown as ServiceWorkerContainer,
      enabled: true,
      warm,
    });
    expect(warm).not.toHaveBeenCalled();
    container.fire('controllerchange');
    expect(warm).toHaveBeenCalledWith('env.js');
  });

  it('does nothing in dev mode or without service worker support', async () => {
    const container = fakeContainer({});
    const warm = vi.fn();
    expect(
      await registerServiceWorker({
        container: container as unknown as ServiceWorkerContainer,
        enabled: false,
        warm,
      }),
    ).toBe(false);
    expect(await registerServiceWorker({ container: undefined, enabled: true, warm })).toBe(false);
    expect(container.register).not.toHaveBeenCalled();
  });

  it('a failed registration leaves the app working online', async () => {
    const container = fakeContainer({});
    container.register.mockRejectedValue(new Error('SecurityError'));
    const warm = vi.fn();
    expect(
      await registerServiceWorker({
        container: container as unknown as ServiceWorkerContainer,
        enabled: true,
        warm,
      }),
    ).toBe(false);
    expect(warm).not.toHaveBeenCalled();
  });
});
