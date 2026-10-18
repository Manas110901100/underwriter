"""Neumorphic styling and small HTML helpers for the Streamlit app.

Every piece of document text shown through these helpers is HTML-escaped,
because submission content is untrusted.
"""
from html import escape                     # makes untrusted text safe to put in HTML

import streamlit as st                      # injects the CSS and HTML

BG = "#e8ecf2"                              # soft grey-blue page and card background
SHADOW_DARK = "#c8ccd4"                     # bottom-right shadow (the "depth")
SHADOW_LIGHT = "#ffffff"                    # top-left highlight (the "light source")

CSS = f"""
<style>
/* Page */
.stApp {{ background: {BG}; }}
.block-container {{ max-width: 1150px; padding-top: 2rem; }}
h1, h2, h3, h4 {{ color: #1f2937; letter-spacing: -0.01em; }}

/* Raised card: st.container(key="card_...") */
[class*="st-key-card"] {{
    background: {BG};
    border-radius: 18px;
    padding: 1.2rem 1.4rem;
    box-shadow: 8px 8px 16px {SHADOW_DARK}, -8px -8px 16px {SHADOW_LIGHT};
    margin-bottom: 1.2rem;
}}

/* Buttons: raised, pressed inset */
.stButton > button, .stDownloadButton > button, .stFormSubmitButton > button {{
    background: {BG};
    border: none;
    border-radius: 12px;
    color: #1f2937;
    font-weight: 600;
    box-shadow: 5px 5px 10px {SHADOW_DARK}, -5px -5px 10px {SHADOW_LIGHT};
    transition: box-shadow .15s ease;
}}
.stButton > button:hover, .stDownloadButton > button:hover {{ color: #4f6bed; }}
.stButton > button:active, .stDownloadButton > button:active {{
    box-shadow: inset 4px 4px 8px {SHADOW_DARK}, inset -4px -4px 8px {SHADOW_LIGHT};
}}
.stButton > button[kind="primary"] {{ color: #ffffff; background: #4f6bed; }}
.stButton > button[kind="primary"]:hover {{ color: #ffffff; background: #4360dd; }}

/* Inputs: inset */
.stTextInput input, .stTextArea textarea, [data-baseweb="select"] > div {{
    background: {BG} !important;
    border: none !important;
    border-radius: 12px !important;
    box-shadow: inset 4px 4px 8px {SHADOW_DARK}, inset -4px -4px 8px {SHADOW_LIGHT} !important;
}}
[data-testid="stFileUploaderDropzone"] {{
    background: {BG};
    border: 2px dashed #b9c0cc;
    border-radius: 14px;
    box-shadow: inset 4px 4px 8px {SHADOW_DARK}, inset -4px -4px 8px {SHADOW_LIGHT};
}}

/* Tabs: raised, the selected one pressed in */
.stTabs [data-baseweb="tab-list"] {{ gap: .6rem; }}
.stTabs [data-baseweb="tab"] {{
    background: {BG};
    border-radius: 12px;
    padding: .4rem .9rem;
    box-shadow: 4px 4px 8px {SHADOW_DARK}, -4px -4px 8px {SHADOW_LIGHT};
}}
.stTabs [aria-selected="true"] {{
    box-shadow: inset 3px 3px 6px {SHADOW_DARK}, inset -3px -3px 6px {SHADOW_LIGHT};
    color: #4f6bed;
}}
.stTabs [data-baseweb="tab-highlight"], .stTabs [data-baseweb="tab-border"] {{ display: none; }}

/* Expanders: inset panel */
[data-testid="stExpander"] details {{
    background: {BG};
    border: none;
    border-radius: 12px;
    box-shadow: inset 3px 3px 6px {SHADOW_DARK}, inset -3px -3px 6px {SHADOW_LIGHT};
}}

/* Pills: small colored status badges */
.pill {{
    display: inline-block; padding: .15rem .65rem; border-radius: 999px;
    font-size: .78rem; font-weight: 700; letter-spacing: .02em; white-space: nowrap;
}}
.pill-pass, .pill-known {{ background: #dcfce7; color: #166534; }}
.pill-flag {{ background: #fee2e2; color: #b42318; }}
.pill-unknown, .pill-conflict, .pill-invalid {{ background: #fef3c7; color: #92400e; }}
.pill-missing {{ background: #fde2e1; color: #9f1d17; }}
.pill-override {{ background: #ede9fe; color: #5b21b6; }}
.pill-neutral {{ background: #e5e7eb; color: #374151; }}

/* Overall status banner */
.banner {{
    border-radius: 18px; padding: 1rem 1.4rem; margin: .4rem 0 1.4rem 0;
    box-shadow: 8px 8px 16px {SHADOW_DARK}, -8px -8px 16px {SHADOW_LIGHT};
    display: flex; align-items: center; gap: 1rem; flex-wrap: wrap;
}}
.banner-ready {{ background: #e7f8ee; border-left: 8px solid #16a34a; }}
.banner-review {{ background: #fff7e6; border-left: 8px solid #d97706; }}
.banner .title {{ font-size: 1.35rem; font-weight: 800; color: #1f2937; }}
.banner .sub {{ color: #4b5563; font-size: .9rem; }}

/* Error / warning cards: high contrast on purpose, never soft */
.alert {{
    border-radius: 14px; padding: .9rem 1.1rem; margin: .6rem 0;
    box-shadow: 4px 4px 10px {SHADOW_DARK};
}}
.alert-error {{ background: #fff1f0; border-left: 7px solid #d92d20; }}
.alert-warning {{ background: #fffaeb; border-left: 7px solid #dc6803; }}
.alert .a-title {{ font-weight: 800; margin-bottom: .25rem; }}
.alert-error .a-title {{ color: #b42318; }}
.alert-warning .a-title {{ color: #b54708; }}
.alert .a-row {{ color: #1f2937; font-size: .92rem; margin-top: .15rem; }}
.alert .a-label {{ font-weight: 700; }}

/* Evidence lines */
.evidence {{ font-size: .86rem; color: #374151; margin: .2rem 0 .2rem .2rem; }}
.evidence .src {{ font-weight: 600; }}
.evidence .quote {{ font-style: italic; color: #111827; }}
.evidence .bad {{ color: #b42318; font-weight: 600; }}
.muted {{ color: #6b7280; font-size: .85rem; }}
.value {{ font-size: 1.05rem; font-weight: 700; color: #111827; }}
</style>
"""


