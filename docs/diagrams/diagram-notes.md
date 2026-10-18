# Diagram notes

Each diagram has a `.json` spec (source), an editable `.excalidraw` file
(open at excalidraw.com or with the VS Code Excalidraw extension) and rendered
`.png` / `.svg` files. To change a diagram, edit its `.json` spec and rebuild it.

## architecture: module overview

| Box | Code |
|---|---|
| Underwriter | user of the browser app or terminal |
| app.py / Streamlit UI | `app.py`: `process()`, `show_review()`, `correction_form()`, `summary_card()` |
| main.py / CLI | `main.py`: `main()`, `--set` overrides, `--out` JSON |
| pipeline.py | `underwriting/pipeline.py`: `collect_paths()`, `extract_all()` sends each file to a reader by suffix through `asyncio.gather` |
| review/ merge + rules | `underwriting/review/merge.py` (`merge_sources`, `apply_override`), `normalize.py`, `rules.py` (`evaluate_rules`, `overall_status`, `derive_loss_runs_present`) |
| narrative.py | `underwriting/review/narrative.py`: `request_items()` (deterministic), `write_narrative()` (LLM) |
| pdf_reader.py | `underwriting/extraction/pdf_reader.py`: pdfplumber text and tables per page, then one model call to classify and extract |
| email_reader.py | `underwriting/extraction/email_reader.py`: `.txt` file or pasted text |
| csv_reader.py | `underwriting/extraction/csv_reader.py`: counts nonblank data rows, no model call |
| llm.py | `underwriting/extraction/llm.py`: `get_llm()` (ChatOpenAI, temperature 0), `ask_model()` (strict JSON schema, `<document>` tags) |
| Submission files | the uploaded packet; `app.py` writes it to a `tempfile.TemporaryDirectory` that is deleted after reading |
| OpenAI API | `OPENAI_MODEL`, default `gpt-4o-mini` |

Simplifications:
- `schema.py` (pydantic models, `contains_excerpt`) is shared by all readers and is not drawn as its own box.
- `narrative.py` calls `llm.with_structured_output` directly; it only uses `get_llm()` from `llm.py`, not `ask_model()`. The "facts only" arrow shows that it goes through the same model client.
- Unsupported file types go to `pipeline._unsupported()` and are not drawn.
- `ui/style.py` (styling only) is not drawn.

## review-sequence: one review in the Streamlit app

Based on `app.py` `process()` (steps 1–19) and `correction_form()` / `show_review()` (steps 20–22).
- Step 3: `run_async` runs the coroutine on a background event loop thread (`_event_loop()`).
- Steps 5–7 are skipped for CSV files; step 7 is `find_page` / `locate_in_pages` (PDF) and `contains_excerpt` (email).
- Step 21: overrides are stored in `review["overrides"]`, and `show_review()` re-applies them and re-runs the rules on every rerun. The summary is marked stale and is regenerated only when the user clicks **Regenerate**. The model is not called again.

## field-resolution: `merge_field()`

Based on `underwriting/review/merge.py` `candidate()` and `merge_field()`, for the seven fields that come from the application PDF and broker email.
- "other" status: `not_provided` → MISSING candidate, `conflicting` → CONFLICT, `ambiguous` / `unparseable` → INVALID. A `stated` value that fails `normalize()` also becomes INVALID.
- The order of the checks matches the code: CONFLICT > INVALID > KNOWN > (failed → INVALID) > MISSING.
- `schedule_vehicle_count` (`merge_schedule`) and `loss_runs_present` (`merge_loss_runs`) follow their own simpler logic and are not shown.

Nothing on these diagrams is inferred: every box and arrow comes from the files listed above.
