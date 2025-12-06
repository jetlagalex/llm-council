import { useState, useEffect } from 'react';
import Sidebar from './components/Sidebar';
import ChatInterface from './components/ChatInterface';
import { api } from './api';
import './App.css';

// Root layout: manages conversation list, selection, and streamed message flow.
function App() {
  const [conversations, setConversations] = useState([]);
  const [currentConversationId, setCurrentConversationId] = useState(null);
  const [currentConversation, setCurrentConversation] = useState(null);
  const [isLoading, setIsLoading] = useState(false);
  const [isMobile, setIsMobile] = useState(
    typeof window !== 'undefined' ? window.innerWidth <= 900 : false
  );
  const [isSidebarOpen, setIsSidebarOpen] = useState(
    typeof window !== 'undefined' ? window.innerWidth > 900 : true
  );
  // Track update progress text so the sidebar can show quick feedback.
  const [updateStatus, setUpdateStatus] = useState('');
  const [isUpdating, setIsUpdating] = useState(false);
  const [updateLog, setUpdateLog] = useState([]);
  // Manage rename modal state to avoid browser-native prompts.
  const [renameTarget, setRenameTarget] = useState(null);
  const [renameValue, setRenameValue] = useState('');
  const [renameError, setRenameError] = useState('');
  // Settings modal state
  const [isSettingsOpen, setIsSettingsOpen] = useState(false);
  const [settingsLoading, setSettingsLoading] = useState(false);
  const [settingsError, setSettingsError] = useState('');
  const [availableModels, setAvailableModels] = useState([]);
  const [settingsForm, setSettingsForm] = useState({
    council_models: [],
    chairman_model: '',
  });
  const [apiKeyInput, setApiKeyInput] = useState('');
  const [removeApiKey, setRemoveApiKey] = useState(false);
  const [hasApiKey, setHasApiKey] = useState(false);
  const [apiKeyLast4, setApiKeyLast4] = useState(null);
  // Surface send errors near the compose box.
  const [sendError, setSendError] = useState('');

  // Load conversations on mount
  useEffect(() => {
    loadConversations();
  }, []);

  // Track viewport size for mobile layout
  useEffect(() => {
    const handleResize = () => {
      const mobile = window.innerWidth <= 900;
      setIsMobile(mobile);
      if (!mobile) {
        setIsSidebarOpen(true);
      }
    };

    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, []);

  // Load conversation details when selected
  useEffect(() => {
    if (currentConversationId) {
      loadConversation(currentConversationId);
    }
  }, [currentConversationId]);

  const loadConversations = async () => {
    try {
      const convs = await api.listConversations();
      setConversations(convs);
      if (!currentConversationId && convs.length > 0) {
        setCurrentConversationId(convs[0].id);
      }
    } catch (error) {
      console.error('Failed to load conversations:', error);
    }
  };

  const loadConversation = async (id) => {
    try {
      const conv = await api.getConversation(id);
      setCurrentConversation(conv);
    } catch (error) {
      console.error('Failed to load conversation:', error);
    }
  };

  const handleNewConversation = async () => {
    try {
      const newConv = await api.createConversation();
      setConversations([
        {
          id: newConv.id,
          created_at: newConv.created_at,
          message_count: 0,
          title: newConv.title,
        },
        ...conversations,
      ]);
      setCurrentConversationId(newConv.id);
      setCurrentConversation({ ...newConv, messages: [] });
      if (isMobile) {
        setIsSidebarOpen(false);
      }
    } catch (error) {
      console.error('Failed to create conversation:', error);
    }
  };

  const handleSelectConversation = (id) => {
    setCurrentConversationId(id);
    if (isMobile) {
      setIsSidebarOpen(false);
    }
  };

  // Open a custom rename modal instead of the browser prompt.
  const handleRenameConversation = (conversation) => {
    if (!conversation) return;
    setRenameTarget(conversation);
    setRenameValue(conversation.title || 'New Conversation');
    setRenameError('');
  };

  // Persist the rename and keep list + active conversation in sync.
  const submitRename = async () => {
    const trimmedTitle = renameValue.trim();
    if (!trimmedTitle) {
      setRenameError('Title cannot be empty.');
      return;
    }
    if (!renameTarget) return;

    try {
      await api.renameConversation(renameTarget.id, trimmedTitle);
      setConversations((prev) =>
        prev.map((conv) =>
          conv.id === renameTarget.id ? { ...conv, title: trimmedTitle } : conv
        )
      );
      setCurrentConversation((prev) =>
        prev && prev.id === renameTarget.id ? { ...prev, title: trimmedTitle } : prev
      );
      setRenameTarget(null);
    } catch (error) {
      console.error('Failed to rename conversation:', error);
      setRenameError('Rename failed. Please try again.');
    }
  };

  // Delete a conversation and choose a sensible fallback selection.
  const handleDeleteConversation = async (id) => {
    const confirmed = window.confirm(
      'Delete this conversation and all messages?'
    );
    if (!confirmed) return;

    try {
      await api.deleteConversation(id);
      setConversations((prev) => {
        const updated = prev.filter((conv) => conv.id !== id);
        if (currentConversationId === id) {
          const fallbackId = updated[0]?.id || null;
          setCurrentConversationId(fallbackId);
          setCurrentConversation(null);
        }
        return updated;
      });
    } catch (error) {
      console.error('Failed to delete conversation:', error);
    }
  };

  // Kick off the system update script without blocking the UI.
  const handleTriggerUpdate = async () => {
    setUpdateStatus('Starting update...');
    setIsUpdating(true);
    setUpdateLog([]);
    try {
      const result = await api.triggerUpdate();
      setUpdateStatus(
        'Update started. The app will restart; refresh after a minute if it disconnects.'
      );
      setUpdateLog([
        `Systemd unit: ${result.unit}`,
        `Logs: ${result.log_path}`,
      ]);
    } catch (error) {
      console.error('Failed to start update:', error);
      setUpdateStatus('Failed to start update. Check server logs.');
    }
    // Mark as not updating after initiating to re-enable the button.
    setIsUpdating(false);
  };

  // Open settings modal and fetch current values.
  const handleOpenSettings = async () => {
    setIsSettingsOpen(true);
    setSettingsLoading(true);
    setSettingsError('');
    try {
      const data = await api.getSettings();
      setAvailableModels(data.available_models || []);
      setSettingsForm({
        council_models: data.council_models || [],
        chairman_model: data.chairman_model || '',
      });
      setHasApiKey(Boolean(data.has_openrouter_key));
      setApiKeyLast4(data.openrouter_key_last4);
      setApiKeyInput('');
      setRemoveApiKey(false);
    } catch (error) {
      console.error('Failed to load settings:', error);
      setSettingsError('Failed to load settings. Try again.');
    } finally {
      setSettingsLoading(false);
    }
  };

  const handleToggleModel = (model) => {
    setSettingsForm((prev) => {
      const exists = prev.council_models.includes(model);
      // Enforce max of 4 selections
      if (!exists && prev.council_models.length >= 4) return prev;
      const nextModels = exists
        ? prev.council_models.filter((m) => m !== model)
        : [...prev.council_models, model];
      const nextChair = nextModels.includes(prev.chairman_model)
        ? prev.chairman_model
        : nextModels[0] || '';
      return { ...prev, council_models: nextModels, chairman_model: nextChair };
    });
  };

  const handleSaveSettings = async () => {
    setSettingsError('');
    if (!settingsForm.council_models.length) {
      setSettingsError('Select at least one council member (max 4).');
      return;
    }
    if (settingsForm.council_models.length > 4) {
      setSettingsError('Council limited to 4 members.');
      return;
    }
    if (!settingsForm.chairman_model) {
      setSettingsError('Choose a chairman from the selected members.');
      return;
    }
    try {
      const payload = {
        openrouter_api_key: removeApiKey ? '' : apiKeyInput.trim() || null,
        council_models: settingsForm.council_models,
        chairman_model: settingsForm.chairman_model,
      };
      const updated = await api.updateSettings(payload);
      setHasApiKey(Boolean(updated.has_openrouter_key));
      setApiKeyLast4(updated.openrouter_key_last4);
      setSettingsForm({
        council_models: updated.council_models,
        chairman_model: updated.chairman_model,
      });
      setRemoveApiKey(false);
      setApiKeyInput('');
      setIsSettingsOpen(false);
      // Clear update status so user knows settings saved
      setUpdateStatus('Settings saved.');
    } catch (error) {
      console.error('Failed to save settings:', error);
      setSettingsError(error.message || 'Failed to save settings.');
    }
  };

  // Kick off a council run and mirror the streaming SSE events into UI state.
  const handleSendMessage = async (content) => {
    if (!currentConversationId) return;

    setIsLoading(true);
    setSendError('');
    // Ensure we have a conversation object to append to (for brand new threads).
    setCurrentConversation((prev) => {
      if (prev) return prev;
      const fallbackTitle =
        conversations.find((c) => c.id === currentConversationId)?.title ||
        'New Conversation';
      return {
        id: currentConversationId,
        title: fallbackTitle,
        messages: [],
      };
    });

    try {
      // Optimistically add user message to UI
      const userMessage = { role: 'user', content };
      setCurrentConversation((prev) => ({
        ...prev,
        messages: [...prev.messages, userMessage],
      }));

      // Create a partial assistant message that will be updated progressively
      const assistantMessage = {
        role: 'assistant',
        stage1: null,
        stage2: null,
        stage3: null,
        metadata: null,
        loading: {
          stage1: false,
          stage2: false,
          stage3: false,
        },
      };

      // Add the partial assistant message
      setCurrentConversation((prev) => ({
        ...prev,
        messages: [...prev.messages, assistantMessage],
      }));

      // Send message with streaming
      await api.sendMessageStream(currentConversationId, content, (eventType, event) => {
        switch (eventType) {
          case 'stage1_start':
            setCurrentConversation((prev) => {
              const messages = [...prev.messages];
              const lastMsg = messages[messages.length - 1];
              lastMsg.loading.stage1 = true;
              return { ...prev, messages };
            });
            break;

          case 'stage1_complete':
            setCurrentConversation((prev) => {
              const messages = [...prev.messages];
              const lastMsg = messages[messages.length - 1];
              lastMsg.stage1 = event.data;
              lastMsg.loading.stage1 = false;
              return { ...prev, messages };
            });
            break;

          case 'stage2_start':
            setCurrentConversation((prev) => {
              const messages = [...prev.messages];
              const lastMsg = messages[messages.length - 1];
              lastMsg.loading.stage2 = true;
              return { ...prev, messages };
            });
            break;

          case 'stage2_complete':
            setCurrentConversation((prev) => {
              const messages = [...prev.messages];
              const lastMsg = messages[messages.length - 1];
              lastMsg.stage2 = event.data;
              lastMsg.metadata = event.metadata;
              lastMsg.loading.stage2 = false;
              return { ...prev, messages };
            });
            break;

          case 'stage3_start':
            setCurrentConversation((prev) => {
              const messages = [...prev.messages];
              const lastMsg = messages[messages.length - 1];
              lastMsg.loading.stage3 = true;
              return { ...prev, messages };
            });
            break;

          case 'stage3_complete':
            setCurrentConversation((prev) => {
              const messages = [...prev.messages];
              const lastMsg = messages[messages.length - 1];
              lastMsg.stage3 = event.data;
              lastMsg.loading.stage3 = false;
              return { ...prev, messages };
            });
            break;

          case 'title_complete':
            // Reload conversations to get updated title
            loadConversations();
            break;

          case 'complete':
            // Stream complete, reload conversations list
            loadConversations();
            setIsLoading(false);
            break;

          case 'error':
            console.error('Stream error:', event.message);
            setSendError(event.message || 'Failed to send message. Check API key in Settings.');
            setIsLoading(false);
            break;

          default:
            console.log('Unknown event type:', eventType);
        }
      });
    } catch (error) {
      console.error('Failed to send message:', error);
      // Remove optimistic messages on error
      setCurrentConversation((prev) => ({
        ...prev,
        messages: prev.messages.slice(0, -2),
      }));
      setSendError(error?.message || 'Failed to send. Verify your API key and model settings.');
      setIsLoading(false);
    }
  };

  return (
    <div className={`app ${isMobile ? 'is-mobile' : ''}`}>
      {isMobile && isSidebarOpen && (
        <div className="sidebar-backdrop" onClick={() => setIsSidebarOpen(false)} />
      )}

      <Sidebar
        conversations={conversations}
        currentConversationId={currentConversationId}
        onSelectConversation={handleSelectConversation}
        onNewConversation={handleNewConversation}
        onRenameConversation={handleRenameConversation}
        onDeleteConversation={handleDeleteConversation}
        onTriggerUpdate={handleTriggerUpdate}
        updateStatus={updateStatus}
        isUpdating={isUpdating}
        updateLog={updateLog}
        onOpenSettings={handleOpenSettings}
        isOpen={isSidebarOpen}
        isMobile={isMobile}
        onClose={() => setIsSidebarOpen(false)}
      />
      <ChatInterface
        conversation={currentConversation}
        onSendMessage={handleSendMessage}
        isLoading={isLoading}
        onOpenSidebar={() => setIsSidebarOpen(true)}
        isMobile={isMobile}
        errorMessage={sendError}
        clearError={() => setSendError('')}
      />
      {isSettingsOpen && (
        <div className="settings-modal-backdrop" onClick={() => setIsSettingsOpen(false)}>
          <div className="settings-modal" onClick={(e) => e.stopPropagation()}>
            <h3>Settings</h3>
            {settingsLoading ? (
              <div className="settings-loading">Loading settings...</div>
            ) : (
              <>
                <div className="settings-section">
                  <div className="settings-row">
                    <div>
                      <div className="settings-label">OpenRouter API Key</div>
                      <div className="settings-help">
                        {hasApiKey
                          ? `Key stored${apiKeyLast4 ? ` (••••${apiKeyLast4})` : ''}.`
                          : 'No key stored.'}
                      </div>
                    </div>
                    <label className="settings-inline">
                      <input
                        type="checkbox"
                        checked={removeApiKey}
                        onChange={(e) => setRemoveApiKey(e.target.checked)}
                      />
                      Remove stored key
                    </label>
                  </div>
                  <input
                    type="text"
                    placeholder="Enter new key (leave blank to keep current)"
                    value={apiKeyInput}
                    onChange={(e) => {
                      setApiKeyInput(e.target.value);
                      setRemoveApiKey(false);
                    }}
                  />
                </div>

                <div className="settings-section">
                  <div className="settings-label">Council Members (max 4)</div>
                  <div className="settings-help">
                    Select which models should participate in the council.
                  </div>
                  <div className="model-grid">
                    {availableModels.map((model) => {
                      const checked = settingsForm.council_models.includes(model);
                      const limitReached =
                        !checked && settingsForm.council_models.length >= 4;
                      return (
                        <label key={model} className={`model-chip ${checked ? 'checked' : ''} ${limitReached ? 'disabled' : ''}`}>
                          <input
                            type="checkbox"
                            checked={checked}
                            disabled={limitReached}
                            onChange={() => handleToggleModel(model)}
                          />
                          {model}
                        </label>
                      );
                    })}
                  </div>
                </div>

                <div className="settings-section">
                  <div className="settings-label">Chairman</div>
                  <div className="settings-help">
                    Choose which selected model synthesizes the final response.
                  </div>
                  <select
                    value={settingsForm.chairman_model}
                    onChange={(e) =>
                      setSettingsForm((prev) => ({ ...prev, chairman_model: e.target.value }))
                    }
                  >
                    {settingsForm.council_models.map((model) => (
                      <option key={model} value={model}>
                        {model}
                      </option>
                    ))}
                  </select>
                </div>

                {settingsError && <div className="settings-error">{settingsError}</div>}

                <div className="settings-actions">
                  <button className="rename-cancel" onClick={() => setIsSettingsOpen(false)}>
                    Close
                  </button>
                  <button className="rename-save" onClick={handleSaveSettings}>
                    Save Settings
                  </button>
                </div>
              </>
            )}
          </div>
        </div>
      )}
      {renameTarget && (
        <div className="rename-modal-backdrop" onClick={() => setRenameTarget(null)}>
          <div className="rename-modal" onClick={(e) => e.stopPropagation()}>
            <h3>Rename Conversation</h3>
            <p className="rename-modal-subtitle">
              Give this thread a descriptive title for later.
            </p>
            <input
              type="text"
              value={renameValue}
              onChange={(e) => {
                setRenameValue(e.target.value);
                setRenameError('');
              }}
              autoFocus
            />
            {renameError && <div className="rename-error">{renameError}</div>}
            <div className="rename-actions">
              <button className="rename-cancel" onClick={() => setRenameTarget(null)}>
                Cancel
              </button>
              <button className="rename-save" onClick={submitRename}>
                Save
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default App;
