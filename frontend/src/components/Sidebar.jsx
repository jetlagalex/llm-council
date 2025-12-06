import { useEffect, useState } from 'react';
import './Sidebar.css';

// Lists available conversations and basic actions.
export default function Sidebar({
  conversations,
  currentConversationId,
  onSelectConversation,
  onNewConversation,
  onRenameConversation,
  onDeleteConversation,
  isOpen,
  isMobile,
  onClose,
}) {
  // Track context menu state for right-click actions.
  const [contextMenu, setContextMenu] = useState({
    visible: false,
    x: 0,
    y: 0,
    conversation: null,
  });

  // Close the context menu when clicking elsewhere or resizing.
  useEffect(() => {
    const hideMenu = () =>
      setContextMenu((prev) =>
        prev.visible ? { ...prev, visible: false, conversation: null } : prev
      );

    // Allow closing the context menu via Esc for quick keyboard access.
    const handleKeyDown = (event) => {
      if (event.key === 'Escape') {
        hideMenu();
      }
    };

    document.addEventListener('click', hideMenu);
    window.addEventListener('resize', hideMenu);
    document.addEventListener('keydown', handleKeyDown);
    return () => {
      document.removeEventListener('click', hideMenu);
      window.removeEventListener('resize', hideMenu);
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, []);

  // Open the context menu on right-click at the pointer location.
  const openContextMenu = (event, conversation) => {
    event.preventDefault();
    setContextMenu({
      visible: true,
      x: event.clientX,
      y: event.clientY,
      conversation,
    });
  };

  // Also support opening the menu via the inline button for touch devices.
  const openMenuFromButton = (event, conversation) => {
    event.stopPropagation();
    const rect = event.currentTarget.getBoundingClientRect();
    setContextMenu({
      visible: true,
      x: rect.right + 4,
      y: rect.bottom + 4,
      conversation,
    });
  };

  const handleRename = () => {
    if (!contextMenu.conversation) return;
    // Delegate rename to parent so state stays centralized.
    onRenameConversation(contextMenu.conversation.id, contextMenu.conversation.title);
    setContextMenu((prev) => ({ ...prev, visible: false, conversation: null }));
  };

  const handleDelete = () => {
    if (!contextMenu.conversation) return;
    // Delegate delete to parent so list and selection remain in sync.
    onDeleteConversation(contextMenu.conversation.id);
    setContextMenu((prev) => ({ ...prev, visible: false, conversation: null }));
  };

  return (
    <div className={`sidebar ${isOpen ? 'open' : ''} ${isMobile ? 'mobile' : ''}`}>
      <div className="sidebar-header">
        <h1>LLM Council</h1>
        <div className="sidebar-actions">
          <button className="new-conversation-btn" onClick={onNewConversation}>
            + New Conversation
          </button>
          {isMobile && (
            <button className="close-sidebar-btn" onClick={onClose}>
              Close
            </button>
          )}
        </div>
      </div>

      <div className="conversation-list">
        {conversations.length === 0 ? (
          <div className="no-conversations">No conversations yet</div>
        ) : (
          conversations.map((conv) => (
            <div
              key={conv.id}
              className={`conversation-item ${
                conv.id === currentConversationId ? 'active' : ''
              }`}
              onClick={() => onSelectConversation(conv.id)}
              onContextMenu={(event) => openContextMenu(event, conv)}
            >
              <div className="conversation-top-row">
                <div className="conversation-title">
                  {conv.title || 'New Conversation'}
                </div>
                <button
                  className="conversation-options"
                  aria-label="Conversation actions"
                  onClick={(event) => openMenuFromButton(event, conv)}
                >
                  ...
                </button>
              </div>
              <div className="conversation-meta">
                {conv.message_count} messages
              </div>
            </div>
          ))
        )}
      </div>

      {contextMenu.visible && (
        <div
          className="conversation-context-menu"
          style={{ top: contextMenu.y, left: contextMenu.x }}
          onClick={(event) => event.stopPropagation()}
        >
          <button className="context-menu-item" onClick={handleRename}>
            Rename
          </button>
          <button className="context-menu-item delete" onClick={handleDelete}>
            Delete
          </button>
        </div>
      )}
    </div>
  );
}
