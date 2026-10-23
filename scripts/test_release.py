"""Exercise the release shell entry point with stubbed Git/GitHub commands; no network or commits."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import zipfile

SCRIPTS = Path(__file__).resolve().parent
COMMIT = "a" * 40


class ReleaseShellTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        scripts = self.root / "scripts"
        scripts.mkdir()
        for name in ("release.sh", "model.py"):
            shutil.copy2(SCRIPTS / name, scripts / name)
        self.inputs = self.root / "build/inputs"
        self.inputs.mkdir(parents=True)
        for variant in ("all", "en"):
            self.bundle(variant)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        git = self.bin / "git"
        git.write_text('''#!/bin/sh
case "$*" in
  'rev-parse HEAD') printf '%s\\n' aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa ;;
  'status --porcelain --untracked-files=all') printf '%s' "${MOCK_DIRTY:-}" ;;
  *) exit 90 ;;
esac
''')
        git.chmod(0o755)
        gh = self.bin / "gh"
        gh.write_text('''#!/usr/bin/env python3
import json,os,sys
from pathlib import Path
args=sys.argv[1:]
with Path(os.environ['GH_LOG']).open('a') as log:
    log.write(json.dumps(args)+'\\n')
if args[0]=='api':
    endpoint=args[1]
    if 'time-whisper/commits/' in endpoint: print('b'*40)
    elif '/commits/' in endpoint: print('a'*40)
    elif '/releases?' in endpoint: print(os.environ.get('MOCK_RELEASE',''))
    elif '/git/matching-refs/' in endpoint: print(os.environ.get('MOCK_TAG',''))
    else: sys.exit(91)
''')
        gh.chmod(0o755)
        self.log = self.root / "gh.jsonl"

    def bundle(self, variant, library="0.6.2"):
        meta = {"version": "0.7.0", "variant": variant, "library_version": library,
                "library_source_commit": "b" * 40, "release_ready": True, "system_spelling": True, "qualification": "make check passed"}
        with zipfile.ZipFile(self.inputs / f"model-{variant}-0.7.0.zip", "w") as archive:
            archive.writestr(f"{variant}/metadata.json", json.dumps(meta))

    def invoke(self, *args, **environment):
        return subprocess.run(["sh", str(self.root / "scripts/release.sh"), *args],
                              cwd=self.root / "bin", text=True, capture_output=True,
                              env={**os.environ, "PATH": str(self.bin) + os.pathsep + os.environ["PATH"],
                                   "GH_LOG": str(self.log), **environment})

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def test_dry_run_makes_no_github_calls(self):
        result = self.invoke("--dry-run", "0.7.0")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls(), [])
        self.assertIn("library_version=0.6.2", result.stdout)
        self.assertIn("gh workflow run release.yml", result.stdout)

    def test_dispatch_uses_library_from_archives_and_actual_checksums(self):
        result = self.invoke("0.7.0")
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.calls()
        dispatch = next(call for call in calls if call[:2] == ["workflow", "run"])
        self.assertIn("library_version=0.6.2", dispatch)
        self.assertIn("source_ref=" + COMMIT, dispatch)
        for variant in ("all", "en"):
            digest = hashlib.sha256((self.inputs / f"model-{variant}-0.7.0.zip").read_bytes()).hexdigest()
            self.assertIn(f"{variant}_sha256={digest}", dispatch)
        create = next(call for call in calls if call[:2] == ["release", "create"])
        self.assertIn("--draft", create)
        self.assertFalse(any(call[:2] == ["release", "edit"] for call in calls))

    def test_existing_draft_can_resume_upload_and_dispatch(self):
        result = self.invoke("0.7.0", MOCK_RELEASE="true")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(any(call[:2] == ["release", "create"] for call in self.calls()))
        self.assertTrue(any(call[:2] == ["workflow", "run"] for call in self.calls()))

    def test_published_release_is_never_modified(self):
        result = self.invoke("0.7.0", MOCK_RELEASE="false")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("already published", result.stderr)
        self.assertTrue(all(call[0] == "api" for call in self.calls()))

    def test_existing_tag_is_never_replaced(self):
        result = self.invoke("0.7.0", MOCK_RELEASE="true", MOCK_TAG="c" * 40)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("already has a tag", result.stderr)
        self.assertTrue(all(call[0] == "api" for call in self.calls()))

    def test_mismatched_variants_fail_before_github(self):
        self.bundle("en", library="0.6.3")
        result = self.invoke("0.7.0")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("same library version", result.stderr)
        self.assertEqual(self.calls(), [])

    def test_missing_inputs_fail_before_github(self):
        for path in self.inputs.glob("*.zip"):
            path.unlink()
        result = self.invoke("0.7.0")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("qualify first", result.stderr)
        self.assertEqual(self.calls(), [])

    def test_all_only_uploads_and_dispatches_only_all(self):
        (self.inputs / "model-en-0.7.0.zip").unlink()
        result = self.invoke("0.7.0")
        self.assertEqual(result.returncode, 0, result.stderr)
        upload = next(call for call in self.calls() if call[:2] == ["release", "upload"])
        self.assertIn(str(self.inputs / "model-all-0.7.0.zip"), upload)
        self.assertFalse(any("model-en-" in arg for arg in upload))
        dispatch = next(call for call in self.calls() if call[:2] == ["workflow", "run"])
        self.assertFalse(any(arg.startswith("en_sha256=") for arg in dispatch))

    def test_custom_relative_input_path_with_spaces_is_used(self):
        custom = self.root / "downloads with spaces"
        self.inputs.rename(custom)
        result = self.invoke("0.7.0", "../downloads with spaces")
        self.assertEqual(result.returncode, 0, result.stderr)
        upload = next(call for call in self.calls() if call[:2] == ["release", "upload"])
        self.assertTrue(any(Path(arg).resolve() == (custom / "model-all-0.7.0.zip").resolve() for arg in upload))

    def test_working_copy_qualification_cannot_be_uploaded(self):
        path = self.inputs / "model-all-0.7.0.zip"
        with zipfile.ZipFile(path) as archive:
            metadata = json.loads(archive.read("all/metadata.json"))
        metadata["release_ready"] = False
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("all/metadata.json", json.dumps(metadata))
        result = self.invoke("0.7.0")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("working copy", result.stderr)
        self.assertEqual(self.calls(), [])

    def test_uncommitted_packaging_is_not_released(self):
        result = self.invoke("0.7.0", MOCK_DIRTY=" M scripts/model.py")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Commit the packaging changes", result.stderr)
        self.assertEqual(self.calls(), [])


if __name__ == "__main__":
    unittest.main()
