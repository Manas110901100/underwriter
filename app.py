"""Streamlit entry point: review one commercial auto submission.

    streamlit run app.py

Upload files or a folder, enter a folder path, or paste the broker email. The
app extracts the fields (AI), merges them, runs R1-R5 (no AI), and writes an
underwriter summary and an editable draft broker email (AI, never sent).
"""
import asyncio                              # runs the async pipeline
import html as html_lib                     # escapes untrusted text shown as HTML
import json                                 # JSON download
import os                                   # reads OPENAI_API_KEY / SAMPLES_DIR
import tempfile                             # fresh folder per review, deleted afterwards
import threading                            # background thread for the event loop
import traceback                            # technical details for unexpected errors
from pathlib import Path                    # file names and paths

import streamlit as st                      # the web UI
from dotenv import load_dotenv              # loads .env into the environment

from underwriting.extraction.llm import get_llm
from underwriting.pipeline import SUPPORTED_SUFFIXES, extract_all, is_ignored
from underwriting.review.merge import ALL_FIELDS, apply_override, fmt, merge_sources
from underwriting.review.narrative import write_narrative
from underwriting.review.normalize import normalize
from underwriting.review.rules import evaluate_rules, overall_status
from underwriting.ui.style import alert, apply_style, banner, evidence_line, html, pill

# Field names as the reviewer sees them
TITLES = {
    "business_name": "Business name",
    "requested_effective_date": "Requested effective date",
    "garaging_state": "Garaging state",
    "operations_description": "Operations",
    "declared_vehicle_count": "Declared vehicle count",
    "operating_radius_miles": "Operating radius (miles)",
    "hazardous_materials": "Hazardous materials",
    "loss_runs_present": "Loss runs report attached",
    "schedule_vehicle_count": "Vehicle schedule count",
}

# Shown as placeholder and in error messages when a correction is invalid
FORMAT_HINTS = {
    "business_name": "Any non-empty name",
    "requested_effective_date": "A real date as YYYY-MM-DD, e.g. 2026-11-01",
    "garaging_state": "Two-letter code or state name, e.g. CA or California",
    "operations_description": "Any non-empty description",
    "declared_vehicle_count": "A whole number, 0 or more",
    "operating_radius_miles": "A number of miles, 0 or more, e.g. 150",
    "hazardous_materials": "Yes or No",
    "loss_runs_present": "Yes or No",
    "schedule_vehicle_count": "A whole number, 0 or more",
}

load_dotenv()                               # OPENAI_API_KEY etc. from .env
st.set_page_config(page_title="Submission Review", page_icon="🚚", layout="wide")
apply_style()                               # neumorphic CSS


# ---------------------------------------------------------------------------
# One event loop for all model calls (the OpenAI client is reused across calls,
# and it breaks if every button click creates a new loop with asyncio.run)
# ---------------------------------------------------------------------------

@st.cache_resource
def _event_loop():
    """Start one asyncio loop in a background thread, shared by every session."""
    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, daemon=True).start()
    return loop


def run_async(coro):
    """Run a coroutine on the shared loop and wait for its result."""
    return asyncio.run_coroutine_threadsafe(coro, _event_loop()).result()


def _esc(text):
    """HTML-escape any value before putting it into markup."""
    return html_lib.escape(str(text))


# ---------------------------------------------------------------------------
# Friendly error messages: what happened + how to fix it
# ---------------------------------------------------------------------------

