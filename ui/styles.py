import streamlit as st

_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;700&display=swap');

/* ══════════════════════════════════════════════════════════════════
   ModelScope  ·  Brutalist Minimalist
   High-contrast light + dark  ·  No gradients / blur / glassmorphism
   ══════════════════════════════════════════════════════════════════ */

/* ─ CSS Tokens ── dark-first (warm amber / copper) ──────────────── */
:root {
    --bg:        #0a0805;
    --surface:   #13100a;
    --surface2:  #1e1a10;
    --border:    #2e2818;
    --text:      #f0e8d4;
    --muted:     #9e8a62;
    --accent:    #d97706;
    --accent-hi: #f59e0b;
    --success:   #22c55e;
    --warn:      #fb923c;
    --error:     #ef4444;
}

/* ─ Light-mode token overrides (warm cream / copper) ───────────── */
/* NOTE: @media (prefers-color-scheme: light) never fires in Streamlit
   because Streamlit forces dark at the document level. We use Streamlit's
   runtime data-theme attribute instead, set via .streamlit/config.toml. */
[data-testid="stApp"][data-theme="light"],
[data-testid="stAppViewContainer"][data-theme="light"],
.stApp[data-theme="light"] {
    --bg:        #faf6ef;
    --surface:   #ffffff;
    --surface2:  #f0e9d8;
    --border:    #d4c49a;
    --text:      #150f06;
    --muted:     #6b5830;
    --accent:    #b45309;
    --accent-hi: #d97706;
}

/* ─ App shell ────────────────────────────────────────────────────── */
html, body, [class*="css"] {
    font-family: 'Space Grotesk', system-ui, sans-serif !important;
}
.stApp,
[data-testid="stApp"] {
    background-color: var(--bg) !important;
}
.main .block-container {
    background-color: var(--bg) !important;
    padding-top: 1.5rem;
}

/* ─ Text — force contrast in both themes ─────────────────────────── */
[data-testid="stMarkdownContainer"] p,
[data-testid="stMarkdownContainer"] li,
[data-testid="stMarkdownContainer"] a,
[data-testid="stText"] p {
    color: var(--text) !important;
}

/* Caption — was #475569 (fails contrast in both modes) */
[data-testid="stCaptionContainer"],
[data-testid="stCaptionContainer"] p {
    color: var(--muted) !important;
    opacity: 1 !important;
    font-size: 0.8rem !important;
}

/* Widget labels */
[data-testid="stWidgetLabel"] p {
    color: var(--text) !important;
    font-weight: 600 !important;
    font-size: 0.75rem !important;
    text-transform: uppercase !important;
    letter-spacing: 0.5px !important;
}

/* ─ Brand header ─────────────────────────────────────────────────── */
.brand-block {
    border-top: 3px solid var(--accent);
    padding-top: 1.4rem;
    margin: 0 0 2rem;
}

.spark-title {
    font-family: 'Space Grotesk', sans-serif !important;
    font-weight: 900 !important;
    font-size: 2.4rem !important;
    letter-spacing: -1.5px;
    line-height: 1;
    color: var(--text) !important;
    -webkit-text-fill-color: var(--text) !important;
    background: none !important;
    background-clip: unset !important;
    filter: none !important;
    padding: 0 !important;
    margin: 0 0 0.45rem !important;
    border: none !important;
}

.app-subtitle {
    color: var(--muted) !important;
    font-family: 'JetBrains Mono', monospace !important;
    font-size: 0.72rem !important;
    letter-spacing: 1.2px;
    text-transform: uppercase;
    margin: 0 !important;
    display: block;
    opacity: 1 !important;
}

/* ─ Section headers ─────────────────────────────────────────────── */
h1, h2, h3, h4 {
    font-family: 'Space Grotesk', sans-serif !important;
}
h2 {
    color: var(--text) !important;
    font-size: 1rem !important;
    font-weight: 700 !important;
    border-left: 3px solid var(--accent) !important;
    padding-left: 12px !important;
    margin-top: 1.1rem !important;
    margin-bottom: 0.6rem !important;
    letter-spacing: 0.1px;
}
h3 {
    color: var(--muted) !important;
    font-size: 0.69rem !important;
    font-weight: 700 !important;
    text-transform: uppercase !important;
    letter-spacing: 1.2px !important;
}

