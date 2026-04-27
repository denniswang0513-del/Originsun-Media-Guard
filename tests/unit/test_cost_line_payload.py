"""Pin CostLineUpdatePayload `exclude_unset` semantics.

The PUT /project-cost-lines endpoint relies on `model_dump(exclude_unset=True)`
to distinguish "client sent null to clear this field" from "client didn't
mention this field at all". If `exclude_none=True` is used by mistake,
explicit-null clears get silently dropped and the column never updates.
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
    assert p.model_dump(exclude_unset=True) == {"estimated_quantity": None}
    # Pin the contrast: exclude_none drops explicit null. If Pydantic ever
    # changes this, re-evaluate whether the endpoint can switch back.
    assert p.model_dump(exclude_none=True) == {}


def test_unset_fields_are_omitted():
    p = CostLineUpdatePayload.model_validate_json(json.dumps({}))
    assert p.model_dump(exclude_unset=True) == {}


def test_partial_update_does_not_touch_unset_fields():
    p = CostLineUpdatePayload.model_validate_json(
        json.dumps({"estimated_quantity": 5}))
    dumped = p.model_dump(exclude_unset=True)
    assert dumped == {"estimated_quantity": 5}
    assert "estimated_unit_price" not in dumped