def friendly(source, error):
    """Turn a technical error into (title, what happened, how to fix)."""
    e = error.lower()
    if any(k in e for k in ("authentication", "api_key", "api key", "model setup failed", "401")):
        return ("OpenAI API key problem",
                f"{source}: OpenAI rejected the request because the API key is missing or invalid.",
                "Put a valid key in the .env file next to app.py (OPENAI_API_KEY=sk-...), restart "
                "the app and click Review submission again.")
    if any(k in e for k in ("ratelimit", "rate limit", "insufficient_quota", "429")):
        return ("OpenAI rate limit or no credit",
                f"{source}: OpenAI refused the request (too many requests or no remaining credit).",
                "Wait a minute and try again, or check billing at platform.openai.com.")
    if any(k in e for k in ("timeout", "connection", "connecterror")):
        return ("Could not reach OpenAI", f"{source}: the request to OpenAI did not complete.",
                "Check your internet connection and try again.")
    if "no extractable text" in e:
        return ("PDF has no selectable text", f"{source} looks scanned or image-only.",
                "Upload a text-based PDF. Scanned documents (OCR) are not supported.")
    if "could not read pdf" in e:
        return ("PDF could not be opened", f"{source} is damaged, password-protected or not a real PDF.",
                "Re-export or re-download the PDF and upload it again.")
    if "could not parse csv" in e:
        return ("Vehicle schedule could not be read", f"{source} is empty or not a valid CSV.",
                "Upload a CSV whose first line is: vehicle_id,vin,year,make,model")
    if "could not read email" in e or "codec" in e:
        return ("Email file could not be read", f"{source} is not plain UTF-8 text.",
                "Save the email as a .txt file (UTF-8), or paste it into the Paste email tab.")
    if "email is empty" in e:
        return ("Email is empty", f"{source} has no text.", "Paste or upload the full broker email.")
    if "unsupported file type" in e:
        return ("File type not supported", f"{source} can't be read (only PDF, TXT and CSV).",
                "If it is the loss runs report, save it as a PDF and add it again.")
    # Anything else: show the raw message
    return (f"Problem with {source}", error, "Try again. If it keeps happening, check this file.")


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------

def input_section(nonce):
    """The four input tabs. Returns (uploaded files, folder path text, pasted email)."""
    t_files, t_folder, t_path, t_paste = st.tabs(
        ["📎 Upload files", "📁 Upload folder", "🗂️ Folder path", "✉️ Paste email"])
    # `nonce` in every key lets "Start new review" reset all widgets at once
    with t_files:
        files = st.file_uploader("Drag and drop the submission files (PDF, TXT, CSV)",
                                 accept_multiple_files=True, key=f"files_{nonce}")
    with t_folder:
        folder = st.file_uploader("Choose the whole submission folder",
                                  accept_multiple_files="directory", key=f"folder_{nonce}")
    with t_path:
        path_text = ""
        # Optional quick-pick of sample packets when SAMPLES_DIR is set in .env
        samples_dir = os.getenv("SAMPLES_DIR")
        if samples_dir and Path(samples_dir).is_dir():
            options = ["(type a path below)"] + sorted(p.name for p in Path(samples_dir).iterdir()
                                                       if p.is_dir())
            choice = st.selectbox("Sample packets", options, key=f"sample_{nonce}")
            if choice != options[0]:
                path_text = str(Path(samples_dir) / choice)
        typed = st.text_input("Folder path on this computer", key=f"path_{nonce}",
                              placeholder=r"C:\Users\you\Downloads\SUB-A")
        # A typed path wins over the quick-pick; quotes from "Copy as path" are removed
        path_text = typed.strip().strip('"') or path_text
    with t_paste:
        pasted = st.text_area("Paste the broker email (headers and body)", height=220,
                              key=f"paste_{nonce}")
    return (files or []) + (folder or []), path_text, pasted


