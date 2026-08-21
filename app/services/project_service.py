"""Create projects and import immutable source images."""

import os
import shutil
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from app.core.models import (
    Page,
    Project,
    ProjectSettings,
    ReadingOrderPreset,
    SourceLanguage,
    utc_now,
)
from app.core.sorting import natural_sort_key
from app.persistence.project_repository import ProjectRepository

type PathInput = str | os.PathLike[str]

_SUPPORTED_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".webp"})
_SUPPORTED_FORMATS = frozenset({"PNG", "JPEG", "WEBP"})
_PROJECT_DIRECTORIES = ("source", "previews", "exports", "cache")


@dataclass(frozen=True, slots=True)
class _ImageInfo:
    source: Path
    width: int
    height: int


class ProjectService:
    @classmethod
    def open_image_files(
        cls,
        image_paths: PathInput | Iterable[PathInput],
        default_source_language: SourceLanguage = SourceLanguage.AUTO,
        default_reading_order: ReadingOrderPreset = ReadingOrderPreset.WEBTOON_VERTICAL,
    ) -> tuple[Project, Path]:
        images = cls._inspect_images(image_paths)
        parents = {image.source.parent for image in images}
        if len(parents) != 1:
            raise ValueError("selected images must share the same parent directory")
        return cls._open_images(
            parents.pop(),
            images,
            default_source_language,
            default_reading_order,
        )

    @classmethod
    def open_image_folder(
        cls,
        image_dir: PathInput,
        default_source_language: SourceLanguage = SourceLanguage.AUTO,
        default_reading_order: ReadingOrderPreset = ReadingOrderPreset.WEBTOON_VERTICAL,
    ) -> tuple[Project, Path]:
        image_directory = Path(image_dir).resolve()
        if not image_directory.is_dir():
            raise NotADirectoryError(f"image folder not found: {image_directory}")

        workspace = image_directory / ".manga-thai-translator"
        root_images = [
            path
            for path in image_directory.iterdir()
            if path.is_file() and path.suffix.lower() in _SUPPORTED_EXTENSIONS
        ]
        if not root_images:
            project_file = workspace / "project.json"
            if project_file.exists() or project_file.is_symlink():
                return ProjectRepository.load(workspace), workspace
        images = cls._inspect_images(root_images or image_directory)
        return cls._open_images(
            image_directory,
            images,
            default_source_language,
            default_reading_order,
        )

    @classmethod
    def _open_images(
        cls,
        image_directory: Path,
        images: list[_ImageInfo],
        default_source_language: SourceLanguage,
        default_reading_order: ReadingOrderPreset,
    ) -> tuple[Project, Path]:
        workspace = image_directory / ".manga-thai-translator"
        project_file = workspace / "project.json"
        if not project_file.exists() and not project_file.is_symlink():
            project = cls.create_project(
                workspace,
                image_directory.name or "Manga",
                [image.source for image in images],
                default_source_language,
                default_reading_order,
                copy_sources=False,
            )
            return project, workspace

        project = ProjectRepository.load(workspace)
        existing_sources = {
            (
                Path(page.source_path)
                if Path(page.source_path).is_absolute()
                else workspace / page.source_path
            ).resolve()
            for page in project.pages
        }
        new_images = [image for image in images if image.source not in existing_sources]
        if new_images:
            copied = project.model_copy(deep=True)
            copied.pages.extend(
                Page(source_path=str(image.source), width=image.width, height=image.height)
                for image in new_images
            )
            copied.pages.sort(
                key=lambda page: (
                    natural_sort_key(Path(page.source_path).name),
                    natural_sort_key(page.source_path),
                )
            )
            copied.updated_at = utc_now()
            ProjectRepository.save(copied, workspace)
            project = copied
        return project, workspace

    @classmethod
    def create_project(
        cls,
        project_dir: PathInput,
        name: str,
        image_paths: PathInput | Iterable[PathInput],
        default_source_language: SourceLanguage = SourceLanguage.AUTO,
        default_reading_order: ReadingOrderPreset = ReadingOrderPreset.MANGA_RTL,
        copy_sources: bool = True,
    ) -> Project:
        directory = Path(project_dir).resolve()
        project_file = directory / "project.json"
        if project_file.exists() or project_file.is_symlink():
            raise FileExistsError(f"project already exists: {project_file}")

        images = cls._inspect_images(image_paths)
        destinations = cls._destinations(directory, images) if copy_sources else []
        pages = [
            Page(
                source_path=(
                    str(Path("source") / image.source.name) if copy_sources else str(image.source)
                ),
                width=image.width,
                height=image.height,
            )
            for image in images
        ]
        project = Project(
            name=name,
            settings=ProjectSettings(
                default_source_language=default_source_language,
                default_reading_order=default_reading_order,
            ),
            pages=pages,
        )

        copied: list[Path] = []
        try:
            directory.mkdir(parents=True, exist_ok=True)
            for child in _PROJECT_DIRECTORIES:
                (directory / child).mkdir(exist_ok=True)
            copied = cls._copy_images(images, destinations) if copy_sources else []
            if project_file.exists() or project_file.is_symlink():
                raise FileExistsError(f"project already exists: {project_file}")
            ProjectRepository.save(project, directory)
        except BaseException:
            cls._cleanup(copied)
            raise
        return project

    @classmethod
    def import_images(
        cls,
        project: Project,
        project_dir: PathInput,
        image_paths: PathInput | Iterable[PathInput],
        copy_sources: bool = True,
    ) -> Project:
        directory = Path(project_dir).resolve()
        images = cls._inspect_images(image_paths)
        destinations = cls._destinations(directory, images) if copy_sources else []
        cls._reject_existing_sources(project, directory, images, destinations)

        new_pages = [
            Page(
                source_path=(
                    str(Path("source") / image.source.name) if copy_sources else str(image.source)
                ),
                width=image.width,
                height=image.height,
            )
            for image in images
        ]
        copied_project = project.model_copy(deep=True)
        updated = copied_project.model_copy(
            update={"pages": [*copied_project.pages, *new_pages], "updated_at": utc_now()}
        )

        copied: list[Path] = []
        try:
            for child in _PROJECT_DIRECTORIES:
                (directory / child).mkdir(parents=True, exist_ok=True)
            copied = cls._copy_images(images, destinations) if copy_sources else []
            ProjectRepository.save(updated, directory)
        except BaseException:
            cls._cleanup(copied)
            raise
        return updated

    @staticmethod
    def _inspect_images(image_paths: PathInput | Iterable[PathInput]) -> list[_ImageInfo]:
        if isinstance(image_paths, (str, os.PathLike)):
            inputs = [image_paths]
        else:
            inputs = list(image_paths)
        if not inputs:
            raise ValueError("at least one image path is required")

        files: list[Path] = []
        for raw_path in inputs:
            path = Path(raw_path).resolve()
            if not path.exists():
                raise FileNotFoundError(f"image path does not exist: {path}")
            if path.is_dir():
                files.extend(
                    child.resolve()
                    for child in path.iterdir()
                    if child.is_file() and child.suffix.lower() in _SUPPORTED_EXTENSIONS
                )
            elif path.is_file():
                if path.suffix.lower() not in _SUPPORTED_EXTENSIONS:
                    raise ValueError(f"unsupported image extension: {path}")
                files.append(path)
            else:
                raise ValueError(f"image path is not a regular file or directory: {path}")

        if not files:
            raise ValueError("no supported images found")
        files.sort(key=lambda path: (natural_sort_key(path.name), natural_sort_key(str(path))))
        if len(files) != len(set(files)):
            raise ValueError("duplicate source image path")

        images: list[_ImageInfo] = []
        for path in files:
            try:
                with Image.open(path) as image:
                    image.load()
                    if image.format not in _SUPPORTED_FORMATS:
                        raise ValueError(f"unsupported image format: {path}")
                    width, height = image.size
            except (UnidentifiedImageError, OSError) as error:
                raise ValueError(f"invalid image: {path}") from error
            images.append(_ImageInfo(source=path, width=width, height=height))
        return images

    @staticmethod
    def _destinations(project_dir: Path, images: list[_ImageInfo]) -> list[Path]:
        destinations = [project_dir / "source" / image.source.name for image in images]
        if len(destinations) != len(set(destinations)):
            raise FileExistsError("multiple source images have the same destination name")
        for destination in destinations:
            if destination.exists() or destination.is_symlink():
                raise FileExistsError(f"source destination already exists: {destination}")
        return destinations

    @staticmethod
    def _reject_existing_sources(
        project: Project,
        project_dir: Path,
        images: list[_ImageInfo],
        destinations: list[Path],
    ) -> None:
        existing = {
            (
                Path(page.source_path)
                if Path(page.source_path).is_absolute()
                else project_dir / page.source_path
            ).resolve()
            for page in project.pages
        }
        for image in images:
            if image.source in existing:
                raise ValueError(f"source image is already in project: {image.source}")
        for destination in destinations:
            if destination.resolve() in existing:
                raise FileExistsError(f"source destination is already in project: {destination}")

    @staticmethod
    def _copy_images(images: list[_ImageInfo], destinations: list[Path]) -> list[Path]:
        copied: list[Path] = []
        try:
            for image, destination in zip(images, destinations, strict=True):
                destination.touch(exist_ok=False)
                copied.append(destination)
                shutil.copy2(image.source, destination)
        except BaseException:
            ProjectService._cleanup(copied)
            raise
        return copied

    @staticmethod
    def _cleanup(paths: Iterable[Path]) -> None:
        for path in paths:
            path.unlink(missing_ok=True)
