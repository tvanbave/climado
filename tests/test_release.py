"""Keep HACS and frontend cache versions aligned."""
import ast
import json
from pathlib import Path


def test_release_versions_match():
    component = Path(__file__).resolve().parents[1] / "custom_components" / "climado"
    version = json.loads((component / "manifest.json").read_text())["version"]
    constants = ast.parse((component / "const.py").read_text())
    cache_version = next(
        ast.literal_eval(node.value)
        for node in constants.body if isinstance(node, ast.Assign)
        if any(isinstance(target, ast.Name) and target.id == "VERSION" for target in node.targets)
    )
    assert cache_version == version
    card = (component / "frontend" / "climado-card.js").read_text()
    assert f'CLIMADO-CARD %c {version} ' in card