def preflight(uploads, path_text, pasted):
    """Collect what will be processed and explain any problem before calling the model.

    Returns (entries, problems, warnings); entries are (name, origin, payload).
    Problems block the review; warnings are shown but the review can continue.
    """
    entries, problems, warnings = [], [], []
    # Uploaded files and folders (system files like desktop.ini are ignored)
    for f in uploads:
        if not is_ignored(f.name):
            entries.append((Path(f.name).name, "upload", f))

    # Folder (or single file) path typed by the user
    if path_text:
        p = Path(path_text)
        if not p.exists():
            problems.append(("Folder not found", f"'{path_text}' does not exist on this computer.",
                             "Check the spelling, or copy the path from File Explorer's address bar."))
        elif p.is_file():
            entries.append((p.name, "folder path", p))
        else:
            found = [x for x in sorted(p.iterdir()) if x.is_file() and not is_ignored(x)]
            if not found:
                problems.append(("Folder is empty", f"'{path_text}' has no files in it.",
                                 "Choose the folder that contains the submission files."))
            entries += [(x.name, "folder path", x) for x in found]

    # Pasted email becomes a virtual file
    if pasted.strip():
        entries.append(("pasted_email.txt", "pasted", pasted))

    if not entries and not problems:
        problems.append(("Nothing to review", "No files, folder or email were provided.",
                         "Upload files or a folder, enter a folder path, or paste the broker email, "
                         "then click Review submission."))
        return [], problems, warnings

    # Only PDF / TXT / CSV can be read. Other files are still passed on as unreadable
    # attachments, so the loss runs check says UNKNOWN instead of "not attached"
    supported = [e for e in entries if Path(e[0]).suffix.lower() in SUPPORTED_SUFFIXES]
    unreadable = [e[0] for e in entries if e not in supported]
    if unreadable:
        warnings.append(("Some files can't be read",
                         "These file types can't be read: " + ", ".join(unreadable) + ". They are "
                         "listed as unreadable attachments, so the loss runs check (R5) will be "
                         "UNKNOWN unless a readable loss runs PDF is also attached.",
                         "Only PDF, TXT (broker email) and CSV (vehicle schedule) are supported. "
                         "If one of them is the loss runs report, save it as a PDF and add it again."))
    if entries and not supported:
        problems.append(("No readable files", "None of the provided files is a PDF, TXT or CSV.",
                         "Add the application PDF, broker email (.txt) and vehicle schedule (.csv)."))
        return [], problems, warnings

    names = [e[0].lower() for e in supported]
    # Mixing input methods may mix two applicants
    if len({e[1] for e in entries}) > 1:
        warnings.append(("Files from more than one place",
                         "You provided files in more than one way (" +
                         ", ".join(sorted({e[1] for e in entries})) + "). They are reviewed together "
                         "as ONE submission.",
                         "If they belong to different applicants, click Start new review and load "
                         "one submission at a time."))
    if len(names) != len(set(names)):
        warnings.append(("Same file name given twice",
                         "Duplicates are renamed, e.g. 'application (2).pdf', and both are read.",
                         "Remove the duplicate if it's the same file."))
    if not any(n.endswith(".pdf") for n in names):
        warnings.append(("No PDF provided",
                         "Without the application PDF the fields come from the broker email only, "
                         "and without a loss runs PDF the loss runs check (R5) will FLAG.",
                         "Add the application PDF and the loss runs report if you have them."))
    csvs = [n for n in names if n.endswith(".csv")]
    if not csvs:
        warnings.append(("No vehicle schedule CSV",
                         "Without the vehicle schedule the vehicle count check (R2) can't complete.",
                         "Add the vehicle schedule CSV (e.g. vehicles.csv)."))
    elif len(csvs) > 1:
        warnings.append(("More than one CSV", "Several vehicle schedules were provided: " +
                         ", ".join(csvs) + ".",
                         "Keep only the current schedule; different counts will show as a conflict."))
    if not os.getenv("OPENAI_API_KEY"):
        warnings.append(("OpenAI API key not set",
                         "OPENAI_API_KEY is empty, so AI extraction and the summary will fail.",
                         "Add OPENAI_API_KEY=sk-... to the .env file next to app.py and restart "
                         "the app. The CSV vehicle count still works."))
    # Unreadable files go through too, so they appear in the attachment inventory
    return entries, problems, warnings


def save_entries(entries, folder):
    """Write uploads, folder files and pasted text into a fresh temp folder."""
    paths = []
    for name, origin, payload in entries:
        target = Path(folder) / name
        # De-duplicate names: "application (2).pdf"
        n = 2
        while target.exists():
            target = Path(folder) / f"{Path(name).stem} ({n}){Path(name).suffix}"
            n += 1
        if origin == "upload":
            target.write_bytes(payload.getvalue())          # Streamlit UploadedFile
        elif origin == "folder path":
            target.write_bytes(payload.read_bytes())        # copy from disk
        else:
            target.write_text(payload, encoding="utf-8")    # pasted email text
        paths.append(target)
    return paths


# ---------------------------------------------------------------------------
# Processing
# ---------------------------------------------------------------------------

