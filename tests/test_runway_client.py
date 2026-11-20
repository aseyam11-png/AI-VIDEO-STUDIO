import hashlib
from pathlib import Path
from unittest.mock import Mock
from urllib.request import Request

import pytest

from app.core.config import settings
from app.integrations.runway import (
    RunwayDevClient,
    RunwayError,
    RunwaySubmissionUnknownError,
    RunwayTransportError,
    RunwayUploadError,
    _CloudFrontRedirectHandler,
)


_TASK_ID = "12345678-1234-5678-1234-567812345678"


class FakeRunwayClient(RunwayDevClient):
    def __init__(self):
        super().__init__()
        self.submit_count = 0
        self.poll_count = 0

    def _upload_ephemeral(self, source, record_path, source_sha256):
        return "runway://ephemeral/reference"

    def _api_request(self, method, path, payload=None):
        if method == "POST" and path == "/v1/image_to_video":
            self.submit_count += 1
            return {"id": _TASK_ID}
        if method == "GET" and path == f"/v1/tasks/{_TASK_ID}":
            self.poll_count += 1
            return {
                "status": "SUCCEEDED",
                "output": ["https://media.cloudfront.net/temporary/output.mp4"],
            }
        raise AssertionError(f"Unexpected Runway request: {method} {path}")

    def _download_output(self, url, destination):
        assert url.startswith("https://media.cloudfront.net/")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"synthetic-video-output")


class InterruptedPollingClient(FakeRunwayClient):
    def _api_request(self, method, path, payload=None):
        if method == "GET" and path == f"/v1/tasks/{_TASK_ID}" and self.poll_count == 0:
            self.poll_count += 1
            raise RunwayTransportError("polling connection was interrupted")
        return super()._api_request(method, path, payload)


class CancelRequestedClient(FakeRunwayClient):
    def _api_request(self, method, path, payload=None):
        if method == "POST" and path == "/v1/image_to_video":
            self.submit_count += 1
            return {"id": _TASK_ID}
        if method == "GET" and path == f"/v1/tasks/{_TASK_ID}":
            self.poll_count += 1
            return {"status": "RUNNING"}
        raise AssertionError(f"Unexpected Runway request: {method} {path}")

    def _cancel_task(self, task_id):
        raise RunwayError("provider cancellation request failed")


class AmbiguousSubmissionClient(FakeRunwayClient):
    def _api_request(self, method, path, payload=None):
        if method == "POST" and path == "/v1/image_to_video":
            self.submit_count += 1
            raise RunwayTransportError("connection closed after request body was sent")
        raise AssertionError(f"Unexpected follow-up request: {method} {path}")


def _source(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * 1024)
    return path


def _configure_runway(monkeypatch):
    monkeypatch.setattr(settings, "runway_enabled", True)
    monkeypatch.setattr(settings, "runway_api_key", "test-api-key")
    monkeypatch.setattr(settings, "runway_job_timeout_seconds", 60)
    monkeypatch.setattr(settings, "runway_poll_interval_seconds", 5)


def _generate(client, source, output_directory):
    return client.generate_image_to_video(
        source,
        "A controlled cinematic product shot with natural movement.",
        "flicker, product morphing",
        4,
        output_directory,
        progress=lambda *_: None,
        cancel_check=lambda: False,
    )


def test_completed_task_is_reused_after_worker_retry_without_second_post(
    tmp_path, monkeypatch
):
    _configure_runway(monkeypatch)
    source = _source(tmp_path / "reference.png")
    output_directory = tmp_path / "job" / "shot-01"
    client = FakeRunwayClient()

    first = _generate(client, source, output_directory)
    second = _generate(client, source, output_directory)

    assert first["task_id"] == _TASK_ID
    assert second["task_id"] == _TASK_ID
    assert second["resumed"] is True
    assert client.submit_count == 1
    assert client.poll_count == 1


def test_ambiguous_submission_is_never_posted_twice_on_retry(tmp_path, monkeypatch):
    _configure_runway(monkeypatch)
    source = _source(tmp_path / "reference.png")
    output_directory = tmp_path / "job" / "shot-01"
    client = AmbiguousSubmissionClient()

    with pytest.raises(RunwaySubmissionUnknownError):
        _generate(client, source, output_directory)

    with pytest.raises(RunwaySubmissionUnknownError, match="will not submit a second"):
        _generate(client, source, output_directory)

    assert client.submit_count == 1


def test_completed_task_cannot_be_reused_with_a_changed_prompt(tmp_path, monkeypatch):
    _configure_runway(monkeypatch)
    source = _source(tmp_path / "reference.png")
    output_directory = tmp_path / "job" / "shot-01"
    client = FakeRunwayClient()
    _generate(client, source, output_directory)

    with pytest.raises(RunwayError, match="different request"):
        client.generate_image_to_video(
            source,
            "A different prompt must not reuse a prior paid task.",
            "",
            4,
            output_directory,
            progress=lambda *_: None,
            cancel_check=lambda: False,
        )

    assert client.submit_count == 1


def test_download_rejects_non_cloudfront_output_hosts(tmp_path, monkeypatch):
    _configure_runway(monkeypatch)
    client = FakeRunwayClient()

    with pytest.raises(RunwayError, match="approved CloudFront"):
        RunwayDevClient._download_output(
            client,
            "https://attacker.example/redirect-to-internal.mp4",
            tmp_path / "unsafe.mp4",
        )


