import nonebot
from nonebot.adapters.milky import Adapter


nonebot.init()
driver = nonebot.get_driver()
driver.register_adapter(Adapter)
nonebot.load_plugin("nao_bot.plugin")
nonebot.load_plugin("nonebot_plugin_memes")
nonebot.load_plugin("nonebot_plugin_auto_emojimix")


if __name__ == "__main__":
    nonebot.run()
