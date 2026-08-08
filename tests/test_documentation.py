from pathlib import Path

from nao_bot.rules import HELP_TEXT


ROOT = Path(__file__).resolve().parents[1]


def test_readme_links_feature_catalog():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "[FEATURES.md](FEATURES.md)" in readme


def test_feature_catalog_covers_help_commands():
    features = (ROOT / "FEATURES.md").read_text(encoding="utf-8")
    commands = {
        line.split(maxsplit=2)[1]
        for line in HELP_TEXT.splitlines()
        if line.startswith("@nao ")
    }

    assert commands
    assert not {command for command in commands if f"@nao {command}" not in features}
