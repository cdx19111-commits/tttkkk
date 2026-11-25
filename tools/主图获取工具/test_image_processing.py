import csv
import unittest
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from zipfile import ZipFile

import numpy as np
from PIL import Image, ImageDraw

import app
from image_processing import FRONT_AND_BACK, POSSIBLE_PAIR, SINGLE_VIEW, prepare, watermark_may_overlap, watermark_template


class WatermarkReviewTests(unittest.TestCase):
    def setUp(self):
        self.photo = Image.new("RGB", (1000, 750), "white")
        ImageDraw.Draw(self.photo).rounded_rectangle((240, 20, 792, 720), 36, fill="#263844")
        self.crop = (230, 10, 800, 730)

    def test_clean_margin_does_not_require_review(self):
        self.assertFalse(watermark_may_overlap(self.photo, self.crop))

    def test_watermark_outside_crop_does_not_require_review(self):
        self.photo.paste(watermark_template(), (850, 620))
        self.assertFalse(watermark_may_overlap(self.photo, self.crop))
        self.assertTrue(watermark_may_overlap(self.photo, (230, 10, 920, 730)))

    def test_watermark_over_dark_subject_requires_review(self):
        template = 255 - np.asarray(watermark_template(), dtype=np.int16)
        overlay = np.asarray(self.photo.crop((700, 600, 770, 650)), dtype=np.int16) + template[:, :, None]
        self.photo.paste(Image.fromarray(overlay.astype(np.uint8)), (700, 600))
        self.assertTrue(watermark_may_overlap(self.photo, self.crop))

    def test_small_margin_requires_review_without_clear_match(self):
        ImageDraw.Draw(self.photo).rectangle((780, 30, 929, 720), fill="#263844")
        self.assertTrue(watermark_may_overlap(self.photo, (230, 10, 935, 730)))


