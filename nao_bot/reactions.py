import base64
import random
from pathlib import Path


REACTION_COOLDOWN_SECONDS = 90
REACTION_PACK_EXTENSIONS = frozenset({".gif", ".png", ".webp"})
REACTION_PHRASES = (
    ("surprise", ("没想到", "居然", "竟然", "太意外", "真意外", "哇")),
    ("sorry", ("抱歉", "对不起", "不好意思", "很遗憾", "我理解错")),
    ("celebrate", ("恭喜", "祝贺", "成功了", "太棒了", "值得庆祝")),
    ("laugh", ("哈哈", "笑死", "太好笑", "真好笑", "忍不住笑")),
    ("cheer", ("加油", "别放弃", "坚持下去", "你一定可以", "相信自己")),
    ("hello", ("你好", "您好", "早上好", "下午好", "晚上好", "很高兴见到")),
    ("thinking", ("让我想想", "需要仔细分析", "值得思考", "我琢磨一下", "需要权衡")),
    ("happy", ("很开心", "真开心", "很高兴", "真不错", "好消息", "做得很好")),
)


def reaction_name_for_text(text: str) -> str | None:
    normalized = text.casefold()
    for name, phrases in REACTION_PHRASES:
        if any(phrase in normalized for phrase in phrases):
            return name
    return None


def select_reaction_asset(
    text: str,
    user_id: int,
    last_sent: dict[int, float],
    asset_dir: Path,
    *,
    now: float,
    cooldown_seconds: int = REACTION_COOLDOWN_SECONDS,
) -> Path | None:
    name = reaction_name_for_text(text)
    if name is None:
        return None

    previous = last_sent.get(user_id)
    if previous is not None and now - previous < cooldown_seconds:
        return None

    asset = asset_dir / f"{name}.png"
    if not asset.is_file():
        return None

    return asset


def record_reaction_sent(last_sent: dict[int, float], user_id: int, *, now: float) -> None:
    last_sent[user_id] = now


def reaction_image_base64(asset: Path) -> str:
    return base64.b64encode(asset.read_bytes()).decode("ascii")


def list_reaction_pack_assets(asset_dir: Path) -> tuple[Path, ...]:
    if not asset_dir.is_dir():
        return ()
    return tuple(
        path
        for path in sorted(asset_dir.iterdir())
        if path.is_file() and path.suffix.casefold() in REACTION_PACK_EXTENSIONS
    )


def select_random_reaction_asset(
    user_id: int,
    last_sent: dict[int, float],
    asset_dir: Path,
    *,
    now: float,
    chance: float,
    cooldown_seconds: int = REACTION_COOLDOWN_SECONDS,
) -> Path | None:
    previous = last_sent.get(user_id)
    if previous is not None and now - previous < cooldown_seconds:
        return None
    if random.random() >= chance:
        return None

    assets = list_reaction_pack_assets(asset_dir)
    return random.choice(assets) if assets else None
