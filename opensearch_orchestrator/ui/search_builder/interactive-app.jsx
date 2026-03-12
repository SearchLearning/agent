const { useEffect, useState, useRef, useCallback } = React;

const WS_URL = 'ws://127.0.0.1:8766';
const WS_PROBE_TIMEOUT_MS = 3000;

const CAP_LABEL = {
  exact: 'Exact', semantic: 'Semantic', structured: 'Structured',
  combined: 'Combined', autocomplete: 'Autocomplete', fuzzy: 'Fuzzy', manual: 'Manual',
};

const MODE_LABEL = {
  hybrid: 'Hybrid', hybrid_structured: 'Hybrid+Filter', structured_filter: 'Filter',
  term: 'Term', prefix: 'Prefix', fuzzy: 'Fuzzy', default: '',
};

function WsGate({ children }) {
  const [status, setStatus] = useState('probing');
  useEffect(() => {
    let settled = false;
    const ws = new WebSocket(WS_URL);
    const timer = setTimeout(() => {
      if (!settled) { settled = true; ws.close(); setStatus('unavailable'); }
    }, WS_PROBE_TIMEOUT_MS);
    ws.onopen = () => { if (!settled) { settled = true; clearTimeout(timer); ws.close(); setStatus('ok'); } };
    ws.onerror = () => { if (!settled) { settled = true; clearTimeout(timer); setStatus('unavailable'); } };
    ws.onclose = () => { if (!settled) { settled = true; clearTimeout(timer); setStatus('unavailable'); } };
    return () => { clearTimeout(timer); try { ws.close(); } catch(e) {} };
  }, []);
  if (status === 'probing') {
    return React.createElement('div', {
      style: { display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100vh', fontFamily: 'var(--font-sans)', fontSize: '14px', color: 'hsl(var(--muted-foreground))', background: 'hsl(var(--background))' }
    }, 'Connecting to search service…');
  }
  if (status === 'unavailable') {
    window.location.replace('/');
    return React.createElement('div', {
      style: { display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100vh', fontFamily: 'var(--font-sans)', fontSize: '14px', color: 'hsl(var(--muted-foreground))', background: 'hsl(var(--background))' }
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

const JUDGMENT_COLORS = {
  1: { border: '2px solid #22c55e', background: 'rgba(34,197,94,0.06)' },
  0: { border: '2px solid #ef4444', background: 'rgba(239,68,68,0.06)' },
};

function JudgmentToggle({ docId, judgment, onJudge }) {
  return React.createElement('div', {
    style: { display: 'flex', gap: '4px', alignItems: 'center', flexShrink: 0 }
  },
    React.createElement('button', {
      title: 'Relevant', onClick: (e) => { e.stopPropagation(); onJudge(docId, judgment === 1 ? null : 1); },
      style: { border: 'none', cursor: 'pointer', padding: '2px 5px', borderRadius: '4px', fontSize: '14px', lineHeight: 1, opacity: judgment === 1 ? 1 : 0.35, background: judgment === 1 ? 'rgba(34,197,94,0.15)' : 'transparent' }
    }, '👍'),
    React.createElement('button', {
      title: 'Irrelevant', onClick: (e) => { e.stopPropagation(); onJudge(docId, judgment === 0 ? null : 0); },
      style: { border: 'none', cursor: 'pointer', padding: '2px 5px', borderRadius: '4px', fontSize: '14px', lineHeight: 1, opacity: judgment === 0 ? 1 : 0.35, background: judgment === 0 ? 'rgba(239,68,68,0.15)' : 'transparent' }
    }, '👎')
  );
}

function EvalDashboard({ dashboardData, onClose, onCommit, committed }) {
  if (!dashboardData) return null;
  const { query_results = [], summary = {} } = dashboardData;
  const scoreColor = (s) => s >= 4 ? '#22c55e' : s >= 3 ? '#f59e0b' : '#ef4444';
  const pctColor = (p) => p >= 0.7 ? '#22c55e' : p >= 0.4 ? '#f59e0b' : '#ef4444';
  const capBreakdown = summary.capability_breakdown || {};
  const recommendations = summary.recommendations || [];
  const config = summary.current_config || {};
  const dims = summary.dimensions || {};
  const DIM_LABELS = { relevance: 'Relevance', query_coverage: 'Query Coverage', ranking_quality: 'Ranking Quality', capability_gaps: 'Capability Gaps' };
  const DIM_ICONS = { relevance: '🎯', query_coverage: '📋', ranking_quality: '📈', capability_gaps: '🔍' };

  return React.createElement('div', {
    style: { display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden' }
  },
    // Header
    React.createElement('div', {
      style: { padding: '14px 16px', borderBottom: '1px solid hsl(var(--border))', display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexShrink: 0 }
    },
      React.createElement('div', { style: { fontWeight: 700, fontSize: '15px' } }, '📊 Evaluation Dashboard'),
      React.createElement('div', { style: { display: 'flex', alignItems: 'center', gap: '8px' } },
        React.createElement('button', {
          onClick: onCommit, disabled: committed,
          style: { padding: '5px 12px', fontSize: '12px', borderRadius: '6px', cursor: committed ? 'default' : 'pointer', background: committed ? '#22c55e' : '#6d28d9', color: 'white', border: 'none', fontWeight: 600 }
        }, committed ? '✓ Sent to Kiro' : '🚀 Send to Kiro'),
        React.createElement('button', {
          onClick: onClose,
          style: { border: 'none', background: 'transparent', cursor: 'pointer', fontSize: '18px', color: 'hsl(var(--muted-foreground))', padding: '2px 6px' }
        }, '✕')
      )
    ),

    // Scrollable content
    React.createElement('div', {
      style: { flex: 1, overflow: 'auto', padding: '14px 16px' }
    },
      // Overall score card
      React.createElement('div', {
        style: { padding: '14px', borderRadius: '8px', background: scoreColor(summary.overall_score || 0) + '11', border: '1px solid ' + scoreColor(summary.overall_score || 0) + '33', marginBottom: '14px' }
      },
        React.createElement('div', { style: { display: 'flex', alignItems: 'center', gap: '10px', marginBottom: '8px' } },
          React.createElement('span', {
            style: { fontSize: '28px', fontWeight: 800, color: scoreColor(summary.overall_score || 0) }
          }, (summary.overall_score || 0) + '/5'),
          React.createElement('div', null,
            React.createElement('div', { style: { fontWeight: 700, fontSize: '14px' } }, summary.verdict || ''),
            React.createElement('div', { style: { fontSize: '12px', color: 'hsl(var(--muted-foreground))' } },
              (summary.queries_evaluated || 0) + ' queries evaluated'
            )
          )
        ),
        React.createElement('div', { style: { display: 'flex', gap: '16px', fontSize: '12px', color: 'hsl(var(--muted-foreground))' } },
          React.createElement('span', null, 'Precision: ' + ((summary.avg_precision || 0) * 100).toFixed(0) + '%'),
          React.createElement('span', null, 'NDCG: ' + (summary.avg_ndcg || 0).toFixed(3)),
          React.createElement('span', null, (summary.total_relevant || 0) + '/' + (summary.total_judged || 0) + ' relevant')
        )
      ),

      // 4 Dimensions
      Object.keys(dims).length > 0 && React.createElement('div', { style: { marginBottom: '14px' } },
        React.createElement('div', { style: { fontWeight: 600, fontSize: '13px', marginBottom: '6px' } }, 'Evaluation Dimensions'),
        Object.entries(dims).map(([key, dim]) =>
          React.createElement('div', {
            key: key,
            style: { padding: '10px', borderRadius: '6px', marginBottom: '6px', background: 'hsl(var(--card))', border: '1px solid hsl(var(--border))' }
          },
            React.createElement('div', { style: { display: 'flex', alignItems: 'center', gap: '6px', marginBottom: '4px' } },
              React.createElement('span', null, DIM_ICONS[key] || ''),
              React.createElement('span', { style: { fontWeight: 600, fontSize: '12px', flex: 1 } }, DIM_LABELS[key] || key),
              React.createElement('span', {
                style: { fontSize: '13px', fontWeight: 700, color: scoreColor(dim.score), padding: '1px 8px', borderRadius: '10px', background: scoreColor(dim.score) + '18' }
              }, dim.score + '/5')
            ),
            React.createElement('div', { style: { fontSize: '11px', color: 'hsl(var(--muted-foreground))', lineHeight: '1.4' } }, dim.detail)
          )
        )
      ),

      // Current config
      config.solution && React.createElement('div', {
        style: { padding: '10px', borderRadius: '6px', background: 'hsl(var(--muted))', marginBottom: '14px', fontSize: '12px' }
      },
        React.createElement('div', { style: { fontWeight: 600, marginBottom: '4px', fontSize: '12px' } }, 'Current Configuration'),
        React.createElement('div', { style: { color: 'hsl(var(--muted-foreground))', lineHeight: '1.5' } },
          React.createElement('div', null, 'Strategy: ' + config.solution),
          config.query_pattern && React.createElement('div', null, 'Query pattern: ' + config.query_pattern),
          config.performance && React.createElement('div', null, 'Performance: ' + config.performance),
          config.budget && React.createElement('div', null, 'Budget: ' + config.budget),
          config.deployment_preference && React.createElement('div', null, 'Deployment: ' + config.deployment_preference)
        ),
        React.createElement('div', { style: { marginTop: '6px', display: 'flex', gap: '4px', flexWrap: 'wrap' } },
          config.has_bm25 && React.createElement('span', { style: { fontSize: '10px', padding: '2px 6px', borderRadius: '10px', background: '#e0f5e8', color: '#1a5c2e', border: '1px solid #b0e0c2' } }, 'BM25'),
          config.has_semantic && React.createElement('span', { style: { fontSize: '10px', padding: '2px 6px', borderRadius: '10px', background: 'hsl(var(--accent))', color: 'hsl(var(--primary))', border: '1px solid hsl(204 80% 80%)' } }, 'Dense Vector'),
          config.has_sparse && React.createElement('span', { style: { fontSize: '10px', padding: '2px 6px', borderRadius: '10px', background: '#eddffb', color: '#4f2f7c', border: '1px solid #d8c0f6' } }, 'Sparse Vector'),
          config.has_hybrid && React.createElement('span', { style: { fontSize: '10px', padding: '2px 6px', borderRadius: '10px', background: '#ffe9ce', color: '#6a3d0a', border: '1px solid #ffd29e' } }, 'Hybrid'),
          config.has_agentic && React.createElement('span', { style: { fontSize: '10px', padding: '2px 6px', borderRadius: '10px', background: '#fde2ec', color: '#7a3048', border: '1px solid #f8bdd2' } }, 'Agentic'),
          ...(config.declared_capabilities || []).map(c =>
            React.createElement('span', { key: c, style: { fontSize: '10px', padding: '2px 6px', borderRadius: '10px', background: 'hsl(var(--secondary))', color: 'hsl(var(--secondary-foreground))' } }, c)
          )
        )
      ),

      // Capability breakdown
      Object.keys(capBreakdown).length > 0 && React.createElement('div', { style: { marginBottom: '14px' } },
        React.createElement('div', { style: { fontWeight: 600, fontSize: '13px', marginBottom: '6px' } }, 'By Capability'),
        Object.entries(capBreakdown).map(([cap, stats]) =>
          React.createElement('div', {
            key: cap,
            style: { display: 'flex', alignItems: 'center', gap: '8px', padding: '6px 0', borderBottom: '1px solid hsl(var(--border) / 0.5)', fontSize: '12px' }
          },
            React.createElement('span', {
              className: 'chip-badge cap-' + cap,
              style: { fontSize: '11px', padding: '2px 8px', borderRadius: '12px' }
            }, CAP_LABEL[cap] || cap),
            React.createElement('span', { style: { color: pctColor(stats.precision) } }, (stats.precision * 100).toFixed(0) + '% precision'),
            React.createElement('span', { style: { color: 'hsl(var(--muted-foreground))' } }, 'NDCG ' + stats.ndcg.toFixed(3)),
            React.createElement('span', { style: { color: 'hsl(var(--muted-foreground))' } }, '(' + stats.count + ' quer' + (stats.count === 1 ? 'y' : 'ies') + ')')
          )
        )
      ),

      // Recommendations
      recommendations.length > 0 && React.createElement('div', { style: { marginBottom: '14px' } },
        React.createElement('div', { style: { fontWeight: 600, fontSize: '13px', marginBottom: '6px' } }, 'Recommendations'),
        recommendations.map((rec, i) =>
          React.createElement('div', {
            key: i,
            style: { padding: '10px', borderRadius: '6px', marginBottom: '6px', background: rec.preference_change && Object.keys(rec.preference_change).length > 0 ? '#f59e0b11' : 'hsl(var(--muted))', border: '1px solid ' + (rec.preference_change && Object.keys(rec.preference_change).length > 0 ? '#f59e0b33' : 'hsl(var(--border))') }
          },
            React.createElement('div', { style: { fontWeight: 600, fontSize: '12px', marginBottom: '2px' } }, rec.area),
            React.createElement('div', { style: { fontSize: '12px', color: 'hsl(var(--muted-foreground))', marginBottom: '4px' } }, rec.issue),
            React.createElement('div', { style: { fontSize: '12px', color: 'hsl(var(--primary))' } }, '→ ' + rec.action),
            rec.preference_change && Object.keys(rec.preference_change).length > 0 && React.createElement('div', {
              style: { marginTop: '4px', display: 'flex', gap: '4px', flexWrap: 'wrap' }
            },
              Object.entries(rec.preference_change).map(([k, v]) =>
                React.createElement('span', {
                  key: k,
                  style: { fontSize: '10px', padding: '2px 6px', borderRadius: '10px', background: '#f59e0b22', color: '#b45309', border: '1px solid #f59e0b44' }
                }, k + ': ' + v)
              )
            )
          )
        )
      ),

      // Per-query results
      React.createElement('div', null,
        React.createElement('div', { style: { fontWeight: 600, fontSize: '13px', marginBottom: '6px' } }, 'Per-Query Results'),
        query_results.map((qr, i) => {
          const m = qr.metrics;
          return React.createElement('div', {
            key: i,
            style: { padding: '8px 10px', borderRadius: '6px', marginBottom: '4px', background: 'hsl(var(--card))', border: '1px solid hsl(var(--border))', fontSize: '12px' }
          },
            React.createElement('div', { style: { display: 'flex', alignItems: 'center', gap: '6px', marginBottom: '2px' } },
              React.createElement('span', { style: { fontWeight: 600, flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' } }, '"' + qr.query + '"'),
              qr.capability && React.createElement('span', {
                className: 'chip-badge cap-' + qr.capability,
                style: { fontSize: '10px', padding: '1px 6px', borderRadius: '10px' }
              }, CAP_LABEL[qr.capability] || qr.capability),
              qr.query_mode && MODE_LABEL[qr.query_mode] && React.createElement('span', {
                style: { fontSize: '10px', padding: '1px 6px', borderRadius: '10px', background: 'hsl(var(--muted))', color: 'hsl(var(--muted-foreground))' }
              }, MODE_LABEL[qr.query_mode])
            ),
            qr.error
              ? React.createElement('div', { style: { color: '#ef4444', fontSize: '11px' } }, qr.error)
              : m
                ? React.createElement('div', { style: { display: 'flex', gap: '10px', color: 'hsl(var(--muted-foreground))', fontSize: '11px' } },
                    React.createElement('span', { style: { color: pctColor(m.precision) } }, (m.precision * 100).toFixed(0) + '% precision'),
                    React.createElement('span', null, 'NDCG ' + m.ndcg.toFixed(3)),
                    React.createElement('span', null, m.n_relevant + '/' + m.n_judged + ' relevant'),
                    m.top_is_relevant === false && React.createElement('span', { style: { color: '#ef4444' } }, '⚠ top irrelevant')
                  )
                : React.createElement('div', { style: { color: 'hsl(var(--muted-foreground))', fontSize: '11px' } }, (qr.hit_count || 0) + ' hits, no judgments')
          );
        })
      )
    )
  );
}

function ResultCard({ hit, index, judgment, onJudge }) {
  const src = hit.source || hit._source || {};
  const score = hit.score != null ? hit.score : hit._score;
  const id = hit.id || hit._id;
  const title = src.primaryTitle || src.title || src.name || src.originalTitle || id;
  const meta = [src.titleType, src.startYear, src.genres].filter(Boolean).join(' · ');
  const judgeStyle = judgment != null ? JUDGMENT_COLORS[judgment] : {};
  return (
    <article className="result-card" style={{ animationDelay: (index * 35) + 'ms', ...judgeStyle }}>
      <div className="result-head">
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', minWidth: 0, flex: 1 }}>
          <span style={{ minWidth: '22px', height: '22px', borderRadius: '50%', background: 'hsl(var(--muted))', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '11px', color: 'hsl(var(--muted-foreground))', fontWeight: 600, flexShrink: 0 }}>{index + 1}</span>
          <span style={{ fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{title}</span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexShrink: 0 }}>
          {score != null && <span className="score">score {score.toFixed(3)}</span>}
          {React.createElement(JudgmentToggle, { docId: id, judgment, onJudge })}
        </div>
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
  const [judgments, setJudgments] = useState({});
  const [evalResult, setEvalResult] = useState(null);
  const [evaluating, setEvaluating] = useState(false);
  const [autoJudging, setAutoJudging] = useState(false);
  const [autoJudgeError, setAutoJudgeError] = useState('');
  const [dashboardData, setDashboardData] = useState(null);
  const [judgeAllProgress, setJudgeAllProgress] = useState(null);
  const [evalCommitted, setEvalCommitted] = useState(false);
  const [showAwsCreds, setShowAwsCreds] = useState(false);
  const [awsAccessKey, setAwsAccessKey] = useState('');
  const [awsSecretKey, setAwsSecretKey] = useState('');
  const [awsSessionToken, setAwsSessionToken] = useState('');
  const [awsRegion, setAwsRegion] = useState('us-east-1');
  const [awsCredsSaved, setAwsCredsSaved] = useState(false);
  const acTimerRef = useRef(null);

  const loadSuggestions = useCallback(async (idx) => {
    try {
      const qs = new URLSearchParams();
      if (idx) qs.set('index', idx);
      const res = await fetch('/api/suggestions?' + qs.toString());
      const data = await res.json();
      const raw = Array.isArray(data.suggestion_meta) ? data.suggestion_meta : [];
      const mapped = raw.map(e => ({
        text: String(e.text || '').trim(),
        capability: String(e.capability || '').trim().toLowerCase(),
        query_mode: String(e.query_mode || 'default').trim(),
        field: String(e.field || '').trim(),
        value: String(e.value || '').trim(),
        case_insensitive: Boolean(e.case_insensitive),
      })).filter(e => e.text);
      if (mapped.length > 0) { setSuggestions(mapped); return; }
      const legacy = Array.isArray(data.suggestions) ? data.suggestions : [];
      setSuggestions(legacy.map(t => ({ text: String(t).trim(), capability: '', query_mode: 'default', field: '', value: '', case_insensitive: false })).filter(e => e.text));
    } catch(err) { setSuggestions([]); }
  }, []);

  useEffect(() => {
    fetch('/api/config').then(r => r.json()).then(data => {
      setBackendType(String(data.backend_type || 'local').trim());
      setBackendEndpoint(String(data.endpoint || '').trim());
      const idx = (data.default_index || '').trim();
      if (idx) { setIndexName(idx); loadSuggestions(idx); }
      else loadSuggestions('');
    }).catch(() => loadSuggestions(''));
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

  const onMessage = useCallback((data) => {
    if (data.type === 'search_results') {
      const d = data.data || {};
      setResults(d.hits || []);
      setSearchMeta({
        queryMode: d.query_mode || '', capability: d.capability || '',
        took_ms: d.took_ms != null ? d.took_ms : 0, usedSemantic: d.used_semantic || false,
        fallbackReason: d.fallback_reason || '', total: d.total != null ? d.total : (d.hits || []).length,
        error: d.error || '',
      });
      setCapability(d.capability || '');
      setQueryMode(d.query_mode || '');
      setSearching(false);
      setJudgments(data.cached_judgments || {});
      setEvalResult(null);
      if (indexName) loadSuggestions(indexName);
    } else if (data.type === 'judgments_update') {
      // Pushed from MCP tool via WebSocket broadcast
      if (data.judgments && Object.keys(data.judgments).length > 0) {
        setJudgments(prev => ({ ...prev, ...data.judgments }));
      }
    } else if (data.type === 'eval_result') {
      setEvaluating(false);
      if (!data.error) {
        setEvalResult(data);
        if (data.judgments) setJudgments(prev => ({ ...prev, ...data.judgments }));
      }
    } else if (data.type === 'auto_judge_result') {
      setAutoJudging(false);
      if (data.error) {
        setAutoJudgeError(data.error);
      } else {
        setAutoJudgeError('');
      }
    } else if (data.type === 'auto_judge_all_progress') {
      setJudgeAllProgress({ current: data.current, total: data.total, query: data.query, capability: data.capability });
    } else if (data.type === 'auto_judge_all_result') {
      setAutoJudging(false);
      setJudgeAllProgress(null);
      if (data.error) {
        setAutoJudgeError(data.error);
      } else {
        setAutoJudgeError('');
        setDashboardData(data);
      }
    } else if (data.type === 'evaluation_committed') {
      setEvalCommitted(true);
    } else if (data.type === 'error') {
      setSearching(false);
      setEvaluating(false);
      setAutoJudging(false);
    }
  }, [indexName, loadSuggestions]);

  const { connected, send } = useWebSocket(onMessage);

  const runSearch = useCallback((overrideQuery, opts) => {
    if (!connected) return;
    const q = overrideQuery != null ? overrideQuery : query;
    const o = opts || {};
    setSearching(true); setResults(null); setSearchMeta(null); setAutocompleteOptions([]);
    send({ action: 'search', query: q, index: indexName, size, intent: o.intent || '', field: o.field || '' });
  }, [connected, query, indexName, size, send]);

  const onJudge = useCallback((docId, value) => {
    setJudgments(prev => {
      const next = { ...prev };
      if (value == null) delete next[docId]; else next[docId] = value;
      return next;
    });
    // Persist to server immediately so manual ratings survive refresh
    if (connected) {
      if (value == null) {
        send({ action: 'remove_judgment', doc_id: docId });
      } else {
        send({ action: 'push_judgments', judgments: { [docId]: value } });
      }
    }
  }, [connected, send]);

  const handleEvaluate = useCallback(() => {
    if (!connected || evaluating) return;
    if (Object.keys(judgments).length === 0) return;
    setEvaluating(true);
    send({ action: 'evaluate', judgments, query, hits: results || [] });
  }, [connected, evaluating, judgments, query, results, send]);

  const handleRestart = useCallback(() => { setEvalResult(null); setJudgments({}); }, []);
  const handleProceedAws = useCallback(() => { setEvalResult(null); }, []);
  const handleAutoJudge = useCallback(() => {
    if (!connected) return;
    setAutoJudging(true);
    setAutoJudgeError('');
    send({ action: 'auto_judge' });
  }, [connected, send]);
  const handleAutoJudgeAll = useCallback(() => {
    if (!connected || suggestions.length === 0) return;
    setAutoJudging(true);
    setAutoJudgeError('');
    setJudgeAllProgress(null);
    setDashboardData(null);
    setEvalCommitted(false);
    send({ action: 'auto_judge_all', index: indexName, suggestions });
  }, [connected, suggestions, indexName, send]);
  const handleSaveAwsCreds = useCallback(() => {
    if (!connected || !awsAccessKey.trim() || !awsSecretKey.trim()) return;
    send({ action: 'set_credentials', access_key: awsAccessKey.trim(), secret_key: awsSecretKey.trim(), session_token: awsSessionToken.trim(), region: awsRegion.trim() || 'us-east-1' });
    setAwsCredsSaved(true);
    setTimeout(() => setAwsCredsSaved(false), 3000);
  }, [connected, awsAccessKey, awsSecretKey, awsSessionToken, awsRegion, send]);
  const handleCommitEvaluation = useCallback(() => {
    if (!connected) return;
    send({ action: 'commit_evaluation' });
  }, [connected, send]);
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

  const hits = results || [];
  const meta = searchMeta;
  const judgedCount = Object.keys(judgments).length;

  return (
    <div style={{ display: 'flex', gap: '0', maxWidth: dashboardData ? '1400px' : '960px', margin: '0 auto', padding: '24px', transition: 'max-width 300ms ease' }}>
      <div className="shell" style={{ flex: dashboardData ? '1 1 60%' : '1 1 100%', maxWidth: dashboardData ? '60%' : '960px', margin: 0, padding: dashboardData ? '0 12px 0 0' : '0' }}>
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
          <input value={indexName} onChange={e => { setIndexName(e.target.value); loadSuggestions(e.target.value); }} placeholder="e.g. imdb_titles" />
          <label style={{ fontSize: '13px', color: 'hsl(var(--muted-foreground))', display: 'flex', alignItems: 'center', gap: '6px', whiteSpace: 'nowrap' }}>
            Size
            <input type="number" min={1} max={100} value={size} onChange={e => setSize(Number(e.target.value))}
              style={{ width: '56px', border: '1px solid hsl(var(--input))', borderRadius: 'var(--radius-xl)', padding: '6px 8px', fontSize: '13px', fontFamily: 'var(--font-sans)', background: 'hsl(var(--background))', color: 'hsl(var(--foreground))' }} />
          </label>
        </div>

        <div className="search-row">
          <div className="query-wrap">
            <span className="query-icon">
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>
              </svg>
            </span>
            <input value={query} onChange={e => setQuery(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') { setAutocompleteOptions([]); runSearch(); } }}
              placeholder="Enter your search query..." disabled={!connected} />
            {autocompleteOptions.length > 0 && (
              <div className="autocomplete-menu">
                {autocompleteOptions.map(opt => (
                  <button key={opt} type="button" className="autocomplete-option"
                    onMouseDown={e => e.preventDefault()} onClick={() => onACOptionClick(opt)}>{opt}</button>
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
            Try these auto-generated queries <span>{showSuggestions ? '▴' : '▾'}</span>
          </button>
          {showSuggestions && (
            <div className="chips">
              {suggestions.map((item, i) => (
                <button key={i} className="chip" onClick={() => onSuggestionClick(item)}>
                  {item.text}
                  {item.capability && CAP_LABEL[item.capability] && (
                    <span className={'chip-badge cap-' + item.capability}>{CAP_LABEL[item.capability]}</span>
                  )}
                  {item.query_mode && MODE_LABEL[item.query_mode] && (
                    <span style={{ fontSize: '10px', padding: '1px 6px', borderRadius: '12px', background: 'hsl(var(--muted))', color: 'hsl(var(--muted-foreground))', fontWeight: 600 }}>{MODE_LABEL[item.query_mode]}</span>
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
            <div style={{ textAlign: 'center', color: 'hsl(var(--muted-foreground))', padding: '24px 0', fontSize: '14px' }}>No results found.</div>
          )}
          {hits.map((hit, i) => React.createElement(ResultCard, { key: hit.id || hit._id || i, hit, index: i, judgment: judgments[hit.id || hit._id] != null ? judgments[hit.id || hit._id] : null, onJudge }))}
        </div>

        {results && results.length > 0 && (
          <div style={{ marginTop: '16px', padding: '12px', borderRadius: '8px', background: 'hsl(var(--muted))', border: '1px solid hsl(var(--border))' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
              <span style={{ fontSize: '13px', color: 'hsl(var(--muted-foreground))' }}>
                {judgedCount > 0 ? `${judgedCount} judged` : 'Rate results with 👍/👎 or auto-judge'}
              </span>
              {autoJudgeError && <span style={{ fontSize: '12px', color: '#ef4444' }}>{autoJudgeError}</span>}
              {judgeAllProgress && <span style={{ fontSize: '12px', color: 'hsl(var(--primary))' }}>
                Judging {judgeAllProgress.current}/{judgeAllProgress.total}: {judgeAllProgress.query}
              </span>}
              <div style={{ flex: 1 }}></div>
              <button onClick={() => setShowAwsCreds(!showAwsCreds)}
                style={{ padding: '6px 14px', fontSize: '12px', borderRadius: '6px', cursor: 'pointer', background: awsCredsSaved ? '#22c55e' : 'hsl(var(--secondary))', color: awsCredsSaved ? 'white' : 'hsl(var(--secondary-foreground))', border: 'none', fontWeight: 600 }}>
                {awsCredsSaved ? '✓ Saved' : '🔑 AWS'}
              </button>
              <button onClick={handleAutoJudge} disabled={autoJudging}
                style={{ padding: '6px 14px', fontSize: '12px', borderRadius: '6px', cursor: 'pointer', background: autoJudging ? '#7c3aed88' : '#8b5cf6', color: 'white', border: 'none', fontWeight: 600 }}>
                {autoJudging ? '⏳ Judging…' : '🤖 Auto-Judge'}
              </button>
              <button onClick={handleAutoJudgeAll} disabled={autoJudging || suggestions.length === 0}
                title="Auto-judge all suggested queries and show evaluation dashboard"
                style={{ padding: '6px 14px', fontSize: '12px', borderRadius: '6px', cursor: suggestions.length > 0 ? 'pointer' : 'default', background: autoJudging ? '#7c3aed88' : '#6d28d9', color: 'white', border: 'none', fontWeight: 600 }}>
                {autoJudging && judgeAllProgress ? `⏳ ${judgeAllProgress.current}/${judgeAllProgress.total}` : '📊 Evaluate All'}
              </button>
              <button onClick={handleEvaluate} disabled={!connected || evaluating || judgedCount === 0}
                style={{ padding: '6px 14px', fontSize: '12px', borderRadius: '6px', cursor: judgedCount > 0 ? 'pointer' : 'default', background: judgedCount > 0 ? 'hsl(var(--primary))' : 'hsl(var(--muted))', color: judgedCount > 0 ? 'white' : 'hsl(var(--muted-foreground))', border: 'none', fontWeight: 600 }}>
                {evaluating ? 'Evaluating…' : `Evaluate${judgedCount > 0 ? ` (${judgedCount})` : ''}`}
              </button>
            </div>
            {showAwsCreds && (
              <div style={{ marginTop: '10px', padding: '10px', borderRadius: '6px', background: 'hsl(var(--card))', border: '1px solid hsl(var(--border))' }}>
                <div style={{ fontSize: '12px', fontWeight: 600, marginBottom: '8px', color: 'hsl(var(--foreground))' }}>AWS Credentials for Auto-Judge (Bedrock)</div>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '6px' }}>
                  <input value={awsAccessKey} onChange={e => setAwsAccessKey(e.target.value)} placeholder="Access Key ID" type="password"
                    style={{ padding: '6px 8px', fontSize: '12px', borderRadius: '4px', border: '1px solid hsl(var(--input))', background: 'hsl(var(--background))', color: 'hsl(var(--foreground))', fontFamily: 'var(--font-mono)' }} />
                  <input value={awsSecretKey} onChange={e => setAwsSecretKey(e.target.value)} placeholder="Secret Access Key" type="password"
                    style={{ padding: '6px 8px', fontSize: '12px', borderRadius: '4px', border: '1px solid hsl(var(--input))', background: 'hsl(var(--background))', color: 'hsl(var(--foreground))', fontFamily: 'var(--font-mono)' }} />
                  <input value={awsSessionToken} onChange={e => setAwsSessionToken(e.target.value)} placeholder="Session Token (optional)" type="password"
                    style={{ padding: '6px 8px', fontSize: '12px', borderRadius: '4px', border: '1px solid hsl(var(--input))', background: 'hsl(var(--background))', color: 'hsl(var(--foreground))', fontFamily: 'var(--font-mono)' }} />
                  <div style={{ display: 'flex', gap: '6px' }}>
                    <input value={awsRegion} onChange={e => setAwsRegion(e.target.value)} placeholder="Region (us-east-1)"
                      style={{ flex: 1, padding: '6px 8px', fontSize: '12px', borderRadius: '4px', border: '1px solid hsl(var(--input))', background: 'hsl(var(--background))', color: 'hsl(var(--foreground))', fontFamily: 'var(--font-mono)' }} />
                    <button onClick={handleSaveAwsCreds} disabled={!awsAccessKey.trim() || !awsSecretKey.trim()}
                      style={{ padding: '6px 12px', fontSize: '12px', borderRadius: '4px', cursor: awsAccessKey.trim() && awsSecretKey.trim() ? 'pointer' : 'default', background: awsAccessKey.trim() && awsSecretKey.trim() ? '#f59e0b' : 'hsl(var(--muted))', color: awsAccessKey.trim() && awsSecretKey.trim() ? 'white' : 'hsl(var(--muted-foreground))', border: 'none', fontWeight: 600, whiteSpace: 'nowrap' }}>
                      Save
                    </button>
                  </div>
                </div>
                <div style={{ fontSize: '11px', color: 'hsl(var(--muted-foreground))', marginTop: '6px' }}>Credentials are sent to the local websocket server only and not stored on disk.</div>
              </div>
            )}
          </div>
        )}

        {evalResult && React.createElement('div', {
          style: { margin: '16px 0', padding: '16px', borderRadius: '8px', background: 'hsl(var(--muted))', border: '1px solid hsl(var(--border))', fontSize: '13px' }
        },
          React.createElement('div', { style: { fontWeight: 700, marginBottom: '10px', fontSize: '14px' } }, '📊 Current Query Evaluation'),
          evalResult.metrics && React.createElement('div', { style: { display: 'flex', gap: '16px', marginBottom: '10px', fontSize: '12px', color: 'hsl(var(--muted-foreground))' } },
            React.createElement('span', null, `Precision: ${(evalResult.metrics.precision * 100).toFixed(0)}%`),
            React.createElement('span', null, `NDCG: ${evalResult.metrics.ndcg}`),
            React.createElement('span', null, `${evalResult.metrics.n_relevant}/${evalResult.metrics.n_judged} relevant`)
          ),
          evalResult.issues && evalResult.issues.length > 0 && React.createElement('div', null,
            evalResult.issues.map((iss, i) => React.createElement('div', { key: i, style: { fontSize: '12px', color: 'hsl(var(--muted-foreground))', marginBottom: '2px' } }, `• ${iss}`))
          )
        )}
      </section>
    </div>

    {dashboardData && (
      <div style={{ flex: '0 0 40%', maxWidth: '40%', minWidth: '340px' }}>
        <div style={{ position: 'sticky', top: '24px', background: 'hsl(var(--card) / 0.92)', border: '1px solid hsl(var(--border))', borderRadius: 'var(--radius-2xl)', boxShadow: 'var(--shadow-sm)', height: 'calc(100vh - 48px)', marginTop: '58px' }}>
          {React.createElement(EvalDashboard, { dashboardData, onClose: () => setDashboardData(null), onCommit: handleCommitEvaluation, committed: evalCommitted })}
        </div>
      </div>
    )}
    </div>
  );
}

ReactDOM.createRoot(document.getElementById('root')).render(
  React.createElement(WsGate, null, React.createElement(InteractiveApp))
);