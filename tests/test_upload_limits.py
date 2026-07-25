from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.apps.comic_gen import api as comic_api
from src.apps.comic_gen.models import StoryboardFrame
from src.apps.playground import api as playground_api

LIMIT_BYTES = 16


def make_pdf_with_text(text: str) -> bytes:
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET".encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>"
        ),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length "
        + str(len(stream)).encode("ascii")
        + b" >>\nstream\n"
        + stream
        + b"\nendstream",
    ]
    payload = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, body in enumerate(objects, start=1):
        offsets.append(len(payload))
        payload.extend(f"{number} 0 obj\n".encode("ascii"))
        payload.extend(body)
        payload.extend(b"\nendobj\n")

    xref_offset = len(payload)
    payload.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    payload.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        payload.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    payload.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode("ascii")
    )
    return bytes(payload)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        comic_api,
        "GENERIC_MEDIA_UPLOAD_POLICY",
        replace(comic_api.GENERIC_MEDIA_UPLOAD_POLICY, max_bytes=LIMIT_BYTES),
    )
    monkeypatch.setattr(
        comic_api,
        "IMAGE_UPLOAD_POLICY",
        replace(comic_api.IMAGE_UPLOAD_POLICY, max_bytes=LIMIT_BYTES),
    )
    monkeypatch.setattr(
        comic_api,
        "TEXT_IMPORT_UPLOAD_POLICY",
        replace(comic_api.TEXT_IMPORT_UPLOAD_POLICY, max_bytes=LIMIT_BYTES),
    )
    monkeypatch.setattr(
        playground_api,
        "IMAGE_UPLOAD_POLICY",
        replace(playground_api.IMAGE_UPLOAD_POLICY, max_bytes=LIMIT_BYTES),
    )
    return TestClient(comic_api.app)


UPLOAD_CASES = [
    ("/upload", "image.png", "image/png", Path("output/uploads")),
    ("/library/assets/upload", "image.png", "image/png", Path("output/uploads")),
    (
        "/projects/project-1/assets/character/asset-1/upload?upload_type=image",
        "image.png",
        "image/png",
        Path("output/uploads"),
    ),
    (
        "/projects/project-1/frames/frame-1/upload_image",
        "image.png",
        "image/png",
        Path("output/uploads"),
    ),
    (
        "/series/import/preview?suggested_episodes=3",
        "script.txt",
        "text/plain",
        Path("output/uploads"),
    ),
    (
        "/playground/upload",
        "image.png",
        "image/png",
        Path("output/playground/uploads"),
    ),
]


@pytest.mark.parametrize("url,filename,content_type,upload_dir", UPLOAD_CASES)
def test_public_uploads_reject_oversized_files_and_remove_partials(
    client,
    url,
    filename,
    content_type,
    upload_dir,
):
    response = client.post(
        url,
        files={"file": (filename, b"x" * (LIMIT_BYTES + 1), content_type)},
    )

    assert response.status_code == 413
    assert not upload_dir.exists() or list(upload_dir.iterdir()) == []


@pytest.mark.parametrize("url,_,__,upload_dir", UPLOAD_CASES)
def test_public_uploads_reject_disallowed_extensions_without_writing_files(
    client,
    url,
    _,
    __,
    upload_dir,
):
    response = client.post(
        url,
        files={"file": ("payload.exe", b"not media", "application/octet-stream")},
    )

    assert response.status_code == 415
    assert not upload_dir.exists() or list(upload_dir.iterdir()) == []


def test_generic_upload_preserves_audio_support(client, monkeypatch):
    class LocalOnlyUploader:
        def upload_image(self, path):
            return None

    monkeypatch.setattr(comic_api, "OSSImageUploader", LocalOnlyUploader)

    response = client.post(
        "/upload",
        files={"file": ("voice.mp3", b"ID3-audio", "audio/mpeg")},
    )

    assert response.status_code == 200
    relative_path = response.json()["url"]
    assert relative_path.startswith("uploads/")
    assert relative_path.endswith(".mp3")
    assert Path("output", relative_path).read_bytes() == b"ID3-audio"


def test_image_upload_rejects_mismatched_declared_media_type(client):
    response = client.post(
        "/library/assets/upload",
        files={"file": ("image.png", b"not an image", "text/plain")},
    )

    assert response.status_code == 415
    assert not Path("output/uploads").exists()


