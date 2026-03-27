import type { AuthUser } from './types';

const ENV_AUTH_API_BASE: string | undefined = (import.meta as any).env?.VITE_AUTH_API_BASE;
const AUTH_APP_HEADER = { 'X-Auth-App': 'mine-city-reiki' };

function getAuthApiBase(): string {
  const isDev = Boolean((import.meta as any).env?.DEV);
  const raw = (ENV_AUTH_API_BASE ?? '').trim().replace(/\/+$/, '');
  if (raw) return raw;
  return isDev ? 'http://127.0.0.1:8787/api' : '/mine-trout-cash-api/api';
}

async function authFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const base = getAuthApiBase();
  const res = await fetch(`${base}${path}`, {
    credentials: 'include',
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...AUTH_APP_HEADER,
      ...(init?.headers || {}),
    },
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const error = typeof (data as any)?.error === 'string' ? (data as any).error : res.statusText;
    throw new Error(error || 'Auth API error');
  }
  return data as T;
}

export async function fetchAuthConfig(): Promise<boolean | null> {
  try {
    const data = await authFetch<{ enabled: boolean }>('/auth/config', { method: 'GET' });
    return Boolean(data?.enabled);
  } catch {
    return null;
  }
}

export async function fetchMe(): Promise<AuthUser | null> {
  try {
    const data = await authFetch<{ enabled?: boolean; user?: AuthUser }>('/auth/me', { method: 'GET' });
    if (data?.enabled === false) return null;
    return data?.user ?? null;
  } catch {
    return null;
  }
}

export async function login(username: string, password: string): Promise<AuthUser | null> {
  try {
    const data = await authFetch<{ user: AuthUser }>('/auth/login', {
      method: 'POST',
      body: JSON.stringify({ username, password, app: 'mine-city-reiki' }),
    });
    return data?.user ?? null;
  } catch {
    return null;
  }
}

export async function logout(): Promise<boolean> {
  try {
    await authFetch('/auth/logout', { method: 'POST' });
    return true;
  } catch {
    return false;
  }
}