def process(entries, warnings):
    """Run the whole pipeline with a visible step-by-step status; returns the review state."""
    with st.status("Reviewing submission…", expanded=True) as status:
        # Files only live in the temp folder while they are being read
        with tempfile.TemporaryDirectory() as tmp:
            st.write("📥 Reading files…")
            paths = save_entries(entries, tmp)
            st.write("🤖 Extracting fields with the AI model and counting vehicles…")
            sources = run_async(extract_all(paths))
        st.write("🔗 Combining values across files…")
        base_fields = merge_sources(sources)
        st.write("✅ Running checks R1–R5…")
        rules = evaluate_rules(base_fields)
        st.write("✍️ Writing the summary and draft email…")
        narrative = run_async(write_narrative(base_fields, rules, sources, get_llm()))

        # Red status if any file or the narrative failed
        if any(s.error for s in sources) or narrative.error:
            status.update(label="Finished with problems: see the red cards below",
                          state="error", expanded=False)
        else:
            status.update(label="Review complete", state="complete", expanded=False)

    # Everything about this applicant lives in this one dict (replaced on every new review)
    return {"sources": sources, "base_fields": base_fields, "overrides": {},
            "narrative": narrative, "narrative_stale": False, "narrative_version": 0,
            "warnings": warnings}


def effective_fields(review):
    """Original merged fields with the reviewer's corrections applied on top."""
    fields = review["base_fields"]
    for name, value in review["overrides"].items():
        fields = apply_override(fields, name, value)
    return fields


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------

def problems_panel(review):
    """Warnings from the pre-checks, per-file errors and suspicious file types, at the top."""
    for title, what, fix in review["warnings"]:
        alert(title, what, fix, level="warning")
    for s in review["sources"]:
        # Unsupported file types are already explained by the pre-check warning above
        if s.error and s.kind != "other":
            title, what, fix = friendly(s.source_file, s.error)
            alert(title, what, fix)
            with st.expander(f"Technical details for {s.source_file}"):
                st.code(s.error, language=None)
        # An unrecognised PDF makes R5 UNKNOWN: explain why (checked per file below)
        if s.kind == "pdf" and s.document_type == "other":
            alert("PDF type not recognised",
                  f"{s.source_file} is not an application or a loss runs report, so the loss runs "
                  f"check is UNKNOWN.",
                  "If it is the loss runs report, check it is the right file and readable.",
                  level="warning")

    # Evidence that doesn't hold up: quote not in the file, or value not in the quote
    shaky = sorted({TITLES[name] for name, f in review["base_fields"].items()
                    for e in f.evidence if not e.verified or e.value_in_quote is False})
    if shaky:
        alert("Some evidence needs a second look",
              "For these fields a quote was not found in its file, or the extracted value was not "
              "found in its quote: " + ", ".join(shaky) + ".",
              "Check the red-marked evidence lines below and correct the value if needed.",
              level="warning")


def rules_card(rules):
    """R1-R5 with status pill, reason and the inputs each rule used."""
    with st.container(key="card_rules"):
        st.markdown("#### Checks")
        for r in rules:
            # e.g. "operating_radius_miles = 80 (KNOWN, human override)"
            inputs = ", ".join(
                f"{n} = {'—' if v['value'] is None else fmt(v['value'])} ({v['state']}"
                f"{', human override' if v.get('human_override') else ''})"
                for n, v in r.inputs.items())
            html(f'<div style="display:flex;gap:.8rem;align-items:flex-start;margin:.55rem 0">'
                 f'<div style="min-width:88px">{pill(r.status)}</div>'
                 f'<div><b>{r.id} · {r.name}</b><br>{_esc(r.reason)}'
                 f'<div class="muted">Inputs: {_esc(inputs)}</div></div></div>')


def field_card(name, f, review):
    """One field: state, value, evidence, conflicts / checked files, override info, correction form."""
    with st.container(key=f"card_field_{name}"):
        # Header: title + state pill (+ override pill)
        tags = pill(f.state)
        if f.human_override:
            tags += " " + pill("HUMAN OVERRIDE", "override")
        value = "—" if f.value is None else fmt(f.value)
        html(f'<div style="display:flex;justify-content:space-between;align-items:center">'
             f'<b>{TITLES[name]}</b><span>{tags}</span></div>'
             f'<div class="value" style="margin:.3rem 0">{_esc(value)}</div>')

        # Evidence: quotes from documents, observations for the CSV / attachment inventory
        for e in f.evidence:
            observation = e.source_file == "uploaded files" or e.source_file.lower().endswith(".csv")
            evidence_line(e.source_file, e.locator, e.excerpt_or_observation, e.verified,
                          is_quote=not observation, value_in_quote=e.value_in_quote)
        if f.state == "CONFLICT" and f.alternatives:
            alts = " vs ".join(f"{fmt(a.value)} ({a.source_file})" for a in f.alternatives)
            html(f'<div class="muted">Sources disagree: {_esc(alts)}. Not resolved until a '
                 f'reviewer corrects it.</div>')
        if f.state == "MISSING" and f.sources_checked:
            html(f'<div class="muted">Checked: {_esc(", ".join(f.sources_checked))}</div>')
        if f.human_override:
            original = f.extracted_state + ("" if f.extracted_value is None
                                            else f" {fmt(f.extracted_value)}")
            html(f'<div class="muted">Originally extracted: {_esc(original)}</div>')
        if f.note and not f.human_override and f.state != "CONFLICT":
            html(f'<div class="muted">{_esc(f.note)}</div>')

        correction_form(name, f, review)


