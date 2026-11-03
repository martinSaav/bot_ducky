const STORAGE_KEY = 'dashboard_token';

/**
 * Token for the bot's /api and /ws (DASHBOARD_TOKEN on the bot).
 * Opened once as /dashboard/?token=..., then remembered by the browser.
 */
export function dashboardToken(): string {
  const fromUrl = new URLSearchParams(window.location.search).get('token');
  if (fromUrl) {
    try {
      localStorage.setItem(STORAGE_KEY, fromUrl);
    } catch {
      // private window or blocked storage: the URL token still works
    }
    return fromUrl;
  }
  try {
    return localStorage.getItem(STORAGE_KEY) ?? '';
  } catch {
    return '';
  }
}
