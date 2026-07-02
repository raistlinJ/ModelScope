import streamlit as st

_CSS = r"""
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
    --accent-glow: rgba(45,212,191,0.08);

    /* Status semantics */
    --success:   #3fb950;
    --warn:      #f0883e;
    --error:     #f85149;
    --cmd-color: #f0883e;
    --prompt-color: #2dd4bf;

    /* Fonts — system stacks only (no CDN import) */
    --font-mono: "JetBrains Mono", Menlo, Monaco, Consolas, "Liberation Mono",
                 "Courier New", monospace;
    --font-sans: "Segoe UI", system-ui, -apple-system, Roboto,
                 "Helvetica Neue", Arial, sans-serif;

    /* Transitions */
    --transition: 0.13s ease;
}

/* ─ Native Layout Colorization ──────────────────────────────────── */
.stApp,
[data-testid="stAppViewContainer"],
[data-testid="stHeader"] {
    background-color: var(--bg) !important;
}

[data-testid="stSidebar"] {
    background-color: var(--surface) !important;
    border-right: 1px solid var(--border) !important;
}

[data-testid="stMainBlockContainer"],
.block-container {
    background-color: var(--bg) !important;
    padding-top: 1.25rem !important;
    padding-bottom: 6rem !important;
    height: auto !important;
    min-height: 100vh !important;
}

/* ─ Typography ──────────────────────────────────────────────────── */
.stApp {
    font-family: var(--font-sans) !important;
}

[data-testid="stMarkdownContainer"] p,
[data-testid="stMarkdownContainer"] li,
[data-testid="stMarkdownContainer"] a,
[data-testid="stText"] p {
    color: var(--text) !important;
}

[data-testid="stCaptionContainer"],
[data-testid="stCaptionContainer"] p {
    color: var(--muted) !important;
    opacity: 1 !important;
    font-size: 0.78rem !important;
    line-height: 1.45 !important;
}

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
    padding-top: 0.9rem;
    margin: 0 0 1.25rem;
}

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
    font-size: 0.97rem !important;
    font-weight: 700 !important;
    border-left: 3px solid var(--accent) !important;
    padding-left: 12px !important;
    margin-top: 1.25rem !important;
    margin-bottom: 0.6rem !important;
    letter-spacing: 0.1px;
    background: linear-gradient(to right, rgba(45,212,191,0.06), transparent 60%) !important;
    border-radius: 0 4px 4px 0 !important;
    padding-top: 4px !important;
    padding-bottom: 4px !important;
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
    border-bottom: 1px solid rgba(48,54,61,0.8) !important;
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
    transition: color var(--transition), border-color var(--transition), background var(--transition);
}
[data-testid="stTabs"] button[role="tab"][aria-selected="true"] {
    background: transparent !important;
    border-bottom-color: var(--accent) !important;
    color: var(--accent) !important;
}
[data-testid="stTabs"] button[role="tab"]:hover:not([aria-selected="true"]) {
    color: var(--text) !important;
    background: var(--accent-glow) !important;
}

/* ─ Expanders ────────────────────────────────────────────────────── */
[data-testid="stExpander"] {
    background: var(--surface) !important;
    border: 1px solid rgba(48,54,61,0.7) !important;
    border-radius: 10px !important;
    box-shadow: 0 2px 8px rgba(0,0,0,0.28), 0 1px 2px rgba(0,0,0,0.18) !important;
    overflow: hidden;
    margin-bottom: 0.75rem;
    transition: border-color var(--transition), box-shadow var(--transition);
}
[data-testid="stExpander"]:focus-within {
    border-color: rgba(45,212,191,0.35) !important;
    box-shadow: 0 2px 8px rgba(0,0,0,0.28), 0 0 0 1px rgba(45,212,191,0.12) !important;
}
[data-testid="stExpander"] summary {
    background: var(--surface2) !important;
    color: var(--text) !important;
    font-weight: 600 !important;
    font-size: 0.78rem !important;
    text-transform: uppercase;
    letter-spacing: 0.35px;
    padding: 10px 16px !important;
    border-radius: 10px 10px 0 0;
    transition: color var(--transition), background var(--transition);
}
[data-testid="stExpander"] details[open] summary {
    background: var(--surface) !important;
    border-bottom: 1px solid rgba(48,54,61,0.5) !important;
    border-radius: 10px 10px 0 0;
}
[data-testid="stExpander"] summary:hover {
    color: var(--accent) !important;
    background: var(--accent-glow) !important;
}

/* ─ Metrics ──────────────────────────────────────────────────────── */
[data-testid="stMetric"] {
    background: var(--surface) !important;
    border: 1px solid rgba(48,54,61,0.7) !important;
    border-radius: 10px !important;
    padding: 12px 16px !important;
    box-shadow: 0 2px 8px rgba(0,0,0,0.28), 0 1px 2px rgba(0,0,0,0.14) !important;
    transition: border-color var(--transition), box-shadow var(--transition);
}
[data-testid="stMetric"]:hover {
    border-color: rgba(45,212,191,0.55) !important;
    box-shadow: 0 4px 14px rgba(0,0,0,0.32), 0 0 0 1px rgba(45,212,191,0.2) !important;
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
.terminal-window {
    background: var(--bg);
    color: var(--text);
    padding: 14px 18px;
    border-radius: 8px;
    height: 500px;
    overflow-y: auto;
    font-family: var(--font-mono);
    font-size: 0.78rem;
    line-height: 1.75;
    border: 1px solid rgba(48,54,61,0.8);
    border-left: 3px solid var(--accent);
    box-shadow: 0 4px 16px rgba(0,0,0,0.4);
    white-space: pre-wrap;
    word-break: break-word;
    letter-spacing: 0.02em;
}
.terminal-window::-webkit-scrollbar { width: 4px; }
.terminal-window::-webkit-scrollbar-track { background: var(--bg); }
.terminal-window::-webkit-scrollbar-thumb { background: var(--border); border-radius: 2px; }
.terminal-window::-webkit-scrollbar-thumb:hover { background: var(--accent); }

.log-init     { color: #56b6c2; }
.log-tools    { color: var(--accent); font-weight: 500; }
.log-llm      { color: var(--text); }
.log-thinking { color: var(--muted); font-style: italic; }
.log-tool     { color: var(--accent); font-weight: 600; }
.log-result   { color: var(--success); }
.log-val      { color: var(--warn); }
.log-warn     { color: var(--warn); }
.log-done     { color: var(--success); font-weight: 700; }
.log-cancel   { color: var(--error); font-weight: 700; text-transform: uppercase; }
.log-tokens   { color: var(--muted); font-size: 0.72rem; }
.log-sys      { color: var(--muted); font-size: 0.74rem; font-style: italic; }
.log-usr      { color: #56b6c2; font-size: 0.74rem; font-style: italic; }
.log-cmd      { color: #d946ef; font-weight: 600; }
.log-prompt   { color: #4ade80; font-weight: 600; }
.log-stream   { color: var(--text); }
.log-decision { color: var(--warn); font-weight: 600; }

/* ─ Inputs ───────────────────────────────────────────────────────── */
[data-testid="stTextInput"] input,
[data-testid="stTextArea"] textarea,
[data-testid="stNumberInput"] input {
    background: var(--surface2) !important;
    border: 1px solid var(--border) !important;
    border-radius: 6px !important;
    color: var(--text) !important;
    font-family: var(--font-mono) !important;
    font-size: 0.82rem !important;
    transition: border-color var(--transition), box-shadow var(--transition);
}
[data-testid="stTextInput"] input:focus,
[data-testid="stTextArea"] textarea:focus,
[data-testid="stNumberInput"] input:focus {
    border-color: var(--accent) !important;
    box-shadow: 0 0 0 3px var(--accent-dim) !important;
    outline: none !important;
}
[data-testid="stTextArea"] textarea {
    line-height: 1.6 !important;
}

/* ─ Select ───────────────────────────────────────────────────────── */
[data-testid="stSelectbox"] > div > div {
    background: var(--surface2) !important;
    border: 1px solid var(--border) !important;
    border-radius: 6px !important;
    color: var(--text) !important;
    transition: border-color var(--transition);
}
[data-testid="stSelectbox"] > div > div:focus-within {
    border-color: var(--accent) !important;
}
/* Prevent typing/editing in selectboxes (make them select-only) */
[data-testid="stSelectbox"] [data-baseweb="select"] input {
    caret-color: transparent !important;
    pointer-events: none !important;
}

/* ─ Buttons ──────────────────────────────────────────────────────── */
div.stButton > button {
    background: var(--surface2) !important;
    border: 1px solid var(--border) !important;
    border-radius: 6px !important;
    color: var(--text) !important;
    font-family: var(--font-sans) !important;
    font-weight: 600 !important;
    font-size: 0.75rem !important;
    letter-spacing: 0.35px;
    text-transform: uppercase;
    transition: background var(--transition), border-color var(--transition),
                color var(--transition), box-shadow var(--transition);
    padding: 6px 14px !important;
}
div.stButton > button:hover {
    background: var(--surface) !important;
    border-color: var(--accent) !important;
    color: var(--accent) !important;
    box-shadow: 0 2px 8px rgba(0,0,0,0.2) !important;
}
div.stButton > button:disabled {
    opacity: 0.3 !important;
}
div.stButton > button:active:not(:disabled) {
    transform: translateY(1px);
}

/* Primary — cyan fill */
div.stButton > button[kind="primary"],
div.stButton > button[data-testid="baseButton-primary"] {
    background: var(--accent) !important;
    border-color: var(--accent) !important;
    color: var(--bg) !important;
    box-shadow: 0 2px 8px rgba(45,212,191,0.25) !important;
}
div.stButton > button[kind="primary"]:hover,
div.stButton > button[data-testid="baseButton-primary"]:hover {
    background: var(--accent-hi) !important;
    border-color: var(--accent-hi) !important;
    color: var(--bg) !important;
    box-shadow: 0 4px 14px rgba(45,212,191,0.35) !important;
}

/* Secondary */
div.stButton > button[kind="secondary"] {
    border-color: var(--border) !important;
    color: var(--muted) !important;
}
div.stButton > button[kind="secondary"]:hover {
    border-color: var(--accent) !important;
    color: var(--text) !important;
}

/* Cancel / destructive */
.cancel-btn > div.stButton > button,
.cancel-btn div.stButton > button[kind="secondary"] {
    background: transparent !important;
    border-color: var(--error) !important;
    color: var(--error) !important;
}

/* ─ Radio buttons — segmented-control look ──────────────────────── */
[data-testid="stRadio"] > div {
    gap: 4px !important;
}
[data-testid="stRadio"] > div[role="radiogroup"] {
    flex-direction: row !important;
    flex-wrap: wrap;
    gap: 6px !important;
}
[data-testid="stRadio"] label {
    display: flex !important;
    align-items: center !important;
    gap: 6px !important;
    padding: 5px 12px !important;
    border-radius: 6px !important;
    border: 1px solid var(--border) !important;
    background: var(--surface2) !important;
    cursor: pointer;
    transition: border-color var(--transition), background var(--transition), color var(--transition);
    font-size: 0.78rem !important;
}
[data-testid="stRadio"] label:hover {
    border-color: var(--accent) !important;
    background: var(--accent-glow) !important;
    color: var(--accent) !important;
}
[data-testid="stRadio"] label:has(input:checked) {
    border-color: var(--accent) !important;
    background: var(--accent-dim) !important;
    color: var(--accent) !important;
    font-weight: 600 !important;
}
[data-testid="stRadio"] input[type="radio"] {
    accent-color: var(--accent) !important;
}

/* ─ Checkbox ─────────────────────────────────────────────────────── */
[data-testid="stCheckbox"] label {
    color: var(--text) !important;
    font-size: 0.83rem !important;
    transition: color var(--transition);
}
[data-testid="stCheckbox"] input[type="checkbox"] {
    accent-color: var(--accent) !important;
}

/* ─ Badges ───────────────────────────────────────────────────────── */
.badge-pass {
    background: var(--surface2);
    color: var(--success);
    padding: 3px 11px;
    border-radius: 999px;
    font-size: 0.68rem;
    font-weight: 700;
    letter-spacing: 0.5px;
    border: 1px solid var(--success);
    display: inline-block;
}
.badge-fail {
    background: var(--surface2);
    color: var(--error);
    padding: 3px 11px;
    border-radius: 999px;
    font-size: 0.68rem;
    font-weight: 700;
    letter-spacing: 0.5px;
    border: 1px solid var(--error);
    display: inline-block;
}
.badge-na {
    background: var(--surface2);
    color: var(--muted);
    padding: 3px 11px;
    border-radius: 999px;
    font-size: 0.68rem;
    font-weight: 700;
    letter-spacing: 0.5px;
    border: 1px solid var(--border);
    display: inline-block;
}

/* ─ Status pills ─────────────────────────────────────────────────── */
.status-pill {
    display: inline-block;
    padding: 3px 10px;
    border-radius: 999px;
    font-size: 0.67rem;
    font-weight: 700;
    margin-right: 5px;
    letter-spacing: 0.25px;
    font-family: var(--font-mono);
    text-transform: uppercase;
    border: 1px solid;
    vertical-align: middle;
}
.status-pill-up {
    background: var(--surface2);
    color: var(--success);
    border-color: var(--success);
}
.status-pill-up.running {
    animation: pulse-service 2.5s ease-in-out infinite;
}
@keyframes pulse-service {
    0%, 100% { opacity: 1; box-shadow: 0 0 0 0 rgba(63,185,80,0); }
    50%       { opacity: 0.75; box-shadow: 0 0 0 4px rgba(63,185,80,0.12); }
}
.status-pill-down {
    background: var(--surface2);
    color: var(--error);
    border-color: var(--error);
}
.status-pill-wait {
    background: var(--surface2);
    color: var(--warn);
    border-color: var(--warn);
}

/* ─ Step cards (unified prompt/command step editor) ──────────────── */
.step-card {
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 12px 14px;
    margin-bottom: 8px;
    background: var(--surface);
    transition: border-color var(--transition), box-shadow var(--transition);
}
.step-card:hover {
    border-color: rgba(48,54,61,1);
    box-shadow: 0 2px 10px rgba(0,0,0,0.22);
}
.step-card-prompt {
    border-left: 3px solid var(--prompt-color) !important;
}
.step-card-command {
    border-left: 3px solid var(--cmd-color) !important;
}

/* Step type badges */
.step-badge {
    display: inline-block;
    padding: 2px 9px;
    border-radius: 999px;
    font-size: 0.64rem;
    font-weight: 700;
    letter-spacing: 0.5px;
    font-family: var(--font-mono);
    text-transform: uppercase;
    border: 1px solid;
    vertical-align: middle;
    margin-right: 6px;
}
.step-badge-prompt {
    color: var(--prompt-color);
    background: rgba(45,212,191,0.1);
    border-color: rgba(45,212,191,0.4);
}
.step-badge-command {
    color: var(--cmd-color);
    background: rgba(240,136,62,0.1);
    border-color: rgba(240,136,62,0.4);
}

/* ─ Service active display box ───────────────────────────────────── */
.service-active-box {
    background: var(--surface);
    border: 1px solid rgba(63,185,80,0.4);
    border-left: 3px solid var(--success);
    border-radius: 8px;
    padding: 10px 14px;
    margin: 6px 0;
    animation: service-glow 3s ease-in-out infinite;
}
@keyframes service-glow {
    0%, 100% { border-left-color: var(--success); }
    50%       { border-left-color: rgba(63,185,80,0.5); }
}
.service-active-box .service-label {
    color: var(--success);
    font-family: var(--font-mono);
    font-size: 0.67rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    margin-bottom: 5px;
}
.service-active-box .service-cmd {
    color: var(--accent);
    font-family: var(--font-mono);
    font-size: 0.76rem;
    word-break: break-all;
    line-height: 1.5;
}

/* Starting / loading state */
.service-loading-box {
    background: var(--surface);
    border: 1px solid rgba(240,136,62,0.35);
    border-left: 3px solid var(--warn);
    border-radius: 8px;
    padding: 10px 14px;
    margin: 6px 0;
    animation: service-loading-pulse 1.8s ease-in-out infinite;
}
@keyframes service-loading-pulse {
    0%, 100% { border-left-color: var(--warn); opacity: 1; }
    50%       { border-left-color: rgba(240,136,62,0.4); opacity: 0.75; }
}
.service-loading-box .service-label {
    color: var(--warn);
    font-family: var(--font-mono);
    font-size: 0.67rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.5px;
}

/* ─ Category chips ───────────────────────────────────────────────── */
.cat-header {
    font-family: var(--font-mono);
    font-size: 0.63rem;
    font-weight: 700;
    letter-spacing: 1.4px;
    text-transform: uppercase;
    padding: 3px 10px;
    border-radius: 999px;
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
    color: var(--accent) !important;
    border-radius: 4px !important;
    font-size: 0.82em !important;
    padding: 2px 5px !important;
    font-family: var(--font-mono) !important;
    border: 1px solid var(--border) !important;
}
[data-testid="stCode"] pre {
    background: var(--surface2) !important;
    border: 1px solid rgba(48,54,61,0.7) !important;
    border-radius: 8px !important;
    font-size: 0.78rem !important;
    padding: 12px 16px !important;
    box-shadow: 0 1px 4px rgba(0,0,0,0.18) !important;
}
[data-testid="stCode"] pre code {
    background: transparent !important;
    border: none !important;
    color: var(--text) !important;
}

/* ─ Alerts ───────────────────────────────────────────────────────── */
[data-testid="stAlert"] {
    border-radius: 8px !important;
    border-left-width: 3px !important;
    background: var(--surface2) !important;
    box-shadow: 0 1px 4px rgba(0,0,0,0.18) !important;
}
[data-testid="stAlert"] p,
[data-testid="stAlert"] span,
[data-testid="stAlert"] [data-testid="stMarkdownContainer"] p {
    color: var(--text) !important;
}
[data-testid="stAlert"][data-baseweb="notification"] {
    border-color: var(--warn) !important;
}
[data-testid="stAlert"]:has([data-testid="stNotificationContentInfo"]) {
    border-color: var(--accent) !important;
}
[data-testid="stAlert"]:has([data-testid="stNotificationContentError"]) {
    border-color: var(--error) !important;
}
[data-testid="stAlert"]:has([data-testid="stNotificationContentSuccess"]) {
    border-color: var(--success) !important;
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
    border-radius: 6px !important;
    font-size: 0.74rem !important;
    font-weight: 600 !important;
    text-transform: uppercase;
    letter-spacing: 0.3px;
    transition: background var(--transition), border-color var(--transition),
                color var(--transition) !important;
}
[data-testid="stDownloadButton"] button:hover {
    border-color: var(--accent) !important;
    color: var(--accent) !important;
    background: var(--surface2) !important;
    box-shadow: 0 2px 8px rgba(0,0,0,0.2) !important;
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
    padding: 7px 12px;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 8px;
    margin-bottom: 8px;
    flex-wrap: wrap;
    box-shadow: 0 1px 4px rgba(0,0,0,0.18);
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

/* ─ Validation steps section ─────────────────────────────────────── */
.validation-section-header {
    font-family: var(--font-mono);
    font-size: 0.67rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.8px;
    color: var(--muted);
    padding: 4px 0 6px;
    border-bottom: 1px solid var(--border);
    margin-bottom: 8px;
}

/* ─ System prompt labeled box ────────────────────────────────────── */
.sys-prompt-label {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 4px;
}
.sys-prompt-label .label-text {
    font-family: var(--font-sans);
    font-size: 0.74rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.45px;
    color: var(--text);
}
.sys-prompt-label .label-badge {
    font-family: var(--font-mono);
    font-size: 0.62rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.4px;
    color: var(--accent);
    background: var(--accent-dim);
    border: 1px solid rgba(45,212,191,0.3);
    border-radius: 4px;
    padding: 1px 6px;
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
   ══════════════════════════════════════════════════════════════════ */
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

.ts-count-pill {
    display: inline-block;
    padding: 3px 10px;
    border-radius: 999px;
    font-size: 0.67rem;
    font-weight: 700;
    letter-spacing: 0.35px;
    font-family: var(--font-mono);
    border: 1px solid;
}
.ts-count-pass { background: var(--surface2); color: var(--success); border-color: var(--success); }
.ts-count-fail { background: var(--surface2); color: var(--error); border-color: var(--error); }
.ts-count-skip { background: var(--surface2); color: var(--muted); border-color: var(--border); }

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
    transition: background var(--transition);
}
.ts-test-row:hover { background: var(--surface); }
.ts-test-row-pass  { border-left-color: var(--success); }
.ts-test-row-fail  { border-left-color: var(--error); background: var(--surface2); }
.ts-test-row-skip  { border-left-color: var(--border); opacity: 0.6; }

.ts-dot { flex-shrink: 0; font-weight: 700; width: 14px; text-align: center; padding-top: 1px; }
.ts-dot-pass { color: var(--success); }
.ts-dot-fail { color: var(--error);   }
.ts-dot-skip { color: var(--muted);   }

.ts-test-name { color: var(--text); word-break: break-all; }
.ts-test-err  { color: var(--error); font-size: 0.7rem; opacity: 0.88; margin-top: 1px; word-break: break-word; }
.ts-test-dur  { color: var(--muted); font-size: 0.67rem; flex-shrink: 0; padding-top: 2px; }

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

.ts-run-stamp { font-family: var(--font-mono); font-size: 0.69rem; color: var(--muted); }
</style>
"""

_SCROLL_JS = """
<script>
    function performAutoScroll() {
        const terminals = document.querySelectorAll('.terminal-window');
        terminals.forEach(el => {
            if (el.scrollHeight - el.scrollTop - el.clientHeight < 150) {
                el.scrollTop = el.scrollHeight;
            }
        });

        const mainViewport = window.parent.document.querySelector('[data-testid="stAppViewContainer"]');
        if (mainViewport) {
            mainViewport.scrollTo({
                top: mainViewport.scrollHeight,
                behavior: 'smooth'
            });
        }
    }

    const observer = new MutationObserver(() => {
        performAutoScroll();
    });

    setTimeout(() => {
        const targetNode = window.parent.document.querySelector('.main') || window.parent.document.body;
        if (targetNode) {
            observer.observe(targetNode, { childList: true, subtree: true });
        }
    }, 1000);
</script>
"""


def inject() -> None:
    st.markdown(_CSS, unsafe_allow_html=True)
    st.html(_SCROLL_JS)