def correction_form(name, f, review):
    """Let the reviewer correct one field (validated) or undo a correction."""
    with st.expander("✏️ Correct this value"):
        # A form so typing doesn't re-run the page; only the button does
        with st.form(f"form_{name}_{st.session_state.nonce}", border=False):
            new_value = st.text_input("Correct value", placeholder=FORMAT_HINTS[name],
                                      key=f"input_{name}_{st.session_state.nonce}")
            submitted = st.form_submit_button("Apply correction and re-run checks",
                                              key=f"apply_{name}_{st.session_state.nonce}")
        if submitted:
            if not new_value.strip():
                alert("No value entered", "The correction box is empty.",
                      f"Type the correct value ({FORMAT_HINTS[name]}).")
            elif normalize(name, new_value.strip()) is None:
                # Reject invalid values up front with the expected format
                alert("That value isn't valid", f"'{new_value}' can't be used for {TITLES[name]}.",
                      f"Enter {FORMAT_HINTS[name][0].lower() + FORMAT_HINTS[name][1:]}.")
            else:
                review["overrides"][name] = new_value.strip()
                review["narrative_stale"] = True          # summary no longer matches the data
                st.rerun()                                # re-run the checks immediately
        if f.human_override and st.button("↩️ Undo correction", key=f"undo_{name}"):
            review["overrides"].pop(name, None)
            review["narrative_stale"] = True
            st.rerun()


def summary_card(review, fields, rules):
    """AI summary (or a clear error), plus the follow-up items worked out without AI."""
    narrative = review["narrative"]
    with st.container(key="card_summary"):
        st.markdown("#### Underwriter summary")
        html(pill("AI-generated", "neutral"))
        if review["narrative_stale"]:
            alert("Summary is out of date",
                  "You corrected values after this summary and email were written.",
                  "Click Regenerate to update them.", level="warning")
        if st.button("🔄 Regenerate summary & email", key="regen"):
            with st.spinner("Writing the summary and draft email…"):
                review["narrative"] = run_async(
                    write_narrative(fields, rules, review["sources"], get_llm()))
            review["narrative_stale"] = False
            review["narrative_version"] += 1             # new widget keys so the new draft shows
            st.rerun()

        if narrative.error:
            # Never show fake text: explain the failure and fall back to the rule-based list
            title, what, fix = friendly("Summary", narrative.error)
            alert("AI summary unavailable: " + title, what, fix)
            st.markdown("**Items that need follow-up** (worked out by the checks, without AI):")
        else:
            st.write(narrative.summary)
            st.markdown("**Items the draft email asks for:**")
        if narrative.items:
            st.markdown("\n".join(f"- {item}" for item in narrative.items))
        else:
            st.markdown("_Nothing to request._")


def email_card(review):
    """Editable draft email with a download button; there is deliberately no send button."""
    narrative = review["narrative"]
    version = review["narrative_version"]
    with st.container(key="card_email"):
        st.markdown("#### Draft email to broker")
        html(pill("DRAFT ONLY · NOT SENT", "flag") +
             ' <span class="muted">Edit freely, then copy or download it. This app never sends email.</span>')
        subject = st.text_input("Subject", value=narrative.email_subject or "",
                                key=f"subject_{version}_{st.session_state.nonce}")
        body = st.text_area("Body", value=narrative.email_body or "", height=320,
                            placeholder="The AI draft is unavailable. Write the request here using "
                                        "the follow-up items in the summary.",
                            key=f"body_{version}_{st.session_state.nonce}")
        st.download_button("⬇️ Download draft (.txt)", f"Subject: {subject}\n\n{body}",
                           file_name="draft_broker_email.txt", mime="text/plain")


