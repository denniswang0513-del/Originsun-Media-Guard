"""Regression test for the cost-line PUT payload "explicit null" handling.

Bug (pre-v1.10.95): the endpoint used `req.model_dump(exclude_none=True)`,
which silently dropped any field set to null. The frontend's blur handler
sends `{"estimated_quantity": null}` when the user clears a numeric input;
the backend then ignored the clear and the field never updated, so after
switching projects the user saw the old value reappear.

Fix: switch to `exclude_unset=True`, which keeps explicit nulls and only
strips fields the client didn't send at all.
"""
import json

from core.schemas import CostLineUpdatePayload


def test_explicit_zero_round_trips():
    p = CostLineUpdatePayload.model_validate_json(
        json.dumps({"estimated_quantity": 0}))
    assert p.model_dump(exclude_unset=True) == {"estimated_quantity": 0}


def test_explicit_null_is_preserved_for_clearing():
    p = CostLineUpdatePayload.model_validate_json(
        json.dumps({"estimated_quantity": None}))
    # The fix: null must reach the update_data so setattr can clear the column.
    assert p.model_dump(exclude_unset=True) == {"estimated_quantity": None}
    # Confirm the buggy behaviour exclude_unset replaces:
    assert p.model_dump(exclude_none=True) == {}, (
        "exclude_none drops explicit null — that's the bug. "
        "If this assertion fails, Pydantic's behaviour changed and the fix "
        "may need re-validating.")


def test_unset_fields_are_omitted():
    p = CostLineUpdatePayload.model_validate_json(json.dumps({}))
    assert p.model_dump(exclude_unset=True) == {}


def test_partial_update_does_not_touch_unset_fields():
    p = CostLineUpdatePayload.model_validate_json(
        json.dumps({"estimated_quantity": 5}))
    dumped = p.model_dump(exclude_unset=True)
    assert dumped == {"estimated_quantity": 5}
    assert "estimated_unit_price" not in dumped
