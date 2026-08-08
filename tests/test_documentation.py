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


def test_ai_documentation_uses_natural_mention_syntax():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    features = (ROOT / "FEATURES.md").read_text(encoding="utf-8")

    assert "@机器人 问 问题" not in readme
    assert "@nao 问 问题" not in features
    assert "@nao 问 问题" not in HELP_TEXT


def test_fun_plugins_are_pinned_and_documented():
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    no_deps = (ROOT / "requirements-plugins-no-deps.txt").read_text(encoding="utf-8")
    features = (ROOT / "FEATURES.md").read_text(encoding="utf-8")

    assert "nonebot-plugin-akinator" not in requirements
    assert "nonebot-plugin-jrrp3==3.4.0" in requirements
    assert "nonebot_plugin_handle==0.4.4" in no_deps
    assert "nonebot_plugin_remake==0.4.4" in no_deps
    assert "@nao 猜人物" in features
    assert "akinator" not in features.lower()
