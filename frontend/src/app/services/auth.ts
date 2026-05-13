import { Injectable, signal } from '@angular/core';
import { createClient, Session, SupabaseClient } from '@supabase/supabase-js';
import { environment } from '../../environments/environment';

@Injectable({ providedIn: 'root' })
export class AuthService {
  private client: SupabaseClient | null = null;
  session = signal<Session | null>(null);

  constructor() {
    if (environment.supabaseUrl && environment.supabaseAnonKey) {
      this.client = createClient(environment.supabaseUrl, environment.supabaseAnonKey, {
        auth: { persistSession: true, autoRefreshToken: true },
      });
      this.client.auth.getSession().then(({ data }) => this.session.set(data.session));
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

  async signUp(email: string, password: string): Promise<{ error: string | null }> {
    if (!this.client) return { error: 'Auth not configured' };
    const { error } = await this.client.auth.signUp({ email, password });
    return { error: error?.message ?? null };
  }

  async signIn(email: string, password: string): Promise<{ error: string | null }> {
    if (!this.client) return { error: 'Auth not configured' };
    const { error } = await this.client.auth.signInWithPassword({ email, password });
    return { error: error?.message ?? null };
  }

  async signOut(): Promise<void> {
    await this.client?.auth.signOut();
  }
}
