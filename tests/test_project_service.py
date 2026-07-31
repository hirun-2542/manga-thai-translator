from pathlib import Path

import pytest
from PIL import Image

from app.core.models import BoundingBox, Project, SourceLanguage, TextBlock
from app.persistence.project_repository import ProjectRepository
from app.services.project_service import ProjectService


def make_image(path: Path, size: tuple[int, int] = (20, 30), color: str = "red") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path)
    return path


def test_create_project_imports_unicode_images_in_natural_order(tmp_path: Path) -> None:
    inputs = tmp_path / "ภาพต้นฉบับ"
    make_image(inputs / "หน้า10.png", (100, 200), "red")
    make_image(inputs / "หน้า2.jpg", (20, 30), "green")
    make_image(inputs / "หน้า1.webp", (40, 50), "blue")
    (inputs / "notes.txt").write_text("ignored", encoding="utf-8")
    (inputs / "nested").mkdir()
    make_image(inputs / "nested" / "หน้า0.png")
    project_dir = tmp_path / "โปรเจกต์"

    project = ProjectService.create_project(project_dir, "เรื่องไทย", inputs)

    assert [Path(page.source_path).name for page in project.pages] == [
        "หน้า1.webp",
        "หน้า2.jpg",
        "หน้า10.png",
    ]
    assert [(page.width, page.height) for page in project.pages] == [
        (40, 50),
        (20, 30),
        (100, 200),
    ]
    assert project.settings.default_source_language is SourceLanguage.AUTO
    assert ProjectRepository.load(project_dir) == project
    assert {path.name for path in project_dir.iterdir()} == {
        "project.json",
        "source",
        "previews",
        "exports",
        "cache",
    }


def test_default_copy_preserves_source_and_reference_mode_is_absolute(tmp_path: Path) -> None:
    source = make_image(tmp_path / "ภาพ.png", color="purple")
    original = source.read_bytes()

    copied = ProjectService.create_project(tmp_path / "copied", "copy", source)
    referenced = ProjectService.create_project(
        tmp_path / "referenced",
        "reference",
        source,
        copy_sources=False,
    )

    assert copied.pages[0].source_path == "source/ภาพ.png"
    assert (tmp_path / "copied" / copied.pages[0].source_path).read_bytes() == original
    assert source.read_bytes() == original
    assert Path(referenced.pages[0].source_path) == source.resolve()
    assert list((tmp_path / "referenced" / "source").iterdir()) == []


def test_create_refuses_existing_project_before_copying(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    ProjectRepository.save(Project(name="existing"), project_dir)
    source = make_image(tmp_path / "page.png")

    with pytest.raises(FileExistsError, match="project already exists"):
        ProjectService.create_project(project_dir, "replacement", source)

    assert ProjectRepository.load(project_dir).name == "existing"
    assert not (project_dir / "source" / source.name).exists()


@pytest.mark.parametrize("filename", ["page.txt", "page.gif"])
def test_create_rejects_unsupported_extension(tmp_path: Path, filename: str) -> None:
    source = tmp_path / filename
    source.write_bytes(b"not an image")
    project_dir = tmp_path / "project"

    with pytest.raises(ValueError, match="unsupported image extension"):
        ProjectService.create_project(project_dir, "test", source)

    assert not (project_dir / "project.json").exists()


def test_create_rejects_invalid_supported_image_before_writing(tmp_path: Path) -> None:
    source = tmp_path / "broken.png"
    source.write_bytes(b"not an image")
    project_dir = tmp_path / "project"

    with pytest.raises(ValueError, match="invalid image"):
        ProjectService.create_project(project_dir, "test", source)

    assert not project_dir.exists()


def test_create_rejects_duplicate_destination_names(tmp_path: Path) -> None:
    first = make_image(tmp_path / "one" / "page.png")
    second = make_image(tmp_path / "two" / "page.png")
    project_dir = tmp_path / "project"

    with pytest.raises(FileExistsError, match="same destination name"):
        ProjectService.create_project(project_dir, "test", [first, second])

    assert not project_dir.exists()


def test_import_images_round_trip_preserves_existing_data_and_input(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    first = make_image(tmp_path / "page1.png")
    project = ProjectService.create_project(project_dir, "test", first)
    page = project.pages[0]
    block = TextBlock(
        page_id=page.id,
        bbox=BoundingBox(x=1, y=2, width=3, height=4),
        reading_order=1,
        source_language=SourceLanguage.JA,
        source_text="ただいま",
        translated_text="กลับมาแล้ว",
    )
    original = project.model_copy(
        update={"pages": [page.model_copy(update={"blocks": [block]})]},
        deep=True,
    )
    ProjectRepository.save(original, project_dir)
    original_json = original.model_dump_json()
    second = make_image(tmp_path / "page2.png", (55, 66))
    third = make_image(tmp_path / "page10.jpeg", (77, 88))

    updated = ProjectService.import_images(original, project_dir, [third, second])
    loaded = ProjectRepository.load(project_dir)

    assert original.model_dump_json() == original_json
    assert updated is not original
    assert updated.pages[0] is not original.pages[0]
    assert updated.pages[0].blocks[0] is not original.pages[0].blocks[0]
    assert updated.pages[0] == original.pages[0]
    assert updated.pages[0].id == original.pages[0].id
    assert updated.pages[0].blocks[0].id == block.id
    assert [Path(page.source_path).name for page in updated.pages] == [
        "page1.png",
        "page2.png",
        "page10.jpeg",
    ]
    assert loaded == updated


def test_failed_import_does_not_mutate_input_or_saved_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_dir = tmp_path / "project"
    project = ProjectService.create_project(
        project_dir,
        "test",
        make_image(tmp_path / "page1.png"),
    )
    saved = (project_dir / "project.json").read_bytes()
    new_source = make_image(tmp_path / "page2.png")

    def fail_save(project: Project, project_dir: Path) -> Path:
        raise OSError("simulated save failure")

    monkeypatch.setattr(ProjectRepository, "save", fail_save)

    with pytest.raises(OSError, match="simulated"):
        ProjectService.import_images(project, project_dir, new_source)

    assert len(project.pages) == 1
    assert (project_dir / "project.json").read_bytes() == saved
    assert not (project_dir / "source" / "page2.png").exists()


def test_import_rejects_duplicate_source_without_changing_project(tmp_path: Path) -> None:
    source = make_image(tmp_path / "page.png")
    project_dir = tmp_path / "project"
    project = ProjectService.create_project(
        project_dir,
        "test",
        source,
        copy_sources=False,
    )
    saved = (project_dir / "project.json").read_bytes()

    with pytest.raises(ValueError, match="already in project"):
        ProjectService.import_images(project, project_dir, source, copy_sources=False)

    assert len(project.pages) == 1
    assert (project_dir / "project.json").read_bytes() == saved
