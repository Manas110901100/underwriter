"""Broker email reader: a .txt file or text pasted into the app."""
import asyncio                              # file reading runs in a thread
from pathlib import Path                    # file names and paths

from underwriting.extraction.llm import ask_model
from underwriting.extraction.schema import (BASE_PROMPT, FIELD_NAMES, SourceResult,
                                            SubmissionFields, contains_excerpt, located)

# Emails have no pages, so tell the model to leave page empty
EMAIL_PROMPT = BASE_PROMPT + """
- The document is a broker email (headers and body). There are no pages; always return page as null."""


async def extract_email(text, source_file, llm):
    """Extract the seven fields from email text (works for pasted text and file contents)."""
    result = SourceResult(source_file=source_file, kind="email", readable=bool(text.strip()),
                          document_type="broker_email")
    if not result.readable:
        result.error = "Email is empty"
        return result

    # Ask the model; on failure report the error and return no values
    try:
        fields = await ask_model(llm, SubmissionFields, EMAIL_PROMPT, source_file, text)
    except Exception as e:
        result.error = f"Model call failed: {type(e).__name__}: {e}"
        return result

    # Check each quote really appears in the email body
    result.fields = {}
    for name in FIELD_NAMES:
        field = getattr(fields, name)
        verified = bool(field.excerpt) and contains_excerpt(text, field.excerpt)
        result.fields[name] = located(field, "email body" if field.excerpt else None, verified)
    return result


async def extract_email_file(path, llm):
    """Read a .txt email from disk, then extract it."""
    path = Path(path)
    try:
        text = await asyncio.to_thread(path.read_text, encoding="utf-8")
    except Exception as e:
        # e.g. not UTF-8 text
        return SourceResult(source_file=path.name, kind="email", readable=False,
                            document_type="broker_email",
                            error=f"Could not read email: {type(e).__name__}: {e}")
    return await extract_email(text, path.name, llm)
