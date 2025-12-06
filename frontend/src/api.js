/**
 * API client for the LLM Council backend.
 */

// Allow overriding the backend URL with VITE_API_BASE, otherwise:
// - Default to the same host on port 8001 so IPv4/IPv6 access in an LXC works
//   without having to edit the frontend bundle.
// - Keep localhost as a special case for dev.
const envBase = (import.meta.env.VITE_API_BASE || '').trim().replace(/\/+$/, '');

const computeHostPortBase = () => {
  if (typeof window === 'undefined') return 'http://localhost:8001';
  const { protocol, hostname } = window.location;
  const host = hostname.includes(':') ? `[${hostname}]` : hostname; // IPv6 safe
  return `${protocol}//${host}:8001`;
};

const API_BASE =
  envBase ||
  computeHostPortBase();

export const api = {
  /**
   * List all conversations.
   */
  async listConversations() {
    const response = await fetch(`${API_BASE}/api/conversations`);
    if (!response.ok) {
      throw new Error('Failed to list conversations');
    }
    return response.json();
  },

  /**
   * Create a new conversation.
   */
  async createConversation() {
    const response = await fetch(`${API_BASE}/api/conversations`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({}),
    });
    if (!response.ok) {
      throw new Error('Failed to create conversation');
    }
    return response.json();
  },

  /**
   * Get a specific conversation.
   */
  async getConversation(conversationId) {
    const response = await fetch(
      `${API_BASE}/api/conversations/${conversationId}`
    );
    if (!response.ok) {
      throw new Error('Failed to get conversation');
    }
    return response.json();
  },

  /**
   * Rename an existing conversation.
   */
  async renameConversation(conversationId, title) {
    const response = await fetch(`${API_BASE}/api/conversations/${conversationId}`, {
      method: 'PATCH',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ title }),
    });
    if (!response.ok) {
      throw new Error('Failed to rename conversation');
    }
    return response.json();
  },

  /**
   * Delete a conversation and all of its messages.
   */
  async deleteConversation(conversationId) {
    const response = await fetch(`${API_BASE}/api/conversations/${conversationId}`, {
      method: 'DELETE',
    });
    if (!response.ok) {
      throw new Error('Failed to delete conversation');
    }
    return response.json();
  },

  /**
   * Trigger the server-side update script.
   */
  async triggerUpdate() {
    const response = await fetch(`${API_BASE}/api/update`, { method: 'POST' });
    if (!response.ok) {
      throw new Error('Failed to start update');
    }
    return response.json();
  },

  /**
   * Fetch server settings (models, API key state).
   */
  async getSettings() {
    const response = await fetch(`${API_BASE}/api/settings`);
    if (!response.ok) {
      throw new Error('Failed to load settings');
    }
    return response.json();
  },

  /**
   * Update server settings.
   */
  async updateSettings(payload) {
    const response = await fetch(`${API_BASE}/api/settings`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!response.ok) {
      const errText = await response.text();
      throw new Error(errText || 'Failed to update settings');
    }
    return response.json();
  },

  /**
   * Send a message in a conversation.
   */
  async sendMessage(conversationId, content) {
    const response = await fetch(
      `${API_BASE}/api/conversations/${conversationId}/message`,
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ content }),
      }
    );
    if (!response.ok) {
      throw new Error('Failed to send message');
    }
    return response.json();
  },

  /**
   * Send a message and receive streaming updates.
   * @param {string} conversationId - The conversation ID
   * @param {string} content - The message content
   * @param {function} onEvent - Callback function for each event: (eventType, data) => void
   * @returns {Promise<void>}
   */
  async sendMessageStream(conversationId, content, onEvent) {
    const response = await fetch(
      `${API_BASE}/api/conversations/${conversationId}/message/stream`,
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ content }),
      }
    );

    if (!response.ok) {
      const text = await response.text();
      // Try to surface server-provided error details for clarity.
      try {
        const parsed = JSON.parse(text);
        if (parsed?.detail) {
          throw new Error(parsed.detail);
        }
      } catch (e) {
        // fall through; e is either JSON parse error or raised above
      }
      throw new Error(text || 'Failed to send message');
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });

      // SSE events are separated by a blank line
      const events = buffer.split('\n\n');
      // Keep last partial chunk (if any) in buffer
      buffer = events.pop() || '';

      for (const evt of events) {
        const line = evt.trim();
        if (!line.startsWith('data: ')) continue;
        const data = line.slice(6);
        try {
          const event = JSON.parse(data);
          onEvent(event.type, event);
        } catch (e) {
          console.error('Failed to parse SSE event chunk:', e, { chunk: data });
        }
      }
    }
  },
};
