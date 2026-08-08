import nonebot
from nonebot.adapters.milky import Adapter
from nonebot.matcher import matchers
from nonebot.rule import to_me


nonebot.init()
driver = nonebot.get_driver()
driver.register_adapter(Adapter)
nonebot.load_plugin("nao_bot.plugin")
nonebot.load_plugin("nonebot_plugin_memes")
for matcher_group in matchers.values():
    for matcher in matcher_group:
        if matcher.plugin_name == "nonebot_plugin_memes":
            matcher.rule &= to_me()
            matcher.block = True
nonebot.load_plugin("nonebot_plugin_auto_emojimix")


if __name__ == "__main__":
    nonebot.run()
