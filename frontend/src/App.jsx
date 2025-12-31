import { useState, useEffect, useCallback } from 'react';
import Sidebar from './components/Sidebar';
import ChatInterface from './components/ChatInterface';
import { api } from './api';
import './App.css';

const getLastInteracted = (conv) =>
  (conv &&
    (conv.lastInteracted ||
      conv.last_interacted_at ||
      conv.updated_at ||
      conv.created_at)) ||
  null;

// Keep newest interactions first without mutating the original list.
const sortConversationsByActivity = (list) => {
  const toTime = (value) => (value ? new Date(value).getTime() : 0);
  return [...list].sort(
    (a, b) =>
      toTime(getLastInteracted(b)) -
      toTime(getLastInteracted(a))
  );
};

// Preserve last-interacted timestamps when refreshing data from the API.
const mergeConversationsWithRecency = (incoming, previous) => {
  const prevMap = new Map(previous.map((conv) => [conv.id, conv]));
  const normalized = incoming.map((conv) => ({
    ...conv,
    council_key: conv.council_key || 'default',
    lastInteracted:
      getLastInteracted(conv) ||
      getLastInteracted(prevMap.get(conv.id)) ||
      conv.created_at,
    last_interacted_at:
      conv.last_interacted_at ||
      prevMap.get(conv.id)?.last_interacted_at ||
      getLastInteracted(conv) ||
      getLastInteracted(prevMap.get(conv.id)) ||
      conv.created_at,
  }));
  return sortConversationsByActivity(normalized);
};

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
  const [councils, setCouncils] = useState([]);
  const [selectedCouncilKey, setSelectedCouncilKey] = useState('default');
  const [editingCouncilKey, setEditingCouncilKey] = useState('default');
  const [editingCouncilName, setEditingCouncilName] = useState('General');
  const [isNewCouncil, setIsNewCouncil] = useState(false);
  const [councilError, setCouncilError] = useState('');
  const [dirtyCouncils, setDirtyCouncils] = useState(() => new Set());
  // Track ad-hoc models users add so they can expand the council roster.
  const [newModelInput, setNewModelInput] = useState('');
  const [addModelError, setAddModelError] = useState('');
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

  // Mark a conversation as recently interacted and re-sort the list.
  const markConversationInteracted = useCallback((id, timestamp) => {
    if (!id) return;
    const ts = timestamp || new Date().toISOString();
    setConversations((prev) => {
      const exists = prev.some((conv) => conv.id === id);
      if (!exists) return prev;
      const updated = prev.map((conv) =>
        conv.id === id
          ? { ...conv, lastInteracted: ts, last_interacted_at: ts }
          : conv
      );
      return sortConversationsByActivity(updated);
    });
  }, []);

  // Load conversations on mount
  useEffect(() => {
    loadConversations();
    loadCouncils();
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

  const loadCouncils = async () => {
    try {
      const councilList = await api.listCouncils();
      setCouncils(councilList);
      if (councilList.length > 0) {
        const fallbackKey =
          councilList.find((c) => c.key === selectedCouncilKey)?.key ||
          councilList[0].key;
        setSelectedCouncilKey(fallbackKey);
      }
    } catch (error) {
      console.error('Failed to load councils:', error);
    }
  };

  const loadConversations = async () => {
    try {
      const convs = await api.listConversations();
      setConversations((prev) => {
        const merged = mergeConversationsWithRecency(convs, prev);
        if (!currentConversationId && merged.length > 0) {
          setCurrentConversationId(merged[0].id);
          setSelectedCouncilKey(merged[0].council_key || 'default');
        }
        return merged;
      });
    } catch (error) {
      console.error('Failed to load conversations:', error);
    }
  };

  const loadConversation = async (id) => {
    try {
      const conv = await api.getConversation(id);
      setCurrentConversation(conv);
      if (conv?.council_key) {
        setSelectedCouncilKey(conv.council_key);
      }
      setConversations((prev) =>
        sortConversationsByActivity(
          prev.map((c) =>
            c.id === id
              ? {
                  ...c,
                  council_key: conv.council_key || c.council_key,
                  lastInteracted: getLastInteracted(conv) || c.lastInteracted || c.created_at,
                  last_interacted_at: conv.last_interacted_at || c.last_interacted_at || c.created_at,
                }
              : c
          )
        )
      );
    } catch (error) {
      console.error('Failed to load conversation:', error);
    }
  };

  const handleNewConversation = useCallback(async () => {
    try {
      const preferredCouncil =
        councils.find((council) => council.key === selectedCouncilKey)?.key ||
        councils[0]?.key ||
        'default';
      const newConv = await api.createConversation(preferredCouncil);
      const newCouncilKey = newConv.council_key || preferredCouncil || 'default';
      const lastInteracted = new Date().toISOString();
      setConversations((prev) =>
        sortConversationsByActivity([
          {
            id: newConv.id,
            created_at: newConv.created_at,
            message_count: 0,
            title: newConv.title,
            council_key: newCouncilKey,
            lastInteracted,
            last_interacted_at: lastInteracted,
          },
          ...prev,
        ])
      );

      setCurrentConversationId(newConv.id);
      setCurrentConversation({ ...newConv, council_key: newCouncilKey || 'default', messages: [] });
      setSelectedCouncilKey(newCouncilKey || 'default');
      if (isMobile) {
        setIsSidebarOpen(false);
      }
    } catch (error) {
      console.error('Failed to create conversation:', error);
    }
  }, [councils, selectedCouncilKey, isMobile]);

  const handleSelectConversation = useCallback((id) => {
    setCurrentConversationId(id);
    const match = conversations.find((conv) => conv.id === id);
    if (match?.council_key) {
      setSelectedCouncilKey(match.council_key);
    }
    if (isMobile) {
      setIsSidebarOpen(false);
    }
  }, [conversations, isMobile, markConversationInteracted]);

  // Open a custom rename modal instead of the browser prompt.
  const handleRenameConversation = useCallback((conversation) => {
    if (!conversation) return;
    setRenameTarget(conversation);
    setRenameValue(conversation.title || 'New Conversation');
    setRenameError('');
  }, []);

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

  const handleChangeConversationCouncil = useCallback(async (councilKey) => {
    if (!currentConversationId) return;
    try {
      const updated = await api.setConversationCouncil(currentConversationId, councilKey);
      setConversations((prev) =>
        prev.map((conv) =>
          conv.id === currentConversationId ? { ...conv, council_key: updated.council_key } : conv
        )
      );
      setCurrentConversation((prev) =>
        prev ? { ...prev, council_key: updated.council_key } : prev
      );
      setSelectedCouncilKey(updated.council_key);
      setUpdateStatus(`Council switched to ${updated.council_key}.`);
    } catch (error) {
      console.error('Failed to update conversation council:', error);
      setSendError(error.message || 'Failed to update council.');
    }
  }, [currentConversationId]);

  // Delete a conversation and choose a sensible fallback selection.
  const handleDeleteConversation = useCallback(async (id) => {
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
          if (fallbackId) {
            const fallback = updated.find((conv) => conv.id === fallbackId);
            if (fallback?.council_key) {
              setSelectedCouncilKey(fallback.council_key);
            }
          } else {
            setSelectedCouncilKey('default');
          }
        }
        return updated;
      });
    } catch (error) {
      console.error('Failed to delete conversation:', error);
    }
  }, [currentConversationId]);

  // Kick off the system update script without blocking the UI.
  const handleTriggerUpdate = useCallback(async () => {
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
  }, []);

  // Open settings modal and fetch current values.
  const handleOpenSettings = useCallback(async () => {
    setIsSettingsOpen(true);
    setSettingsLoading(true);
    setSettingsError('');
    try {
      const data = await api.getSettings();
      const councilList = await api.listCouncils();
      setCouncils(councilList);
      setAvailableModels(data.available_models || []);
      const preferredCouncilKey =
        currentConversation?.council_key ||
        selectedCouncilKey ||
        'default';
      const activeCouncil =
        councilList.find((c) => c.key === preferredCouncilKey) ||
        councilList[0] ||
        null;
      setEditingCouncilKey(activeCouncil?.key || preferredCouncilKey || 'default');
      setEditingCouncilName(activeCouncil?.name || 'General');
      setIsNewCouncil(!activeCouncil);
      setSettingsForm({
        council_models: activeCouncil?.council_models || data.council_models || [],
        chairman_model: activeCouncil?.chairman_model || data.chairman_model || '',
      });
      setHasApiKey(Boolean(data.has_openrouter_key));
      setApiKeyLast4(data.openrouter_key_last4);
      setApiKeyInput('');
      setRemoveApiKey(false);
      setNewModelInput('');
      setAddModelError('');
      setCouncilError('');
      setDirtyCouncils(new Set());
    } catch (error) {
      console.error('Failed to load settings:', error);
      setSettingsError('Failed to load settings. Try again.');
    } finally {
      setSettingsLoading(false);
    }
  }, [currentConversation, selectedCouncilKey]);

  // Allow adding custom OpenRouter model IDs to the available council roster.
  const handleAddModel = () => {
    const trimmed = newModelInput.trim();
    if (!trimmed) {
      setAddModelError('Enter an OpenRouter model id to add.');
      return;
    }
    if (availableModels.includes(trimmed)) {
      setAddModelError('That model is already listed.');
      return;
    }
    setAvailableModels((prev) => [...prev, trimmed]);
    setAddModelError('');
    setNewModelInput('');
  };

  // Remove a model from the available roster and clean up any councils using it.
  const handleRemoveModel = (model) => {
    setSettingsError('');
    setCouncilError('');

    const blockingCouncil = councils.find(
      (council) =>
        council.key !== editingCouncilKey &&
        council.council_models.includes(model) &&
        council.council_models.length <= 1
    );
    if (blockingCouncil) {
      setCouncilError(
        `${blockingCouncil.name} would have no members if you remove ${model}. Add another model first.`
      );
      return;
    }

    const editingUsesModel = settingsForm.council_models.includes(model);
    const nextCouncilModels = editingUsesModel
      ? settingsForm.council_models.filter((m) => m !== model)
      : settingsForm.council_models;
    const nextChair = nextCouncilModels.includes(settingsForm.chairman_model)
      ? settingsForm.chairman_model
      : nextCouncilModels[0] || '';

    const changedKeys = [];
    setCouncils((prev) =>
      prev.map((council) => {
        if (!council.council_models.includes(model)) return council;
        const trimmedModels = council.council_models.filter((m) => m !== model);
        const trimmedChair = trimmedModels.includes(council.chairman_model)
          ? council.chairman_model
          : trimmedModels[0] || '';
        changedKeys.push(council.key);
        return {
          ...council,
          council_models: trimmedModels,
          chairman_model: trimmedChair,
        };
      })
    );

    setDirtyCouncils((prev) => {
      const merged = new Set(prev);
      changedKeys.forEach((key) => merged.add(key));
      if (editingUsesModel && !isNewCouncil) {
        merged.add(editingCouncilKey);
      }
      return merged;
    });

    setSettingsForm((prev) => ({
      ...prev,
      council_models: nextCouncilModels,
      chairman_model: nextChair,
    }));

    setAvailableModels((prev) => prev.filter((m) => m !== model));
  };

  const handleSelectEditingCouncil = (key) => {
    const council = councils.find((c) => c.key === key);
    if (!council) return;
    setEditingCouncilKey(council.key);
    setEditingCouncilName(council.name);
    setIsNewCouncil(false);
    setSettingsForm({
      council_models: council.council_models || [],
      chairman_model: council.chairman_model || '',
    });
    setCouncilError('');
    setAddModelError('');
    setNewModelInput('');
  };

  const handleStartNewCouncil = () => {
    const placeholderKey = `new-${Date.now()}`;
    setEditingCouncilKey(placeholderKey);
    setEditingCouncilName('New Council');
    setIsNewCouncil(true);
    setSettingsForm({
      council_models: [],
      chairman_model: '',
    });
    setCouncilError('');
    setAddModelError('');
    setNewModelInput('');
  };

  const handleDeleteCouncil = async () => {
    if (isNewCouncil) {
      setCouncilError('Save this council before deleting.');
      return;
    }
    if (editingCouncilKey === 'default') {
      setCouncilError('Default council cannot be deleted.');
      return;
    }
    const confirmed = window.confirm('Delete this council? Conversations using it must switch first.');
    if (!confirmed) return;
    try {
      await api.deleteCouncil(editingCouncilKey);
      const latest = await api.listCouncils();
      setCouncils(latest);
      const fallback = latest.find((c) => c.key === 'default') || latest[0] || null;
      if (fallback) {
        handleSelectEditingCouncil(fallback.key);
        setSelectedCouncilKey((prev) => (prev === editingCouncilKey ? fallback.key : prev));
      } else {
        setEditingCouncilKey('default');
        setEditingCouncilName('General');
        setSettingsForm({ council_models: [], chairman_model: '' });
      }
      setCouncilError('');
    } catch (error) {
      console.error('Failed to delete council:', error);
      setCouncilError(error.message || 'Failed to delete council.');
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
    setCouncilError('');
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
      // Normalize and deduplicate the available list before persisting it.
      const cleanedAvailable = Array.from(
        new Set(
          availableModels
            .map((model) => model.trim())
            .filter(Boolean)
        )
      );
      const councilName = editingCouncilName.trim() || 'Untitled Council';

      let savedCouncil;
      if (isNewCouncil) {
        savedCouncil = await api.createCouncil({
          name: councilName,
          council_models: settingsForm.council_models,
          chairman_model: settingsForm.chairman_model,
        });
      } else {
        savedCouncil = await api.updateCouncil(editingCouncilKey, {
          name: councilName,
          council_models: settingsForm.council_models,
          chairman_model: settingsForm.chairman_model,
        });
      }

      const dirtyKeys = Array.from(dirtyCouncils).filter(
        (key) => key !== (savedCouncil?.key || editingCouncilKey)
      );
      for (const key of dirtyKeys) {
        const council = councils.find((c) => c.key === key);
        if (!council) continue;
        await api.updateCouncil(key, {
          name: council.name,
          council_models: council.council_models,
          chairman_model: council.chairman_model,
        });
      }

      const latestCouncils = await api.listCouncils();
      setCouncils(latestCouncils);
      setDirtyCouncils(new Set());

      const defaultCouncil =
        latestCouncils.find((council) => council.key === 'default') || savedCouncil;
      const settingsCouncil = defaultCouncil || savedCouncil;

      const payload = {
        openrouter_api_key: removeApiKey ? '' : apiKeyInput.trim() || null,
        council_models: settingsCouncil?.council_models || settingsForm.council_models,
        chairman_model: settingsCouncil?.chairman_model || settingsForm.chairman_model,
        available_models: cleanedAvailable,
      };

      const updated = await api.updateSettings(payload);
      setHasApiKey(Boolean(updated.has_openrouter_key));
      setApiKeyLast4(updated.openrouter_key_last4);
      setAvailableModels(updated.available_models || cleanedAvailable);

      setSettingsForm({
        council_models: savedCouncil?.council_models || settingsForm.council_models,
        chairman_model: savedCouncil?.chairman_model || settingsForm.chairman_model,
      });
      setEditingCouncilKey(savedCouncil?.key || editingCouncilKey);
      setEditingCouncilName(savedCouncil?.name || councilName);
      setIsNewCouncil(false);
      setSelectedCouncilKey(savedCouncil?.key || selectedCouncilKey);
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
  const handleSendMessage = useCallback(async (content) => {
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
    markConversationInteracted(currentConversationId);

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
      const message = error?.message || 'Failed to send. Verify your API key and model settings.';
      // Give a more helpful hint for network-layer failures.
      if (message.toLowerCase().includes('network') || message.toLowerCase().includes('failed to fetch')) {
        setSendError(
          'Failed to reach the backend. Ensure the server is running and your API key/models are set in Settings.'
        );
      } else {
        setSendError(message);
      }
      setIsLoading(false);
    }
  }, [currentConversationId, conversations, markConversationInteracted]);

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
        councils={councils}
        selectedCouncilKey={selectedCouncilKey}
        onSelectCouncil={setSelectedCouncilKey}
        isOpen={isSidebarOpen}
        isMobile={isMobile}
        onClose={useCallback(() => setIsSidebarOpen(false), [])}
      />
      <ChatInterface
        conversation={currentConversation}
        onSendMessage={handleSendMessage}
        isLoading={isLoading}
        onOpenSidebar={useCallback(() => setIsSidebarOpen(true), [])}
        isMobile={isMobile}
        errorMessage={sendError}
        clearError={useCallback(() => setSendError(''), [])}
        councils={councils}
        activeCouncilKey={currentConversation?.council_key || selectedCouncilKey}
        onChangeCouncil={handleChangeConversationCouncil}
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
                  <div className="settings-label">Councils</div>
                  <div className="settings-help">
                    Group up to 4 models per council and switch them per conversation.
                  </div>
                  <div className="council-switcher">
                    <select
                      value={editingCouncilKey}
                      onChange={(e) => handleSelectEditingCouncil(e.target.value)}
                    >
                      {councils.map((council) => (
                        <option key={council.key} value={council.key}>
                          {council.name}
                        </option>
                      ))}
                      {isNewCouncil && (
                        <option value={editingCouncilKey}>{editingCouncilName || 'New Council'}</option>
                      )}
                    </select>
                    <button type="button" className="model-add-btn" onClick={handleStartNewCouncil}>
                      New Council
                    </button>
                    {!isNewCouncil && editingCouncilKey !== 'default' && (
                      <button type="button" className="delete-council-btn" onClick={handleDeleteCouncil}>
                        Delete
                      </button>
                    )}
                  </div>
                  <input
                    type="text"
                    placeholder="Council name"
                    value={editingCouncilName}
                    onChange={(e) => setEditingCouncilName(e.target.value)}
                  />
                  {councilError && (
                    <div className="settings-field-error">{councilError}</div>
                  )}

                  <div className="settings-label">Council Members (max 4)</div>
                  <div className="settings-help">
                    Select which models should participate in this council.
                  </div>
                  <div className="model-add-row">
                    <input
                      type="text"
                      placeholder="Add another OpenRouter model (e.g., anthropic/claude-3.5)"
                      value={newModelInput}
                      onChange={(e) => {
                        setNewModelInput(e.target.value);
                        setAddModelError('');
                      }}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') {
                          e.preventDefault();
                          handleAddModel();
                        }
                      }}
                    />
                    <button className="model-add-btn" onClick={handleAddModel}>
                      Add Model
                    </button>
                  </div>
                  {addModelError && (
                    <div className="settings-field-error">{addModelError}</div>
                  )}
                  <div className="model-grid">
                    {availableModels.map((model) => {
                      const checked = settingsForm.council_models.includes(model);
                      const limitReached =
                        !checked && settingsForm.council_models.length >= 4;
                      return (
                        <div
                          key={model}
                          className={`model-chip ${checked ? 'checked' : ''} ${limitReached ? 'disabled' : ''}`}
                        >
                          <label className="model-chip-main">
                            <input
                              type="checkbox"
                              checked={checked}
                              disabled={limitReached}
                              onChange={() => handleToggleModel(model)}
                            />
                            <span className="model-chip-name">{model}</span>
                          </label>
                          <button
                            type="button"
                            className="model-delete-btn"
                            onClick={(e) => {
                              e.preventDefault();
                              e.stopPropagation();
                              handleRemoveModel(model);
                            }}
                            aria-label={`Remove ${model}`}
                          >
                            Remove
                          </button>
                        </div>
                      );
                    })}
                  </div>

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
                {councilError && !settingsError && (
                  <div className="settings-error">{councilError}</div>
                )}

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