def test_import_preview_preserves_txt_support(client, monkeypatch):
    monkeypatch.setattr(
        comic_api.pipeline,
        "import_file_and_split",
        lambda text, count: [{"title": "Episode 1", "text": text, "count": count}],
    )

    response = client.post(
        "/series/import/preview?suggested_episodes=3",
        files={"file": ("script.txt", "hello story".encode(), "text/plain")},
    )

    assert response.status_code == 200
    assert response.json()["text_length"] == len("hello story")
    assert response.json()["episodes"][0]["count"] == 3


def test_import_preview_defaults_to_one_episode(client, monkeypatch):
    monkeypatch.setattr(
        comic_api.pipeline,
        "import_file_and_split",
        lambda text, count: [{"title": "Episode 1", "text": text, "count": count}],
    )

    response = client.post(
        "/series/import/preview",
        files={"file": ("script.txt", b"hello story", "text/plain")},
    )

    assert response.status_code == 200
    assert response.json()["suggested_episodes"] == 1
    assert response.json()["episodes"][0]["count"] == 1


def test_import_preview_extracts_pdf_text(client, monkeypatch):
    monkeypatch.setattr(
        comic_api,
        "TEXT_IMPORT_UPLOAD_POLICY",
        replace(comic_api.TEXT_IMPORT_UPLOAD_POLICY, max_bytes=1024 * 1024),
    )
    monkeypatch.setattr(
        comic_api.pipeline,
        "import_file_and_split",
        lambda text, count: [{"title": "Episode 1", "text": text, "count": count}],
    )

    response = client.post(
        "/series/import/preview",
        files={
            "file": (
                "script.pdf",
                make_pdf_with_text("Hello PDF story"),
                "application/pdf",
            )
        },
    )

    assert response.status_code == 200
    assert response.json()["episodes"][0]["text"] == "Hello PDF story"
    assert response.json()["episodes"][0]["count"] == 1


def test_import_preview_rejects_pdf_without_extractable_text(client, monkeypatch):
    monkeypatch.setattr(
        comic_api,
        "TEXT_IMPORT_UPLOAD_POLICY",
        replace(comic_api.TEXT_IMPORT_UPLOAD_POLICY, max_bytes=1024 * 1024),
    )

    response = client.post(
        "/series/import/preview",
        files={"file": ("empty.pdf", make_pdf_with_text(""), "application/pdf")},
    )

    assert response.status_code == 400
    assert "no extractable text" in response.json()["detail"]


def test_playground_upload_preserves_image_support(client):
    response = client.post(
        "/playground/upload",
        files={"file": ("reference.webp", b"webp-data", "image/webp")},
    )

    assert response.status_code == 200
    path = Path(response.json()["path"])
    assert path.suffix == ".webp"
    assert not path.is_absolute()
    assert path.parts[:2] == ("playground", "uploads")
    assert Path("output", path).read_bytes() == b"webp-data"


def test_playground_upload_delete_removes_server_file_and_is_idempotent(client):
    upload = client.post(
        "/playground/upload",
        files={"file": ("reference.webp", b"webp-data", "image/webp")},
    )
    assert upload.status_code == 200
    relative_path = upload.json()["path"]
    stored_path = Path("output", relative_path)
    assert stored_path.is_file()

    deleted = client.request("DELETE", "/playground/upload", json={"path": relative_path})
    assert deleted.status_code == 200
    assert deleted.json() == {"ok": True}
    assert not stored_path.exists()

    repeated = client.request("DELETE", "/playground/upload", json={"path": relative_path})
    assert repeated.status_code == 200

    escaped = client.request("DELETE", "/playground/upload", json={"path": "../outside.webp"})
    assert escaped.status_code == 400


def test_upload_t2i_success_contract_is_unchanged(client, monkeypatch):
    recorded = {}

    def fake_upload_t2i_frame(script_id, frame_id, relative_path):
        recorded.update(
            script_id=script_id,
            frame_id=frame_id,
            relative_path=relative_path,
        )
        return StoryboardFrame(
            id=frame_id,
            scene_id="scene-1",
            t2i_image_urls=[relative_path],
        )

    monkeypatch.setattr(comic_api.pipeline, "upload_t2i_frame", fake_upload_t2i_frame)
    monkeypatch.setattr(comic_api, "signed_response", lambda value: value)

    response = client.post(
        "/projects/project-1/frames/frame-1/upload_t2i",
        files={"file": ("first-frame.png", b"png-data", "image/png")},
    )

    assert response.status_code == 200
    assert recorded["script_id"] == "project-1"
    assert recorded["frame_id"] == "frame-1"
    assert recorded["relative_path"].startswith("uploads/t2i_")
    assert Path("output", recorded["relative_path"]).read_bytes() == b"png-data"