def apply_style():
    """Inject the CSS once per page run."""
    st.markdown(CSS, unsafe_allow_html=True)


def pill(text, kind=None):
    """Small colored badge; `kind` picks the color (defaults to the text, e.g. 'PASS')."""
    kind = (kind or text).lower()
    return f'<span class="pill pill-{escape(kind)}">{escape(text)}</span>'


def html(markup):
    """Render trusted markup (callers escape any document text first)."""
    st.markdown(markup, unsafe_allow_html=True)


def alert(title, what=None, fix=None, level="error"):
    """Clearly visible problem card: what happened and how to fix it."""
    icon = "⛔" if level == "error" else "⚠️"
    rows = ""
    if what:
        rows += f'<div class="a-row"><span class="a-label">What happened:</span> {escape(what)}</div>'
    if fix:
        rows += f'<div class="a-row"><span class="a-label">How to fix:</span> {escape(fix)}</div>'
    html(f'<div class="alert alert-{level}"><div class="a-title">{icon} {escape(title)}</div>{rows}</div>')


def banner(status, business_name):
    """Big READY / NEEDS REVIEW banner with the applicant name."""
    ready = status == "READY_FOR_REVIEW"
    label = "Ready for review" if ready else "Needs review"
    sub = ("All five checks passed." if ready else "One or more checks are FLAG or UNKNOWN.")
    html(f'<div class="banner {"banner-ready" if ready else "banner-review"}">'
         f'<div><div class="title">{escape(business_name)}</div>'
         f'<div class="sub">{escape(sub)} Readiness for human review only, not a coverage, '
         f'pricing or binding decision.</div></div>'
         f'<div style="margin-left:auto">{pill(label, "pass" if ready else "unknown")}</div></div>')


def evidence_line(source, locator, text, verified, is_quote=True, value_in_quote=None):
    """One evidence row. Quotes are shown in quote marks; observations (CSV count,
    attachment inventory) are shown as plain text. Red markers flag a quote that
    isn't in the file, or a quote that doesn't contain the extracted value."""
    check = "" if verified else ' <span class="bad">(quote not found in file)</span>'
    if value_in_quote is False:
        check += ' <span class="bad">(value not found in this quote)</span>'
    if not text:
        quote_html = ""
    elif is_quote:
        quote_html = f' &mdash; <span class="quote">“{escape(text)}”</span>'
    else:
        quote_html = f' &mdash; {escape(text)}'
    html(f'<div class="evidence">📄 <span class="src">{escape(source)}</span>'
         f'{", " + escape(locator) if locator else ""}{quote_html}{check}</div>')
