import hashlib
import threading
import time
from concurrent.futures import ThreadPoolExecutor
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
    _select_seedance25_ratio,
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
                "output": ["https://dnf8butk8bbsy.cloudfront.net/temporary/output.mp4"],
            }
        raise AssertionError(f"Unexpected Runway request: {method} {path}")

    def _download_output(self, url, destination):
        assert url.startswith("https://dnf8butk8bbsy.cloudfront.net/")
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
    import app.integrations.runway as runway_module

    monkeypatch.setattr(
        runway_module,
        "probe_media",
        lambda _path: {
            "streams": [{"codec_type": "video", "width": 1280, "height": 720}]
        },
    )
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
    request = Request("https://dnf8butk8bbsy.cloudfront.net/output.mp4")

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
        client.generate_image_to_video(
            source,
            "A product shot",
            "",
            4,
            output_directory,
            progress=lambda *_: None,
            cancel_check=lambda: False,
        )

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
        client.generate_image_to_video(
            source,
            "A product shot",
            "",
            4,
            output_directory,
            progress=lambda *_: None,
            cancel_check=lambda: False,
        )

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
                    "uploadUrl": "https://runway-uploads-prod.s3.amazonaws.com/presigned",
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



@pytest.mark.parametrize(
    ("width", "height", "expected_ratio"),
    [
        (1200, 1600, "834:1112"),
        (1800, 1800, "960:960"),
        (1920, 1080, "1280:720"),
        (720, 1280, "720:1280"),
    ],
)
def test_seedance_ratio_tracks_reference_orientation(
    monkeypatch, tmp_path, width, height, expected_ratio
):
    import app.integrations.runway as runway_module

    monkeypatch.setattr(
        runway_module,
        "probe_media",
        lambda _path: {
            "streams": [
                {
                    "codec_type": "video",
                    "width": width,
                    "height": height,
                }
            ]
        },
    )

    assert _select_seedance25_ratio(tmp_path / "reference.png") == expected_ratio


def test_seedance_ratio_rejects_images_without_dimensions(monkeypatch, tmp_path):
    import app.integrations.runway as runway_module

    monkeypatch.setattr(runway_module, "probe_media", lambda _path: {"streams": []})

    with pytest.raises(RunwayError, match="reference image dimensions"):
        _select_seedance25_ratio(tmp_path / "reference.png")


def test_concurrent_duplicate_workers_cannot_submit_two_paid_tasks(
    tmp_path, monkeypatch
):
    _configure_runway(monkeypatch)

    class BlockingSubmitClient(FakeRunwayClient):
        def __init__(self):
            super().__init__()
            self.submit_started = threading.Event()
            self.release_submit = threading.Event()

        def _api_request(self, method, path, payload=None):
            if method == "POST" and path == "/v1/image_to_video":
                self.submit_count += 1
                self.submit_started.set()
                if not self.release_submit.wait(timeout=5):
                    raise TimeoutError("test submit release timed out")
                return {"id": _TASK_ID}
            return super()._api_request(method, path, payload)

    source = _source(tmp_path / "reference.png")
    output_directory = tmp_path / "job" / "shot-01"
    client = BlockingSubmitClient()
    second_started = threading.Event()

    def first_attempt():
        return _generate(client, source, output_directory)

    def second_attempt():
        second_started.set()
        return _generate(client, source, output_directory)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(first_attempt)
        assert client.submit_started.wait(timeout=2)
        second = pool.submit(second_attempt)
        assert second_started.wait(timeout=2)
        time.sleep(0.1)
        client.release_submit.set()

        first_result = first.result(timeout=5)
        second_result = second.result(timeout=5)

    assert first_result["task_id"] == _TASK_ID
    assert second_result["task_id"] == _TASK_ID
    assert second_result["resumed"] is True
    assert client.submit_count == 1



@pytest.mark.parametrize(
    "upload_url",
    [
        "https://runway-uploads-prod.s3.amazonaws.com/presigned",
        "https://runway-datasets.s3.us-east-1.amazonaws.com/presigned",
        "https://api.dev.runwayml.com/presigned",
        "https://storage.runwayml.cloud/presigned",
        "https://worker.eu.storage.runwayml.cloud/presigned",
    ],
)
def test_upload_url_accepts_runway_storage_domains(upload_url):
    import app.integrations.runway as runway_module

    parsed = runway_module._safe_upload_url(upload_url)

    assert parsed.scheme == "https"


@pytest.mark.parametrize(
    "upload_url",
    [
        "https://uploads.example.test/presigned",
        "https://runway-uploads-prod.s3.amazonaws.com.attacker.test/presigned",
        "https://attacker.cloudfront.net/presigned",
        "https://127.0.0.1/presigned",
        "http://runway-uploads-prod.s3.amazonaws.com/presigned",
        "https://user@runway-uploads-prod.s3.amazonaws.com/presigned",
        "https://runway-uploads-prod.s3.amazonaws.com:444/presigned",
    ],
)
def test_upload_url_rejects_untrusted_hosts_and_authorities(upload_url):
    import app.integrations.runway as runway_module

    with pytest.raises(RunwayUploadError, match="invalid ephemeral upload URL"):
        runway_module._safe_upload_url(upload_url)


def test_download_rejects_arbitrary_cloudfront_distribution_host(tmp_path):
    client = FakeRunwayClient()

    with pytest.raises(RunwayError, match="approved CloudFront host"):
        RunwayDevClient._download_output(
            client,
            "https://attacker.cloudfront.net/output.mp4",
            tmp_path / "unsafe.mp4",
        )


def test_cloudfront_redirect_handler_accepts_documented_runway_host():
    handler = _CloudFrontRedirectHandler()
    request = Request("https://dnf8butk8bbsy.cloudfront.net/output.mp4")

    redirected = handler.redirect_request(
        request,
        None,
        302,
        "Found",
        {},
        "https://d1y6zccyuj4x6c.cloudfront.net/output.mp4",
    )

    assert redirected is not None
