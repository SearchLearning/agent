const { useEffect, useState, useRef, useCallback } = React;

const WS_URL = 'ws://127.0.0.1:8766';
const WS_PROBE_TIMEOUT_MS = 3000; // how long to wait before falling back to standalone

const CAP_LABEL = {
  exact: 'Exact', semantic: 'Semantic', structured: 'Structured',
  combined: 'Combined', autocomplete: 'Autocomplete', fuzzy: 'Fuzzy', manual: 'Manual',
};

// Probe whether the WS server is reachable before rendering the interactive UI.
// Renders null while probing, then either the app or a redirect to standalone.
function WsGate({ children }) {
  const [status, setStatus] = useState('probing'); // 'probing' | 'ok' | 'unavailable'

  useEffect(() => {
    let settled = false;
    const ws = new WebSocket(WS_URL);
    const timer = setTimeout(() => {
      if (!settled) { settled = true; ws.close(); setStatus('unavailable'); }
    }, WS_PROBE_TIMEOUT_MS);

    ws.onopen = () => {
      if (!settled) { settled = true; clearTimeout(timer); ws.close(); setStatus('ok'); }
    };
    ws.onerror = () => {
      if (!settled) { settled = true; clearTimeout(timer); setStatus('unavailable'); }
    };
    ws.onclose = () => {
      if (!settled) { settled = true; clearTimeout(timer); setStatus('unavailable'); }
    };
    return () => { clearTimeout(timer); try { ws.close(); } catch(e) {} };
  }, []);

  if (status === 'probing') {
    return React.createElement('div', {
      style: {
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        height: '100vh', fontFamily: 'var(--font-sans)', fontSize: '14px',
        color: 'hsl(var(--muted-foreground))', background: 'hsl(var(--background))',
      }
    }, 'Connecting to search service…');
  }

  if (status === 'unavailable') {
    // Redirect to standalone UI
    window.location.replace('/');
    return React.createElement('div', {
      style: {
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        height: '100vh', fontFamily: 'var(--font-sans)', fontSize: '14px',
        color: 'hsl(var(--muted-foreground))', background: 'hsl(var(--background))',
      }
    }, 'Search service unavailable — redirecting to Search Builder…');
  }

  return children;
}

function useWebSocket(onMessage) {
  const [connected, setConnected] = useState(false);
  const wsRef = useRef(null);
  const timerRef = useRef(null);
  const connect = useCallback(() => {
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) return;
    const ws = new WebSocket(WS_URL);
    wsRef.current = ws;
    ws.onopen = () => { setConnected(true); ws.send(JSON.stringify({ action: 'get_status' })); };
    ws.onmessage = (e) => { try { onMessage(JSON.parse(e.data)); } catch(err) {} };
    ws.onclose = () => { setConnected(false); timerRef.current = setTimeout(connect, 2000); };
    ws.onerror = () => ws.close();
  }, [onMessage]);
  useEffect(() => {
    connect();
    return () => { clearTimeout(timerRef.current); if (wsRef.current) wsRef.current.close(); };
  }, [connect]);
  const send = useCallback((data) => {
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) wsRef.current.send(JSON.stringify(data));
  }, []);
  return { connected, send };
}

