import pathlib
import tomllib

from text_extraction_service.version import __version__


def test_versions_are_in_sync():
    path = pathlib.Path(__file__).resolve().parents[2] / "pyproject.toml"
    with path.open("rb") as handle:
        pyproject = tomllib.load(handle)
    assert __version__ == pyproject["project"]["version"]
