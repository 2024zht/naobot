from pathlib import Path

import yaml

from nao_bot.rules import HELP_TEXT


ROOT = Path(__file__).resolve().parents[1]


def test_readme_links_feature_catalog():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "[FEATURES.md](FEATURES.md)" in readme


def test_lagrange_deployment_has_required_login_configuration():
    compose = yaml.safe_load((ROOT / "compose.yaml").read_text(encoding="utf-8"))
    example_env = (ROOT / ".env.example").read_text(encoding="utf-8")
    environment = compose["services"]["lagrange-milky"]["environment"]

    assert environment["Lagrange__Protocol__Signer__Token"].startswith(
        "${LAGRANGE_SIGNER_TOKEN:"
    )
    assert environment["Milky__HttpServer__Host"] == "*"
    assert "LAGRANGE_SIGNER_TOKEN=" in example_env


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


def test_ai_reaction_pack_is_complete_and_documented():
    features = (ROOT / "FEATURES.md").read_text(encoding="utf-8")
    reaction_dir = ROOT / "nao_bot" / "assets" / "reactions"

    assert "90 秒冷却" in features
    assert "只用于 AI 成功回答" in features
    for name in ("hello", "happy", "laugh", "thinking", "cheer", "celebrate", "sorry", "surprise"):
        assert (reaction_dir / f"{name}.png").is_file()


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
