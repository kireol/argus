from __future__ import annotations

from PIL import Image, ImageDraw

from argus_test_creator.assets import AssetManager
from argus_test_creator.demo import MoviesDemoApp
from argus_test_creator.models import Rect
from argus_test_creator.observation import (
    AssertionSuggester,
    CaptureStore,
    FakeOCRProvider,
    compare_images,
)
from argus_test_creator.observation.diff import is_stable
from argus_test_creator.observation.ocr import group_lines


def _img(color=(0, 0, 0), size=(200, 100)):
    return Image.new("RGB", size, color)


def test_capture_store_persists_thumbnails_and_metadata(tmp_path):
    store = CaptureStore(tmp_path / "caps", thumbnail_cache=1)
    c1 = store.save(_img((255, 0, 0)), phase="after", metadata={"visible_text": []})
    c2 = store.save(_img((0, 255, 0)))
    assert (tmp_path / "caps" / f"{c1.id}.png").is_file() and c1.sha256 != c2.sha256
    assert store.metadata(c1) == {"visible_text": []} and store.metadata(c2) == {}
    thumb = store.thumbnail(c1, size=(50, 50))
    assert thumb.size[0] <= 50
    assert (tmp_path / "caps" / "thumbnails" / f"{c1.id}@50x50.png").is_file()
    store.thumbnail(c2, size=(50, 50))
    assert len(store._thumb_cache) == 1  # bounded cache
    assert store.load(c1).getpixel((0, 0)) == (255, 0, 0)
    assert len(store) == 2 and store.get(c1.id) == c1


def test_compare_images_and_stability():
    a = _img()
    b = _img()
    ImageDraw.Draw(b).rectangle((10, 10, 29, 29), fill=(255, 255, 255))
    diff = compare_images(a, b)
    assert diff.significant and not diff.major
    assert diff.changed_region == Rect(x=10, y=10, width=20, height=20)
    assert compare_images(a, a).changed_fraction == 0.0
    assert compare_images(a, _img(size=(10, 10))).changed_fraction == 1.0
    assert is_stable([a, a, a]) and not is_stable([a, b])


def test_fake_ocr_and_line_grouping():
    app = MoviesDemoApp()
    app.navigate("details")
    app.state.selected = "Batman Begins"
    meta = app.screen_metadata()
    obs = FakeOCRProvider().extract(app.render(), capture_id="c", metadata=meta)
    assert "Batman Begins" in obs.lines() and obs.provider == "fake"
    lines = group_lines(obs)
    assert any(text == "Batman Begins" and rect is not None for text, rect in lines)
    region = Rect(x=100, y=250, width=300, height=100)
    partial = FakeOCRProvider().extract(app.render(), capture_id="c", region=region,
                                        metadata=meta)
    assert "Play" in partial.lines() and "Batman Begins" not in partial.lines()


def test_suggestions_prefer_new_specific_text_and_regions():
    app = MoviesDemoApp()
    before = FakeOCRProvider().extract(app.render(), capture_id="b",
                                       metadata=app.screen_metadata())
    app.navigate("details")
    app.state.selected = "Batman Begins"
    after_img = app.render()
    after = FakeOCRProvider().extract(after_img, capture_id="a", metadata=app.screen_metadata())
    diff = compare_images(_img(size=app.size), after_img)
    candidates = AssertionSuggester().suggest(diff=diff, ocr_before=before, ocr_after=after,
                                              capture_after="a", screen_size=app.size)
    texts = [c.condition.params.get("text") for c in candidates
             if c.condition.type == "text_present"]
    assert texts[0] == "Batman Begins" and "Argus Movies" not in texts
    assert all(c.synchronize for c in candidates)
    assert AssertionSuggester().suggest(diff=compare_images(after_img, after_img),
                                        ocr_before=after, ocr_after=after,
                                        capture_after="a") == []


def test_asset_manager_crop_promote_dedup(tmp_path):
    manager = AssetManager(tmp_path / "assets", tmp_path / "ws")
    screen = tmp_path / "screen.png"
    img = _img((0, 0, 255), (300, 200))
    ImageDraw.Draw(img).rectangle((20, 20, 80, 60), fill=(255, 255, 0))
    img.save(screen)
    crop = manager.crop(screen, Rect(x=20, y=20, width=60, height=40), label="Yellow Box!")
    assert crop.parent == tmp_path / "ws" and crop.name.startswith("yellow_box_")
    asset = manager.promote(crop, label="Yellow Box!", source_capture_id="c1",
                            source_region=Rect(x=20, y=20, width=60, height=40))
    assert asset.width == 60 and asset.height == 40 and manager.exists(asset.relative_path)
    again = manager.promote(manager.crop(screen, Rect(x=20, y=20, width=60, height=40),
                                         label="other name"), label="other name")
    assert again.relative_path == asset.relative_path  # deduplicated by content
    assert len(manager.list_assets()) == 1
    manager.clear_workspace()
    assert not (tmp_path / "ws").exists()


def test_asset_manager_rejects_out_of_bounds(tmp_path):
    import pytest

    from argus_test_creator.core.errors import AssetError

    manager = AssetManager(tmp_path / "assets", tmp_path / "ws")
    with pytest.raises(AssetError):
        manager.crop(_img(), Rect(x=190, y=90, width=50, height=50))
