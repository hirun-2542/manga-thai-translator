import json
import os
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.core.models import (
    BoundingBox,
    Page,
    Project,
    ProjectSettings,
    ReadingOrderPreset,
    SourceLanguage,
    TextBlock,
)
from app.persistence.project_repository import ProjectRepository


def multilingual_project(source_path: Path) -> Project:
    page_id = UUID(int=10)
    texts = [
        (SourceLanguage.JA, "おかえり"),
        (SourceLanguage.EN, "Welcome home"),
        (SourceLanguage.KO, "어서 와"),
        (SourceLanguage.ZH_HANS, "欢迎回来"),
        (SourceLanguage.ZH_HANT, "歡迎回來"),
    ]
    blocks = [
        TextBlock(
            id=UUID(int=index),
            page_id=page_id,
            bbox=BoundingBox(x=index * 20, y=10, width=15, height=10),
            reading_order=index,
            source_language=language,
            source_text=text,
            translated_text="ยินดีต้อนรับกลับ",
        )
        for index, (language, text) in enumerate(texts, start=1)
    ]
    return Project(
        name="เรื่องทดสอบ",
        settings=ProjectSettings(
            default_source_language=SourceLanguage.JA,
            default_reading_order=ReadingOrderPreset.MANGA_RTL,
        ),
        pages=[
            Page(
                id=page_id,
                source_path=str(source_path),
                width=1200,
                height=1800,
                source_language=SourceLanguage.KO,
                reading_order=ReadingOrderPreset.WEBTOON_VERTICAL,
                blocks=blocks,
            )
        ],
    )


def test_save_load_round_trip_unicode_and_language_overrides(tmp_path: Path) -> None:
    source = tmp_path / "ต้นฉบับ.png"
    source.write_bytes(b"source-image")
    project_dir = tmp_path / "โปรเจกต์มังงะ"
    project = multilingual_project(source)
    repository = ProjectRepository()

    saved_path = repository.save(project, project_dir)
    loaded = repository.load(project_dir)

    assert saved_path == project_dir / "project.json"
    assert loaded == project
    assert source.read_bytes() == b"source-image"
    assert loaded.pages[0].source_language is SourceLanguage.KO
    assert loaded.pages[0].blocks[-1].source_language is SourceLanguage.ZH_HANT
    serialized = saved_path.read_text(encoding="utf-8")
    assert "歡迎回來" in serialized
    assert "ยินดีต้อนรับกลับ" in serialized
    assert "api_key" not in serialized.lower()


def test_save_uses_atomic_replace_with_temp_file_in_project_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_dir = tmp_path / "project"
    calls: list[tuple[Path, Path]] = []
    fsynced: list[int] = []
    real_replace = os.replace
    real_fsync = os.fsync

    def recording_replace(source: str | Path, destination: str | Path) -> None:
        calls.append((Path(source), Path(destination)))
        real_replace(source, destination)

    def recording_fsync(file_descriptor: int) -> None:
        fsynced.append(file_descriptor)
        real_fsync(file_descriptor)

    monkeypatch.setattr(os, "replace", recording_replace)
    monkeypatch.setattr(os, "fsync", recording_fsync)

    ProjectRepository.save(multilingual_project(tmp_path / "page.png"), project_dir)

    assert len(calls) == 1
    assert len(fsynced) == 1
    temporary, destination = calls[0]
    assert temporary.parent == project_dir
    assert destination == project_dir / "project.json"
    assert not temporary.exists()


def test_load_rejects_unknown_schema_version(tmp_path: Path) -> None:
    repository = ProjectRepository()
    project_dir = tmp_path / "project"
    project_file = repository.save(
        multilingual_project(tmp_path / "page.png"),
        project_dir,
    )
    data = json.loads(project_file.read_text(encoding="utf-8"))
    data["schema_version"] = 2
    project_file.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(ValidationError, match="schema_version"):
        repository.load(project_dir)


def test_failed_atomic_replace_preserves_previous_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_dir = tmp_path / "project"
    original = multilingual_project(tmp_path / "page.png")
    project_file = ProjectRepository.save(original, project_dir)

    def failed_replace(source: str | Path, destination: str | Path) -> None:
        raise OSError("simulated replace failure")

    monkeypatch.setattr(os, "replace", failed_replace)

    with pytest.raises(OSError, match="simulated"):
        ProjectRepository.save(original.model_copy(update={"name": "changed"}), project_dir)

    assert ProjectRepository.load(project_dir) == original
    assert list(project_dir.iterdir()) == [project_file]
