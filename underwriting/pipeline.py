"""Send each file of one submission to the right reader, all at the same time."""
import asyncio                              # runs the readers concurrently
from pathlib import Path                    # file names and paths

from underwriting.extraction.csv_reader import extract_csv
from underwriting.extraction.email_reader import extract_email_file
from underwriting.extraction.llm import get_llm
from underwriting.extraction.pdf_reader import extract_pdf
from underwriting.extraction.schema import SourceResult

# File types the readers understand
SUPPORTED_SUFFIXES = {".pdf", ".txt", ".csv"}

# Operating-system clutter that is never part of a submission
IGNORED_NAMES = {"desktop.ini", "thumbs.db", ".ds_store"}


def is_ignored(path):
    """True for hidden and system files (e.g. .DS_Store, desktop.ini)."""
    name = Path(path).name.lower()
    return name in IGNORED_NAMES or name.startswith(".")


def collect_paths(inputs):
    """Expand folders into their files; keep individual file paths as they are."""
    paths = []
    for item in map(Path, inputs):
        paths += sorted(item.iterdir()) if item.is_dir() else [item]
    return [p for p in paths if not is_ignored(p)]


async def _unsupported(path):
    """An attachment we can't read is still listed, so rule R5 can say UNKNOWN instead of FLAG."""
    return SourceResult(source_file=Path(path).name, kind="other", readable=False,
                        error=f"Unsupported file type '{Path(path).suffix or '(none)'}': "
                              f"only PDF, TXT and CSV can be read")


async def extract_all(paths):
    """Run the matching reader for every file concurrently; returns one SourceResult each."""
    llm = get_llm()                         # one model object shared by all files
    tasks = []
    for path in paths:
        if is_ignored(path):
            continue
        suffix = Path(path).suffix.lower()
        if suffix == ".pdf":
            tasks.append(extract_pdf(path, llm))
        elif suffix == ".txt":
            tasks.append(extract_email_file(path, llm))      # .txt = broker email
        elif suffix == ".csv":
            tasks.append(extract_csv(path))                  # no model needed
        else:
            tasks.append(_unsupported(path))                 # e.g. .docx, .xlsx, .png
    return await asyncio.gather(*tasks)
