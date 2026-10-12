import copy

import pytest
from temporalio.exceptions import ApplicationError

from services.pi_transport import ScopedProtocolBlobs
from services.temporal.pi_protocol import INLINE_LIMIT, canonical_json


def test_large_receipt_uses_verified_reference_and_preserves_native_integers(tmp_path):
    bridge = ScopedProtocolBlobs(tmp_path, {"workflow_id": "root", "generation": 1}, 2)
    receipt = {"content": "é" * INLINE_LIMIT, "provider_state": {"id": 9007199254740993}}
    encoded = bridge.encode(receipt)
    assert set(encoded) == {"pi_file"}
    assert len(str(encoded)) < 1024
    assert bridge.decode(encoded) == receipt
    assert bridge.encode(receipt) == encoded
    assert bridge.encode({"small": True}) == {"small": True}


def test_inline_boundary_includes_the_lf_delimiter(tmp_path):
    bridge = ScopedProtocolBlobs(tmp_path, {"workflow_id": "root"}, 1)
    inline = "x" * (INLINE_LIMIT - 3)
    assert len((canonical_json(inline) + "\n").encode()) == INLINE_LIMIT
    assert bridge.encode(inline) == inline
    at_body_limit = "x" * (INLINE_LIMIT - 2)
    reference = bridge.encode(at_body_limit)
    assert reference["pi_file"]["bytes"] == INLINE_LIMIT
    assert bridge.decode(reference) == at_body_limit


@pytest.mark.parametrize("field,value", [("name", "../escape.json"), ("scope_digest", "wrong"),
    ("epoch", 1), ("bytes", 1), ("sha256", "invalid")])
def test_large_receipt_cannot_change_scope_epoch_path_size_or_digest(tmp_path, field, value):
    bridge = ScopedProtocolBlobs(tmp_path, {"workflow_id": "root"}, 2)
    reference = bridge.encode({"content": "x" * INLINE_LIMIT})
    changed = copy.deepcopy(reference)
    changed["pi_file"][field] = value
    with pytest.raises(ApplicationError, match="scope"):
        bridge.decode(changed)


def test_corrupted_buffer_cannot_become_a_checkpoint(tmp_path):
    bridge = ScopedProtocolBlobs(tmp_path, {"workflow_id": "root"}, 2)
    reference = bridge.encode({"entries": ["x" * INLINE_LIMIT]})
    path = tmp_path / reference["pi_file"]["name"]
    path.write_bytes(b"corrupted")
    with pytest.raises(ApplicationError, match="integrity"):
        bridge.decode(reference)
