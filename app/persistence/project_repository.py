import json
import os
import tempfile
from pathlib import Path

from app.core.models import Project


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
        return Project.model_validate_json(project_file.read_text(encoding="utf-8"))
