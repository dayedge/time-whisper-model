"""Release-input safety checks that do not require trained weights."""
import hashlib
import json
import argparse
import subprocess
from types import SimpleNamespace
from unittest.mock import patch
import model
from pathlib import Path
import tempfile
import unittest
import zipfile

from model import unpack, validate


class ModelInputTests(unittest.TestCase):
    def test_changed_archive_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "input.zip"
            source.write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "checksum"):
                unpack(source, "0" * 64, Path(temporary) / "output")

    def test_archive_cannot_escape_destination(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "input.zip"
            with zipfile.ZipFile(source, "w") as archive:
                archive.writestr("../outside", "bad")
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            with self.assertRaisesRegex(ValueError, "Unsafe"):
                unpack(source, digest, Path(temporary) / "output")
            self.assertFalse((Path(temporary) / "outside").exists())

    def test_missing_real_model_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(ValueError, "Missing"):
                validate(Path(temporary), ["en"])

    def test_wrong_language_variant_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            for name in ["TimeWhisperTagger.mlpackage", "tokenizer.json", "tokenizer_config.json", "labels.json", "reference.json"]:
                (folder / name).write_text("{}")
            (folder / "export.json").write_text(json.dumps({"languages": ["en", "pl"], "max_pieces": 128}))
            with self.assertRaisesRegex(ValueError, "languages"):
                validate(folder, ["en"])


class QualificationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.library = self.root / "library"
        self.library.mkdir()
        (self.library / "build.gradle.kts").write_text('version = "0.7.0"\n')
        self.folder = self.root / "downloaded export"
        self.folder.mkdir()
        (self.folder / "TimeWhisperTagger.mlpackage").mkdir()
        (self.folder / "TimeWhisperTagger.mlpackage/Manifest.json").write_text("{}")
        labels = {"base": "test", "labels": ["T"], "intents": ["task", "event", "none"], "urgencies": ["none", "L", "M", "H"]}
        (self.folder / "labels.json").write_text(json.dumps(labels))
        for name in ("tokenizer.json", "tokenizer_config.json"):
            (self.folder / name).write_text("{}")
        (self.folder / "reference.json").write_text(json.dumps([{"input_ids": [0], "labels": [[1]], "intent": [1, 0, 0], "urgency": [1, 0, 0, 0]}]))
        self.args = SimpleNamespace(library=self.library, library_version=None, version=None,
                                    training_commit=None, all=self.folder, en=None)
        self.state = {"commit": "a" * 40, "clean": True, "sha256": "b" * 64}
        self.addCleanup(patch.stopall)
        patch.object(model, "ROOT", self.root).start()
        self.state_mock = patch.object(model, "source_state", return_value=self.state).start()
        patch.object(model.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout="a" * 40 + "\n")).start()
        self.check = patch.object(model, "run").start()

    def test_legacy_all_export_qualifies_without_english_or_invented_metadata(self):
        release = model.qualify(self.args)
        self.assertEqual(release, "0.7.0")
        destination = self.root / "build/inputs/model-all-0.7.0.zip"
        with zipfile.ZipFile(destination) as zipped:
            meta = json.loads(zipped.read("all/metadata.json"))
        self.assertIsNone(meta["export"])
        self.assertIsNone(meta["training_commit"])
        self.assertTrue(meta["release_ready"])
        self.assertFalse((self.folder / "export.json").exists())
        self.assertFalse((destination.parent / "model-en-0.7.0.zip").exists())
        self.assertIn("SPELLING=1", self.check.call_args.args)
        self.assertIn("CORPORA=en pl", self.check.call_args.args)

    def test_working_copy_results_are_marked_local_only(self):
        self.state["clean"] = False
        model.qualify(self.args)
        with zipfile.ZipFile(self.root / "build/inputs/model-all-0.7.0.zip") as zipped:
            meta = json.loads(zipped.read("all/metadata.json"))
        self.assertFalse(meta["release_ready"])
        self.assertEqual(meta["library_source_sha256"], self.state["sha256"])

    def test_failed_system_spelling_gate_produces_no_upload_zip(self):
        self.check.side_effect = subprocess.CalledProcessError(2, ["make", "check"])
        with self.assertRaises(subprocess.CalledProcessError):
            model.qualify(self.args)
        self.assertFalse((self.root / "build/inputs/model-all-0.7.0.zip").exists())

    def test_all_only_selection_removes_stale_english_zip_for_same_version(self):
        inputs = self.root / "build/inputs"
        inputs.mkdir(parents=True)
        stale = inputs / "model-en-0.7.0.zip"
        stale.write_bytes(b"previous selection")
        model.qualify(self.args)
        self.assertFalse(stale.exists())

    def test_all_only_build_exposes_no_english_product_or_old_release_asset(self):
        model.qualify(self.args)
        zipped = self.root / "build/inputs/model-all-0.7.0.zip"
        verified = self.root / "build/verified"
        model.unpack(zipped, hashlib.sha256(zipped.read_bytes()).hexdigest(), verified)
        framework = self.root / "fixture/TimeWhisperModelAll.xcframework"
        framework.mkdir(parents=True)
        (framework / "Info.plist").write_text("fixture")
        old = self.root / "build/distributions/TimeWhisperModelEnglish-old.zip"
        old.parent.mkdir(parents=True)
        old.write_bytes(b"previous build")
        with patch.object(model, "build_framework", return_value=framework) as build:
            model.build(SimpleNamespace(inputs=verified, version="0.7.0", library_version="0.7.0"))
        self.assertEqual(build.call_count, 1)
        for package in ("distributions", "package"):
            manifest = (self.root / f"build/{package}/Package.swift").read_text()
            self.assertIn('name: "TimeWhisperModelAll"', manifest)
            self.assertNotIn("TimeWhisperModelEnglish", manifest)
        self.assertFalse(old.exists())

    def test_no_variant_is_an_error(self):
        self.args.all = None
        with self.assertRaisesRegex(ValueError, "at least one"):
            model.qualify(self.args)


if __name__ == "__main__":
    unittest.main()
