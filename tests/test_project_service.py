from pathlib import Path

import pytest
from PIL import Image

from app.core.models import (
    BoundingBox,
    Project,
    ReadingOrderPreset,
    SourceLanguage,
    TextBlock,
)
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


def test_open_image_folder_creates_reopens_and_syncs_hidden_workspace(tmp_path: Path) -> None:
    images = tmp_path / "มังงะไทย"
    paths = [
        make_image(images / "หน้า10.PNG", color="red"),
        make_image(images / "หน้า2.jpg", color="green"),
        make_image(images / "หน้า1.jpeg", color="blue"),
        make_image(images / "หน้า3.webp", color="purple"),
    ]
    original_bytes = {path: path.read_bytes() for path in paths}

    project, workspace = ProjectService.open_image_folder(images)

    assert workspace == images / ".manga-thai-translator"
    assert [Path(page.source_path).name for page in project.pages] == [
        "หน้า1.jpeg",
        "หน้า2.jpg",
        "หน้า3.webp",
        "หน้า10.PNG",
    ]
    assert all(Path(page.source_path).is_absolute() for page in project.pages)
    assert project.settings.default_source_language is SourceLanguage.AUTO
    assert project.settings.default_reading_order is ReadingOrderPreset.WEBTOON_VERTICAL
    assert (workspace / "project.json").is_file()
    assert list((workspace / "source").iterdir()) == []

    preserved_page = project.pages[1]
    preserved_block = TextBlock(
        page_id=preserved_page.id,
        bbox=BoundingBox(x=1, y=2, width=3, height=4),
        reading_order=1,
        translated_text="เก็บฉันไว้",
    )
    preserved_page.blocks = [preserved_block]
    ProjectRepository.save(project, workspace)
    new_path = make_image(images / "หน้า4.png", color="orange")
    original_bytes[new_path] = new_path.read_bytes()

    reopened, reopened_workspace = ProjectService.open_image_folder(
        images,
        default_source_language=SourceLanguage.JA,
        default_reading_order=ReadingOrderPreset.MANGA_RTL,
    )

    assert reopened_workspace == workspace
    assert [Path(page.source_path).name for page in reopened.pages] == [
        "หน้า1.jpeg",
        "หน้า2.jpg",
        "หน้า3.webp",
        "หน้า4.png",
        "หน้า10.PNG",
    ]
    restored = next(page for page in reopened.pages if page.id == preserved_page.id)
    assert restored.blocks[0].id == preserved_block.id
    assert restored.blocks[0].translated_text == "เก็บฉันไว้"
    assert reopened.settings.default_source_language is SourceLanguage.AUTO
    assert reopened.settings.default_reading_order is ReadingOrderPreset.WEBTOON_VERTICAL
    assert ProjectRepository.load(workspace) == reopened
    assert all(path.read_bytes() == content for path, content in original_bytes.items())


def test_open_image_files_creates_reopens_adds_and_is_idempotent(tmp_path: Path) -> None:
    images = tmp_path / "ภาพที่เลือก"
    unselected = make_image(images / "หน้า3.png", color="green")
    selected = [
        make_image(images / "หน้า10.webp", color="red"),
        make_image(images / "หน้า2.jpeg", color="blue"),
        make_image(images / "หน้า1.JPG", color="purple"),
    ]
    original_bytes = {path: path.read_bytes() for path in [unselected, *selected]}

    project, workspace = ProjectService.open_image_files(selected)

    assert workspace == images / ".manga-thai-translator"
    assert [Path(page.source_path).name for page in project.pages] == [
        "หน้า1.JPG",
        "หน้า2.jpeg",
        "หน้า10.webp",
    ]
    assert all(Path(page.source_path).is_absolute() for page in project.pages)
    assert list((workspace / "source").iterdir()) == []

    preserved_page = project.pages[1]
    preserved_block = TextBlock(
        page_id=preserved_page.id,
        bbox=BoundingBox(x=1, y=2, width=3, height=4),
        reading_order=1,
        translated_text="เก็บการแก้ไข",
    )
    preserved_page.blocks = [preserved_block]
    ProjectRepository.save(project, workspace)

    reopened, reopened_workspace = ProjectService.open_image_files([unselected, selected[1]])

    assert reopened_workspace == workspace
    assert [Path(page.source_path).name for page in reopened.pages] == [
        "หน้า1.JPG",
        "หน้า2.jpeg",
        "หน้า3.png",
        "หน้า10.webp",
    ]
    restored = next(page for page in reopened.pages if page.id == preserved_page.id)
    assert restored.blocks[0].id == preserved_block.id
    assert restored.blocks[0].translated_text == "เก็บการแก้ไข"
    saved = (workspace / "project.json").read_bytes()

    unchanged, unchanged_workspace = ProjectService.open_image_files([selected[1], unselected])

    assert unchanged_workspace == workspace
    assert unchanged == reopened
    assert (workspace / "project.json").read_bytes() == saved
    assert all(path.read_bytes() == content for path, content in original_bytes.items())


def test_open_image_files_rejects_mixed_parents_before_creating_workspace(
    tmp_path: Path,
) -> None:
    first = make_image(tmp_path / "one" / "page1.png")
    second = make_image(tmp_path / "two" / "page2.png")

    with pytest.raises(ValueError, match="same parent directory"):
        ProjectService.open_image_files([first, second])

    assert not (first.parent / ".manga-thai-translator").exists()
    assert not (second.parent / ".manga-thai-translator").exists()
