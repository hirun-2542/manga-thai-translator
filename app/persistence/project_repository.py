import json
import os
import tempfile
from pathlib import Path

from app.core.models import SCHEMA_VERSION, Project


class ProjectRepository:
    _FILENAME = "project.json"

    @classmethod
    def save(cls, project: Project, project_dir: Path | str) -> Path:
        directory = Path(project_dir)
        directory.mkdir(parents=True, exist_ok=True)
        destination = directory / cls._FILENAME
        temporary_path: Path | None = None

        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=directory,
                prefix=".project-",
                suffix=".tmp",
                delete=False,
            ) as temporary_file:
                temporary_path = Path(temporary_file.name)
                json.dump(
                    project.model_dump(mode="json"),
                    temporary_file,
                    ensure_ascii=False,
                    indent=2,
                )
                temporary_file.write("\n")
                temporary_file.flush()
                os.fsync(temporary_file.fileno())

            os.replace(temporary_path, destination)
        except BaseException:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
            raise

        return destination

    @classmethod
    def load(cls, project_dir: Path | str) -> Project:
        project_file = Path(project_dir) / cls._FILENAME
        data = json.loads(project_file.read_text(encoding="utf-8"))
        if data.get("schema_version") == 1:
            data = cls._migrate_v1(data)
        return Project.model_validate(data)

    @staticmethod
    def _migrate_v1(data: dict) -> dict:
        migrated = dict(data)
        migrated["schema_version"] = SCHEMA_VERSION
        migrated_pages = []
        for page in data.get("pages", []):
            migrated_page = dict(page)
            migrated_blocks = []
            for block in page.get("blocks", []):
                migrated_block = dict(block)
                migrated_block["rotation_degrees"] = migrated_block.pop(
                    "typesetting_rotation_degrees",
                    0.0,
                )
                migrated_block.setdefault("mirror_horizontal", False)
                migrated_block.setdefault("mirror_vertical", False)
                migrated_blocks.append(migrated_block)
            migrated_page["blocks"] = migrated_blocks
            migrated_pages.append(migrated_page)
        migrated["pages"] = migrated_pages
        return migrated
