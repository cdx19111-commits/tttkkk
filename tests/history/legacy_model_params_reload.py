"""Ensure the Gaia-ID parameter index refreshes after its static JSON is replaced."""
from __future__ import annotations

import json
import os
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import assets_api  # noqa: E402


class ModelParameterReloadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = pathlib.Path(self.tmp.name) / 'model_params.json'
        self.old_path = assets_api.MODEL_PARAMS_PATH
        self.old_cache = assets_api._MODEL_PARAMS_CACHE
        self.old_mtime = assets_api._MODEL_PARAMS_CACHE_MTIME_NS
        assets_api.MODEL_PARAMS_PATH = self.path
        assets_api._MODEL_PARAMS_CACHE = None
        assets_api._MODEL_PARAMS_CACHE_MTIME_NS = None

    def tearDown(self):
        assets_api.MODEL_PARAMS_PATH = self.old_path
        assets_api._MODEL_PARAMS_CACHE = self.old_cache
        assets_api._MODEL_PARAMS_CACHE_MTIME_NS = self.old_mtime
        self.tmp.cleanup()

    def test_reloads_after_registry_file_replacement(self):
        self.path.write_text(json.dumps({'models': {'233': {'params': [['CPU型号', 'A']]} }}), encoding='utf-8')
        first_mtime = 1_700_000_000_000_000_000
        os.utime(self.path, ns=(first_mtime, first_mtime))
        self.assertEqual(assets_api.model_params_for('233'), [{'name': 'CPU型号', 'value': 'A'}])

        self.path.write_text(json.dumps({'models': {
            '233': {'params': [['CPU型号', 'A']]},
            '555': {'params': [['屏幕尺寸', '6.1英寸']]},
        }}), encoding='utf-8')
        second_mtime = first_mtime + 2_000_000_000
        os.utime(self.path, ns=(second_mtime, second_mtime))
        self.assertEqual(assets_api.model_params_for('555'), [{'name': '屏幕尺寸', 'value': '6.1英寸'}])
        self.assertEqual(assets_api._MODEL_PARAMS_CACHE_MTIME_NS, second_mtime)


if __name__ == '__main__':
    unittest.main(verbosity=2)
