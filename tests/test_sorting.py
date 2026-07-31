from pathlib import Path

from app.core.sorting import natural_sort_key, natural_sorted


def test_natural_sort_handles_numbers_case_and_unicode() -> None:
    names = ["หน้า10.png", "หน้า2.png", "หน้า1.png", "PAGE3.png", "page02.png"]

    assert natural_sorted(names) == [
        "page02.png",
        "PAGE3.png",
        "หน้า1.png",
        "หน้า2.png",
        "หน้า10.png",
    ]


def test_natural_sort_is_deterministic_for_case_variants() -> None:
    names = ["page1.png", "Page1.png"]

    assert natural_sorted(reversed(names)) == ["Page1.png", "page1.png"]
    assert natural_sorted(names) == ["Page1.png", "page1.png"]


def test_natural_sort_accepts_paths_and_has_public_key() -> None:
    paths = [Path("chapter10.png"), Path("chapter2.png")]

    assert natural_sorted(paths) == [Path("chapter2.png"), Path("chapter10.png")]
    assert natural_sort_key("file2") < natural_sort_key("file10")
