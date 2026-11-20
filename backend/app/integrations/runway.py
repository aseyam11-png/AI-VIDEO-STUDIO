import hashlib
import http.client
import ipaddress
import json
import math
import re
import ssl
import time
import uuid
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from app.core.config import settings
from app.integrations.ffmpeg import probe_media
from app.media.storage import atomic_write_json, read_json


_RUNWAY_API_BASE = "https://api.dev.runwayml.com"
_RUNWAY_API_VERSION = "2024-11-06"
_RUNWAY_MODEL = "seedance2_5"
_RUNWAY_720P_RATIOS = (
    ("1280:720", 1280 / 720),
    ("720:1280", 720 / 1280),
    ("960:960", 1.0),
    ("1112:834", 1112 / 834),
    ("834:1112", 834 / 1112),
    ("992:432", 992 / 432),
)
_RUNWAY_UPLOAD_MAX_BYTES = 200 * 1024 * 1024
_RUNWAY_OUTPUT_HOST_SUFFIX = ".cloudfront.net"


class RunwayError(RuntimeError):
    pass


class RunwayUploadError(RunwayError):
    """Upload failed before a billable generation task was submitted."""


class RunwaySubmissionUnknownError(RunwayError):
    """The POST outcome is ambiguous; resubmission could create a duplicate charge."""


class RunwayHttpError(RunwayError):
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        super().__init__(detail[:1200])


class RunwayTransportError(RunwayError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_upload_url(value: str):
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443)
    ):
        raise RunwayUploadError("Runway returned an invalid ephemeral upload URL.")
    try:
        ipaddress.ip_address(parsed.hostname)
    except ValueError:
        pass
    else:
        raise RunwayUploadError("Runway upload URLs must use a DNS hostname, not an IP address.")
    if parsed.hostname.lower() in {"localhost", "metadata.google.internal"}:
        raise RunwayUploadError("Runway returned a disallowed upload hostname.")
    return parsed


class _RunwayApiRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        parsed = urlsplit(new_url)
        if (
            parsed.scheme != "https"
            or parsed.hostname != "api.dev.runwayml.com"
            or parsed.username
            or parsed.password
            or parsed.port not in (None, 443)
        ):
            raise RunwayError(
                "Runway API redirect left the configured HTTPS API host."
            )
        return super().redirect_request(
            request, response, code, message, headers, new_url
        )


class _CloudFrontRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        parsed = urlsplit(new_url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.port not in (None, 443)
            or not parsed.hostname.lower().endswith(_RUNWAY_OUTPUT_HOST_SUFFIX)
        ):
            raise RunwayError(
                "Runway output redirect left the approved HTTPS CloudFront host."
            )
        return super().redirect_request(
            request, response, code, message, headers, new_url
        )


def _select_seedance25_ratio(source_image: Path) -> str:
    """Pick the closest supported 720p framing to avoid unwanted center-cropping."""
    metadata = probe_media(source_image)
    streams = metadata.get("streams", [])
    dimensions = next(
        (
            (stream.get("width"), stream.get("height"))
            for stream in streams
            if stream.get("codec_type") == "video"
            and isinstance(stream.get("width"), int)
            and isinstance(stream.get("height"), int)
            and stream["width"] > 0
            and stream["height"] > 0
        ),
        None,
    )
    if dimensions is None:
        raise RunwayError("Could not determine the reference image dimensions for Seedance 2.5.")

    width, height = dimensions
    aspect_ratio = width / height
    return min(
        _RUNWAY_720P_RATIOS,
        key=lambda item: abs(math.log(aspect_ratio / item[1])),
    )[0]