/* ─ Tabs — underline style ──────────────────────────────────────── */
[data-testid="stTabs"] [role="tablist"] {
    border-bottom: 2px solid var(--border) !important;
    padding-bottom: 0;
    gap: 0;
    background: transparent;
}
[data-testid="stTabs"] button[role="tab"] {
    background: transparent !important;
    border: none !important;
    border-radius: 0 !important;
    border-bottom: 2px solid transparent !important;
    margin-bottom: -2px;
    color: var(--muted) !important;
    font-family: 'Space Grotesk', sans-serif !important;
    font-size: 0.76rem !important;
    font-weight: 600 !important;
    letter-spacing: 0.5px;
    padding: 8px 20px !important;
    text-transform: uppercase;
    transition: color 0.15s, border-color 0.15s;
}
[data-testid="stTabs"] button[role="tab"][aria-selected="true"] {
    background: transparent !important;
    border-bottom-color: var(--accent) !important;
    color: var(--text) !important;
}
[data-testid="stTabs"] button[role="tab"]:hover:not([aria-selected="true"]) {
    color: var(--text) !important;
    background: var(--surface) !important;
}

/* ─ Expanders ────────────────────────────────────────────────────── */
[data-testid="stExpander"] {
    background: var(--surface) !important;
    border: 1px solid var(--border) !important;
    border-radius: 2px !important;
    box-shadow: none !important;
    backdrop-filter: none !important;
    overflow: hidden;
}
[data-testid="stExpander"] summary {
    background: var(--surface2) !important;
    color: var(--text) !important;
    font-weight: 600 !important;
    font-size: 0.82rem !important;
    text-transform: uppercase;
    letter-spacing: 0.4px;
}
[data-testid="stExpander"] details[open] summary {
    background: var(--surface) !important;
    border-bottom: 1px solid var(--border) !important;
}
[data-testid="stExpander"] summary:hover {
    color: var(--accent) !important;
}

/* ─ Metrics ──────────────────────────────────────────────────────── */
[data-testid="stMetric"] {
    background: var(--surface) !important;
    border: 1px solid var(--border) !important;
    border-radius: 2px !important;
    padding: 12px 16px !important;
    box-shadow: none !important;
    transition: border-color 0.15s;
}
[data-testid="stMetric"]:hover {
    border-color: var(--accent) !important;
}
[data-testid="stMetricLabel"] {
    color: var(--muted) !important;
    font-size: 0.67rem !important;
    text-transform: uppercase !important;
    letter-spacing: 0.8px !important;
    font-weight: 700 !important;
}
[data-testid="stMetricValue"] {
    color: var(--text) !important;
    font-size: 1.35rem !important;
    font-weight: 700 !important;
    font-family: 'JetBrains Mono', monospace !important;
}

