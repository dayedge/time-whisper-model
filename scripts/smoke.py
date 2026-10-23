"""Build independent consumers, verify runtime bundles, and check the embedded model against PyTorch."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile

from model import ROOT, VARIANTS, present_variants, run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library", required=True, type=Path)
    parser.add_argument("--inputs", type=Path, default=ROOT / "build/verified")
    args = parser.parse_args()
    for variant in present_variants(args.inputs):
        module, languages = VARIANTS[variant]
        with tempfile.TemporaryDirectory(prefix="time-whisper-model-consumer-") as temporary:
            consumer = Path(temporary)
            source = consumer / "Sources/Smoke"
            source.mkdir(parents=True)
            (source / "main.swift").write_text((ROOT / "Tests/Smoke.swift").read_text().replace("MODEL_MODULE", module).replace("MODEL_ACCESSOR", module.removeprefix("TimeWhisperModel") + "Model"))
            (consumer / "Package.swift").write_text(f'''// swift-tools-version: 5.9
import PackageDescription
let package = Package(name: "Smoke", platforms: [.macOS("15.0")],
    dependencies: [.package(name: "TimeWhisper", path: {json.dumps(str(args.library.resolve()))}),
                   .package(name: "TimeWhisperModel", path: {json.dumps(str(ROOT / "build/package"))})],
    targets: [.executableTarget(name: "Smoke", dependencies: [
        .product(name: "TimeWhisper", package: "TimeWhisper"),
        .product(name: "{module}", package: "TimeWhisperModel")])])
''')
            run("swift", "build", "--package-path", consumer)
            run("swift", "run", "--skip-build", "--package-path", consumer, "Smoke")
            bin_path = subprocess.check_output(["swift", "build", "--package-path", str(consumer), "--show-bin-path"], text=True).strip()
            dependencies = subprocess.check_output(["otool", "-L", str(Path(bin_path) / "Smoke")], text=True)
            other = next(name for key, (name, _) in VARIANTS.items() if key != variant)
            if other + ".framework" in dependencies:
                raise SystemExit("Consumer linked the unselected model")
        # Test the actual packaged resources, not only the original export. reference.json stays outside the framework.
        framework = ROOT / "build/frameworks" / f"{module}.framework/Resources/TimeWhisperTagger"
        with tempfile.TemporaryDirectory(prefix="time-whisper-model-parity-") as temporary:
            import shutil
            folder = Path(temporary) / "model"
            shutil.copytree(framework, folder)
            shutil.copy2(args.inputs / variant / "export/reference.json", folder / "reference.json")
            run("swift", "test", "--package-path", args.library.resolve(), "--filter", "CoreMLTaggerTests",
                env={**os.environ, "TIME_WHISPER_TAGGER": str(folder)})


if __name__ == "__main__":
    main()
