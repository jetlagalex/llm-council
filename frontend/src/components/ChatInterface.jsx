import { useState, useEffect, useRef, memo } from 'react';
import ReactMarkdown from 'react-markdown';
import Stage1 from './Stage1';
import Stage2 from './Stage2';
import Stage3 from './Stage3';
import './ChatInterface.css';

// Memoized message list so typing in the composer doesn't re-render
// the entire transcript (ReactMarkdown parsing is expensive).
const MessagesView = memo(function MessagesView({ messages = [], isLoading }) {
  const messagesEndRef = useRef(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  return (
    <div className="messages-container">
      {messages.length === 0 ? (
        <div className="empty-state">
          <h2>Start a conversation</h2>
          <p>Ask a question to consult the LLM Council</p>
        </div>
      ) : (
        messages.map((msg, index) => (
          <div key={index} className="message-group">
            {msg.role === 'user' ? (
              <div className="user-message">
                <div className="message-label">You</div>
                <div className="message-content">
                  <div className="markdown-content">
                    <ReactMarkdown>{msg.content}</ReactMarkdown>
                  </div>
                </div>
              </div>
            ) : (
              <div className="assistant-message">
                <div className="message-label">LLM Council</div>

                {/* Stage 1 */}
                {msg.loading?.stage1 && (
                  <div className="stage-loading">
                    <div className="spinner"></div>
                    <span>Running Stage 1: Collecting individual responses...</span>
                  </div>
                )}
                {msg.stage1 && <Stage1 responses={msg.stage1} />}

                {/* Stage 2 */}
                {msg.loading?.stage2 && (
                  <div className="stage-loading">
                    <div className="spinner"></div>
                    <span>Running Stage 2: Peer rankings...</span>
                  </div>
                )}
                {msg.stage2 && (
                  <Stage2
                    rankings={msg.stage2}
                    labelToModel={msg.metadata?.label_to_model}
                    aggregateRankings={msg.metadata?.aggregate_rankings}
                  />
                )}

                {/* Stage 3 */}
                {msg.loading?.stage3 && (
                  <div className="stage-loading">
                    <div className="spinner"></div>
                    <span>Running Stage 3: Final synthesis...</span>
                  </div>
                )}
                {msg.stage3 && <Stage3 finalResponse={msg.stage3} />}
              </div>
            )}
          </div>
        ))
      )}

      {isLoading && (
        <div className="loading-indicator">
          <div className="spinner"></div>
          <span>Consulting the council...</span>
        </div>
      )}

      <div ref={messagesEndRef} />
    </div>
  );
});

// Shows the conversation thread and wiring for the compose box.
export default function ChatInterface({
  conversation,
  onSendMessage,
  isLoading,
  onOpenSidebar,
  isMobile,
  errorMessage,
  clearError,
  councils = [],
  activeCouncilKey,
  onChangeCouncil,
}) {
  const [input, setInput] = useState('');

  const handleSubmit = (e) => {
    e.preventDefault();
    if (input.trim() && !isLoading) {
      onSendMessage(input);
      setInput('');
      clearError?.();
    }
  };

  const handleKeyDown = (e) => {
    // Submit on Enter (without Shift)
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSubmit(e);
    }
  };

  if (!conversation) {
    return (
      <div className="chat-interface">
        <div className="empty-state">
          <h2>Welcome to LLM Council</h2>
          <p>Create a new conversation to get started</p>
        </div>
      </div>
    );
  }

  return (
    <div className="chat-interface">
      <div className="chat-header">
        {isMobile && (
          <button className="sidebar-toggle" onClick={onOpenSidebar} aria-label="Open conversations">
            <span className="burger-lines" aria-hidden="true">
              <span></span>
              <span></span>
              <span></span>
            </span>
          </button>
        )}
        <div className="chat-title">
          {conversation.title || 'New Conversation'}
        </div>
        {councils.length > 0 && (
          <div className="chat-council-picker">
            <label htmlFor="chat-council-select">Council</label>
            <select
              id="chat-council-select"
              value={activeCouncilKey || councils[0]?.key || ''}
              onChange={(e) => onChangeCouncil?.(e.target.value)}
              disabled={!conversation}
            >
              {councils.map((council) => (
                <option key={council.key} value={council.key}>
                  {council.name}
                </option>
              ))}
            </select>
          </div>
        )}
      </div>

      <MessagesView messages={conversation.messages} isLoading={isLoading} />

      <form className="input-form" onSubmit={handleSubmit}>
        {errorMessage && (
          <div className="input-error" role="alert">
            {errorMessage}
          </div>
        )}
        <textarea
          className="message-input"
          placeholder="Ask your question... (Shift+Enter for new line, Enter to send)"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          disabled={isLoading}
          rows={3}
        />
        <button
          type="submit"
          className="send-button"
          disabled={!input.trim() || isLoading}
        >
          Send
        </button>
      </form>
    </div>
  );
}
