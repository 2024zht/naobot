import json
from pathlib import Path

import pytest

from nao_bot.reactions import (
    ReactionCatalog,
    list_reaction_pack_assets,
    reaction_image_base64,
    reaction_probability,
    select_reaction_asset,
)


@pytest.mark.parametrize(
    ("scene", "context", "confidence", "expected"),
    [
        (None, "playful", 1.0, 0.0),
        ("开心", "playful", 0.5, 0.0),
        ("开心", "serious", 1.0, 0.1),
        ("开心", "casual", 1.0, 0.85),
        ("开心", "playful", 0.8, 0.76),
    ],
)
def test_reaction_probability_uses_context_and_confidence(
    scene: str | None,
    context: str,
    confidence: float,
    expected: float,
):
    assert reaction_probability(scene, context, confidence) == pytest.approx(expected)


def test_catalog_sync_adds_new_assets_without_overwriting_labels(tmp_path: Path):
    asset_root = tmp_path / "packs"
    pack = asset_root / "monthly_salary_cat"
    pack.mkdir(parents=True)
    first = pack / "01.webp"
    first.touch()
    catalog_file = tmp_path / "reaction_catalog.json"
    catalog = ReactionCatalog(catalog_file, asset_root)

    assert catalog.sync() == 1
    data = json.loads(catalog_file.read_text(encoding="utf-8"))
    data["stickers"][0]["scenes"] = ["加班", "无语"]
    catalog_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    second = pack / "02.webp"
    second.touch()
    assert catalog.sync() == 1

    data = json.loads(catalog_file.read_text(encoding="utf-8"))
    assert data["stickers"] == [
        {
            "file": "monthly_salary_cat/01.webp",
            "scenes": ["加班", "无语"],
            "enabled": True,
        },
        {
            "file": "monthly_salary_cat/02.webp",
            "scenes": [],
            "enabled": True,
        },
    ]


def test_catalog_returns_only_enabled_assets_matching_the_scene(tmp_path: Path):
    asset_root = tmp_path / "packs"
    pack = asset_root / "monthly_salary_cat"
    pack.mkdir(parents=True)
    matching = pack / "01.webp"
    disabled = pack / "02.webp"
    unlabeled = pack / "03.webp"
    for asset in (matching, disabled, unlabeled):
        asset.touch()
    catalog_file = tmp_path / "reaction_catalog.json"
    catalog_file.write_text(
        json.dumps(
            {
                "version": 1,
                "stickers": [
                    {"file": "monthly_salary_cat/01.webp", "scenes": ["无语"], "enabled": True},
                    {"file": "monthly_salary_cat/02.webp", "scenes": ["无语"], "enabled": False},
                    {"file": "monthly_salary_cat/03.webp", "scenes": [], "enabled": True},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    catalog = ReactionCatalog(catalog_file, asset_root)

    assert catalog.assets_for_scene("无语") == (matching,)


def test_catalog_rejects_assets_outside_the_pack_root(tmp_path: Path):
    asset_root = tmp_path / "packs"
    asset_root.mkdir()
    outside = tmp_path / "outside.webp"
    outside.touch()
    catalog_file = tmp_path / "reaction_catalog.json"
    catalog_file.write_text(
        '{"version":1,"stickers":[{"file":"../outside.webp",'
        '"scenes":["无语"],"enabled":true}]}',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="outside reaction pack root"):
        ReactionCatalog(catalog_file, asset_root).assets_for_scene("无语")


def test_contextual_selection_uses_matching_assets_and_avoids_recent_one(
    tmp_path: Path,
    monkeypatch,
):
    builtin_dir = tmp_path / "builtin"
    builtin_dir.mkdir()
    builtin = builtin_dir / "happy.png"
    builtin.touch()
    catalog_asset = tmp_path / "monthly.webp"
    catalog_asset.touch()
    monkeypatch.setattr("nao_bot.reactions.random.random", lambda: 0.0)

    assert (
        select_reaction_asset(
            "开心",
            "playful",
            1.0,
            builtin_dir,
            (catalog_asset,),
            recent_assets=(builtin,),
        )
        == catalog_asset
    )


def test_contextual_selection_does_not_use_unmatched_or_low_confidence_assets(
    tmp_path: Path,
):
    candidate = tmp_path / "monthly.webp"
    candidate.touch()

    assert select_reaction_asset(None, "playful", 1.0, tmp_path, (candidate,)) is None
    assert select_reaction_asset("开心", "playful", 0.5, tmp_path, (candidate,)) is None


def test_reaction_asset_can_be_embedded_as_base64(tmp_path: Path):
    asset = tmp_path / "hello.png"
    asset.write_bytes(b"sticker-bytes")

    assert reaction_image_base64(asset) == "c3RpY2tlci1ieXRlcw=="


def test_reaction_pack_lists_supported_images_in_order(tmp_path: Path):
    (tmp_path / "02.webp").touch()
    (tmp_path / "01.gif").touch()
    (tmp_path / "03.png").touch()
    (tmp_path / "notes.txt").touch()
    (tmp_path / "04.webp").mkdir()

    assert [asset.name for asset in list_reaction_pack_assets(tmp_path)] == [
        "01.gif",
        "02.webp",
        "03.png",
    ]
