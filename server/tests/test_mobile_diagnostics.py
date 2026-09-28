"""Actionable provider failures without leaking request/response contents."""

from unittest.mock import Mock

import pytest

from nodes.mobile.runtime.errors import describe_error
from nodes.mobile import _diagnostics


@pytest.mark.parametrize("status,code", [(400, "model_request_rejected"), (401, "model_authentication_failed"),
                                      (403, "model_permission_denied"), (404, "model_not_found"),
                                      (429, "model_rate_limited"), (503, "model_service_unavailable")])
def test_client_error_retains_status_without_content(status, code):
    class ClientError(Exception):
        pass
    error = ClientError("secret-key private prompt base64-screenshot")
    error.code = status
    error.status = "NOT_FOUND" if status == 404 else "unsafe response content"
    result = describe_error(error, "task_execution")
    assert result["code"] == code
    assert result["http_status"] == status
    assert result["error_type"] == "ClientError"
    assert "secret" not in str(result)
    assert "prompt" not in str(result)
    assert "screenshot" not in str(result)
    assert "unsafe" not in str(result)


def test_invalid_google_key_is_not_reported_as_model_capability_error():
    error = RuntimeError("API key not valid. secret-key")
    error.code = 400
    assert describe_error(error, "engine_initialization")["code"] == "model_authentication_failed"


def test_mobile_events_reach_terminal_pipeline_with_only_safe_fields(monkeypatch, tmp_path):
    import core.logging
    logger = Mock()
    monkeypatch.setattr(core.logging, "get_logger", lambda name: logger)
    monkeypatch.setattr(_diagnostics, "mobile_root", lambda: tmp_path)
    try:
        _diagnostics.event("engine_failed", failed=True, http_status=404, model="selected-model",
                           execution_id="execution-2", prompt="private", api_key="secret")
        logger.error.assert_called_once()
        args, fields = logger.error.call_args
        assert args == ("Android: engine_failed",)
        assert fields["http_status"] == 404
        assert fields["execution_id"] == "execution-2"
        assert "prompt" not in fields and "api_key" not in fields
        assert '"http_status": 404' in (tmp_path / "mobile.log").read_text()
    finally:
        for handler in list(_diagnostics._logger.handlers):
            handler.close()
            _diagnostics._logger.removeHandler(handler)


def test_terminal_logger_failure_does_not_lose_file_diagnostics(monkeypatch, tmp_path):
    import core.logging
    logger = Mock()
    logger.info.side_effect = RuntimeError("logger unavailable")
    monkeypatch.setattr(core.logging, "get_logger", lambda name: logger)
    monkeypatch.setattr(_diagnostics, "mobile_root", lambda: tmp_path)
    try:
        _diagnostics.event("phone_ready")
        assert "phone_ready" in (tmp_path / "mobile.log").read_text()
    finally:
        for handler in list(_diagnostics._logger.handlers):
            handler.close()
            _diagnostics._logger.removeHandler(handler)
