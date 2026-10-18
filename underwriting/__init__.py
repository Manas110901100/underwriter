"""Commercial auto submission review.

Package layout (data flows top to bottom):
    extraction/  read PDFs, the broker email and the vehicle CSV into per-file results
    pipeline.py  send each uploaded file to the right reader, concurrently
    review/      normalize + merge the per-file values, run rules R1-R5, write the narrative
    ui/          styling for the Streamlit app (app.py at the project root)
"""