class ViewRecognitionTests(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)

    @staticmethod
    def draw_back(draw, x):
        draw.rounded_rectangle((x, 80, x+233, 570), 20, fill="#f6f6f6", outline="#b1b1b1", width=4)
        for y in (100, 160, 220):
            draw.ellipse((x+18, y, x+65, y+47), fill="#18202c", outline="#7da3c2", width=5)
            draw.ellipse((x+30, y+12, x+52, y+35), fill="#304258", outline="#ccd4d8", width=3)

    def make_photo(self, name, *, front_at=None, second_back=False):
        photo = Image.new("RGB", (840, 650), "white")
        draw = ImageDraw.Draw(photo)
        self.draw_back(draw, 190)
        if second_back:
            self.draw_back(draw, 430)
        if front_at is not None:
            draw.rounded_rectangle((front_at, 80, front_at+233, 570), 20, fill="#10151d")
            draw.rounded_rectangle((front_at+10, 90, front_at+223, 560), 13, fill="#a2c7d9")
            draw.text((front_at+105, 150), "10:30", fill="white")
        path = self.folder / name
        photo.save(path)
        return path

    def test_detects_a_pale_back_beside_an_overlapping_or_separate_screen(self):
        for front_at in (365, 430):
            with self.subTest(front_at=front_at):
                source = self.make_photo("pair.png", front_at=front_at)
                metadata, _ = prepare(source, self.folder / "result.jpg")
                self.assertEqual(metadata["view"], FRONT_AND_BACK)
                self.assertFalse(metadata["review"])

    def test_single_view_is_discarded(self):
        source = self.make_photo("single.png")
        self.assertIsNone(prepare(source, self.folder / "single.jpg"))

    def test_unconfirmed_pair_is_kept_and_single_is_dropped(self):
        single = self.make_photo("single.png")
        pair = self.make_photo("two-backs.png", second_back=True)
        images = [{"id":str(index), "url":"https://detail.zol.com.cn/", "local":str(path)}
                  for index, path in enumerate((single, pair), 1)]
        data = {"status":"已匹配", "colors":[{"name":"白色", "images":images}]}
        with patch.object(app, "DATA", self.folder):
            result = app.process_picture_set("abcdef123456", {"id":1}, data)
        color = result["colors"][0]
        self.assertEqual([item["view"] for item in color["candidates"]], [POSSIBLE_PAIR])
        self.assertEqual(color["status"], "待复核")
        self.assertIsNone(color["selected"])

    def test_exports_untagged_dual_candidates_only(self):
        batch = "abcdef123456"
        url = "https://detail.zol.com.cn/"
        single = {"id":"1", "url":url, "local":str(self.make_photo("single.png"))}
        pair = {"id":"2", "url":url, "local":str(self.make_photo("pair.png", second_back=True))}
        data = {"status":"已匹配", "colors":[{"name":"白色", "images":[single]}],
                "unlabeled":[single, pair, pair]}
        row = {"id":1, "model":"测试机", "brand":""}
        with patch.object(app, "DATA", self.folder):
            row.update(app.process_picture_set(batch, row, data))
            result = BytesIO()
            app.export_zip({"id":batch, "rows":[row]}, result)
        # 白色只有单面 → 缺图；未标色双面可导出
        self.assertEqual(row["colors"][0]["status"], "缺图")
        self.assertEqual([c["selected"] for c in row["colors"] if c.get("selected")], ["2"])
        with ZipFile(result) as archive:
            rows = list(csv.DictReader(archive.read("采集结果.csv").decode("utf-8-sig").splitlines()))
            self.assertEqual([r["颜色"] for r in rows], ["白色", ""])
            self.assertEqual([r["文件"] for r in rows], ["", "0001_测试机/未标色/01.jpg"])
            self.assertEqual(rows[1]["视角"], POSSIBLE_PAIR)

    def test_zip_saves_review_candidates_and_keeps_the_review_status(self):
        batch = "abcdef123456"
        row = {"id":1, "model":"测试机", "brand":""}
        url = "https://detail.zol.com.cn/"
        source = self.make_photo("pair.png", second_back=True)
        data = {"status":"已匹配", "colors":[
            {"name":"白色", "images":[{"id":"1", "url":url, "local":str(source)}]},
            {"name":"黑色", "images":[], "reason":"该颜色缺少可核实的图库标签"},
        ]}
        with patch.object(app, "DATA", self.folder):
            row.update(app.process_picture_set(batch, row, data))
            candidate = row["colors"][0]["candidates"][0]
            row["colors"].append({"name":"", "number":1, "status":"待复核",
                                  "candidates":[candidate], "selected":None, "reason":"核对水印"})
            result = BytesIO()
            app.export_zip({"id":batch, "rows":[row]}, result)
        with ZipFile(result) as archive:
            rows = list(csv.DictReader(archive.read("采集结果.csv").decode("utf-8-sig").splitlines()))
            self.assertEqual([r["状态"] for r in rows], ["待复核", "待复核", "待复核"])
            self.assertEqual([r["颜色"] for r in rows], ["白色", "黑色", ""])
            self.assertEqual([r["文件"] for r in rows], [
                "0001_测试机/待复核/白色.jpg", "", "0001_测试机/待复核/未标色/01.jpg",
            ])
            self.assertEqual(rows[2]["来源"], url)
            self.assertEqual(rows[2]["备注"], "核对水印")

    def test_untagged_promo_graphic_is_not_exported_as_a_phone(self):
        photo = Image.new("RGB", (840, 650), "white")
        brush = ImageDraw.Draw(photo)
        brush.polygon(((415, 83), (663, 302), (440, 459), (181, 283)), fill="#4384e5")
        brush.ellipse((183, 495, 657, 563), fill="#7764dc")
        source = self.folder / "promotion.png"
        photo.save(source)
        self.assertIsNone(prepare(source, self.folder / "result.jpg", require_product=True))


if __name__ == "__main__":
    unittest.main()
