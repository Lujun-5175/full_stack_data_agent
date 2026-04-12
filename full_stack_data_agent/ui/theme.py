from __future__ import annotations


def get_ui_css() -> str:
    return """
    <style>
      :root {
        --page: #07111f;
        --page-2: #0a1728;
        --shell: rgba(7, 16, 30, 0.88);
        --rail: rgba(7, 16, 30, 0.82);
        --panel: rgba(12, 21, 36, 0.92);
        --panel-2: rgba(16, 27, 45, 0.96);
        --panel-3: rgba(20, 34, 55, 0.98);
        --ink: #f5f7fb;
        --muted: #9fb0c4;
        --soft: #7f92a9;
        --line: rgba(151, 175, 211, 0.16);
        --line-strong: rgba(151, 175, 211, 0.28);
        --accent: #5b8cff;
        --accent-2: #7a7cff;
        --accent-soft: rgba(91, 140, 255, 0.16);
        --success: #3dd598;
        --success-soft: rgba(61, 213, 152, 0.14);
        --warn: #ffb86b;
        --warn-soft: rgba(255, 184, 107, 0.14);
        --danger: #ff6b7a;
        --danger-soft: rgba(255, 107, 122, 0.14);
        --radius-xl: 26px;
        --radius-lg: 20px;
        --radius-md: 16px;
        --radius-sm: 12px;
        --shadow: 0 18px 40px rgba(0, 0, 0, 0.22);
        --glow: 0 0 0 1px rgba(91, 140, 255, 0.15), 0 0 36px rgba(91, 140, 255, 0.10);
      }

      html, body, [class*="css"] {
        color-scheme: dark;
      }

      * {
        font-family: "Nunito Sans", "Aptos", "Segoe UI Variable Text", "Segoe UI", "Inter", "SF Pro Display", "Noto Sans", sans-serif;
      }

      .stApp {
        background:
          radial-gradient(circle at 15% 5%, rgba(122, 124, 255, 0.22), transparent 18%),
          radial-gradient(circle at 80% 0%, rgba(67, 188, 255, 0.18), transparent 20%),
          radial-gradient(circle at 100% 70%, rgba(91, 140, 255, 0.16), transparent 22%),
          linear-gradient(180deg, #0a1424 0%, #07111f 100%);
        color: var(--ink);
      }

      [data-testid="stAppViewContainer"] {
        color: var(--ink);
      }

      [data-testid="stSidebar"] {
        display: none;
      }

      .block-container {
        max-width: 1720px;
        padding-top: 4.25rem;
        padding-left: 1.15rem;
        padding-right: 1.15rem;
        padding-bottom: 1.5rem;
      }

      textarea,
      input {
        color: var(--ink) !important;
      }

      div[data-baseweb="textarea"] textarea,
      div[data-baseweb="input"] input,
      div[data-testid="stTextArea"] textarea,
      div[data-testid="stTextInput"] input {
        background: linear-gradient(180deg, rgba(10,18,30,0.96), rgba(8,14,24,0.96)) !important;
        border: 1px solid var(--line) !important;
        border-radius: 18px !important;
        color: var(--ink) !important;
        box-shadow: inset 0 1px 0 rgba(255,255,255,0.02);
      }

      div[data-baseweb="textarea"] textarea:focus,
      div[data-baseweb="input"] input:focus,
      div[data-testid="stTextArea"] textarea:focus,
      div[data-testid="stTextInput"] input:focus {
        border-color: rgba(91, 140, 255, 0.48) !important;
        box-shadow: 0 0 0 3px rgba(91, 140, 255, 0.12) !important;
      }

      .stButton > button,
      div[data-testid="stBaseButton-secondary"] button,
      div[data-testid="stBaseButton-primary"] button {
        border-radius: 14px !important;
        padding: 0.74rem 1rem !important;
        border: 1px solid var(--line) !important;
        background: linear-gradient(180deg, rgba(17,29,47,0.95), rgba(11,19,33,0.95)) !important;
        color: var(--ink) !important;
        font-weight: 650 !important;
        box-shadow: none !important;
        transition: transform 140ms ease, border-color 140ms ease, background 140ms ease;
      }

      .stButton > button:hover,
      div[data-testid="stBaseButton-secondary"] button:hover,
      div[data-testid="stBaseButton-primary"] button:hover {
        transform: translateY(-1px);
        border-color: var(--line-strong) !important;
        background: linear-gradient(180deg, rgba(22,36,58,0.96), rgba(13,22,37,0.96)) !important;
      }

      div[data-testid="stBaseButton-primary"] button,
      .stButton > button[kind="primary"] {
        border-color: rgba(91, 140, 255, 0.26) !important;
        background: linear-gradient(180deg, rgba(91,140,255,0.96), rgba(58,101,214,0.96)) !important;
        color: #f9fbff !important;
        box-shadow: 0 0 0 1px rgba(91,140,255,0.18), 0 10px 24px rgba(45,83,180,0.28) !important;
      }

      div[data-testid="stBaseButton-primary"] button:hover,
      .stButton > button[kind="primary"]:hover {
        background: linear-gradient(180deg, rgba(107,152,255,0.98), rgba(63,107,223,0.98)) !important;
      }

      div[data-testid="stAlert"] {
        border-radius: 14px;
        border: 1px solid var(--line);
      }

      details summary {
        color: var(--ink) !important;
      }

      .shell-header {
        display: flex;
        justify-content: space-between;
        gap: 1rem;
        align-items: flex-start;
        padding: 1.1rem 1.2rem 1.15rem 1.2rem;
        border-radius: var(--radius-xl);
        border: 1px solid rgba(151, 175, 211, 0.14);
        background: linear-gradient(180deg, rgba(8,16,29,0.88), rgba(10,19,33,0.92));
        box-shadow: var(--glow);
        margin-bottom: 1.1rem;
      }

      .shell-header__eyebrow {
        font-size: 0.75rem;
        color: var(--soft);
        letter-spacing: 0.12em;
        text-transform: uppercase;
        line-height: 1.35;
        margin-bottom: 0.38rem;
      }

      .shell-header__title-row {
        display: flex;
        align-items: center;
        gap: 0.6rem;
        flex-wrap: wrap;
      }

      .shell-header h1 {
        margin: 0;
        color: var(--ink);
        font-size: 1.64rem;
        line-height: 1.12;
        letter-spacing: -0.04em;
      }

      .shell-header p {
        margin: 0.45rem 0 0 0;
        max-width: 62rem;
        color: var(--muted);
        line-height: 1.56;
        font-size: 0.94rem;
      }

      .shell-header__badges {
        display: flex;
        flex-wrap: wrap;
        justify-content: flex-end;
        gap: 0.58rem;
        padding-top: 0.18rem;
      }

      .badge {
        display: inline-flex;
        align-items: center;
        padding: 0.42rem 0.76rem;
        border-radius: 999px;
        border: 1px solid var(--line);
        background: rgba(14, 24, 40, 0.82);
        color: var(--muted);
        font-size: 0.82rem;
        font-weight: 650;
        line-height: 1.2;
      }

      .badge--ok {
        background: var(--success-soft);
        color: var(--success);
        border-color: rgba(61, 213, 152, 0.22);
      }

      .badge--warn {
        background: var(--danger-soft);
        color: var(--danger);
        border-color: rgba(255, 107, 122, 0.22);
      }

      .status-dot {
        width: 11px;
        height: 11px;
        border-radius: 999px;
        background: var(--soft);
        box-shadow: 0 0 12px rgba(159, 176, 196, 0.18);
      }

      .status-dot--ok {
        background: var(--success);
        box-shadow: 0 0 14px rgba(61, 213, 152, 0.28);
      }

      .status-dot--warn {
        background: var(--danger);
        box-shadow: 0 0 14px rgba(255, 107, 122, 0.24);
      }

      .workspace-grid {
        display: grid;
        grid-template-columns: 260px minmax(0, 1.8fr) minmax(340px, 0.92fr);
        gap: 1.1rem;
        align-items: start;
      }

      .workspace-column {
        display: grid;
        gap: 1.1rem;
      }

      .rail-shell,
      .main-shell,
      .inspector-shell {
        border-radius: var(--radius-xl);
        border: 1px solid var(--line);
        background: linear-gradient(180deg, rgba(8,15,28,0.94), rgba(6,13,23,0.98));
        box-shadow: var(--shadow);
      }

      .rail-shell {
        background: linear-gradient(180deg, rgba(7,14,26,0.92), rgba(5,10,18,0.98));
      }

      .main-shell,
      .inspector-shell {
        background: linear-gradient(180deg, rgba(10,18,31,0.94), rgba(8,14,24,0.98));
      }

      .section {
        padding: 1.08rem 1.12rem;
      }

      .section + .section {
        border-top: 1px solid var(--line);
      }

      .panel-title {
        margin: 0 0 0.55rem 0;
        color: var(--ink);
        font-size: 0.84rem;
        font-weight: 700;
        letter-spacing: 0.08em;
        text-transform: uppercase;
        line-height: 1.3;
      }

      .panel-subtitle {
        margin: -0.05rem 0 0.9rem 0;
        color: var(--muted);
        font-size: 0.85rem;
        line-height: 1.52;
      }

      .rail-brand {
        display: flex;
        align-items: center;
        gap: 0.85rem;
      }

      .rail-brand__icon {
        width: 42px;
        height: 42px;
        border-radius: 14px;
        display: grid;
        place-items: center;
        background: linear-gradient(135deg, rgba(91,140,255,0.26), rgba(122,124,255,0.14));
        color: #dfe8ff;
        font-weight: 800;
        letter-spacing: 0.04em;
      }

      .rail-brand__name {
        color: var(--ink);
        font-weight: 700;
      }

      .rail-brand__meta {
        color: var(--muted);
        font-size: 0.82rem;
        margin-top: 0.18rem;
      }

      .nav-list {
        display: grid;
        gap: 0.45rem;
      }

      .nav-item {
        border: 1px solid transparent;
        border-radius: 14px;
        padding: 0.72rem 0.82rem;
        color: var(--muted);
        background: rgba(14, 24, 40, 0.42);
      }

      .nav-item--active {
        color: var(--ink);
        background: rgba(91,140,255,0.12);
        border-color: rgba(91,140,255,0.22);
      }

      .workspace-breadcrumb {
        color: var(--soft);
        font-size: 0.78rem;
        letter-spacing: 0.06em;
        text-transform: uppercase;
        line-height: 1.35;
        margin-bottom: 0.78rem;
      }

      .workspace-heading__title {
        color: var(--ink);
        font-size: 1.28rem;
        font-weight: 720;
        line-height: 1.18;
        margin-bottom: 0.34rem;
      }

      .workspace-heading__subtitle {
        color: var(--muted);
        font-size: 0.88rem;
        line-height: 1.55;
      }

      .task-shell,
      .history-shell {
        border: 1px solid var(--line);
        border-radius: var(--radius-lg);
        background: linear-gradient(180deg, rgba(13,22,38,0.92), rgba(9,16,28,0.96));
        overflow: visible;
      }

      .history-turn + .history-turn {
        margin-top: 0.95rem;
      }

      .history-card {
        border: 1px solid var(--line);
        border-radius: var(--radius-md);
        background: rgba(13, 22, 37, 0.96);
        padding: 0.98rem 1rem;
      }

      .history-card + .history-card {
        margin-top: 0.5rem;
      }

      .history-card--user {
        background: rgba(15, 24, 39, 0.88);
      }

      .history-card--assistant {
        background: linear-gradient(180deg, rgba(14,24,40,0.98), rgba(11,19,33,0.98));
        border-color: rgba(91,140,255,0.18);
      }

      .history-card__top {
        display: flex;
        justify-content: space-between;
        gap: 0.8rem;
        margin-bottom: 0.42rem;
        flex-wrap: wrap;
      }

      .history-card__role {
        color: var(--ink);
        font-size: 0.87rem;
        font-weight: 700;
      }

      .history-card__meta {
        color: var(--soft);
        font-size: 0.75rem;
        white-space: nowrap;
      }

      .history-card__body {
        color: var(--ink);
        line-height: 1.64;
        white-space: pre-wrap;
      }

      .history-card__footer {
        margin-top: 0.55rem;
        padding-top: 0.52rem;
        border-top: 1px solid var(--line);
        color: var(--muted);
        font-size: 0.78rem;
      }

      .file-card {
        border: 1px solid var(--line);
        border-radius: var(--radius-md);
        background: rgba(14, 24, 40, 0.72);
        padding: 0.84rem 0.88rem;
      }

      .file-card + .file-card {
        margin-top: 0.6rem;
      }

      .file-card__top {
        display: flex;
        justify-content: space-between;
        gap: 0.6rem;
        margin-bottom: 0.2rem;
      }

      .file-card__name {
        color: var(--ink);
        font-weight: 700;
      }

      .file-card__status {
        color: var(--success);
        font-size: 0.74rem;
      }

      .file-card__meta,
      .file-card__summary {
        color: var(--muted);
        font-size: 0.82rem;
        line-height: 1.42;
      }

      .file-card__summary {
        margin-top: 0.25rem;
      }

      .stat-stack {
        display: grid;
        gap: 0.55rem;
      }

      .stat-chip {
        display: flex;
        justify-content: space-between;
        gap: 0.8rem;
        align-items: center;
        border: 1px solid var(--line);
        border-radius: 14px;
        background: rgba(14, 24, 40, 0.7);
        padding: 0.72rem 0.82rem;
      }

      .stat-chip__label {
        color: var(--soft);
        font-size: 0.76rem;
        text-transform: uppercase;
        letter-spacing: 0.08em;
      }

      .stat-chip__value {
        color: var(--ink);
        font-weight: 700;
      }

      .inspector-card,
      .timeline-card,
      .diagnostic-card {
        border: 1px solid var(--line);
        border-radius: var(--radius-md);
        background: rgba(14, 24, 40, 0.82);
        padding: 0.88rem 0.94rem;
      }

      .inspector-card__title {
        color: var(--ink);
        font-weight: 700;
        margin-bottom: 0.28rem;
      }

      .inspector-card__body {
        color: var(--muted);
        font-size: 0.85rem;
        line-height: 1.52;
      }

      .timeline-card + .timeline-card {
        margin-top: 0.65rem;
      }

      .timeline-card__top {
        display: flex;
        justify-content: space-between;
        gap: 0.6rem;
        margin-bottom: 0.24rem;
      }

      .timeline-card__title {
        color: var(--ink);
        font-size: 0.87rem;
        font-weight: 700;
      }

      .timeline-card__meta {
        color: var(--soft);
        font-size: 0.75rem;
      }

      .timeline-card__body,
      .timeline-card__footer {
        color: var(--muted);
        font-size: 0.82rem;
        line-height: 1.4;
      }

      .timeline-card__footer {
        margin-top: 0.3rem;
      }

      .diagnostic-grid {
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 0.65rem;
      }

      .mini-label {
        color: var(--soft);
        font-size: 0.74rem;
        letter-spacing: 0.06em;
        text-transform: uppercase;
        margin-bottom: 0.18rem;
      }

      .mini-value {
        color: var(--ink);
        font-size: 0.92rem;
        font-weight: 700;
      }

      .empty-state {
        border: 1px dashed var(--line-strong);
        border-radius: var(--radius-md);
        background: rgba(15, 24, 39, 0.74);
        padding: 0.98rem 1rem;
      }

      .empty-state--compact {
        padding: 0.78rem 0.82rem;
      }

      .conversation-empty {
        min-height: 210px;
        display: flex;
        flex-direction: column;
        justify-content: center;
      }

      .empty-state__title {
        color: var(--ink);
        font-size: 0.93rem;
        font-weight: 700;
        margin-bottom: 0.22rem;
      }

      .empty-state__body {
        color: var(--muted);
        font-size: 0.86rem;
        line-height: 1.45;
      }

      @media (max-width: 1280px) {
        .workspace-grid {
          grid-template-columns: 240px minmax(0, 1.45fr) minmax(300px, 0.95fr);
        }
      }

      @media (max-width: 1120px) {
        .workspace-grid {
          grid-template-columns: 1fr;
        }
      }

      @media (max-width: 860px) {
        .shell-header {
          flex-direction: column;
        }

        .shell-header__badges {
          justify-content: flex-start;
          padding-top: 0;
        }

        .diagnostic-grid {
          grid-template-columns: 1fr;
        }
      }
      </style>
      <style>
      .rail-brand__detail {
        color: var(--soft);
        font-size: 0.76rem;
        margin-top: 0.2rem;
      }

      .main-shell {
        display: flex;
        flex-direction: column;
        gap: 1rem;
      }

      .conversation-section {
        min-height: 62vh;
      }

      .composer-shell {
        margin-top: auto;
        display: grid;
        gap: 0.8rem;
      }

      .history-card {
        margin-bottom: 0.85rem;
      }

      .history-card--user {
        border-left: 3px solid var(--accent);
      }

      .history-card--assistant {
        border-left: 3px solid var(--success);
      }
      </style>
    """
