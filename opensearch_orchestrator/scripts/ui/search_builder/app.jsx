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
          <svg viewBox="0 0 100 100" xmlns="http://www.w3.org/2000/svg">
            <defs>
              <linearGradient id="lg" x1="0%" y1="0%" x2="100%" y2="100%">
                <stop offset="0%" stopColor="#005EB8" />
                <stop offset="100%" stopColor="#00A3E0" />
              </linearGradient>
            </defs>
            <path d="M50 5C25.2 5 5 25.2 5 50c0 11.4 4.3 21.8 11.3 29.7C22 73 31 68.5 41 67.3c12.7-1.6 24.5-7.4 33-17.3 5-5.8 8.5-12.6 10.3-19.8C81.5 17.5 67.3 5 50 5z" fill="url(#lg)" />
            <path d="M50 95c24.8 0 45-20.2 45-45 0-3.6-.4-7.1-1.2-10.5-1.8 7.2-5.3 14-10.3 19.8-8.5 9.9-20.3 15.7-33 17.3-10 1.2-19 5.7-24.7 12.4C33.2 93 41.3 95 50 95z" fill="url(#lg)" opacity="0.6" />
          </svg>
          OpenSearch
        </div>
        <div className="divider"></div>
        <div className="title">Search Builder</div>
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
          {loading && <span className="loading-eval">⏳ Evaluating relevance with LLM...</span>}
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
              className={`result-card ${item.relevance_color ? `relevance-${item.relevance_color}` : ''}`}
              key={item.id || idx}
              style={{ animationDelay: `${idx * 35}ms` }}
            >
              <div className="result-head">
                <span>ID: {item.id || "(none)"}</span>
                <span className="score">score {Number(item.score || 0).toFixed(3)}</span>
                {item.relevance_score !== undefined && (
                  <span className={`relevance-badge relevance-${item.relevance_color}`}>
                    {item.relevance_score === 1 ? "✓ Relevant" : "✗ Not Relevant"}
                  </span>
                )}
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
