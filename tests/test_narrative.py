"""Request list and narrative failure handling; no LLM calls (a fake model is used)."""
import asyncio

from underwriting.review.merge import apply_override, merge_sources
from underwriting.review.narrative import Narrative, request_items, write_narrative
from underwriting.review.rules import evaluate_rules
from test_merge import LOSS_RUNS, application, csv, email, said, stated


def sub_c():
    """Merged fields shaped like SUB-C (conflicts, 4 vs 3 vehicles, no loss runs)."""
    return merge_sources([
        application(declared_vehicle_count=stated(4), operating_radius_miles=stated(80.0),
                    hazardous_materials=stated(False)),
        email(declared_vehicle_count=stated(4), operating_radius_miles=stated(180.0),
              hazardous_materials=stated(True)),
        csv(3),
    ])


def items_for(fields):
    """The broker request list for these fields after running the rules."""
    return request_items(fields, evaluate_rules(fields))


def test_sub_c_requests_conflicts_count_and_loss_runs():
    """SUB-C asks to confirm both conflicts, reconcile the count and send the loss runs."""
    items = items_for(sub_c())
    text = "\n".join(items)
    assert "application.pdf says 80, broker_email.txt says 180" in text
    assert "application.pdf says No, broker_email.txt says Yes" in text
    assert "declares 4 vehicles but the vehicle schedule lists 3" in text
    assert "loss runs" in text
    assert len(items) == 4


def test_sub_b_requests_missing_values():
    """SUB-B asks for the three missing values and the loss runs."""
    nope = said("not_provided", "Not provided")
    fields = merge_sources([
        application(declared_vehicle_count=stated(2), requested_effective_date=nope,
                    operating_radius_miles=nope, hazardous_materials=nope),
        email(declared_vehicle_count=stated(2), requested_effective_date=nope,
              operating_radius_miles=nope, hazardous_materials=nope),
        csv(2),
    ])
    items = items_for(fields)
    assert "Please provide the requested effective date." in items
    assert "Please provide the operating radius (miles)." in items
    assert any("hazardous materials" in i for i in items)
    assert any("loss runs" in i for i in items)
    assert len(items) == 4


def test_sub_a_requests_nothing():
    """A clean packet produces no requests."""
    assert items_for(merge_sources([application(), email(), csv(3), LOSS_RUNS])) == []


def test_overridden_field_is_not_requested():
    """A field the reviewer corrected is not asked about again."""
    fields = apply_override(sub_c(), "operating_radius_miles", "80")
    assert not any("operating radius" in i for i in items_for(fields))


def test_flagged_values_are_requested():
    """Clear but flagged values (radius over 150, hazmat Yes) are asked about."""
    fields = apply_override(sub_c(), "hazardous_materials", "Yes")
    fields = apply_override(fields, "operating_radius_miles", "180")
    text = "\n".join(items_for(fields))
    assert "radius is stated as 180 miles" in text
    assert "hazardous materials are transported" in text


def test_failed_extraction_is_not_asked_of_the_broker():
    """Our own model failures are not turned into requests to the broker."""
    from underwriting.extraction.schema import SourceResult
    failed = [SourceResult(source_file="application.pdf", kind="pdf", readable=True,
                           document_type="application", error="Model call failed"),
              SourceResult(source_file="broker_email.txt", kind="email", readable=True,
                           document_type="broker_email", error="Model call failed"),
              csv(3)]
    items = items_for(merge_sources(failed))
    assert not any("Please clarify" in i for i in items)


# --- write_narrative with fake models ----------------------------------------------

class FakeLLM:
    """Stand-in for ChatOpenAI: returns a fixed reply or raises a fixed error, and records the messages."""
    def __init__(self, reply=None, error=None):
        """Store the reply or the error to give back."""
        self.reply, self.error, self.messages = reply, error, None

    def with_structured_output(self, *_args, **_kwargs):
        """Same API as ChatOpenAI; the fake simply returns itself."""
        return self

    async def ainvoke(self, messages):
        """Remember the messages, then raise the error or return the reply."""
        self.messages = messages
        if self.error:
            raise self.error
        return self.reply


def run(fields, llm):
    """Run write_narrative on these fields with the given fake model."""
    return asyncio.run(write_narrative(fields, evaluate_rules(fields), [], llm))


def test_narrative_success_returns_model_text_and_items():
    """A working model's text is returned with the items, and the model only sees the facts JSON."""
    llm = FakeLLM(Narrative(summary="S", email_subject="Subj", email_body="Body"))
    result = run(sub_c(), llm)
    assert (result.summary, result.email_subject, result.email_body, result.error) == (
        "S", "Subj", "Body", None)
    assert len(result.items) == 4
    # The model only sees resolved facts, including the request list
    assert "items_to_request" in llm.messages[1].content
    assert "<facts>" in llm.messages[1].content


def test_narrative_failure_keeps_items_and_invents_nothing():
    """A failing model gives an error, no invented text, and keeps the request items."""
    result = run(sub_c(), FakeLLM(error=RuntimeError("no key")))
    assert result.error == "Model call failed: RuntimeError: no key"
    assert result.summary is None and result.email_body is None
    assert len(result.items) == 4
