const { useEffect, useState } = React;

function App() {
  const [indexName, setIndexName] = useState("");
  const [query, setQuery] = useState("");
  const [suggestions, setSuggestions] = useState([]);
  const [showSuggestions, setShowSuggestions] = useState(true);
  const [results, setResults] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [stats, setStats] = useState("Ready");
  const [queryMode, setQueryMode] = useState("");
  const [capability, setCapability] = useState("");
  const [fallbackReason, setFallbackReason] = useState("");
  const [usedSemantic, setUsedSemantic] = useState(false);
  const [autocompleteField, setAutocompleteField] = useState("");
  const [autocompleteOptions, setAutocompleteOptions] = useState([]);
  const [backendType, setBackendType] = useState("");
  const [backendEndpoint, setBackendEndpoint] = useState("");
  const [backendConnected, setBackendConnected] = useState(false);

  const capabilityLabel = {
    exact: "Exact",
    semantic: "Semantic",
    structured: "Structured",
    combined: "Combined",
    autocomplete: "Autocomplete",
    fuzzy: "Fuzzy",
    manual: "Manual",
  };

  const loadSuggestions = async (index) => {
    try {
      const qs = new URLSearchParams();
      if (index) {
        qs.set("index", index);
      }
      const res = await fetch(`/api/suggestions?${qs.toString()}`);
      const data = await res.json();
      const rawMeta = Array.isArray(data.suggestion_meta) ? data.suggestion_meta : [];
      const mappedMeta = rawMeta
        .map((entry) => ({
          text: String(entry.text || "").trim(),
          capability: String(entry.capability || "").trim().toLowerCase(),
          query_mode: String(entry.query_mode || "default").trim(),
          field: String(entry.field || "").trim(),
          value: String(entry.value || "").trim(),
          case_insensitive: Boolean(entry.case_insensitive),
        }))
        .filter((entry) => entry.text.length > 0 && entry.capability.length > 0);
      if (mappedMeta.length > 0) {
        setSuggestions(mappedMeta);
        return;
      }
      const legacy = Array.isArray(data.suggestions) ? data.suggestions : [];
      const fallbackText = legacy
        .map((text) => String(text || "").trim())
        .find((text) => text.length > 0);
      if (!fallbackText) {
        setSuggestions([]);
        return;
      }
      setSuggestions([
        {
          text: fallbackText,
          capability: "",
          query_mode: "default",
          field: "",
          value: "",
          case_insensitive: false,
        },
      ]);
    } catch (_err) {
      setSuggestions([]);
    }
  };

  const loadConfig = async () => {
    try {
      const res = await fetch("/api/config");
      const data = await res.json();
      setBackendType(String(data.backend_type || "").trim());
      setBackendEndpoint(String(data.endpoint || "").trim());
      setBackendConnected(Boolean(data.connected));
      const defaultIndex = (data.default_index || "").trim();
      if (defaultIndex) {
        setIndexName(defaultIndex);
        await loadSuggestions(defaultIndex);
        return;
      }
      await loadSuggestions("");
    } catch (_err) {
      await loadSuggestions("");
    }
  };

  useEffect(() => {
    loadConfig();
  }, []);

  useEffect(() => {
    const effectiveIndex = indexName.trim();
    const prefix = query.trim();
    const autocompleteActive =
      (capability === "autocomplete" ||
        queryMode.startsWith("autocomplete") ||
        autocompleteField.length > 0) &&
      effectiveIndex.length > 0 &&
      prefix.length >= 2;

    if (!autocompleteActive) {
      setAutocompleteOptions([]);
      return;
    }

    let cancelled = false;
    const timer = setTimeout(async () => {
      try {
        const qs = new URLSearchParams();
        qs.set("index", effectiveIndex);
        qs.set("q", prefix);
        qs.set("size", "8");
        if (autocompleteField) {
          qs.set("field", autocompleteField);
        }
        const res = await fetch(`/api/autocomplete?${qs.toString()}`);
        const data = await res.json();
        const resolvedField = String(data.field || "").trim();
        const options = Array.isArray(data.options)
          ? data.options
              .map((value) => String(value || "").trim())
              .filter((value) => value.length > 0)
          : [];
        if (!cancelled) {
          if (resolvedField) {
            setAutocompleteField((prev) => (prev === resolvedField ? prev : resolvedField));
          }
          setAutocompleteOptions(options);
        }
      } catch (_err) {
        if (!cancelled) {
          setAutocompleteOptions([]);
        }
      }
    }, 120);

    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [indexName, query, capability, queryMode, autocompleteField]);

  const runSearch = async (overrideQuery = null, options = {}) => {
    const effectiveQuery = (overrideQuery !== null ? overrideQuery : query).trim();
    const effectiveIndex = indexName.trim();
    const searchIntent = String(options.intent || "").trim();
    const fieldHint = String(options.field || "").trim();
    if (!effectiveIndex) {
      setError("Please enter an index name.");
      return;
    }
    setError("");
    setLoading(true);
    try {
      const qs = new URLSearchParams();
      qs.set("index", effectiveIndex);
      qs.set("q", effectiveQuery);
      qs.set("size", "20");
      qs.set("debug", "1");
      if (searchIntent) {
        qs.set("intent", searchIntent);
      }
      if (fieldHint) {
        qs.set("field", fieldHint);
      }
      const res = await fetch(`/api/search?${qs.toString()}`);
      const data = await res.json();
      if (data.error) {
        setError(data.error);
        setResults([]);
        setStats("Search failed");
        setQueryMode("");
        setCapability("");
        setFallbackReason("");
        setUsedSemantic(false);
      } else {
        setResults(Array.isArray(data.hits) ? data.hits : []);
        setStats(`Loaded ${data.total ?? 0} hit(s) in ${data.took_ms ?? 0} ms`);
        setQueryMode(String(data.query_mode || ""));
        setCapability(String(data.capability || ""));
        setFallbackReason(String(data.fallback_reason || ""));
        setUsedSemantic(Boolean(data.used_semantic));
        await loadSuggestions(effectiveIndex);
      }
    } catch (err) {
      setError(`Request failed: ${err.message}`);
      setResults([]);
      setStats("Search failed");
      setQueryMode("");
      setCapability("");
      setFallbackReason("");
      setUsedSemantic(false);
    } finally {
      setLoading(false);
    }
  };

  const onSuggestionClick = (entry) => {
    const text = String(entry?.text || "").trim();
    const entryCapability = String(entry?.capability || "").trim().toLowerCase();
    const entryField = String(entry?.field || "").trim();
    if (!text) {
      return;
    }
    setAutocompleteField(entryCapability === "autocomplete" ? entryField : "");
    setAutocompleteOptions([]);
    setQuery(text);
    runSearch(text);
  };

  const onAutocompleteOptionClick = (value) => {
    const text = String(value || "").trim();
    if (!text) {
      return;
    }
    setAutocompleteOptions([]);
    setQuery(text);
    runSearch(text, {
      intent: "autocomplete_selection",
      field: autocompleteField,
    });
  };

  return (
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
        {backendType && (
          <div className={`backend-badge ${backendType} ${backendConnected ? "connected" : "disconnected"}`}>
            <span className="backend-dot"></span>
            <span className="backend-label">
              {backendType === "cloud" ? "AWS Cloud" : "Local"}
            </span>
            {backendEndpoint && (
              <span className="backend-endpoint">{backendEndpoint}</span>
            )}
          </div>
        )}
      </header>

      <section className="panel">
        <h2>Test Your Search</h2>
        <div className="index-row">
          <span>Index:</span>
          <input
            value={indexName}
            onChange={(e) => setIndexName(e.target.value)}
            placeholder="e.g. movies-index"
          />
        </div>
        <div className="search-row">
          <div className="query-wrap">
            <span className="query-icon">
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="11" cy="11" r="8"/>
                <line x1="21" y1="21" x2="16.65" y2="16.65"/>
              </svg>
            </span>
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  setAutocompleteOptions([]);
                  runSearch();
                }
              }}
              placeholder="Enter your search query..."
            />
            {autocompleteOptions.length > 0 && (
              <div className="autocomplete-menu">
                {autocompleteOptions.map((option) => (
                  <button
                    key={option}
                    type="button"
                    className="autocomplete-option"
                    onMouseDown={(e) => e.preventDefault()}
                    onClick={() => onAutocompleteOptionClick(option)}
                  >
                    {option}
                  </button>
                ))}
              </div>
            )}
          </div>
          <button className="search-btn" onClick={() => runSearch()} disabled={loading}>
            {loading ? "..." : "Search"}
          </button>
        </div>

        <div className="suggestions">
          <button className="suggestion-toggle" onClick={() => setShowSuggestions(!showSuggestions)}>
            Try these auto-generated queries
            <span>{showSuggestions ? "▴" : "▾"}</span>
          </button>
          {showSuggestions && (
            <div className="chips">
              {suggestions.map((item) => (
                <button
                  key={`${item.text}-${item.capability || "none"}`}
                  className="chip"
                  onClick={() => onSuggestionClick(item)}
                >
                  {item.text}
                  {item.capability && (
                    <span className={`chip-badge cap-${item.capability}`}>
                      {capabilityLabel[item.capability] || item.capability}
                    </span>
                  )}
                </button>
              ))}
            </div>
          )}
        </div>

        <div className="status-row">
          <span>{stats}</span>
          {queryMode && <span>mode: {queryMode}</span>}
          {capability && <span>capability: {capability}</span>}
          {!error && <span>semantic: {usedSemantic ? "on" : "off"}</span>}
          {fallbackReason && <span>fallback: {fallbackReason}</span>}
          {error && <span className="error">{error}</span>}
        </div>

        {loading && (
          <div className="loading-container">
            <div className="loading-bar">
              <div className="loading-bar-progress"></div>
            </div>
            <div className="loading-text">Searching...</div>
          </div>
        )}

        <div className="results">
          {results.map((item, idx) => (
            <article
              className="result-card"
              key={item.id || idx}
              style={{ animationDelay: `${idx * 35}ms` }}
            >
              <div className="result-head">
                <span>ID: {item.id || "(none)"}</span>
                <span className="score">score {Number(item.score || 0).toFixed(3)}</span>
              </div>
              <div className="preview">{item.preview}</div>
              <details>
                <summary>View full document</summary>
                <pre>{JSON.stringify(item.source, null, 2)}</pre>
              </details>
            </article>
          ))}
        </div>
      </section>
    </div>
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(<App />);
