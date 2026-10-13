import { Injectable, signal } from '@angular/core';
import { createClient, Session, SupabaseClient } from '@supabase/supabase-js';
import { runtimeSupabaseConfig } from './runtime-config';

@Injectable({ providedIn: 'root' })
export class AuthService {
  private client: SupabaseClient | null = null;
  session = signal<Session | null>(null);
  ready: Promise<void> = Promise.resolve();

  constructor() {
    const { supabaseUrl, supabaseAnonKey } = runtimeSupabaseConfig();
    if (supabaseUrl && supabaseAnonKey) {
      this.client = createClient(supabaseUrl, supabaseAnonKey, {
        auth: { persistSession: true, autoRefreshToken: true },
      });
      this.ready = this.client.auth.getSession().then(({ data }) => {
        this.session.set(data.session);
      });
      this.client.auth.onAuthStateChange((_e, session) => this.session.set(session));
    }
  }

  isConfigured(): boolean {
    return this.client !== null;
  }

  get accessToken(): string | null {
    return this.session()?.access_token ?? null;
  }

  get userEmail(): string | null {
    return this.session()?.user?.email ?? null;
  }

  async signIn(email: string, password: string): Promise<{ error: string | null }> {
    if (!this.client) return { error: 'Auth not configured' };
    try {
      const { data, error } = await this.client.auth.signInWithPassword({ email, password });
      if (!error) this.session.set(data.session);
      return { error: error?.message ?? null };
    } catch {
      return { error: '登入服務暫時無法使用，請稍後再試' };
    }
  }

  async signOut(): Promise<void> {
    await this.client?.auth.signOut();
  }
}
