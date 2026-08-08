from importlib import import_module

import nonebot
from nonebot.adapters.milky import Adapter
from nonebot.matcher import matchers
from nonebot.rule import Rule, to_me


def configure_plugin_matchers(
    plugin_name: str,
    group_rule: Rule | None = None,
    require_mention: bool = False,
) -> None:
    for matcher_group in matchers.values():
        for matcher in matcher_group:
            if matcher.plugin_name != plugin_name:
                continue
            if group_rule is not None:
                matcher.rule &= group_rule
            if require_mention:
                matcher.rule &= to_me()
            matcher.block = True


nonebot.init()
driver = nonebot.get_driver()
driver.register_adapter(Adapter)
nonebot.load_plugin("nao_bot.plugin")
nonebot.load_plugin("nonebot_plugin_memes")
configure_plugin_matchers("nonebot_plugin_memes", require_mention=True)
nonebot.load_plugin("nonebot_plugin_auto_emojimix")

is_test_group = getattr(import_module("nao_bot.plugin"), "is_test_group")
test_group_rule = Rule(is_test_group)
for plugin_name in (
    "nonebot_plugin_jrrp3",
    "nonebot_plugin_handle",
    "nonebot_plugin_remake",
):
    if nonebot.load_plugin(plugin_name) is None:
        raise RuntimeError(f"Failed to load plugin: {plugin_name}")
    configure_plugin_matchers(plugin_name, test_group_rule)


@driver.on_startup
async def configure_delayed_plugin_matchers() -> None:
    configure_plugin_matchers("nonebot_plugin_jrrp3", test_group_rule, True)


if __name__ == "__main__":
    nonebot.run()
