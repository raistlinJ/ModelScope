import streamlit as st

_CSS = """
<style>
/* ══════════════════════════════════════════════════════════════════
   ModelScope  ·  Security Research Tool
   Dark-first  ·  Cyan accent  ·  GitHub-dark neutrals
   No external font CDN — uses system stacks only (CSP safe)
   ══════════════════════════════════════════════════════════════════ */

/* ─ CSS Tokens ── dark-first (cyber-cyan / blue-teal) ───────────── */
:root {
    /* Page canvas */
    --bg:        #0d1117;
    --surface:   #161b22;
    --surface2:  #21262d;
    --border:    #30363d;

    /* Typography */
    --text:      #e6edf3;
    --muted:     #8b949e;

    /* Accent — cyan that reads "security / terminal" */
    --accent:    #2dd4bf;
    --accent-hi: #5eead4;
    --accent-dim: rgba(45,212,191,0.15);

    /* Status semantics */
    --success:   #3fb950;
    --warn:      #f0883e;   /* orange — kept distinct from yellow log-val */
    --error:     #f85149;

    /* Fonts — system stacks only (no CDN import) */
    --font-mono: "JetBrains Mono", Menlo, Monaco, Consolas, "Liberation Mono",
                 "Courier New", monospace;
    --font-sans: "Segoe UI", system-ui, -apple-system, Roboto,
                 "Helvetica Neue", Arial, sans-serif;
}

/* ─ App shell ────────────────────────────────────────────────────── */
html, body, [class*="css"] {
    font-family: var(--font-sans) !important;
}
.stApp,
[data-testid="stApp"] {
    background-color: var(--bg) !important;
}
.main .block-container {
    background-color: var(--bg) !important;
    padding-top: 1.25rem;
    /* layout="wide" — let Streamlit manage the width for full dashboard density */
}

/* ─ Text — force contrast ─────────────────────────────────────────── */
[data-testid="stMarkdownContainer"] p,
[data-testid="stMarkdownContainer"] li,
[data-testid="stMarkdownContainer"] a,
[data-testid="stText"] p {
    color: var(--text) !important;
}

/* Caption */
[data-testid="stCaptionContainer"],
[data-testid="stCaptionContainer"] p {
    color: var(--muted) !important;
    opacity: 1 !important;
    font-size: 0.78rem !important;
    line-height: 1.45 !important;
}

/* Widget labels — concise uppercase system */
[data-testid="stWidgetLabel"] p {
    color: var(--text) !important;
    font-weight: 600 !important;
    font-size: 0.74rem !important;
    text-transform: uppercase !important;
    letter-spacing: 0.45px !important;
}

/* ─ Brand header ─────────────────────────────────────────────────── */
.brand-block {
    border-top: 2px solid var(--accent);
    padding-top: 1rem;
    margin: 0 0 1.5rem;
}

/* Compact inline title — NOT a giant hero */
.spark-title {
    font-family: var(--font-sans) !important;
    font-weight: 800 !important;
    font-size: 1.65rem !important;
    letter-spacing: -0.8px;
    line-height: 1;
    color: var(--text) !important;
    -webkit-text-fill-color: var(--text) !important;
    background: none !important;
    background-clip: unset !important;
    filter: none !important;
    padding: 0 !important;
    margin: 0 0 0.3rem !important;
    border: none !important;
    display: inline-block;
}

/* Accent dot after title — purely visual */
.spark-title::after {
    content: ".";
    color: var(--accent);
}

.app-subtitle {
    color: var(--muted) !important;
    font-family: var(--font-mono) !important;
    font-size: 0.69rem !important;
    letter-spacing: 1.0px;
    text-transform: uppercase;
    margin: 0 !important;
    display: block;
    opacity: 1 !important;
}

/* ─ Section headers ─────────────────────────────────────────────── */
h1, h2, h3, h4 {
    font-family: var(--font-sans) !important;
}
h2 {
    color: var(--text) !important;
    font-size: 0.95rem !important;
    font-weight: 700 !important;
    border-left: 2px solid var(--accent) !important;
    padding-left: 10px !important;
    margin-top: 1rem !important;
    margin-bottom: 0.5rem !important;
    letter-spacing: 0.1px;
}
h3 {
    color: var(--muted) !important;
    font-size: 0.68rem !important;
    font-weight: 700 !important;
    text-transform: uppercase !important;
    letter-spacing: 1.1px !important;
}

/* ─ Tabs — underline style ──────────────────────────────────────── */
[data-testid="stTabs"] [role="tablist"] {
    border-bottom: 1px solid var(--border) !important;
    padding-bottom: 0;
    gap: 0;
    background: transparent;
}
[data-testid="stTabs"] button[role="tab"] {
    background: transparent !important;
    border: none !important;
    border-radius: 0 !important;
    border-bottom: 2px solid transparent !important;
    margin-bottom: -1px;
    color: var(--muted) !important;
    font-family: var(--font-sans) !important;
    font-size: 0.74rem !important;
    font-weight: 600 !important;
    letter-spacing: 0.3px;
    padding: 7px 16px !important;
    text-transform: uppercase;
    transition: color 0.12s, border-color 0.12s;
}
[data-testid="stTabs"] button[role="tab"][aria-selected="true"] {
    background: transparent !important;
    border-bottom-color: var(--accent) !important;
    color: var(--accent) !important;
}
[data-testid="stTabs"] button[role="tab"]:hover:not([aria-selected="true"]) {
    color: var(--text) !important;
    background: var(--surface) !important;
}

/* ─ Expanders ────────────────────────────────────────────────────── */
[data-testid="stExpander"] {
    background: var(--surface) !important;
    border: 1px solid var(--border) !important;
    border-radius: 3px !important;
    box-shadow: none !important;
    backdrop-filter: none !important;
    overflow: hidden;
    margin-bottom: 0.6rem;
}
[data-testid="stExpander"] summary {
    background: var(--surface2) !important;
    color: var(--text) !important;
    font-weight: 600 !important;
    font-size: 0.78rem !important;
    text-transform: uppercase;
    letter-spacing: 0.35px;
    padding: 10px 14px !important;
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
    border-radius: 3px !important;
    padding: 10px 14px !important;
    box-shadow: none !important;
    transition: border-color 0.12s;
}
[data-testid="stMetric"]:hover {
    border-color: var(--accent) !important;
}
[data-testid="stMetricLabel"] {
    color: var(--muted) !important;
    font-size: 0.66rem !important;
    text-transform: uppercase !important;
    letter-spacing: 0.7px !important;
    font-weight: 700 !important;
}
[data-testid="stMetricValue"] {
    color: var(--text) !important;
    font-size: 1.25rem !important;
    font-weight: 700 !important;
    font-family: var(--font-mono) !important;
}

/* ─ Terminal log ─────────────────────────────────────────────────── */
/* Terminal is always dark — intentionally not themed to light mode */
.terminal-window {
    background: #0d1117;
    color: #c9d1d9;
    padding: 14px 18px;
    border-radius: 3px;
    min-height: 260px;
    max-height: 520px;
    overflow-y: auto;
    font-family: var(--font-mono);
    font-size: 0.78rem;
    line-height: 1.75;
    border: 1px solid var(--border);
    border-left: 3px solid var(--accent);
    white-space: pre-wrap;
    word-break: break-word;
    letter-spacing: 0.02em;
}
.terminal-window::-webkit-scrollbar { width: 4px; }
.terminal-window::-webkit-scrollbar-track { background: #0d1117; }
.terminal-window::-webkit-scrollbar-thumb { background: #30363d; border-radius: 2px; }
.terminal-window::-webkit-scrollbar-thumb:hover { background: var(--accent); }

/* Log-line colour coding
   Tag mapping (from execute_tab.py / caf_tab.py):
   init     → [INIT][CLEANUP][CAF]        — system startup, dim cyan
   tools    → [TOOLS]                     — tool list, accent
   llm      → [LLM][RESPONSE][CAF OUTPUT] — model output, white
   thinking → [THINKING]                  — italicised muted
   tool     → [TOOL CALL]                 — bright cyan (active call)
   result   → [TOOL RESULT]               — success green
   val      → [VALIDATE]                  — blue-violet (validation phase)
   warn     → [WARN][ERROR][ABORTED]      — amber warning
   done     → [DONE][COMPLETE]            — accent green, bold
   cancel   → [CANCEL]                    — red alert, caps
   tokens   → [TOKENS]                    — muted stats line
   sys      → [SYS]                       — dim system message
   usr      → [USR]                       — dim user turn
*/
.log-init     { color: #56b6c2; }
.log-tools    { color: var(--accent); font-weight: 500; }
.log-llm      { color: #e6edf3; }
.log-thinking { color: #8b949e; font-style: italic; }
.log-tool     { color: var(--accent); font-weight: 600; }
.log-result   { color: var(--success); }
.log-val      { color: #e3b341; }   /* yellow — per spec; warn is orange so these stay apart */
.log-warn     { color: var(--warn); }
.log-done     { color: var(--success); font-weight: 700; }
.log-cancel   { color: var(--error); font-weight: 700; text-transform: uppercase; }
.log-tokens   { color: var(--muted); font-size: 0.72rem; }
.log-sys      { color: #6e7681; font-size: 0.74rem; font-style: italic; }
.log-usr      { color: #56b6c2; font-size: 0.74rem; font-style: italic; }

/* ─ Inputs ───────────────────────────────────────────────────────── */
[data-testid="stTextInput"] input,
[data-testid="stTextArea"] textarea,
[data-testid="stNumberInput"] input {
    background: var(--surface2) !important;
    border: 1px solid var(--border) !important;
    border-radius: 3px !important;
    color: var(--text) !important;
    font-family: var(--font-mono) !important;
    font-size: 0.82rem !important;
    transition: border-color 0.12s, box-shadow 0.12s;
}
[data-testid="stTextInput"] input:focus,
[data-testid="stTextArea"] textarea:focus,
[data-testid="stNumberInput"] input:focus {
    border-color: var(--accent) !important;
    box-shadow: 0 0 0 2px var(--accent-dim) !important;
    outline: none !important;
}
[data-testid="stTextArea"] textarea {
    line-height: 1.6 !important;
}

/* ─ Select ───────────────────────────────────────────────────────── */
[data-testid="stSelectbox"] > div > div {
    background: var(--surface2) !important;
    border: 1px solid var(--border) !important;
    border-radius: 3px !important;
    color: var(--text) !important;
}

/* ─ Buttons ──────────────────────────────────────────────────────── */
div.stButton > button {
    background: var(--surface) !important;
    border: 1px solid var(--border) !important;
    border-radius: 3px !important;
    color: var(--text) !important;
    font-family: var(--font-sans) !important;
    font-weight: 600 !important;
    font-size: 0.75rem !important;
    letter-spacing: 0.35px;
    text-transform: uppercase;
    transition: background 0.1s, border-color 0.1s, color 0.1s;
    padding: 6px 14px !important;
}
div.stButton > button:hover {
    background: var(--surface2) !important;
    border-color: var(--accent) !important;
    color: var(--accent) !important;
}
div.stButton > button:disabled {
    opacity: 0.3 !important;
}

/* Primary — cyan fill, stands out clearly */
div.stButton > button[kind="primary"],
div.stButton > button[data-testid="baseButton-primary"] {
    background: var(--accent) !important;
    border-color: var(--accent) !important;
    color: #0d1117 !important;
}
div.stButton > button[kind="primary"]:hover,
div.stButton > button[data-testid="baseButton-primary"]:hover {
    background: var(--accent-hi) !important;
    border-color: var(--accent-hi) !important;
    color: #0d1117 !important;
}

/* Secondary — visible muted border */
div.stButton > button[kind="secondary"] {
    border-color: var(--border) !important;
    color: var(--muted) !important;
}
div.stButton > button[kind="secondary"]:hover {
    border-color: var(--accent) !important;
    color: var(--text) !important;
}

/* Cancel / destructive — red-bordered */
.cancel-btn > div.stButton > button,
.cancel-btn div.stButton > button[kind="secondary"] {
    background: transparent !important;
    border-color: var(--error) !important;
    color: var(--error) !important;
}

/* ─ Badges ───────────────────────────────────────────────────────── */
.badge-pass {
    background: rgba(63,185,80,0.12);
    color: var(--success);
    padding: 2px 10px;
    border-radius: 3px;
    font-size: 0.7rem;
    font-weight: 700;
    letter-spacing: 0.5px;
    border: 1px solid rgba(63,185,80,0.3);
}
.badge-fail {
    background: rgba(248,81,73,0.12);
    color: var(--error);
    padding: 2px 10px;
    border-radius: 3px;
    font-size: 0.7rem;
    font-weight: 700;
    letter-spacing: 0.5px;
    border: 1px solid rgba(248,81,73,0.3);
}
.badge-na {
    background: var(--surface2);
    color: var(--muted);
    padding: 2px 10px;
    border-radius: 3px;
    font-size: 0.7rem;
    font-weight: 700;
    letter-spacing: 0.5px;
    border: 1px solid var(--border);
}

/* ─ Status pills ─────────────────────────────────────────────────── */
.status-pill {
    display: inline-block;
    padding: 2px 8px;
    border-radius: 3px;
    font-size: 0.68rem;
    font-weight: 700;
    margin-right: 4px;
    letter-spacing: 0.25px;
    font-family: var(--font-mono);
    text-transform: uppercase;
    border: 1px solid;
    vertical-align: middle;
}
/* up = green, reserved for "running/connected" states only */
.status-pill-up {
    background: rgba(63,185,80,0.10);
    color: var(--success);
    border-color: rgba(63,185,80,0.35);
}
/* Pulse only on actively running services, not every "up" pill */
.status-pill-up.running {
    animation: pulse-service 2.5s ease-in-out infinite;
}
@keyframes pulse-service {
    0%, 100% { opacity: 1; }
    50%       { opacity: 0.65; }
}
.status-pill-down {
    background: rgba(248,81,73,0.10);
    color: var(--error);
    border-color: rgba(248,81,73,0.35);
}
.status-pill-wait {
    background: rgba(210,153,34,0.10);
    color: var(--warn);
    border-color: rgba(210,153,34,0.35);
}

/* ─ Category chips ───────────────────────────────────────────────── */
.cat-header {
    font-family: var(--font-mono);
    font-size: 0.63rem;
    font-weight: 700;
    letter-spacing: 1.4px;
    text-transform: uppercase;
    padding: 2px 8px;
    border-radius: 3px;
    margin: 10px 0 4px;
    display: inline-block;
}

/* ─ Criterion text ───────────────────────────────────────────────── */
.criterion {
    color: var(--text) !important;
    font-family: var(--font-mono) !important;
    font-size: 0.77rem !important;
    line-height: 1.5 !important;
    word-break: break-word;
}

/* ─ Dividers ─────────────────────────────────────────────────────── */
hr {
    border: none !important;
    border-top: 1px solid var(--border) !important;
    margin: 0.9rem 0 !important;
    opacity: 0.6 !important;
}

/* ─ Code ─────────────────────────────────────────────────────────── */
code {
    background: var(--surface2) !important;
    color: #79c0ff !important;
    border-radius: 3px !important;
    font-size: 0.82em !important;
    padding: 2px 5px !important;
    font-family: var(--font-mono) !important;
    border: 1px solid var(--border) !important;
}
/* st.code() — pre > code */
[data-testid="stCode"] pre {
    background: var(--surface2) !important;
    border: 1px solid var(--border) !important;
    border-radius: 3px !important;
    font-size: 0.78rem !important;
    padding: 12px 16px !important;
}
[data-testid="stCode"] pre code {
    background: transparent !important;
    border: none !important;
    color: var(--text) !important;
}

/* ─ Alerts ───────────────────────────────────────────────────────── */
[data-testid="stAlert"] {
    border-radius: 3px !important;
    border-left-width: 3px !important;
    background: var(--surface2) !important;
}
[data-testid="stAlert"] p,
[data-testid="stAlert"] span,
[data-testid="stAlert"] [data-testid="stMarkdownContainer"] p {
    color: var(--text) !important;
}
/* Warning → amber border */
[data-testid="stAlert"][data-baseweb="notification"] {
    border-color: var(--warn) !important;
}
/* Info → accent border */
[data-testid="stAlert"]:has([data-testid="stNotificationContentInfo"]) {
    border-color: var(--accent) !important;
}
/* Error → error border */
[data-testid="stAlert"]:has([data-testid="stNotificationContentError"]) {
    border-color: var(--error) !important;
}
/* Success → success border */
[data-testid="stAlert"]:has([data-testid="stNotificationContentSuccess"]) {
    border-color: var(--success) !important;
}

/* ─ Checkbox ─────────────────────────────────────────────────────── */
[data-testid="stCheckbox"] label {
    color: var(--text) !important;
    font-size: 0.83rem !important;
}

/* ─ Radio buttons ────────────────────────────────────────────────── */
[data-testid="stRadio"] label {
    color: var(--text) !important;
    font-size: 0.83rem !important;
}

/* ─ Slider ───────────────────────────────────────────────────────── */
[data-testid="stSlider"] [data-testid="stThumbValue"] {
    color: var(--text) !important;
    font-family: var(--font-mono) !important;
    font-size: 0.74rem !important;
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
    color: var(--muted) !important;
    border-radius: 3px !important;
    font-size: 0.74rem !important;
    font-weight: 600 !important;
    text-transform: uppercase;
    letter-spacing: 0.3px;
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
    gap: 5px;
    padding: 6px 0 8px;
    border-bottom: 1px solid var(--border);
    margin-bottom: 4px;
    flex-wrap: wrap;
}
.model-status-bar .status-label {
    font-family: var(--font-mono);
    font-size: 0.65rem;
    font-weight: 700;
    letter-spacing: 0.4px;
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
    border-radius: 3px;
    height: 6px;
    overflow: hidden;
}
.ts-progress-bar {
    height: 100%;
    border-radius: 3px;
    transition: width 0.4s ease;
}

/* ─ Count pills ──────────────────────────────────────────────────── */
.ts-count-pill {
    display: inline-block;
    padding: 2px 8px;
    border-radius: 3px;
    font-size: 0.67rem;
    font-weight: 700;
    letter-spacing: 0.35px;
    font-family: var(--font-mono);
    border: 1px solid;
}
.ts-count-pass {
    background: rgba(63,185,80,0.10);
    color: var(--success);
    border-color: rgba(63,185,80,0.28);
}
.ts-count-fail {
    background: rgba(248,81,73,0.10);
    color: var(--error);
    border-color: rgba(248,81,73,0.28);
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
    border-left: 2px solid transparent;
    font-family: var(--font-mono);
    font-size: 0.76rem;
    line-height: 1.5;
    border-radius: 0 2px 2px 0;
    transition: background 0.08s;
}
.ts-test-row:hover {
    background: var(--surface);
}
.ts-test-row-pass  { border-left-color: rgba(63,185,80,0.45); }
.ts-test-row-fail  { border-left-color: var(--error); background: rgba(248,81,73,0.04); }
.ts-test-row-skip  { border-left-color: var(--border); opacity: 0.6; }

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
    font-size: 0.7rem;
    opacity: 0.88;
    margin-top: 1px;
    word-break: break-word;
}
.ts-test-dur {
    color: var(--muted);
    font-size: 0.67rem;
    flex-shrink: 0;
    padding-top: 2px;
}

/* ─ File header ──────────────────────────────────────────────────── */
.ts-file-header {
    font-family: var(--font-mono);
    font-size: 0.72rem;
    font-weight: 600;
    color: var(--accent);
    letter-spacing: 0.15px;
    padding: 7px 0 3px;
    border-bottom: 1px solid var(--border);
    margin-bottom: 2px;
}

/* ─ Coverage map ─────────────────────────────────────────────────── */
.ts-cov-row {
    display: flex;
    align-items: flex-start;
    gap: 8px;
    padding: 2px 0;
    font-family: var(--font-mono);
    font-size: 0.74rem;
    border-left: 2px solid var(--border);
    padding-left: 8px;
}
.ts-cov-feature { color: var(--text); font-weight: 600; font-size: 0.77rem; }
.ts-cov-count   { color: var(--muted); font-size: 0.68rem; }

/* ─ Run stamp ────────────────────────────────────────────────────── */
.ts-run-stamp {
    font-family: var(--font-mono);
    font-size: 0.69rem;
    color: var(--muted);
}
</style>
"""


def inject() -> None:
    st.markdown(_CSS, unsafe_allow_html=True)