function ResultCard({ hit, index }) {
  const src = hit.source || hit._source || {};
  const score = hit.score != null ? hit.score : hit._score;
  const id = hit.id || hit._id;
  const title = src.primaryTitle || src.title || src.name || src.originalTitle || id;
  const meta = [src.titleType, src.startYear, src.genres].filter(Boolean).join(' · ');
  return (
    <article className="result-card" style={{ animationDelay: (index * 35) + 'ms' }}>
      <div className="result-head">
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', minWidth: 0, flex: 1 }}>
          <span style={{ minWidth: '22px', height: '22px', borderRadius: '50%', background: 'hsl(var(--muted))', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '11px', color: 'hsl(var(--muted-foreground))', fontWeight: 600, flexShrink: 0 }}>{index + 1}</span>
          <span style={{ fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{title}</span>
        </div>
        {score != null && <span className="score">score {score.toFixed(3)}</span>}
      </div>
      {meta && <div className="preview" style={{ fontSize: '13px' }}>{meta}</div>}
      <div style={{ fontSize: '11px', color: 'hsl(var(--muted-foreground))', fontFamily: 'var(--font-mono)', marginTop: '2px' }}>id: {id}</div>
      <details>
        <summary style={{ cursor: 'pointer', fontSize: '12px', color: 'hsl(var(--primary))', marginTop: '4px' }}>View full doc</summary>
        <pre>{JSON.stringify(src, null, 2)}</pre>
      </details>
    </article>
  );
}

function ChatMessage({ msg }) {
  const role = msg.role === 'error' ? 'error' : msg.role;
  const label = msg.role === 'user' ? 'You' : msg.role === 'error' ? 'Error' : 'Agent';
  return (
    <div className={'iui-msg ' + role}>
      <div className="iui-msg-label">{label} · {msg.time}</div>
      <div className="iui-msg-bubble">{msg.text}</div>
    </div>
  );
}

function InteractiveApp() {
  const [query, setQuery] = useState('');
  const [indexName, setIndexName] = useState('');
  const [size, setSize] = useState(20);
  const [suggestions, setSuggestions] = useState([]);
  const [showSuggestions, setShowSuggestions] = useState(true);
  const [autocompleteField, setAutocompleteField] = useState('');
  const [autocompleteOptions, setAutocompleteOptions] = useState([]);
  const [capability, setCapability] = useState('');
  const [queryMode, setQueryMode] = useState('');
  const [results, setResults] = useState(null);
  const [searching, setSearching] = useState(false);
  const [searchMeta, setSearchMeta] = useState(null);
  const [backendType, setBackendType] = useState('local');
  const [backendEndpoint, setBackendEndpoint] = useState('');
  const [chatMessages, setChatMessages] = useState([]);
  const [chatInput, setChatInput] = useState('');
  const [chatLoading, setChatLoading] = useState(false);
  const [chatWidth, setChatWidth] = useState(360);
  const chatEndRef = useRef(null);
  const acTimerRef = useRef(null);
  const dragRef = useRef(null);

  const onDividerMouseDown = useCallback((e) => {
    e.preventDefault();
    const startX = e.clientX;
    const startWidth = chatWidth;
    const onMove = (ev) => {
      const delta = startX - ev.clientX;
      setChatWidth(Math.max(240, Math.min(600, startWidth + delta)));
    };
    const onUp = () => {
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
    };
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
  }, [chatWidth]);

  const loadSuggestions = useCallback(async (idx) => {
    try {
      const qs = new URLSearchParams();
      if (idx) qs.set('index', idx);
      const res = await fetch('/api/suggestions?' + qs.toString());
      const data = await res.json();
      const raw = Array.isArray(data.suggestion_meta) ? data.suggestion_meta : [];
      const mapped = raw
        .map(e => ({
          text: String(e.text || '').trim(),
          capability: String(e.capability || '').trim().toLowerCase(),
          query_mode: String(e.query_mode || 'default').trim(),
          field: String(e.field || '').trim(),
          value: String(e.value || '').trim(),
          case_insensitive: Boolean(e.case_insensitive),
        }))
        .filter(e => e.text);
      if (mapped.length > 0) { setSuggestions(mapped); return; }
      const legacy = Array.isArray(data.suggestions) ? data.suggestions : [];
      setSuggestions(legacy.map(t => ({ text: String(t).trim(), capability: '', query_mode: 'default', field: '', value: '', case_insensitive: false })).filter(e => e.text));
    } catch(err) { setSuggestions([]); }
  }, []);

  useEffect(() => {
    fetch('/api/config')
      .then(r => r.json())
      .then(data => {
        setBackendType(String(data.backend_type || 'local').trim());
        setBackendEndpoint(String(data.endpoint || '').trim());
        const idx = (data.default_index || '').trim();
        if (idx) { setIndexName(idx); loadSuggestions(idx); }
        else loadSuggestions('');
      })
      .catch(() => loadSuggestions(''));
  }, [loadSuggestions]);

  useEffect(() => {
    const isAC = (capability === 'autocomplete' || (queryMode && queryMode.startsWith('autocomplete')) || autocompleteField) && indexName && query.length >= 2;
    if (!isAC) { setAutocompleteOptions([]); return; }
    clearTimeout(acTimerRef.current);
    acTimerRef.current = setTimeout(async () => {
      try {
        const qs = new URLSearchParams({ index: indexName, q: query, size: '8' });
        if (autocompleteField) qs.set('field', autocompleteField);
        const res = await fetch('/api/autocomplete?' + qs.toString());
        const data = await res.json();
        if (data.field) setAutocompleteField(data.field);
        setAutocompleteOptions((data.options || []).map(v => String(v).trim()).filter(Boolean));
      } catch(err) { setAutocompleteOptions([]); }
    }, 120);
    return () => clearTimeout(acTimerRef.current);
  }, [indexName, query, autocompleteField, capability, queryMode]);

  const addChat = (role, text) => {
    const time = new Date().toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' });
    setChatMessages(prev => [...prev, { role, text, time }]);
  };

  const onMessage = useCallback((data) => {
    if (data.type === 'connected') {
      addChat('agent', data.message);
    } else if (data.type === 'search_results') {
      const d = data.data || {};
      setResults(d.hits || []);
      setSearchMeta({
        queryMode: d.query_mode || '',
        capability: d.capability || '',
        took_ms: d.took_ms != null ? d.took_ms : 0,
        usedSemantic: d.used_semantic || false,
        fallbackReason: d.fallback_reason || '',
        total: d.total != null ? d.total : (d.hits || []).length,
        error: d.error || '',
      });
      setCapability(d.capability || '');
      setQueryMode(d.query_mode || '');
      setSearching(false);
      if (indexName) loadSuggestions(indexName);
    } else if (data.type === 'chat_response') {
      setChatLoading(false);
      addChat('agent', data.message);
    } else if (data.type === 'error') {
      setSearching(false);
      setChatLoading(false);
      addChat('error', data.message);
    }
  }, [indexName, loadSuggestions]);

  const { connected, send } = useWebSocket(onMessage);

  useEffect(() => {
    if (chatEndRef.current) chatEndRef.current.scrollIntoView({ behavior: 'smooth' });
  }, [chatMessages]);

  const runSearch = useCallback((overrideQuery, opts) => {
    if (!connected) return;
    const q = overrideQuery != null ? overrideQuery : query;
    const o = opts || {};
    setSearching(true);
    setResults(null);
    setSearchMeta(null);
    setAutocompleteOptions([]);
    send({ action: 'search', query: q, index: indexName, size: size, intent: o.intent || '', field: o.field || '' });
  }, [connected, query, indexName, size, send]);

  const onSuggestionClick = (entry) => {
    setAutocompleteField(entry.capability === 'autocomplete' ? entry.field : '');
    setQuery(entry.text);
    runSearch(entry.text);
  };

  const onACOptionClick = (value) => {
    setAutocompleteOptions([]);
    setQuery(value);
    runSearch(value, { intent: 'autocomplete_selection', field: autocompleteField });
  };

  const handleChat = (e) => {
    e.preventDefault();
    const msg = chatInput.trim();
    if (!msg || !connected) return;
    addChat('user', msg);
    setChatInput('');
    setChatLoading(true);
    send({ action: 'chat', message: msg });
  };

  const hits = results || [];
  const meta = searchMeta;

  return (
    <div className="iui-layout">

      <div className="iui-search-panel">
        <div className="iui-search-scroll">
          <div className="shell">

            <header className="topbar">
              <div className="brand">
                <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 42.6667 42.6667" fill="none" aria-label="OpenSearch" role="img">
                  <path fill="#075985" d="M41.1583 15.6667C40.3252 15.6667 39.6499 16.342 39.6499 17.1751C39.6499 29.5876 29.5876 39.6499 17.1751 39.6499C16.342 39.6499 15.6667 40.3252 15.6667 41.1583C15.6667 41.9913 16.342 42.6667 17.1751 42.6667C31.2537 42.6667 42.6667 31.2537 42.6667 17.1751C42.6667 16.342 41.9913 15.6667 41.1583 15.6667Z"/>
                  <path fill="#082F49" d="M32.0543 25.3333C33.5048 22.967 34.9077 19.8119 34.6317 15.3947C34.06 6.24484 25.7726 -0.696419 17.9471 0.0558224C14.8835 0.350311 11.7379 2.84747 12.0173 7.32032C12.1388 9.26409 13.0902 10.4113 14.6363 11.2933C16.1079 12.1328 17.9985 12.6646 20.1418 13.2674C22.7308 13.9956 25.7339 14.8135 28.042 16.5144C30.8084 18.553 32.6994 20.9162 32.0543 25.3333Z"/>
                  <path fill="#075985" d="M2.6124 9.33333C1.16184 11.6997 -0.241004 14.8548 0.0349954 19.2719C0.606714 28.4218 8.89407 35.3631 16.7196 34.6108C19.7831 34.3164 22.9288 31.8192 22.6493 27.3463C22.5279 25.4026 21.5765 24.2554 20.0304 23.3734C18.5588 22.5339 16.6681 22.0021 14.5248 21.3992C11.9358 20.6711 8.93276 19.8532 6.62463 18.1522C3.85831 16.1136 1.96728 13.7505 2.6124 9.33333Z"/>
                </svg>
                OpenSearch
              </div>
              <div className="divider"></div>
              <div className="title">Search Builder</div>
              <div className={'backend-badge ' + backendType + ' ' + (connected ? 'connected' : 'disconnected')}>
                <span className="backend-dot"></span>
                <span className="backend-label">{connected ? 'Connected' : 'Disconnected'}</span>
                {backendEndpoint && <span className="backend-endpoint">{backendEndpoint}</span>}
              </div>
            </header>

            <section className="panel">
              <h2>Search</h2>

              <div className="index-row">
                <span>Index:</span>
                <input
                  value={indexName}
                  onChange={e => { setIndexName(e.target.value); loadSuggestions(e.target.value); }}
                  placeholder="e.g. imdb_titles"
                />
                <label style={{ fontSize: '13px', color: 'hsl(var(--muted-foreground))', display: 'flex', alignItems: 'center', gap: '6px', whiteSpace: 'nowrap' }}>
                  Size
                  <input
                    type="number" min={1} max={100} value={size}
                    onChange={e => setSize(Number(e.target.value))}
                    style={{ width: '56px', border: '1px solid hsl(var(--input))', borderRadius: 'var(--radius-xl)', padding: '6px 8px', fontSize: '13px', fontFamily: 'var(--font-sans)', background: 'hsl(var(--background))', color: 'hsl(var(--foreground))' }}
                  />
                </label>
              </div>

              <div className="search-row">
                <div className="query-wrap">
                  <span className="query-icon">
                    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                      <circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>
                    </svg>
                  </span>
                  <input
                    value={query}
                    onChange={e => setQuery(e.target.value)}
                    onKeyDown={e => { if (e.key === 'Enter') { setAutocompleteOptions([]); runSearch(); } }}
                    placeholder="Enter your search query..."
                    disabled={!connected}
                  />
                  {autocompleteOptions.length > 0 && (
                    <div className="autocomplete-menu">
                      {autocompleteOptions.map(opt => (
                        <button key={opt} type="button" className="autocomplete-option"
                          onMouseDown={e => e.preventDefault()}
                          onClick={() => onACOptionClick(opt)}>
                          {opt}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
                <button className="search-btn" onClick={() => runSearch()} disabled={!connected || searching}>
                  {searching ? '...' : 'Search'}
                </button>
              </div>

              <div className="suggestions">
                <button className="suggestion-toggle" onClick={() => setShowSuggestions(!showSuggestions)}>
                  Try these auto-generated queries
                  <span>{showSuggestions ? '▴' : '▾'}</span>
                </button>
                {showSuggestions && (
                  <div className="chips">
                    {suggestions.map((item, i) => (
                      <button key={i} className="chip" onClick={() => onSuggestionClick(item)}>
                        {item.text}
                        {item.capability && CAP_LABEL[item.capability] && (
                          <span className={'chip-badge cap-' + item.capability}>
                            {CAP_LABEL[item.capability]}
                          </span>
                        )}
                      </button>
                    ))}
                  </div>
                )}
              </div>

              <div className="status-row">
                {meta ? (
                  meta.error
                    ? React.createElement('span', { className: 'error' }, meta.error)
                    : React.createElement(React.Fragment, null,
                        React.createElement('span', null, meta.total + ' hit' + (meta.total !== 1 ? 's' : '') + ' in ' + meta.took_ms + 'ms'),
                        meta.queryMode ? React.createElement('span', null, 'mode: ' + meta.queryMode) : null,
                        meta.capability ? React.createElement('span', null, 'capability: ' + meta.capability) : null,
                        React.createElement('span', null, 'semantic: ' + (meta.usedSemantic ? 'on' : 'off')),
                        meta.fallbackReason ? React.createElement('span', null, 'fallback: ' + meta.fallbackReason) : null
                      )
                ) : React.createElement('span', null, 'Ready')}
              </div>

              {searching && (
                <div className="loading-container">
                  <div className="loading-bar"><div className="loading-bar-progress"></div></div>
                  <div className="loading-text">Searching...</div>
                </div>
              )}

              <div className="results">
                {!searching && results !== null && hits.length === 0 && (
                  <div style={{ textAlign: 'center', color: 'hsl(var(--muted-foreground))', padding: '24px 0', fontSize: '14px' }}>
                    No results found.
                  </div>
                )}
                {hits.map((hit, i) => React.createElement(ResultCard, { key: hit.id || hit._id || i, hit: hit, index: i }))}
              </div>
            </section>

          </div>
        </div>
      </div>

      <div
        onMouseDown={onDividerMouseDown}
        style={{
          width: '5px',
          flexShrink: 0,
          cursor: 'col-resize',
          background: 'hsl(var(--border))',
          transition: 'background 150ms',
          position: 'relative',
          zIndex: 10,
        }}
        onMouseEnter={e => e.currentTarget.style.background = 'hsl(var(--primary))'}
        onMouseLeave={e => e.currentTarget.style.background = 'hsl(var(--border))'}
        title="Drag to resize"
      />

      <div className="iui-chat-panel" style={{ width: chatWidth + 'px' }}>
        <div className="iui-chat-header">Agent Chat</div>
        <div className="iui-chat-messages">
          {chatMessages.map((msg, i) => React.createElement(ChatMessage, { key: i, msg: msg }))}
          {chatLoading && React.createElement('div', { className: 'iui-thinking' }, 'Thinking...')}
          <div ref={chatEndRef}></div>
        </div>
        <div className="iui-chat-input-area">
          <form className="iui-chat-form" onSubmit={handleChat}>
            <input
              className="iui-chat-input"
              value={chatInput}
              onChange={e => setChatInput(e.target.value)}
              placeholder={connected ? 'Ask about the results...' : 'Connecting...'}
              disabled={!connected || chatLoading}
            />
            <button type="submit" className="search-btn"
              style={{ padding: '0 16px', fontSize: '14px' }}
              disabled={!connected || chatLoading || !chatInput.trim()}>
              Send
            </button>
          </form>
          <div className="iui-chat-hint">
            Try: "evaluate these results" · "suggest a better query" · "why is #1 ranked first?"
          </div>
        </div>
      </div>

    </div>
  );
}

ReactDOM.createRoot(document.getElementById('root')).render(
  React.createElement(WsGate, null, React.createElement(InteractiveApp))
);
