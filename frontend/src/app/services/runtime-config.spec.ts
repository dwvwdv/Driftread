import { environment } from '../../environments/environment';
import { runtimeSupabaseConfig } from './runtime-config';

describe('runtimeSupabaseConfig', () => {
  afterEach(() => {
    window.__env = undefined;
  });

  it('falls back to environment.ts when window.__env was never set', () => {
    window.__env = undefined;

    expect(runtimeSupabaseConfig()).toEqual({
      supabaseUrl: environment.supabaseUrl,
      supabaseAnonKey: environment.supabaseAnonKey,
    });
  });

  it('falls back to environment.ts when env.js shipped its local-dev empty defaults', () => {
    window.__env = { supabaseUrl: '', supabaseAnonKey: '' };

    expect(runtimeSupabaseConfig()).toEqual({
      supabaseUrl: environment.supabaseUrl,
      supabaseAnonKey: environment.supabaseAnonKey,
    });
  });

  it('prefers the runtime-injected values once the Docker entrypoint rendered real ones', () => {
    window.__env = { supabaseUrl: 'https://proj.supabase.co', supabaseAnonKey: 'anon-key' };

    expect(runtimeSupabaseConfig()).toEqual({
      supabaseUrl: 'https://proj.supabase.co',
      supabaseAnonKey: 'anon-key',
    });
  });

  it('falls back field-by-field when only one of the two was actually rendered', () => {
    window.__env = { supabaseUrl: 'https://proj.supabase.co', supabaseAnonKey: '' };

    expect(runtimeSupabaseConfig()).toEqual({
      supabaseUrl: 'https://proj.supabase.co',
      supabaseAnonKey: environment.supabaseAnonKey,
    });
  });
});