def test_download_redirect_handler_rejects_redirects_off_cloudfront():
    handler = _CloudFrontRedirectHandler()
    request = Request("https://media.cloudfront.net/output.mp4")

    with pytest.raises(RunwayError, match="left the approved"):
        handler.redirect_request(
            request,
            None,
            302,
            "Found",
            {},
            "https://attacker.example/steal",
        )


def test_submitted_task_is_polled_after_worker_interruption_without_second_post(
    tmp_path, monkeypatch
):
    _configure_runway(monkeypatch)
    source = _source(tmp_path / "reference.png")
    output_directory = tmp_path / "job" / "shot-01"
    client = InterruptedPollingClient()

    with pytest.raises(RunwayTransportError, match="polling connection"):
        _generate(client, source, output_directory)

    resumed = _generate(client, source, output_directory)

    assert resumed["task_id"] == _TASK_ID
    assert resumed["resumed"] is False
    assert client.submit_count == 1
    assert client.poll_count == 2


def test_unconfirmed_remote_cancellation_blocks_new_paid_submission(
    tmp_path, monkeypatch
):
    _configure_runway(monkeypatch)
    source = _source(tmp_path / "reference.png")
    output_directory = tmp_path / "job" / "shot-01"
    client = CancelRequestedClient()
    cancel_checks = iter([False, True])

    with pytest.raises(RunwayError, match="Cancellation requested"):
        client.generate_image_to_video(
            source,
            "A product shot",
            "",
            4,
            output_directory,
            progress=lambda *_: None,
            cancel_check=lambda: next(cancel_checks),
        )

    with pytest.raises(RunwaySubmissionUnknownError):
        _generate(client, source, output_directory)

    assert client.submit_count == 1


def test_api_request_accepts_empty_204_delete_response(monkeypatch):
    _configure_runway(monkeypatch)
    import app.integrations.runway as runway_module

    class Empty204Response:
        status = 204

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self, _size):
            return b""

    opener = Mock()
    opener.open.return_value = Empty204Response()
    monkeypatch.setattr(runway_module, "build_opener", lambda *_: opener)
    client = RunwayDevClient()

    assert client._api_request("DELETE", f"/v1/tasks/{_TASK_ID}") == {}
    request = opener.open.call_args.args[0]
    assert request.get_method() == "DELETE"
    assert request.get_header("X-runway-version") == "2024-11-06"


def test_cancel_404_is_idempotent_and_never_submits_a_second_task(
    tmp_path, monkeypatch
):
    _configure_runway(monkeypatch)

    class AlreadyCancelledClient(FakeRunwayClient):
        def _api_request(self, method, path, payload=None):
            if method == "POST" and path == "/v1/image_to_video":
                self.submit_count += 1
                return {"id": _TASK_ID}
            if method == "DELETE" and path == f"/v1/tasks/{_TASK_ID}":
                from app.integrations.runway import RunwayHttpError

                raise RunwayHttpError(404, "already cancelled")
            raise AssertionError(f"Unexpected Runway request: {method} {path}")

    source = _source(tmp_path / "reference.png")
    output_directory = tmp_path / "job" / "shot-01"
    client = AlreadyCancelledClient()
    checks = iter([False, True])

    with pytest.raises(RunwayError, match="Cancellation requested"):
        client.generate_image_to_video(
            source,
            "A product shot",
            "",
            4,
            output_directory,
            progress=lambda *_: None,
            cancel_check=lambda: next(checks),
        )

    with pytest.raises(RunwayError, match="already cancelled"):
        _generate(client, source, output_directory)

    assert client.submit_count == 1


def test_upload_fails_if_image_changes_after_fingerprint(
    tmp_path, monkeypatch
):
    _configure_runway(monkeypatch)
    import app.integrations.runway as runway_module

    class FakeUploadConnection:
        def __init__(self, *_args, **_kwargs):
            self.sent = bytearray()

        def putrequest(self, *_args, **_kwargs):
            pass

        def putheader(self, *_args, **_kwargs):
            pass

        def endheaders(self):
            pass

        def send(self, data):
            self.sent.extend(data)

        def getresponse(self):
            class Response:
                status = 204

                def read(self, _size):
                    return b""

            return Response()

        def close(self):
            pass

    monkeypatch.setattr(
        runway_module.http.client,
        "HTTPSConnection",
        FakeUploadConnection,
    )

    class MutatingUploadClient(RunwayDevClient):
        def _api_request(self, method, path, payload=None):
            if method == "POST" and path == "/v1/uploads":
                return {
                    "uploadUrl": "https://upload.example.test/presigned",
                    "runwayUri": "runway://ephemeral/reference",
                    "fields": {"policy": "test"},
                }
            raise AssertionError(f"Unexpected Runway request: {method} {path}")

    source = _source(tmp_path / "reference.png")
    original_digest = hashlib.sha256(source.read_bytes()).hexdigest()
    source.write_bytes(b"y" * 1024)
    record = tmp_path / "source.json"
    client = MutatingUploadClient()

    with pytest.raises(RunwayUploadError, match="changed while it was being uploaded"):
        client._upload_ephemeral(source, record, original_digest)
