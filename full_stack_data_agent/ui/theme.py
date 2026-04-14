from __future__ import annotations


def get_ui_css() -> str:
    return """
    <style>
      :root {
        --page: #081018;
        --page-accent: #10202d;
        --panel: rgba(10, 19, 27, 0.92);
        --panel-strong: rgba(14, 25, 35, 0.98);
        --panel-soft: rgba(18, 31, 43, 0.78);
        --bubble-assistant: linear-gradient(180deg, rgba(13, 23, 33, 0.98), rgba(10, 18, 26, 0.98));
        --bubble-user: linear-gradient(180deg, rgba(21, 52, 78, 0.98), rgba(15, 38, 58, 0.98));
        --bubble-border: rgba(157, 198, 230, 0.12);
        --line: rgba(163, 196, 223, 0.12);
        --line-strong: rgba(163, 196, 223, 0.2);
        --text: #eef5fb;
        --muted: #a7bacb;
        --soft: #7f97aa;
        --accent: #63c6b7;
        --accent-strong: #2e8fca;
        --accent-soft: rgba(99, 198, 183, 0.16);
        --info: #8dd8ff;
        --danger: #ff9f95;
        --warn: #ffd18a;
        --shadow: 0 16px 38px rgba(0, 0, 0, 0.32);
        --radius-xl: 18px;
        --radius-lg: 14px;
        --radius-md: 12px;
      }

      html, body, [class*="css"] {
        color-scheme: dark;
      }

      * {
        font-family: "Segoe UI Variable Text", "SF Pro Display", "Segoe UI", "PingFang SC", "Noto Sans SC", sans-serif;
      }

      .stApp {
        background:
          radial-gradient(circle at top left, rgba(99, 198, 183, 0.14), transparent 20%),
          radial-gradient(circle at top right, rgba(46, 143, 202, 0.16), transparent 24%),
          linear-gradient(180deg, #061019 0%, #081018 50%, #0a1520 100%);
        color: var(--text);
      }

      [data-testid="stSidebar"] {
        display: none;
      }

      .block-container {
        max-width: 1460px;
        padding-top: 4.25rem;
        padding-left: 1.2rem;
        padding-right: 1.2rem;
        padding-bottom: 9rem;
      }

      textarea,
      input {
        color: var(--text) !important;
      }

      div[data-baseweb="textarea"] textarea,
      div[data-testid="stTextArea"] textarea,
      div[data-baseweb="input"] input {
        background: rgba(11, 21, 31, 0.96) !important;
        border: 1px solid var(--line) !important;
        border-radius: 14px !important;
        color: var(--text) !important;
        line-height: 1.55 !important;
        font-size: 0.98rem !important;
      }

      div[data-baseweb="textarea"] textarea:focus,
      div[data-testid="stTextArea"] textarea:focus {
        border-color: rgba(99, 198, 183, 0.55) !important;
        box-shadow: 0 0 0 2px rgba(99, 198, 183, 0.14) !important;
      }

      .stButton > button,
      div[data-testid="stBaseButton-primary"] button,
      div[data-testid="stBaseButton-secondary"] button {
        border-radius: 12px !important;
        border: 1px solid var(--line) !important;
        background: linear-gradient(180deg, rgba(15, 25, 35, 0.98), rgba(11, 19, 28, 0.98)) !important;
        color: var(--text) !important;
        font-weight: 600 !important;
        letter-spacing: -0.01em !important;
        min-height: 2.9rem !important;
        box-shadow: none !important;
      }

      div[data-testid="stBaseButton-primary"] button,
      .stButton > button[kind="primary"] {
        background: linear-gradient(135deg, var(--accent), var(--accent-strong)) !important;
        border-color: rgba(99, 198, 183, 0.4) !important;
        color: #f7feff !important;
      }

      .stButton > button:hover,
      div[data-testid="stBaseButton-primary"] button:hover,
      div[data-testid="stBaseButton-secondary"] button:hover {
        border-color: var(--line-strong) !important;
        transform: translateY(-1px);
      }

      .sidebar-shell {
        position: sticky;
        top: 4rem;
        border: 1px solid var(--line);
        border-radius: var(--radius-xl);
        background:
          linear-gradient(180deg, rgba(13, 23, 33, 0.96), rgba(9, 17, 25, 0.96)),
          var(--panel);
        box-shadow: var(--shadow);
        padding: 0.9rem;
      }

      .sidebar-shell--collapsed {
        padding: 0.8rem 0.65rem;
      }

      .sidebar-brand {
        display: flex;
        gap: 0.85rem;
        align-items: center;
        margin-bottom: 0.9rem;
      }

      .sidebar-brand--compact {
        justify-content: center;
        margin-bottom: 0.7rem;
      }

      .sidebar-brand__logo {
        width: 40px;
        height: 40px;
        border-radius: 12px;
        display: grid;
        place-items: center;
        background: linear-gradient(135deg, rgba(99, 198, 183, 0.16), rgba(46, 143, 202, 0.2));
        color: #dffbf7;
        font-weight: 700;
        letter-spacing: -0.03em;
        border: 1px solid rgba(141, 216, 255, 0.16);
      }

      .sidebar-brand__name {
        font-size: 0.98rem;
        font-weight: 700;
        color: var(--text);
        letter-spacing: -0.02em;
      }

      .sidebar-brand__meta {
        color: var(--muted);
        font-size: 0.84rem;
        line-height: 1.35;
      }

      .sidebar-item,
      .empty-card,
      .session-card {
        border: 1px solid var(--line);
        border-radius: var(--radius-md);
        background: linear-gradient(180deg, rgba(16, 28, 39, 0.9), rgba(11, 20, 29, 0.9));
        padding: 0.78rem 0.85rem;
      }

      .sidebar-item {
        margin-bottom: 0.6rem;
      }

      .sidebar-item__title {
        color: var(--text);
        font-weight: 600;
        font-size: 0.9rem;
        line-height: 1.3;
        letter-spacing: -0.01em;
      }

      .sidebar-item__meta,
      .empty-card {
        color: var(--muted);
        font-size: 0.8rem;
        line-height: 1.45;
      }

      .session-card__row {
        display: flex;
        justify-content: space-between;
        gap: 0.75rem;
        color: var(--muted);
        font-size: 0.82rem;
        padding: 0.18rem 0;
      }

      .session-card__row strong {
        color: var(--text);
        font-weight: 600;
      }

      .settings-list {
        display: grid;
        gap: 0.65rem;
      }

      .settings-list > div {
        display: flex;
        justify-content: space-between;
        gap: 0.75rem;
        color: var(--muted);
        font-size: 0.85rem;
      }

      .settings-list strong {
        color: var(--text);
      }

      .status-banner {
        margin-bottom: 0.9rem;
        border-radius: var(--radius-md);
        padding: 0.75rem 0.95rem;
        border: 1px solid var(--line);
        background: linear-gradient(180deg, rgba(16, 27, 37, 0.9), rgba(11, 20, 28, 0.9));
        color: var(--muted);
      }

      .status-banner--error {
        border-color: rgba(255, 143, 143, 0.28);
        color: var(--danger);
      }

      .status-banner--warn {
        border-color: rgba(255, 202, 133, 0.28);
        color: var(--warn);
      }

      .status-banner--info {
        border-color: rgba(141, 216, 255, 0.24);
        color: var(--info);
      }

      .conversation-shell {
        margin-bottom: 1.2rem;
      }

      .conversation-title {
        color: var(--text);
        font-size: 1.42rem;
        font-weight: 700;
        letter-spacing: -0.03em;
        line-height: 1.1;
      }

      .conversation-subtitle {
        margin-top: 0.25rem;
        color: var(--muted);
        font-size: 0.9rem;
        line-height: 1.45;
      }

      .chat-empty-state {
        border: 1px dashed rgba(141, 216, 255, 0.2);
        border-radius: var(--radius-xl);
        background:
          radial-gradient(circle at top, rgba(99, 198, 183, 0.09), transparent 46%),
          linear-gradient(180deg, rgba(13, 22, 31, 0.8), rgba(10, 18, 25, 0.82));
        padding: 2.3rem 1.5rem;
        text-align: center;
        box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.03);
      }

      .chat-empty-state__title {
        color: var(--text);
        font-size: 1.15rem;
        font-weight: 700;
        letter-spacing: -0.02em;
      }

      .chat-empty-state__body {
        margin-top: 0.45rem;
        color: var(--muted);
        max-width: 38rem;
        margin-left: auto;
        margin-right: auto;
      }

      .chat-shell {
        margin-bottom: 1rem;
      }

      .chat-shell [data-testid="stChatMessage"] {
        border: 1px solid var(--bubble-border);
        border-radius: 16px;
        padding: 0.2rem 0.24rem;
        box-shadow: var(--shadow);
        gap: 0.35rem;
        backdrop-filter: blur(10px);
      }

      .chat-shell--assistant [data-testid="stChatMessage"] {
        background: var(--bubble-assistant);
        border-color: rgba(163, 196, 223, 0.1);
      }

      .chat-shell--user [data-testid="stChatMessage"] {
        background: var(--bubble-user);
        border-color: rgba(141, 216, 255, 0.18);
      }

      .chat-shell--streaming [data-testid="stChatMessage"] {
        border-color: rgba(99, 198, 183, 0.34);
      }

      .chat-shell--error [data-testid="stChatMessage"] {
        border-color: rgba(255, 159, 149, 0.38);
      }

      .chat-shell--user [data-testid="stChatMessageAvatar"],
      .chat-shell--assistant [data-testid="stChatMessageAvatar"] {
        display: none;
      }

      .chat-shell [data-testid="stChatMessageContent"] {
        width: 100%;
        min-width: 0;
      }

      .message-header {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 0.75rem;
        margin-bottom: 0.42rem;
      }

      .message-role {
        text-transform: capitalize;
        color: var(--text);
        font-size: 0.8rem;
        font-weight: 700;
        letter-spacing: -0.01em;
      }

      .chat-shell--assistant .message-role {
        color: #dbedf9;
      }

      .chat-shell--user .message-role {
        color: #dbfbff;
      }

      .message-time {
        color: var(--soft);
        font-size: 0.74rem;
      }

      .message-text,
      .chat-shell p,
      .chat-shell li {
        color: var(--text);
        line-height: 1.62;
        overflow-wrap: anywhere;
        font-size: 0.98rem;
      }

      .chat-shell pre {
        overflow-x: auto;
        border-radius: 12px;
        border: 1px solid rgba(163, 196, 223, 0.1);
        background: rgba(6, 13, 19, 0.96);
        padding: 0.88rem;
      }

      .chat-shell code {
        white-space: pre-wrap;
      }

      .chat-shell table {
        display: block;
        width: 100%;
        overflow-x: auto;
      }

      .message-subtitle {
        margin-top: 0.8rem;
        margin-bottom: 0.4rem;
        color: var(--muted);
        font-size: 0.76rem;
        text-transform: uppercase;
        letter-spacing: 0.08em;
      }

      .message-streaming {
        color: var(--muted);
        font-style: italic;
      }

      .composer-dock {
        position: sticky;
        bottom: 0.8rem;
        z-index: 20;
        margin-top: 1.25rem;
        padding: 0.95rem;
        border: 1px solid var(--line);
        border-radius: 18px;
        background:
          linear-gradient(180deg, rgba(11, 19, 28, 0.97), rgba(8, 15, 23, 0.97)),
          var(--panel);
        box-shadow: 0 -4px 30px rgba(0, 0, 0, 0.16);
        backdrop-filter: blur(16px);
      }

      .composer-heading {
        margin-bottom: 0.8rem;
      }

      .composer-heading__title {
        color: var(--text);
        font-size: 0.98rem;
        font-weight: 700;
        letter-spacing: -0.02em;
      }

      .composer-heading__meta {
        color: var(--muted);
        font-size: 0.84rem;
        margin-top: 0.18rem;
        line-height: 1.4;
      }

      div[data-testid="stExpander"] {
        border: 1px solid var(--line);
        border-radius: var(--radius-md);
        background: linear-gradient(180deg, rgba(16, 26, 36, 0.84), rgba(11, 19, 27, 0.84));
      }

      div[data-testid="stExpander"] summary {
        letter-spacing: -0.01em;
      }

      div[data-testid="stDataFrame"] {
        border: 1px solid var(--line);
        border-radius: 14px;
        overflow: hidden;
      }

      [data-testid="stMarkdownContainer"] img,
      .stImage img,
      .vega-embed,
      canvas {
        max-width: 100% !important;
        border-radius: 14px;
      }

      @media (max-width: 980px) {
        .block-container {
          padding-left: 0.7rem;
          padding-right: 0.7rem;
          padding-bottom: 8.5rem;
        }

        .message-bubble,
        .message-bubble--user {
          width: 100%;
        }
      }
    </style>
    """
