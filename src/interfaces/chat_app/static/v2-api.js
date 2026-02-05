/**
 * API Integration for Archi Chat UI
 * 
 * Provides client-side utilities for interacting with the PostgreSQL-based
 * API endpoints for:
 * - User preferences (theme, preferred model)
 * - BYOK API key management  
 * - Document selection preferences
 * - Analytics
 */

// Storage keys
const USER_PREFERENCES_KEY = 'archi_user_preferences';
const THEME_KEY = 'archi_theme';

/**
 * API client for user management
 */
class V2ApiClient {
  constructor() {
    this.baseUrl = '/api';
    this.clientId = this._getClientId();
  }

  _getClientId() {
    const CLIENT_ID_STORAGE_KEY = 'archi_client_id';
    let existingId = localStorage.getItem(CLIENT_ID_STORAGE_KEY);
    if (!existingId) {
      existingId = this._generateClientId();
      localStorage.setItem(CLIENT_ID_STORAGE_KEY, existingId);
    }
    return existingId;
  }

  _generateClientId() {
    if (window.crypto && typeof window.crypto.randomUUID === 'function') {
      return window.crypto.randomUUID();
    }
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
      const r = (Math.random() * 16) | 0;
      const v = c === 'x' ? r : (r & 0x3) | 0x8;
      return v.toString(16);
    });
  }

  async _fetch(endpoint, options = {}) {
    const url = `${this.baseUrl}${endpoint}`;
    const response = await fetch(url, {
      ...options,
      headers: {
        'Content-Type': 'application/json',
        ...options.headers,
      },
    });

    if (response.status === 401) {
      window.location.href = '/';
      return null;
    }

    const data = await response.json().catch(() => null);
    if (!response.ok) {
      throw new Error(data?.error || `Request failed (${response.status})`);
    }
    return data;
  }

  // =========================================================================
  // User API
  // =========================================================================

  /**
   * Get current user info or create user on first access
   */
  async getOrCreateUser() {
    return this._fetch(`/users/${encodeURIComponent(this.clientId)}`);
  }

  /**
   * Update user preferences
   */
  async updatePreferences(preferences) {
    return this._fetch(`/users/${encodeURIComponent(this.clientId)}/preferences`, {
      method: 'PUT',
      body: JSON.stringify(preferences),
    });
  }

  /**
   * Get user preferences (cached locally)
   */
  async getPreferences() {
    // Try local cache first
    const cached = localStorage.getItem(USER_PREFERENCES_KEY);
    if (cached) {
      try {
        return JSON.parse(cached);
      } catch (e) {
        // Invalid cache, fetch fresh
      }
    }

    const user = await this.getOrCreateUser();
    if (user) {
      const prefs = {
        theme: user.theme || 'system',
        preferred_model: user.preferred_model,
        preferred_temperature: user.preferred_temperature,
      };
      localStorage.setItem(USER_PREFERENCES_KEY, JSON.stringify(prefs));
      return prefs;
    }
    return null;
  }

  /**
   * Set preferred model
   */
  async setPreferredModel(modelId) {
    const result = await this.updatePreferences({ preferred_model: modelId });
    if (result) {
      // Update local cache
      const cached = JSON.parse(localStorage.getItem(USER_PREFERENCES_KEY) || '{}');
      cached.preferred_model = modelId;
      localStorage.setItem(USER_PREFERENCES_KEY, JSON.stringify(cached));
    }
    return result;
  }

  /**
   * Set theme preference
   */
  async setTheme(theme) {
    const result = await this.updatePreferences({ theme });
    if (result) {
      localStorage.setItem(THEME_KEY, theme);
      const cached = JSON.parse(localStorage.getItem(USER_PREFERENCES_KEY) || '{}');
      cached.theme = theme;
      localStorage.setItem(USER_PREFERENCES_KEY, JSON.stringify(cached));
      this._applyTheme(theme);
    }
    return result;
  }

  _applyTheme(theme) {
    const root = document.documentElement;
    if (theme === 'system') {
      const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
      root.setAttribute('data-theme', prefersDark ? 'dark' : 'light');
    } else {
      root.setAttribute('data-theme', theme);
    }
  }

  // =========================================================================
  // BYOK API Key Management
  // =========================================================================

  /**
   * Store a BYOK API key (encrypted server-side)
   */
  async setApiKey(provider, apiKey) {
    return this._fetch(`/users/${encodeURIComponent(this.clientId)}/api-keys/${provider}`, {
      method: 'PUT',
      body: JSON.stringify({ api_key: apiKey }),
    });
  }

  /**
   * Check if user has API key for provider
   */
  async hasApiKey(provider) {
    try {
      const result = await this._fetch(
        `/users/${encodeURIComponent(this.clientId)}/api-keys/${provider}/status`
      );
      return result?.has_key || false;
    } catch (e) {
      return false;
    }
  }

  /**
   * Delete a BYOK API key
   */
  async deleteApiKey(provider) {
    return this._fetch(`/users/${encodeURIComponent(this.clientId)}/api-keys/${provider}`, {
      method: 'DELETE',
    });
  }

  /**
   * List providers with stored API keys
   */
  async listApiKeys() {
    return this._fetch(`/users/${encodeURIComponent(this.clientId)}/api-keys`);
  }

  // =========================================================================
  // Document Selection API
  // =========================================================================

  /**
   * Get effective document selection for conversation
   */
  async getDocumentSelection(conversationId = null) {
    const params = new URLSearchParams({
      user_id: this.clientId,
    });
    if (conversationId) {
      params.set('conversation_id', conversationId);
    }
    return this._fetch(`/documents/selection?${params}`);
  }

  /**
   * Set user's default document selection
   */
  async setDefaultDocumentSelection(sourceIds) {
    return this._fetch(`/documents/user-default`, {
      method: 'PUT',
      body: JSON.stringify({
        user_id: this.clientId,
        source_ids: sourceIds,
      }),
    });
  }

  /**
   * Set document selection for specific conversation
   */
  async setConversationDocuments(conversationId, sourceIds) {
    return this._fetch(`/documents/conversation-override`, {
      method: 'PUT',
      body: JSON.stringify({
        conversation_id: conversationId,
        source_ids: sourceIds,
      }),
    });
  }

  // =========================================================================
  // Analytics API
  // =========================================================================

  /**
   * Get model usage statistics
   */
  async getModelStats(days = 7) {
    return this._fetch(`/analytics/model-usage?days=${days}`);
  }

  /**
   * Get A/B comparison results
   */
  async getAbComparisonResults(days = 30) {
    return this._fetch(`/analytics/ab-comparisons?days=${days}`);
  }
}

// =========================================================================
// Theme Management
// =========================================================================

function initTheme() {
  const savedTheme = localStorage.getItem(THEME_KEY) || 'system';
  const root = document.documentElement;
  
  if (savedTheme === 'system') {
    const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
    root.setAttribute('data-theme', prefersDark ? 'dark' : 'light');
    
    // Listen for system theme changes
    window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', (e) => {
      if (localStorage.getItem(THEME_KEY) === 'system') {
        root.setAttribute('data-theme', e.matches ? 'dark' : 'light');
      }
    });
  } else {
    root.setAttribute('data-theme', savedTheme);
  }
}

// =========================================================================
// Export singleton instance
// =========================================================================

const v2Api = new V2ApiClient();

// Initialize theme on load
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initTheme);
} else {
  initTheme();
}

// Make available globally for non-module scripts
window.v2Api = v2Api;
window.V2ApiClient = V2ApiClient;

export { V2ApiClient, v2Api, initTheme };
