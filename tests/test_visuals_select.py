"""Image selection tests (offline, uses generated files)."""

import json
import os
import sys

import pytest
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.config import load_settings
from pipeline.visuals import ATTRIBUTIONS_FILE, select_images


def make_settings(per_segment=2):
    os.environ["IMAGES_PER_SEGMENT"] = str(per_segment)
    try:
        return load_settings()
    finally:
        os.environ.pop("IMAGES_PER_SEGMENT", None)


def put_image(path, size):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    Image.new("RGB", size, (100, 100, 100)).save(path)


class TestSelect:
    def test_largest_images_win(self, tmp_path):
        root = str(tmp_path / "candidates")
        put_image(f"{root}/seg_000/00_loc.jpg", (400, 300))
        put_image(f"{root}/seg_000/01_openverse.jpg", (1600, 900))
        put_image(f"{root}/seg_000/02_wikimedia.jpg", (800, 600))
        with open(f"{root}/{ATTRIBUTIONS_FILE}", "w") as fh:
            json.dump({"seg_000/01_openverse.jpg": "big one"}, fh)
        sets, attrs = select_images(root, 1, make_settings(per_segment=2))
        names = [os.path.basename(p) for p in sets[0]]
        assert names == ["01_openverse.jpg", "02_wikimedia.jpg"]
        assert "big one" in attrs

    def test_review_deletion_respected(self, tmp_path):
        # After a human deletes files, only what remains is used.
        root = str(tmp_path / "candidates")
        put_image(f"{root}/seg_000/00_loc.jpg", (1600, 900))
        sets, _ = select_images(root, 1, make_settings(per_segment=3))
        assert len(sets[0]) == 1

    def test_empty_segment_reuses_previous(self, tmp_path):
        root = str(tmp_path / "candidates")
        put_image(f"{root}/seg_000/00_loc.jpg", (1600, 900))
        os.makedirs(f"{root}/seg_001", exist_ok=True)   # reviewed away entirely
        sets, attrs = select_images(root, 2, make_settings())
        assert sets[1] == [sets[0][0]]
        assert any("missing image" in a for a in attrs)

    def test_empty_first_segment_raises(self, tmp_path):
        root = str(tmp_path / "candidates")
        os.makedirs(f"{root}/seg_000", exist_ok=True)
        with pytest.raises(RuntimeError):
            select_images(root, 1, make_settings())

    def test_operator_added_custom_image(self, tmp_path):
        # The review flow allows dropping your own files into a segment dir.
        root = str(tmp_path / "candidates")
        put_image(f"{root}/seg_000/my_own_photo.png", (1920, 1080))
        sets, attrs = select_images(root, 1, make_settings())
        assert os.path.basename(sets[0][0]) == "my_own_photo.png"
        assert attrs == ["seg_000/my_own_photo.png"]