class RunwayDevClient:
    """Optional remote Seedance 2.5 adapter; no calls occur without explicit workflow routing."""

    def __init__(self):
        if not settings.runway_enabled or not settings.runway_api_key:
            raise RunwayError("Runway is disabled or RUNWAY_API_KEY is not configured.")
        self.api_key = settings.runway_api_key
        self.request_timeout = settings.runway_request_timeout_seconds

    def _api_request(self, method: str, path: str, payload: dict | None = None) -> dict:
        data = (
            json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
            if payload is not None
            else None
        )
        request = Request(
            f"{_RUNWAY_API_BASE}{path}",
            data=data,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "X-Runway-Version": _RUNWAY_API_VERSION,
                "Content-Type": "application/json",
            },
            method=method,
        )
        try:
            with build_opener(_RunwayApiRedirectHandler()).open(
                request, timeout=self.request_timeout
            ) as response:
                body = response.read(1024 * 1024 + 1)
                if not body and getattr(response, "status", None) == 204:
                    return {}
                if len(body) > 1024 * 1024:
                    raise RunwayError("Runway returned an unexpectedly large JSON response.")
                try:
                    value = json.loads(body.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise RunwayError("Runway returned invalid JSON.") from exc
                if not isinstance(value, dict):
                    raise RunwayError("Runway returned an invalid API response.")
                return value
        except HTTPError as exc:
            detail = exc.read(1200).decode("utf-8", errors="replace")
            raise RunwayHttpError(exc.code, detail) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise RunwayTransportError(str(exc)) from exc

    def _upload_ephemeral(self, source: Path, record_path: Path, source_sha256: str) -> str:
        suffix = source.suffix.lower()
        mime_types = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".webp": "image/webp",
        }
        if suffix not in mime_types:
            raise RunwayUploadError("Seedance 2.5 requires a JPEG, PNG or WebP reference image.")
        size = source.stat().st_size
        if size < 512 or size > _RUNWAY_UPLOAD_MAX_BYTES:
            raise RunwayUploadError("Runway reference images must be between 512 bytes and 200 MB.")

        if record_path.is_file():
            saved = read_json(record_path)
            if saved.get("source_sha256") == source_sha256:
                try:
                    age = time.time() - float(saved["created_at_epoch"])
                except (KeyError, TypeError, ValueError):
                    age = 24 * 60 * 60
                if (
                    0 <= age < 23 * 60 * 60
                    and str(saved.get("runway_uri", "")).startswith("runway://")
                ):
                    return saved["runway_uri"]

        try:
            upload_info = self._api_request(
                "POST",
                "/v1/uploads",
                {"filename": f"reference{suffix}", "type": "ephemeral"},
            )
        except RunwayError as exc:
            raise RunwayUploadError(f"Could not create a Runway upload session: {exc}") from exc

        upload_url = upload_info.get("uploadUrl")
        runway_uri = upload_info.get("runwayUri")
        fields = upload_info.get("fields")
        if (
            not isinstance(upload_url, str)
            or not isinstance(runway_uri, str)
            or not runway_uri.startswith("runway://")
            or not isinstance(fields, dict)
            or len(fields) > 30
        ):
            raise RunwayUploadError("Runway returned an incomplete ephemeral upload session.")

        parsed = _safe_upload_url(upload_url)
        boundary = f"----AIStudio{uuid.uuid4().hex}"
        field_parts = []
        for key, value in fields.items():
            if (
                not isinstance(key, str)
                or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", key)
                or not isinstance(value, str)
                or len(value) > 4000
                or "\r" in value
                or "\n" in value
            ):
                raise RunwayUploadError("Runway returned invalid multipart upload fields.")
            field_parts.append(
                (
                    f"--{boundary}\r\n"
                    f'Content-Disposition: form-data; name="{key}"\r\n\r\n'
                    f"{value}\r\n"
                ).encode("utf-8")
            )

        filename = f"reference{suffix}"
        file_header = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            f"Content-Type: {mime_types[suffix]}\r\n\r\n"
        ).encode("utf-8")
        file_suffix = f"\r\n--{boundary}--\r\n".encode("ascii")
        body_prefix = b"".join(field_parts) + file_header
        content_length = len(body_prefix) + size + len(file_suffix)
        target = urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
        connection = http.client.HTTPSConnection(
            parsed.hostname,
            parsed.port or 443,
            timeout=self.request_timeout,
            context=ssl.create_default_context(),
        )
        try:
            connection.putrequest("POST", target)
            connection.putheader("Host", parsed.hostname)
            connection.putheader("Content-Type", f"multipart/form-data; boundary={boundary}")
            connection.putheader("Content-Length", str(content_length))
            connection.endheaders()
            connection.send(body_prefix)
            with source.open("rb") as stream:
                sent = 0
                uploaded_digest = hashlib.sha256()
                while chunk := stream.read(1024 * 1024):
                    sent += len(chunk)
                    if sent > size:
                        raise RunwayUploadError(
                            "Reference image changed while it was being uploaded."
                        )
                    uploaded_digest.update(chunk)
                    connection.send(chunk)
            if sent != size or uploaded_digest.hexdigest() != source_sha256:
                raise RunwayUploadError(
                    "Reference image changed while it was being uploaded."
                )
            connection.send(file_suffix)
            response = connection.getresponse()
            response.read(4096)
            if response.status < 200 or response.status >= 300:
                raise RunwayUploadError(
                    f"Runway ephemeral image upload failed with HTTP {response.status}; "
                    "the upload session was not retried."
                )
        except RunwayUploadError:
            raise
        except (OSError, TimeoutError, http.client.HTTPException) as exc:
            raise RunwayUploadError(
                "Runway ephemeral upload failed; a new upload session is required on retry."
            ) from exc
        finally:
            connection.close()

        atomic_write_json(
            record_path,
            {
                "schema_version": 1,
                "source_sha256": source_sha256,
                "runway_uri": runway_uri,
                "created_at_epoch": time.time(),
            },
        )
        return runway_uri

    def _download_output(self, url: str, destination: Path) -> None:
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.port not in (None, 443)
            or not parsed.hostname.lower().endswith(_RUNWAY_OUTPUT_HOST_SUFFIX)
        ):
            raise RunwayError("Runway returned an output URL outside the approved CloudFront host.")
        temporary = destination.with_suffix(destination.suffix + ".partial")
        temporary.parent.mkdir(parents=True, exist_ok=True)
        size = 0
        request = Request(url, headers={"Accept": "video/mp4"})
        try:
            opener = build_opener(_CloudFrontRedirectHandler())
            with opener.open(request, timeout=self.request_timeout) as response:
                declared_length = response.headers.get("Content-Length")
                if declared_length:
                    try:
                        if int(declared_length) > settings.max_model_output_bytes:
                            raise RunwayError(
                                "Runway output exceeds the configured output-size limit."
                            )
                    except ValueError as exc:
                        raise RunwayError(
                            "Runway returned an invalid output Content-Length."
                        ) from exc
                with temporary.open("wb") as stream:
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        size += len(chunk)
                        if size > settings.max_model_output_bytes:
                            raise RunwayError(
                                "Runway output exceeded the configured output-size limit."
                            )
                        stream.write(chunk)
                    stream.flush()
            if size == 0:
                raise RunwayError("Runway returned an empty video file.")
            temporary.replace(destination)
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            temporary.unlink(missing_ok=True)
            raise RunwayError(f"Could not download the completed Runway output: {exc}") from exc
        except Exception:
            temporary.unlink(missing_ok=True)
            raise

    def _cancel_task(self, task_id: str) -> None:
        try:
            self._api_request("DELETE", f"/v1/tasks/{task_id}")
        except RunwayHttpError as exc:
            if exc.status_code != 404:
                raise

    def generate_image_to_video(
        self,
        source_image: Path,
        prompt: str,
        negative_prompt: str,
        duration_seconds: int,
        output_directory: Path,
        *,
        progress,
        cancel_check,
    ) -> dict:
        if not 4 <= int(duration_seconds) <= 10:
            raise RunwayError("Seedance 2.5 is enabled here only for 4–10 second shots.")
        output_directory.mkdir(parents=True, exist_ok=True)
        output_path = output_directory / "seedance2_5.mp4"
        task_record_path = output_directory / "runway-task.json"
        source_record_path = output_directory / "runway-source.json"
        source_sha256 = _sha256(source_image)
        ratio = _select_seedance25_ratio(source_image)
        request_material = json.dumps(
            {
                "model": _RUNWAY_MODEL,
                "source_sha256": source_sha256,
                "prompt": prompt,
                "negative_prompt": negative_prompt,
                "duration_seconds": int(duration_seconds),
                "ratio": ratio,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        request_sha256 = hashlib.sha256(request_material).hexdigest()

        task_record = None
        task_id = None
        if task_record_path.is_file():
            task_record = read_json(task_record_path)
            if task_record.get("request_sha256") != request_sha256:
                raise RunwayError(
                    "A different request already uses this shot task record; refusing to reuse it."
                )
            state = task_record.get("status")
            if state in {"submission_started", "submission_unknown", "cancel_requested"}:
                raise RunwaySubmissionUnknownError(
                    "Runway submission or cancellation outcome is unknown. The Studio will not "
                    "submit a second billable task automatically; check the Runway project before "
                    "creating a new job."
                )
            if state in {"rejected", "failed", "cancelled"}:
                raise RunwayError(
                    f"Runway task is already {state}; edit the prompt or create a new workflow."
                )
            if state == "succeeded":
                if (
                    output_path.is_file()
                    and _sha256(output_path) == task_record.get("output_sha256")
                ):
                    return {
                        "path": output_path,
                        "task_id": task_record["task_id"],
                        "provider": _RUNWAY_MODEL,
                        "resumed": True,
                    }
                raise RunwayError(
                    "Runway task already succeeded but its local output is missing or changed; "
                    "refusing to create a duplicate generation."
                )
            if state == "submitted":
                candidate_task_id = task_record.get("task_id")
                if (
                    not isinstance(candidate_task_id, str)
                    or not re.fullmatch(r"[A-Fa-f0-9-]{32,36}", candidate_task_id)
                ):
                    raise RunwaySubmissionUnknownError(
                        "The saved Runway task ID is missing or invalid; refusing to submit "
                        "another billable task."
                    )
                task_id = candidate_task_id
            else:
                raise RunwaySubmissionUnknownError(
                    "The saved Runway task record has an unknown state; refusing to resubmit."
                )

        if task_id is None:
            source_uri = self._upload_ephemeral(
                source_image,
                source_record_path,
                source_sha256,
            )
            if cancel_check():
                raise RunwayError(
                    "Cancellation requested before submitting the remote generation."
                )

            task_record = {
                "schema_version": 1,
                "provider": "runway_dev",
                "model": _RUNWAY_MODEL,
                "request_sha256": request_sha256,
                "source_sha256": source_sha256,
                "status": "submission_started",
                "started_at_epoch": time.time(),
            }
            atomic_write_json(task_record_path, task_record)
            prompt_text = f"{prompt}\nAvoid: {negative_prompt}" if negative_prompt else prompt
            try:
                response = self._api_request(
                    "POST",
                    "/v1/image_to_video",
                    {
                        "model": _RUNWAY_MODEL,
                        "promptImage": source_uri,
                        "promptText": prompt_text[:15000],
                        "duration": int(duration_seconds),
                        "ratio": ratio,
                        "audio": True,
                        "outputFormat": "mp4",
                    },
                )
            except RunwayHttpError as exc:
                task_record["status"] = (
                    "rejected" if 400 <= exc.status_code < 500 else "submission_unknown"
                )
                task_record["http_status"] = exc.status_code
                atomic_write_json(task_record_path, task_record)
                if exc.status_code >= 500:
                    raise RunwaySubmissionUnknownError(
                        "Runway returned a server error after submission; the task may have been "
                        "accepted. Check Runway before retrying."
                    ) from exc
                raise RunwayError(
                    f"Runway rejected the generation request (HTTP {exc.status_code}); "
                    "no automatic retry was attempted."
                ) from exc
            except RunwayTransportError as exc:
                task_record["status"] = "submission_unknown"
                atomic_write_json(task_record_path, task_record)
                raise RunwaySubmissionUnknownError(
                    "The Runway submission timed out or lost its connection. Its billing outcome "
                    "is unknown, so the Studio will not submit it again automatically."
                ) from exc

            candidate_task_id = response.get("id")
            if (
                not isinstance(candidate_task_id, str)
                or not re.fullmatch(r"[A-Fa-f0-9-]{32,36}", candidate_task_id)
            ):
                task_record["status"] = "submission_unknown"
                atomic_write_json(task_record_path, task_record)
                raise RunwaySubmissionUnknownError(
                    "Runway accepted a response without a valid task ID; check the provider "
                    "console "
                    "before attempting another generation."
                )
            task_id = candidate_task_id
            task_record["task_id"] = task_id
            task_record["status"] = "submitted"
            task_record["submitted_at_epoch"] = time.time()
            atomic_write_json(task_record_path, task_record)
        else:
            progress(
                40,
                "resuming_seedance_task",
                "Resuming the previously accepted Runway task; no new generation will be "
                "submitted.",
            )

        deadline = time.monotonic() + settings.runway_job_timeout_seconds
        delay = max(5, settings.runway_poll_interval_seconds)
        while time.monotonic() < deadline:
            if cancel_check():
                try:
                    self._cancel_task(task_id)
                    task_record["status"] = "cancelled"
                except RunwayError:
                    task_record["status"] = "cancel_requested"
                atomic_write_json(task_record_path, task_record)
                raise RunwayError(
                    "Cancellation requested for the Runway task. Verify its final state in Runway."
                )

            task = self._api_request("GET", f"/v1/tasks/{task_id}")
            status = str(task.get("status", "")).upper()
            if status in {"FAILED", "CANCELLED", "CANCELED"}:
                task_record["status"] = "failed" if status == "FAILED" else "cancelled"
                task_record["provider_status"] = status
                atomic_write_json(task_record_path, task_record)
                raise RunwayError(
                    f"Runway task {task_id} finished with status {status}; "
                    "it will not be resubmitted."
                )
            if status == "SUCCEEDED":
                outputs = task.get("output")
                if not isinstance(outputs, list) or not outputs or not isinstance(outputs[0], str):
                    task_record["status"] = "failed"
                    atomic_write_json(task_record_path, task_record)
                    raise RunwayError(
                        "Runway reported success without a downloadable video output."
                    )
                progress(
                    88,
                    "downloading_seedance_output",
                    "Downloading the completed Runway video to local storage.",
                )
                self._download_output(outputs[0], output_path)
                output_sha256 = _sha256(output_path)
                task_record["status"] = "succeeded"
                task_record["output_filename"] = output_path.name
                task_record["output_sha256"] = output_sha256
                task_record["completed_at_epoch"] = time.time()
                atomic_write_json(task_record_path, task_record)
                return {
                    "path": output_path,
                    "task_id": task_id,
                    "provider": _RUNWAY_MODEL,
                    "resumed": False,
                }
            if status not in {"PENDING", "THROTTLED", "RUNNING", "PROCESSING", "IN_PROGRESS"}:
                raise RunwayError(f"Runway returned an unknown task status: {status or 'missing'}.")
            progress(45, "seedance_generation", f"Seedance 2.5 task {task_id} is {status.lower()}.")
            time.sleep(delay)
            delay = min(20, max(5, int(delay * 1.25)))

        raise RunwayError(
            f"Runway task {task_id} exceeded the configured wait limit. Its task ID was saved; "
            "retry will poll the same task rather than submit another generation."
        )
