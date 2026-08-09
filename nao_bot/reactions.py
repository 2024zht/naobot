import base64
import json
import os
import random
from pathlib import Path
from typing import Iterable


REACTION_PACK_EXTENSIONS = frozenset({".gif", ".png", ".webp"})
BUILTIN_REACTION_SCENES = {
    "开心": "happy",
    "震惊": "surprise",
    "鼓励": "cheer",
    "道歉": "sorry",
    "问候": "hello",
    "思考": "thinking",
    "庆祝": "celebrate",
    "好笑": "laugh",
}
REACTION_CONTEXT_CHANCES = {
    "serious": 0.1,
    "casual": 0.45,
    "playful": 0.9,
}
MIN_REACTION_CONFIDENCE = 0.6


class ReactionCatalog:
    def __init__(self, path: Path, asset_root: Path):
        self.path = path
        self.asset_root = asset_root

    def _read(self) -> dict:
        if not self.path.is_file():
            return {"version": 1, "stickers": []}
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("version") != 1:
            raise ValueError("reaction catalog version must be 1")
        if not isinstance(data.get("stickers"), list):
            raise ValueError("reaction catalog stickers must be a list")
        return data

    def _asset_path(self, relative: str) -> Path:
        root = self.asset_root.resolve()
        asset = (root / relative).resolve()
        if not asset.is_relative_to(root):
            raise ValueError("reaction asset is outside reaction pack root")
        return asset

    def _validate_entry(self, entry: object) -> tuple[str, tuple[str, ...], bool]:
        if not isinstance(entry, dict):
            raise ValueError("reaction catalog entry must be an object")
        relative = entry.get("file")
        scenes = entry.get("scenes")
        enabled = entry.get("enabled", True)
        if not isinstance(relative, str) or not relative.strip():
            raise ValueError("reaction catalog file must be a relative path")
        if not isinstance(scenes, list) or not all(isinstance(scene, str) for scene in scenes):
            raise ValueError("reaction catalog scenes must be a list of strings")
        if not isinstance(enabled, bool):
            raise ValueError("reaction catalog enabled must be boolean")
        return relative, tuple(scene.strip() for scene in scenes if scene.strip()), enabled

    def _write(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.tmp")
        temporary.write_text(
            f"{json.dumps(data, ensure_ascii=False, indent=2)}\n",
            encoding="utf-8",
        )
        os.replace(temporary, self.path)

    def sync(self) -> int:
        data = self._read()
        known: set[str] = set()
        for entry in data["stickers"]:
            relative, _, _ = self._validate_entry(entry)
            self._asset_path(relative)
            known.add(Path(relative).as_posix())

        discovered = []
        if self.asset_root.is_dir():
            for asset in sorted(self.asset_root.rglob("*")):
                if not asset.is_file() or asset.suffix.casefold() not in REACTION_PACK_EXTENSIONS:
                    continue
                relative = asset.relative_to(self.asset_root).as_posix()
                if relative not in known:
                    discovered.append({"file": relative, "scenes": [], "enabled": True})

        if discovered:
            data["stickers"].extend(discovered)
            self._write(data)
        elif not self.path.exists():
            self._write(data)
        return len(discovered)

    def assets_for_scene(self, scene: str) -> tuple[Path, ...]:
        assets: list[Path] = []
        seen: set[Path] = set()
        for entry in self._read()["stickers"]:
            relative, scenes, enabled = self._validate_entry(entry)
            asset = self._asset_path(relative)
            if (
                enabled
                and scene in scenes
                and asset.is_file()
                and asset.suffix.casefold() in REACTION_PACK_EXTENSIONS
                and asset not in seen
            ):
                assets.append(asset)
                seen.add(asset)
        return tuple(assets)


def reaction_probability(scene: str | None, context: str, confidence: float) -> float:
    if scene is None or confidence < MIN_REACTION_CONFIDENCE:
        return 0.0
    return REACTION_CONTEXT_CHANCES.get(context, 0.0) * min(max(confidence, 0.0), 1.0)


def select_reaction_asset(
    scene: str | None,
    context: str,
    confidence: float,
    builtin_dir: Path,
    catalog_assets: Iterable[Path],
    *,
    recent_assets: Iterable[Path] = (),
) -> Path | None:
    chance = reaction_probability(scene, context, confidence)
    if chance == 0:
        return None

    candidates: list[Path] = []
    builtin_name = BUILTIN_REACTION_SCENES.get(scene or "")
    if builtin_name:
        builtin = builtin_dir / f"{builtin_name}.png"
        if builtin.is_file():
            candidates.append(builtin)
    for asset in catalog_assets:
        if asset.is_file() and asset not in candidates:
            candidates.append(asset)
    if not candidates or random.random() >= chance:
        return None

    recent = set(recent_assets)
    fresh = [asset for asset in candidates if asset not in recent]
    return random.choice(fresh or candidates)


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
