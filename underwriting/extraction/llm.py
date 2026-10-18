"""OpenAI access through LangChain: create the model and send one document to it."""
import os                                                        # reads OPENAI_MODEL from the environment

from langchain_core.messages import HumanMessage, SystemMessage  # chat message types
from langchain_openai import ChatOpenAI                          # LangChain's OpenAI chat model


class _UnavailableModel:
    """Stands in when the model can't be created, so each file reports the error
    instead of the whole run crashing."""

    def __init__(self, error):
        """Remember the setup error so every later call can report it."""
        self.error = error                 # the setup error message to report later

    def with_structured_output(self, *_args, **_kwargs):
        """Mimic ChatOpenAI's API so callers don't need a special case."""
        return self

    async def ainvoke(self, _messages):
        """Every call fails with the original setup error."""
        raise RuntimeError(self.error)


def get_llm(model=None):
    """ChatOpenAI model, or a stand-in that raises the setup error on every call."""
    try:
        # temperature 0 = most repeatable output; retries cover brief network hiccups
        return ChatOpenAI(model=model or os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
                          temperature=0, timeout=90, max_retries=2)
    except Exception as e:
        # e.g. no API key: report it per file rather than crash
        return _UnavailableModel(f"Model setup failed: {type(e).__name__}: {e}")


async def ask_model(llm, schema, system_prompt, source_file, document_text):
    """Send one document to the model and return an instance of `schema`."""
    # strict JSON schema mode: OpenAI must return exactly the pydantic shape
    extractor = llm.with_structured_output(schema, method="json_schema", strict=True)
    # Stop the document from closing its own data tag (prompt-injection guard)
    document_text = document_text.replace("</document>", "&lt;/document&gt;")
    # System prompt = rules; human message = the file name and the document as data
    return await extractor.ainvoke([
        SystemMessage(system_prompt),
        HumanMessage(f"File name: {source_file}\n\n<document>\n{document_text}\n</document>"),
    ])
