import streamlit as st

from ui.theme import ThemeName, css_tokens

_CSS = r"""
<style>
/* ══════════════════════════════════════════════════════════════════
   ModelScope  ·  Security Research Tool
   Dark-first  ·  Cyan accent  ·  GitHub-dark neutrals
   No external font CDN — uses system stacks only (CSP safe)
   ══════════════════════════════════════════════════════════════════ */

/* ─ Non-colour tokens. All structural colours come from ui.theme. ─ */
:root {
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
    color: var(--input-text) !important;
    caret-color: var(--accent) !important;
}

[data-testid="stSidebar"] {
    background-color: var(--surface) !important;
    border-right: 1px solid var(--border) !important;
    color: var(--text) !important;
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
    background: linear-gradient(to right, var(--accent-glow), transparent 60%) !important;
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
    border-bottom: 1px solid var(--border-subtle) !important;
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
    border: 1px solid var(--border-subtle) !important;
    border-radius: 10px !important;
    box-shadow: 0 2px 8px var(--shadow) !important;
    overflow: hidden;
    margin-bottom: 0.75rem;
    transition: border-color var(--transition), box-shadow var(--transition);
}
[data-testid="stExpander"]:focus-within {
    border-color: var(--accent) !important;
    box-shadow: 0 2px 8px var(--shadow), 0 0 0 1px var(--accent-dim) !important;
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
    border-bottom: 1px solid var(--border-subtle) !important;
    border-radius: 10px 10px 0 0;
}
[data-testid="stExpander"] summary:hover {
    color: var(--accent) !important;
    background: var(--accent-glow) !important;
}

/* ─ Metrics ──────────────────────────────────────────────────────── */
[data-testid="stMetric"] {
    background: var(--surface) !important;
    border: 1px solid var(--border-subtle) !important;
    border-radius: 10px !important;
    padding: 12px 16px !important;
    box-shadow: 0 2px 8px var(--shadow) !important;
    transition: border-color var(--transition), box-shadow var(--transition);
}
[data-testid="stMetric"]:hover {
    border-color: var(--accent) !important;
    box-shadow: 0 4px 14px var(--shadow), 0 0 0 1px var(--accent-dim) !important;
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
    background: var(--terminal-bg);
    color: var(--terminal-text);
    padding: 14px 18px;
    border-radius: 8px;
    height: 500px;
    overflow-y: auto;
    font-family: var(--font-mono);
    font-size: 0.78rem;
    line-height: 1.75;
    border: 1px solid var(--terminal-border);
    border-left: 3px solid var(--accent);
    box-shadow: 0 4px 16px var(--shadow);
    white-space: pre-wrap;
    word-break: break-word;
    letter-spacing: 0.02em;
}
.terminal-window::-webkit-scrollbar { width: 4px; }
.terminal-window::-webkit-scrollbar-track { background: var(--terminal-bg); }
.terminal-window::-webkit-scrollbar-thumb { background: var(--border); border-radius: 2px; }
.terminal-window::-webkit-scrollbar-thumb:hover { background: var(--accent); }

.log-init     { color: var(--info); }
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
.log-usr      { color: var(--info); font-size: 0.74rem; font-style: italic; }
.log-cmd      { color: var(--cmd-color); font-weight: 600; }
.log-prompt   { color: var(--prompt-color); font-weight: 600; }
.log-stream   { color: var(--text); }
.log-decision { color: var(--warn); font-weight: 600; }

/* ─ Inputs ───────────────────────────────────────────────────────── */
[data-testid="stTextInput"] input,
[data-testid="stTextArea"] textarea,
[data-testid="stNumberInput"] input {
    background: var(--input-bg) !important;
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
[data-testid="stSelectbox"] [data-baseweb="select"] > div {
    background: var(--input-bg) !important;
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
    background: var(--button-bg) !important;
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
    min-height: 34px !important;
    padding: 6px 14px !important;
    white-space: nowrap !important;
}
div.stButton > button:hover {
    background: var(--surface) !important;
    border-color: var(--accent) !important;
    color: var(--accent) !important;
    box-shadow: 0 2px 8px var(--shadow) !important;
}
div.stButton > button:disabled {
    opacity: 0.3 !important;
}
div.stButton > button:active:not(:disabled) {
    transform: translateY(1px);
}

/* Primary — a restrained accent tint rather than a dense filled block. */
div.stButton > button[kind="primary"],
div.stButton > button[data-testid="baseButton-primary"] {
    background: var(--accent-dim) !important;
    border-color: var(--accent) !important;
    color: var(--accent-hi) !important;
    box-shadow: none !important;
}
div.stButton > button[kind="primary"]:hover,
div.stButton > button[data-testid="baseButton-primary"]:hover {
    background: var(--accent-glow) !important;
    border-color: var(--accent-hi) !important;
    color: var(--accent-hi) !important;
    box-shadow: 0 2px 8px var(--shadow) !important;
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
/* Execution-target and step-type radios carry their context in the expander
   or section heading. Streamlit still emits a widget-label shell even with
   label_visibility="collapsed"; hide that redundant shell explicitly so it
   cannot inherit the pill styling or flash on hover. */
[data-testid="stRadio"] > label[data-testid="stWidgetLabel"] {
    display: none !important;
}
[data-testid="stRadio"] > div[role="radiogroup"] {
    flex-direction: row !important;
    flex-wrap: wrap;
    gap: 6px !important;
}
[data-testid="stRadio"] label[data-baseweb="radio"] {
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
[data-testid="stRadio"] label[data-baseweb="radio"]:hover {
    border-color: var(--accent) !important;
    background: var(--accent-glow) !important;
    color: var(--accent) !important;
}
[data-testid="stRadio"] label[data-baseweb="radio"]:has(input:checked) {
    border-color: var(--accent) !important;
    background: var(--accent-dim) !important;
    color: var(--accent) !important;
    font-weight: 600 !important;
}
[data-testid="stRadio"] input[type="radio"] {
    accent-color: var(--accent) !important;
}
[data-testid="stRadio"] label[data-baseweb="radio"] > div:first-child {
    background: var(--surface) !important;
    border-color: var(--border) !important;
}
[data-testid="stRadio"] label[data-baseweb="radio"] > div:first-child > div {
    background: var(--surface) !important;
}
[data-testid="stRadio"] label[data-baseweb="radio"]:has(input:checked) > div:first-child {
    background: var(--accent) !important;
    border-color: var(--accent) !important;
}
[data-testid="stRadio"] label[data-baseweb="radio"]:has(input:checked) > div:first-child > div {
    background: var(--on-accent) !important;
}

/* ─ Checkbox ─────────────────────────────────────────────────────── */
[data-testid="stCheckbox"] label {
    display: flex !important;
    align-items: center !important;
    color: var(--text) !important;
    font-size: 0.83rem !important;
    transition: color var(--transition);
}
[data-testid="stCheckbox"] input[type="checkbox"] {
    accent-color: var(--accent) !important;
}
[data-testid="stCheckbox"] label[data-baseweb="checkbox"] > span:first-child {
    background: var(--surface) !important;
    border-color: var(--border) !important;
    box-sizing: border-box !important;
    flex: 0 0 16px !important;
    width: 16px !important;
    height: 16px !important;
    position: relative !important;
}
/* Checked box: an explicit ::after tick, since setting `background` above
   (rather than `background-color`) clobbers baseweb's own checkmark image. */
[data-testid="stCheckbox"] label[data-baseweb="checkbox"]:has(input:checked) > span:first-child {
    background-color: var(--accent) !important;
    border-color: var(--accent) !important;
}
[data-testid="stCheckbox"] label[data-baseweb="checkbox"]:has(input:checked) > span:first-child::after {
    content: "" !important;
    position: absolute !important;
    left: 4px !important;
    top: 1px !important;
    width: 5px !important;
    height: 9px !important;
    border: solid var(--on-accent) !important;
    border-width: 0 2px 2px 0 !important;
    transform: rotate(45deg) !important;
}
/* Disabled box stays visually distinct from both the unchecked and the
   checked states, in either theme. */
[data-testid="stCheckbox"] label[data-baseweb="checkbox"]:has(input:disabled) > span:first-child {
    background-color: var(--surface2) !important;
    border-color: var(--border-subtle) !important;
    opacity: 0.65 !important;
}
[data-testid="stCheckbox"] label:has(input:disabled) {
    color: var(--muted) !important;
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
    0%, 100% { opacity: 1; box-shadow: 0 0 0 0 transparent; }
    50%       { opacity: 0.75; box-shadow: 0 0 0 4px var(--success-dim); }
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
    border-color: var(--accent);
    box-shadow: 0 2px 10px var(--shadow);
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
    background: var(--accent-dim);
    border-color: var(--prompt-color);
}
.step-badge-command {
    color: var(--cmd-color);
    background: var(--warn-dim);
    border-color: var(--cmd-color);
}

/* ─ Service active display box ───────────────────────────────────── */
.service-active-box {
    background: var(--surface);
    border: 1px solid var(--success);
    border-left: 3px solid var(--success);
    border-radius: 8px;
    padding: 10px 14px;
    margin: 6px 0;
    animation: service-glow 3s ease-in-out infinite;
}
@keyframes service-glow {
    0%, 100% { border-left-color: var(--success); }
    50%       { border-left-color: var(--border); }
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
    border: 1px solid var(--warn);
    border-left: 3px solid var(--warn);
    border-radius: 8px;
    padding: 10px 14px;
    margin: 6px 0;
    animation: service-loading-pulse 1.8s ease-in-out infinite;
}
@keyframes service-loading-pulse {
    0%, 100% { border-left-color: var(--warn); opacity: 1; }
    50%       { border-left-color: var(--border); opacity: 0.75; }
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
    border: 1px solid var(--border-subtle) !important;
    border-radius: 8px !important;
    font-size: 0.78rem !important;
    padding: 12px 16px !important;
    box-shadow: 0 1px 4px var(--shadow) !important;
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
    box-shadow: 0 1px 4px var(--shadow) !important;
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
    min-height: 34px !important;
    padding: 6px 14px !important;
    white-space: nowrap !important;
    transition: background var(--transition), border-color var(--transition),
                color var(--transition) !important;
}
[data-testid="stDownloadButton"] button:hover {
    border-color: var(--accent) !important;
    color: var(--accent) !important;
    background: var(--surface2) !important;
    box-shadow: 0 2px 8px var(--shadow) !important;
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
    box-shadow: 0 1px 4px var(--shadow);
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
    border: 1px solid var(--accent);
    border-radius: 4px;
    padding: 1px 6px;
}

/* ─ Streamlit structural surfaces and portal content ────────────── */
[data-testid="stVerticalBlockBorderWrapper"],
[data-testid="stHorizontalBlock"],
[data-testid="stForm"] {
    border-color: var(--border) !important;
    color: var(--text) !important;
}

[data-testid="stPopoverBody"],
[data-baseweb="popover"],
[data-baseweb="menu"],
[role="listbox"] {
    background: var(--popover-bg) !important;
    border-color: var(--border) !important;
    color: var(--text) !important;
    box-shadow: 0 8px 24px var(--shadow) !important;
}
[role="option"],
[data-baseweb="menu"] li {
    background: var(--popover-bg) !important;
    color: var(--text) !important;
}
[role="option"]:hover,
[role="option"][aria-selected="true"],
[data-baseweb="menu"] li:hover {
    background: var(--surface-hover) !important;
    color: var(--text) !important;
}

/* Dialogs are rendered in a portal, outside the normal app subtree. */
[data-testid="stDialog"] {
    background-color: var(--overlay) !important;
    color: var(--text) !important;
}
[data-testid="stDialog"] [role="dialog"],
[data-testid="stDialog"] [data-baseweb="modal"],
[role="dialog"][aria-modal="true"] {
    background: var(--dialog-bg) !important;
    border: 1px solid var(--border) !important;
    color: var(--text) !important;
    box-shadow: 0 16px 48px var(--shadow) !important;
}
[data-testid="stDialog"] h1,
[data-testid="stDialog"] h2,
[data-testid="stDialog"] h3,
[data-testid="stDialog"] label,
[data-testid="stDialog"] p,
[data-testid="stDialog"] span,
[role="dialog"][aria-modal="true"] h1,
[role="dialog"][aria-modal="true"] h2,
[role="dialog"][aria-modal="true"] h3,
[role="dialog"][aria-modal="true"] label {
    color: var(--text) !important;
}
[data-testid="stDialog"] button[aria-label="Close"],
[role="dialog"][aria-modal="true"] button[aria-label="Close"] {
    background: var(--button-bg) !important;
    border: 1px solid var(--border) !important;
    color: var(--text) !important;
}
[data-testid="stDialog"] button[aria-label="Close"] svg,
[role="dialog"][aria-modal="true"] button[aria-label="Close"] svg {
    color: var(--text) !important;
    fill: currentColor !important;
}

/* Streamlit's reconnect/error overlay is a frontend-owned modal, outside the
   stDialog test-id tree. Theme its generic dialog/modal roots and descendants
   so a light card can never retain the dark-theme's near-white text. */
[role="dialog"],
[data-baseweb="modal"],
[data-testid="stConnectionError"] {
    background: var(--dialog-bg) !important;
    color: var(--text) !important;
    border-color: var(--border) !important;
}
[role="dialog"] h1,
[role="dialog"] h2,
[role="dialog"] h3,
[role="dialog"] p,
[role="dialog"] span,
[data-baseweb="modal"] h1,
[data-baseweb="modal"] h2,
[data-baseweb="modal"] h3,
[data-baseweb="modal"] p,
[data-baseweb="modal"] span,
[data-testid="stConnectionError"] h1,
[data-testid="stConnectionError"] h2,
[data-testid="stConnectionError"] h3,
[data-testid="stConnectionError"] p,
[data-testid="stConnectionError"] span {
    color: var(--text) !important;
    -webkit-text-fill-color: var(--text) !important;
    opacity: 1 !important;
}
[data-testid="stConnectionError"] code,
[data-testid="stConnectionError"] pre,
[role="dialog"] code,
[role="dialog"] pre {
    background: var(--terminal-bg) !important;
    color: var(--terminal-text) !important;
    border-color: var(--terminal-border) !important;
}

/* Help ("?") tooltip icon next to widget labels — scoped separately from
   the close-button rule above. A fixed dark badge in both themes (so it
   reads as a deliberate control, not themed text) with an UNFILLED svg:
   filling it would merge the circle outline and question-mark into a
   solid dot. Dark mode adds a border so the dark badge stays visible
   against the also-dark page background. */
/* Streamlit uses the same tooltip wrapper for actual help icons and for
   ordinary controls carrying a `help=` tooltip (such as project delete).
   Restrict the circular badge to the former so tooltip-wrapped action buttons
   retain their normal geometry. */
[data-testid="stTooltipIcon"]:has(button[aria-label^="Help for"]) {
    display: inline-flex !important;
    align-items: center !important;
    justify-content: center !important;
    background: var(--tooltip-bg) !important;
    border: 1px solid var(--tooltip-border) !important;
    border-radius: 50% !important;
    width: 1.15rem !important;
    height: 1.15rem !important;
    min-width: 1.15rem !important;
    padding: 0 !important;
    box-sizing: border-box !important;
}
[data-testid="stTooltipIcon"] button[aria-label^="Help for"],
[data-testid="stTooltipHoverTarget"] > button[aria-label^="Help for"] {
    display: inline-flex !important;
    align-items: center !important;
    justify-content: center !important;
    width: 1.15rem !important;
    height: 1.15rem !important;
    min-width: 1.15rem !important;
    padding: 0 !important;
    background: var(--tooltip-bg) !important;
    border: 1px solid var(--tooltip-border) !important;
    border-radius: 50% !important;
    color: var(--tooltip-icon) !important;
}
[data-testid="stTooltipIcon"] button[aria-label^="Help for"] svg {
    fill: none !important;
    stroke: var(--tooltip-icon) !important;
    color: var(--tooltip-icon) !important;
}
[data-testid="stTooltipHoverTarget"] > button[aria-label^="Help for"] svg {
    fill: none !important;
    stroke: var(--tooltip-icon) !important;
    color: var(--tooltip-icon) !important;
}

/* Current Streamlit input shells, including controls rendered in portals. */
[data-testid="stTextInput"] [data-baseweb="input"],
[data-testid="stNumberInput"] [data-baseweb="input"],
[data-testid="stTextArea"] [data-baseweb="textarea"],
[data-testid="stSelectbox"] [data-baseweb="select"] > div,
[data-testid="stMultiSelect"] [data-baseweb="select"] > div,
[role="dialog"] [data-baseweb="input"],
[role="dialog"] [data-baseweb="textarea"],
[role="dialog"] [data-baseweb="select"] > div {
    background: var(--input-bg) !important;
    border-color: var(--border) !important;
    color: var(--text) !important;
}
[data-testid="stTextInput"] input,
[data-testid="stNumberInput"] input,
[data-testid="stTextArea"] textarea,
[data-testid="stSelectbox"] input,
[data-testid="stMultiSelect"] input,
[role="dialog"] input,
[role="dialog"] textarea {
    background: var(--input-bg) !important;
    color: var(--input-text) !important;
    -webkit-text-fill-color: var(--input-text) !important;
    caret-color: var(--accent) !important;
}
input::placeholder,
textarea::placeholder {
    color: var(--muted) !important;
    opacity: 0.68 !important;
    font-style: italic !important;
}

/* Password reveal controls are BaseWeb buttons nested inside an input shell.
   They otherwise follow the browser's system theme rather than the selected
   in-app theme. */
[data-testid="stTextInput"] [data-baseweb="input"] button {
    background: var(--input-bg) !important;
    border: 0 !important;
    color: var(--muted) !important;
}
[data-testid="stTextInput"] [data-baseweb="input"] button:hover {
    background: var(--surface2) !important;
    color: var(--text) !important;
}
[data-testid="stTextInput"] [data-baseweb="input"] button svg {
    fill: none !important;
    stroke: currentColor !important;
}

/* Disabled input shells must never resolve to the same colours as an
   enabled control — this rule must come after the generic input-shell
   rules above so it wins regardless of import order elsewhere. */
[data-testid="stNumberInput"]:has(input:disabled) [data-baseweb="input"],
[data-testid="stTextInput"]:has(input:disabled) [data-baseweb="input"] {
    background: var(--surface2) !important;
    border-color: var(--border-subtle) !important;
}
[data-testid="stNumberInput"] input:disabled,
[data-testid="stTextInput"] input:disabled {
    color: var(--muted) !important;
    -webkit-text-fill-color: var(--muted) !important;
    opacity: 0.65 !important;
}

/* ─ Optional-parameter cards ─────────────────────────────────────────
   Shared by Llama-Server-Bot and CAF + llama.cpp's advanced runtime
   grid (ui.optional_param_card) — an enabled card (its checkbox
   checked) gets an accent border and tinted surface; a disabled one
   stays muted. The disabled input-shell rule above already dims the
   actual control inside it. */
[class*="st-key-advcard_"] {
    border: 1px solid var(--border) !important;
    border-radius: 8px !important;
    background: var(--surface) !important;
    transition: border-color var(--transition), background var(--transition);
}
[class*="st-key-advcard_"]:has(input[type="checkbox"]:checked) {
    border-color: var(--accent) !important;
    background: var(--accent-dim) !important;
}
[class*="st-key-advcard_"]:has(input[type="checkbox"]:not(:checked)) {
    background: var(--surface2) !important;
    border-color: var(--border-subtle) !important;
}

/* Ordinary, arrow, form, download, and number-stepper buttons. */
[data-testid="stButton"] button,
button[data-testid^="stBaseButton-"],
[data-testid="stPopoverButton"],
[data-testid="stFormSubmitButton"] button,
[data-testid="stDownloadButton"] button,
[data-testid="stNumberInput"] button,
button[data-testid="stNumberInputStepUp"],
button[data-testid="stNumberInputStepDown"] {
    background: var(--button-bg) !important;
    border-color: var(--border) !important;
    color: var(--text) !important;
}
[data-testid="stButton"] button:hover:not(:disabled),
button[data-testid^="stBaseButton-"]:hover:not(:disabled),
[data-testid="stPopoverButton"]:hover:not(:disabled),
[data-testid="stFormSubmitButton"] button:hover:not(:disabled),
[data-testid="stDownloadButton"] button:hover:not(:disabled),
[data-testid="stNumberInput"] button:hover:not(:disabled) {
    background: var(--surface-hover) !important;
    border-color: var(--accent) !important;
    color: var(--accent) !important;
}
[data-testid="stButton"] button[kind="primary"],
button[data-testid="stBaseButton-primary"],
[data-testid="stFormSubmitButton"] button[kind="primary"] {
    background: var(--accent-dim) !important;
    border-color: var(--accent) !important;
    color: var(--accent-hi) !important;
}
[data-testid="stButton"] button[kind="primary"]:hover:not(:disabled),
button[data-testid="stBaseButton-primary"]:hover:not(:disabled),
[data-testid="stFormSubmitButton"] button[kind="primary"]:hover:not(:disabled) {
    background: var(--accent-glow) !important;
    border-color: var(--accent-hi) !important;
    color: var(--accent-hi) !important;
}
[data-testid="stButton"] button:disabled,
button[data-testid^="stBaseButton-"]:disabled,
[data-testid="stPopoverButton"]:disabled,
[data-testid="stNumberInput"] button:disabled {
    background: var(--surface2) !important;
    border-color: var(--border-subtle) !important;
    color: var(--muted) !important;
}

/* Toggle and checkbox tracks must not inherit the opposite native theme. */
[data-testid="stToggle"] label,
[data-testid="stCheckbox"] label {
    color: var(--text) !important;
}
[data-testid="stToggle"] button[role="switch"] {
    background: var(--surface2) !important;
    border: 1px solid var(--border) !important;
}
[data-testid="stToggle"] button[role="switch"][aria-checked="true"] {
    background: var(--accent) !important;
    border-color: var(--accent) !important;
}

/* Preserve semantics while deriving the alert's structural surface. */
[data-testid="stAlert"]:has([data-testid="stNotificationContentInfo"]) {
    background: var(--accent-dim) !important;
}
[data-testid="stAlert"]:has([data-testid="stNotificationContentWarning"]) {
    background: var(--warn-dim) !important;
}
[data-testid="stAlert"]:has([data-testid="stNotificationContentError"]) {
    background: var(--error-dim) !important;
}
[data-testid="stAlert"]:has([data-testid="stNotificationContentSuccess"]) {
    background: var(--success-dim) !important;
}

/* The destructive treatment belongs on the confirmation action, not the
   compact trash affordance in the project action row. */
[class*="st-key-btn_confirm_delete_project"] button {
    background: var(--error-dim) !important;
    border-color: var(--error) !important;
    color: var(--error) !important;
}
[class*="st-key-btn_confirm_delete_project"] button:hover {
    background: var(--error) !important;
    border-color: var(--error) !important;
    color: var(--on-error) !important;
}

/* Validation editor controls live inside a popover over a dialog. Keep their
   nested cards compact and visibly distinct without Streamlit's oversized
   default container frame. */
[data-testid="stPopoverBody"] [data-testid="stVerticalBlockBorderWrapper"] {
    border-width: 1px !important;
    border-radius: 6px !important;
    padding: 0.45rem 0.55rem !important;
    background: var(--surface2) !important;
}
[data-testid="stPopoverBody"] [data-testid="stVerticalBlockBorderWrapper"] [data-baseweb="input"],
[data-testid="stPopoverBody"] [data-testid="stVerticalBlockBorderWrapper"] [data-baseweb="select"] > div {
    background: var(--input-bg) !important;
}

/* BaseWeb renders the popover in a portal. Its direct content wrapper can
   carry an independent native-theme surface, so reset that wrapper and the
   text hierarchy explicitly rather than relying on inherited page styles. */
[data-testid="stPopoverBody"],
[data-baseweb="popover"] {
    background: var(--surface2) !important;
    color: var(--text) !important;
}
[data-testid="stPopoverBody"] > div,
[data-baseweb="popover"] > div {
    background: transparent !important;
    color: inherit !important;
}
[data-testid="stPopoverBody"] [data-testid="stMarkdownContainer"] p,
[data-testid="stPopoverBody"] [data-testid="stMarkdownContainer"] span,
[data-testid="stPopoverBody"] [data-testid="stCaptionContainer"] p,
[data-baseweb="popover"] [data-testid="stMarkdownContainer"] p,
[data-baseweb="popover"] [data-testid="stMarkdownContainer"] span,
[data-baseweb="popover"] [data-testid="stCaptionContainer"] p {
    color: var(--text) !important;
}
[data-testid="stPopoverBody"] [data-testid="stCaptionContainer"] p,
[data-baseweb="popover"] [data-testid="stCaptionContainer"] p {
    color: var(--muted) !important;
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


def inject(theme: ThemeName) -> None:
    st.markdown(_CSS, unsafe_allow_html=True)
    st.markdown(css_tokens(theme), unsafe_allow_html=True)
    st.html(_SCROLL_JS)
