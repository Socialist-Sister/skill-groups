import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from sg import errors, util


class TestReadJson(unittest.TestCase):
    def test_reads_back_what_was_written(self):
        data = {"name": "测试", "items": [1, 2, 3], "nested": {"ok": True}}
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "data.json"
            path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            self.assertEqual(util.read_json(path), data)

    def test_missing_file_raises_env_error(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(errors.EnvError):
                util.read_json(Path(td) / "nope.json")

    def test_invalid_json_raises_env_error(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "bad.json"
            path.write_text("{not json", encoding="utf-8")
            with self.assertRaises(errors.EnvError):
                util.read_json(path)


class TestWriteJson(unittest.TestCase):
    def test_creates_parent_dirs_and_roundtrips(self):
        data = {"x": 1, "列表": ["甲", "乙"]}
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "a" / "b" / "file.json"
            util.write_json(path, data)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), data)

    def test_unwritable_parent_raises_env_error(self):
        with tempfile.TemporaryDirectory() as td:
            blocker = Path(td) / "blocker"
            blocker.write_text("not a dir", encoding="utf-8")
            with self.assertRaises(errors.EnvError):
                util.write_json(blocker / "sub" / "file.json", {"x": 1})


class TestEnsureDir(unittest.TestCase):
    def test_creates_and_returns_path(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "new" / "dir"
            result = util.ensure_dir(path)
            self.assertIsInstance(result, Path)
            self.assertTrue(path.is_dir())

    def test_existing_file_raises_env_error(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "file.txt"
            path.write_text("hi", encoding="utf-8")
            with self.assertRaises(errors.EnvError):
                util.ensure_dir(path)


class TestSha256Id(unittest.TestCase):
    def test_same_input_same_result(self):
        self.assertEqual(util.sha256_id("hello"), util.sha256_id("hello"))

    def test_different_input_different_result(self):
        self.assertNotEqual(util.sha256_id("hello"), util.sha256_id("world"))

    def test_length_10_hex(self):
        result = util.sha256_id("whatever")
        self.assertEqual(len(result), 10)
        self.assertTrue(all(c in "0123456789abcdef" for c in result))


class TestCopyTree(unittest.TestCase):
    def test_nested_copy_is_byte_identical(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src = root / "src"
            dst = root / "dst"
            (src / "sub" / "deep").mkdir(parents=True)
            (src / "a.txt").write_bytes(b"hello world")
            (src / "sub" / "b.bin").write_bytes(bytes(range(256)))
            (src / "sub" / "deep" / "c.txt").write_text("nested", encoding="utf-8")
            util.copy_tree(src, dst)
            for rel in ("a.txt", "sub/b.bin", "sub/deep/c.txt"):
                self.assertEqual(
                    (dst / rel).read_bytes(), (src / rel).read_bytes(), msg=rel
                )

    def test_missing_src_raises_env_error(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(errors.EnvError):
                util.copy_tree(Path(td) / "missing", Path(td) / "dst")


class TestWhich(unittest.TestCase):
    def test_existing_command_found(self):
        name = os.path.basename(sys.executable)
        self.assertIsNotNone(util.which(name))

    def test_nonexistent_command_returns_none(self):
        self.assertIsNone(util.which("definitely-not-a-real-cmd-xyz"))


if __name__ == "__main__":
    unittest.main()