/* ─ Terminal log ─────────────────────────────────────────────────── */
/* Terminal is intentionally always-dark — do not theme to light mode */
.terminal-window {
    background: #000000;
    color: #cccccc;
    padding: 16px 20px;
    border-radius: 0;
    min-height: 280px;
    max-height: 520px;
    overflow-y: auto;
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.79rem;
    line-height: 1.7;
    border: 1px solid #1e1e1e;
    border-left: 3px solid var(--accent);
    white-space: pre-wrap;
    word-break: break-word;
    letter-spacing: 0.02em;
}
.terminal-window::-webkit-scrollbar { width: 4px; }
.terminal-window::-webkit-scrollbar-track { background: #000; }
.terminal-window::-webkit-scrollbar-thumb { background: #333; border-radius: 0; }

/* Log-line colour coding */
.log-init     { color: #4a4030; }
.log-tools    { color: #d97706; font-weight: 500; }
.log-llm      { color: #e8dfc4; }
.log-thinking { color: #c4a060; font-style: italic; }
.log-tool     { color: #f59e0b; font-weight: 600; }
.log-result   { color: #22c55e; }
.log-warn     { color: #f87171; }
.log-done     { color: #d97706; font-weight: 600; }
.log-val      { color: #60a5fa; }
.log-cancel   { color: #ef4444; font-weight: bold; text-transform: uppercase; }
.log-tokens   { color: #9e8a62; font-size: 0.72rem; }
.log-sys      { color: #818cf8; font-size: 0.77rem; font-style: italic; }
.log-usr      { color: #86efac; font-size: 0.77rem; font-style: italic; }

/* ─ Inputs ───────────────────────────────────────────────────────── */
[data-testid="stTextInput"] input,
[data-testid="stTextArea"] textarea,
[data-testid="stNumberInput"] input {
    background: var(--surface2) !important;
    border: 1px solid var(--border) !important;
    border-radius: 2px !important;
    color: var(--text) !important;
    font-family: 'JetBrains Mono', monospace !important;
    font-size: 0.82rem !important;
    transition: border-color 0.15s;
}
[data-testid="stTextInput"] input:focus,
[data-testid="stTextArea"] textarea:focus,
[data-testid="stNumberInput"] input:focus {
    border-color: var(--accent) !important;
    box-shadow: none !important;
    outline: none !important;
}

/* ─ Select ───────────────────────────────────────────────────────── */
[data-testid="stSelectbox"] > div > div {
    background: var(--surface2) !important;
    border: 1px solid var(--border) !important;
    border-radius: 2px !important;
    color: var(--text) !important;
}

/* ─ Buttons ──────────────────────────────────────────────────────── */
div.stButton > button {
    background: var(--surface) !important;
    border: 1px solid var(--border) !important;
    border-radius: 2px !important;
    color: var(--text) !important;
    font-family: 'Space Grotesk', sans-serif !important;
    font-weight: 600 !important;
    font-size: 0.76rem !important;
    letter-spacing: 0.4px;
    text-transform: uppercase;
    transition: background 0.12s, border-color 0.12s;
    padding: 6px 14px !important;
}
div.stButton > button:hover {
    background: var(--surface2) !important;
    border-color: var(--accent) !important;
    color: var(--text) !important;
}
div.stButton > button:disabled {
    opacity: 0.35 !important;
}

/* Primary */
div.stButton > button[kind="primary"],
div.stButton > button[data-testid="baseButton-primary"] {
    background: var(--accent) !important;
    border-color: var(--accent) !important;
    color: #ffffff !important;
}
div.stButton > button[kind="primary"]:hover,
div.stButton > button[data-testid="baseButton-primary"]:hover {
    background: var(--accent-hi) !important;
    border-color: var(--accent-hi) !important;
    color: #ffffff !important;
}

/* Secondary — give visible border so buttons aren't invisible on dark bg */
div.stButton > button[kind="secondary"] {
    border-color: var(--muted) !important;
}
div.stButton > button[kind="secondary"]:hover {
    border-color: var(--accent) !important;
}

/* Cancel — red-bordered destructive action */
.cancel-btn > div.stButton > button,
.cancel-btn div.stButton > button[kind="secondary"] {
    background: transparent !important;
    border-color: var(--error) !important;
    color: var(--error) !important;
}

/* ─ Badges ───────────────────────────────────────────────────────── */
.badge-pass {
    background: rgba(34,197,94,0.12);
    color: var(--success);
    padding: 2px 10px;
    border-radius: 2px;
    font-size: 0.7rem;
    font-weight: 700;
    letter-spacing: 0.6px;
    border: 1px solid rgba(34,197,94,0.35);
}
.badge-fail {
    background: rgba(239,68,68,0.12);
    color: var(--error);
    padding: 2px 10px;
    border-radius: 2px;
    font-size: 0.7rem;
    font-weight: 700;
    letter-spacing: 0.6px;
    border: 1px solid rgba(239,68,68,0.35);
}
.badge-na {
    background: var(--surface2);
    color: var(--muted);
    padding: 2px 10px;
    border-radius: 2px;
    font-size: 0.7rem;
    font-weight: 700;
    letter-spacing: 0.6px;
    border: 1px solid var(--border);
}

/* ─ Status pills ─────────────────────────────────────────────────── */
.status-pill {
    display: inline-block;
    padding: 3px 9px;
    border-radius: 2px;
    font-size: 0.7rem;
    font-weight: 700;
    margin-right: 5px;
    letter-spacing: 0.3px;
    font-family: 'JetBrains Mono', monospace;
    text-transform: uppercase;
    border: 1px solid;
}
.status-pill-up {
    background: rgba(34,197,94,0.12);
    color: var(--success);
    border-color: rgba(34,197,94,0.4);
    animation: pulse-up 2.5s ease-in-out infinite;
}
@keyframes pulse-up {
    0%, 100% { opacity: 1; }
    50%       { opacity: 0.7; }
}
.status-pill-down {
    background: rgba(239,68,68,0.12);
    color: var(--error);
    border-color: rgba(239,68,68,0.4);
}
.status-pill-wait {
    background: rgba(251,146,60,0.12);
    color: var(--warn);
    border-color: rgba(251,146,60,0.4);
}

/* ─ Category chips ───────────────────────────────────────────────── */
.cat-header {
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.66rem;
    font-weight: 700;
    letter-spacing: 1.5px;
    text-transform: uppercase;
    padding: 2px 8px;
    border-radius: 2px;
    margin: 12px 0 4px;
    display: inline-block;
}

/* ─ Criterion text ───────────────────────────────────────────────── */
.criterion {
    color: var(--text) !important;
    font-family: 'JetBrains Mono', monospace !important;
    font-size: 0.79rem !important;
    line-height: 1.5 !important;
    word-break: break-word;
}

/* ─ Dividers ─────────────────────────────────────────────────────── */
hr {
    border: none !important;
    border-top: 1px solid var(--border) !important;
    margin: 1rem 0 !important;
}

/* ─ Code ─────────────────────────────────────────────────────────── */
code {
    background: var(--surface2) !important;
    color: var(--accent-hi) !important;
    border-radius: 2px !important;
    font-size: 0.82em !important;
    padding: 2px 6px !important;
    font-family: 'JetBrains Mono', monospace !important;
    border: 1px solid var(--border) !important;
}
/* st.code() renders pre > code — needs its own overrides */
[data-testid="stCode"] pre {
    background: var(--surface2) !important;
    border: 1px solid var(--border) !important;
    border-radius: 2px !important;
    font-size: 0.79rem !important;
}
[data-testid="stCode"] pre code {
    background: transparent !important;
    border: none !important;
    color: var(--text) !important;
}

/* ─ Alerts ───────────────────────────────────────────────────────── */
[data-testid="stAlert"] {
    border-radius: 2px !important;
    border-left-width: 3px !important;
    background: var(--surface2) !important;
}
/* Force text inside alerts to use themed color, not Streamlit's defaults */
[data-testid="stAlert"] p,
[data-testid="stAlert"] span,
[data-testid="stAlert"] [data-testid="stMarkdownContainer"] p {
    color: var(--text) !important;
}
/* Warning alerts → amber border */
[data-testid="stAlert"][data-baseweb="notification"] {
    border-color: var(--warn) !important;
}
/* Info alerts → accent border */
[data-testid="stAlert"]:has([data-testid="stNotificationContentInfo"]) {
    border-color: var(--accent) !important;
}
/* Error alerts → error border */
[data-testid="stAlert"]:has([data-testid="stNotificationContentError"]) {
    border-color: var(--error) !important;
}
/* Success alerts → success border */
[data-testid="stAlert"]:has([data-testid="stNotificationContentSuccess"]) {
    border-color: var(--success) !important;
}

/* ─ Checkbox ─────────────────────────────────────────────────────── */
[data-testid="stCheckbox"] label {
    color: var(--text) !important;
    font-size: 0.84rem !important;
}

/* ─ Slider ───────────────────────────────────────────────────────── */
[data-testid="stSlider"] [data-testid="stThumbValue"] {
    color: var(--text) !important;
    font-family: 'JetBrains Mono', monospace !important;
    font-size: 0.76rem !important;
}
[data-testid="stSlider"] input[type="range"]::-webkit-slider-thumb {
    background: var(--accent) !important;
    border-color: var(--accent) !important;
}
[data-testid="stSlider"] input[type="range"]::-webkit-slider-runnable-track {
    background: var(--border) !important;
}
[data-testid="stSlider"] [data-testid="stSliderTrack"] > div:first-child {
    background: var(--accent) !important;
}

/* ─ Download button ──────────────────────────────────────────────── */
[data-testid="stDownloadButton"] button {
    background: var(--surface) !important;
    border: 1px solid var(--border) !important;
    color: var(--text) !important;
    border-radius: 2px !important;
    font-size: 0.76rem !important;
    font-weight: 600 !important;
    text-transform: uppercase;
}
[data-testid="stDownloadButton"] button:hover {
    border-color: var(--accent) !important;
    color: var(--accent) !important;
    background: var(--surface2) !important;
}

/* ─ Spinner ──────────────────────────────────────────────────────── */
[data-testid="stSpinner"] {
    color: var(--accent) !important;
}

/* ─ Model status bar ─────────────────────────────────────────────── */
.model-status-bar {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 8px 0 10px;
    border-bottom: 1px solid var(--border);
    margin-bottom: 6px;
    flex-wrap: wrap;
}
.model-status-bar .status-label {
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.67rem;
    font-weight: 700;
    letter-spacing: 0.5px;
    text-transform: uppercase;
    color: var(--muted);
    margin-right: 2px;
}

/* ─ Hide deploy button and main menu ────────────────────────────── */
[data-testid="stDeployButton"],
[data-testid="stAppDeployButton"],
.stAppDeployButton,
button[aria-label="Deploy this app"],
button[kind="header"],
[data-testid="stMainMenu"],
button[data-testid="baseButton-header"] { display: none !important; }

/* ══════════════════════════════════════════════════════════════════
   Test Suite Visualization
   Inspired by Cypress Cloud, mabl, FitNesse, GitHub CI
   ══════════════════════════════════════════════════════════════════ */

/* ─ Progress bar ─────────────────────────────────────────────────── */
.ts-progress-wrap {
    background: var(--border);
    border-radius: 2px;
    height: 7px;
    overflow: hidden;
}
.ts-progress-bar {
    height: 100%;
    border-radius: 2px;
    transition: width 0.5s ease;
}

/* ─ Count pills ──────────────────────────────────────────────────── */
.ts-count-pill {
    display: inline-block;
    padding: 2px 8px;
    border-radius: 2px;
    font-size: 0.69rem;
    font-weight: 700;
    letter-spacing: 0.4px;
    font-family: 'JetBrains Mono', monospace;
    border: 1px solid;
}
.ts-count-pass {
    background: rgba(34,197,94,0.12);
    color: var(--success);
    border-color: rgba(34,197,94,0.3);
}
.ts-count-fail {
    background: rgba(239,68,68,0.12);
    color: var(--error);
    border-color: rgba(239,68,68,0.3);
}
.ts-count-skip {
    background: var(--surface2);
    color: var(--muted);
    border-color: var(--border);
}

/* ─ Test rows ────────────────────────────────────────────────────── */
.ts-test-row {
    display: flex;
    align-items: flex-start;
    gap: 7px;
    padding: 3px 6px 3px 8px;
    border-left: 3px solid transparent;
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.78rem;
    line-height: 1.55;
    border-radius: 0 2px 2px 0;
    transition: background 0.1s;
}
.ts-test-row:hover {
    background: var(--surface);
}
.ts-test-row-pass  { border-left-color: rgba(34,197,94,0.5); }
.ts-test-row-fail  { border-left-color: var(--error); background: rgba(239,68,68,0.04); }
.ts-test-row-skip  { border-left-color: var(--border); opacity: 0.65; }

.ts-dot {
    flex-shrink: 0;
    font-weight: 700;
    width: 14px;
    text-align: center;
    padding-top: 1px;
}
.ts-dot-pass { color: var(--success); }
.ts-dot-fail { color: var(--error);   }
.ts-dot-skip { color: var(--muted);   }

.ts-test-name { color: var(--text); word-break: break-all; }
.ts-test-err  {
    color: var(--error);
    font-size: 0.72rem;
    opacity: 0.9;
    margin-top: 1px;
    word-break: break-word;
}
.ts-test-dur {
    color: var(--muted);
    font-size: 0.69rem;
    flex-shrink: 0;
    padding-top: 2px;
}

/* ─ File header ──────────────────────────────────────────────────── */
.ts-file-header {
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.74rem;
    font-weight: 600;
    color: var(--accent);
    letter-spacing: 0.2px;
    padding: 8px 0 4px;
    border-bottom: 1px solid var(--border);
    margin-bottom: 2px;
}

/* ─ Coverage map ─────────────────────────────────────────────────── */
.ts-cov-row {
    display: flex;
    align-items: flex-start;
    gap: 8px;
    padding: 3px 0;
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.76rem;
    border-left: 2px solid var(--border);
    padding-left: 8px;
}
.ts-cov-feature { color: var(--text); font-weight: 600; font-size: 0.79rem; }
.ts-cov-count   { color: var(--muted); font-size: 0.7rem; }

/* ─ Run stamp ────────────────────────────────────────────────────── */
.ts-run-stamp {
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.71rem;
    color: var(--muted);
}
</style>
"""


def inject() -> None:
    st.markdown(_CSS, unsafe_allow_html=True)