def files_card(review, fields, rules):
    """Attachment inventory, loss runs values and the full JSON download."""
    with st.container(key="card_files"):
        st.markdown("#### Files & attachments")
        for s in review["sources"]:
            status = pill("ERROR", "flag") if s.error else pill("READ", "pass")
            doc_type = pill(s.document_type or "unclassified", "neutral")
            pages = f" · {s.page_count} page(s)" if s.page_count else ""
            html(f'<div style="margin:.35rem 0">{status} {doc_type} '
                 f'<b>{_esc(s.source_file)}</b><span class="muted">{pages}</span></div>')
            # Loss runs values (claims, losses, period) under the report
            if s.loss_runs_fields:
                for n, f in s.loss_runs_fields.items():
                    value = "—" if f.value is None else fmt(f.value)
                    html(f'<div class="evidence" style="margin-left:1.6rem">'
                         f'{_esc(n.replace("_", " ").capitalize())}: <b>{_esc(value)}</b>'
                         f'<span class="muted"> ({_esc(f.locator or "no excerpt")})</span></div>')
        export = {
            "sources": [s.model_dump() for s in review["sources"]],
            "fields": {n: f.model_dump() for n, f in fields.items()},
            "rules": [r.model_dump() for r in rules],
            "overall_status": overall_status(rules),
            "narrative": review["narrative"].model_dump(),
        }
        st.download_button("⬇️ Download full review (JSON)",
                           json.dumps(export, indent=2, ensure_ascii=False),
                           file_name="submission_review.json", mime="application/json")


def show_review(review):
    """Render the whole review page; the rules re-run on every page run (they're instant)."""
    fields = effective_fields(review)
    rules = evaluate_rules(fields)

    banner(overall_status(rules), fields["business_name"].value or "Applicant name not found")
    problems_panel(review)
    rules_card(rules)

    # Field cards in two columns
    st.markdown("### Extracted fields")
    left, right = st.columns(2, gap="large")
    for i, name in enumerate(ALL_FIELDS):
        with (left if i % 2 == 0 else right):
            field_card(name, fields[name], review)

    summary_card(review, fields, rules)
    email_card(review)
    files_card(review, fields, rules)


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------

def main():
    """Header, input section, then either the review or the empty start screen."""
    st.session_state.setdefault("nonce", 0)          # bumps to reset input widgets
    review = st.session_state.get("review")

    head, action = st.columns([5, 1])
    with head:
        st.markdown("## 🚚 Commercial Auto Submission Review")
        st.markdown('<span class="muted">Extract with evidence · five checks · summary & draft '
                    'email for the underwriter</span>', unsafe_allow_html=True)
    with action:
        if review and st.button("➕ Start new review", width="stretch"):
            st.session_state.pop("review", None)
            st.session_state.nonce += 1          # resets every input widget
            st.rerun()

    # Inputs: a card on the empty screen, a collapsed expander once a review is shown
    container = st.container(key="card_inputs") if not review else st.expander(
        "📂 Load a different submission", expanded=False)
    with container:
        if not review:
            st.markdown("#### 1 · Add one submission")
            st.markdown('<span class="muted">Use any tab, whichever is easiest. Everything you add '
                        'is reviewed together as one submission.</span>', unsafe_allow_html=True)
        uploads, path_text, pasted = input_section(st.session_state.nonce)
        entries, problems, warnings = preflight(uploads, path_text, pasted)

        if entries:
            st.markdown("**Files to process:** " + ", ".join(
                f"`{name}` ({origin})" for name, origin, _ in entries))
            # Show problems before the user spends a model call
            for title, what, fix in warnings:
                alert(title, what, fix, level="warning")
        clicked = st.button("🔍 Review submission", type="primary")

    if clicked:
        if problems:
            for title, what, fix in problems:
                alert(title, what, fix)
            return
        st.session_state.pop("review", None)       # never keep the previous applicant's data
        try:
            st.session_state.review = process(entries, warnings)
        except Exception as e:
            alert("Review failed", f"{type(e).__name__}: {e}",
                  "Check the files and try again. Technical details are below.")
            with st.expander("Technical details"):
                st.code(traceback.format_exc(), language=None)
            return
        st.rerun()

    if review:
        show_review(review)
    elif not clicked:
        html('<div class="muted" style="margin-top:1rem">No submission loaded yet. Results will '
             'appear here.</div>')


main()
