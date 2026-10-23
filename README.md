# Time Whisper Model

Swift packages for the on-device [Time Whisper](https://github.com/dayedge/time-whisper)
text tagger. Supports macOS 15+, Apple Silicon and Intel.

| Product and module | Resource accessor | Languages |
| --- | --- | --- |
| `TimeWhisperModelAll` | `AllModel.folder` | English and Polish |
| `TimeWhisperModelEnglish` | `EnglishModel.folder` | English |

Add `https://github.com/dayedge/time-whisper-model` as a Swift package dependency and select
one product. Add the Time Whisper library separately, then pass it the bundled model:

```swift
import TimeWhisper
import TimeWhisperModelAll

let tagger = CoreMLTagger(folder: AllModel.folder)
let parser = try TimeWhisper(taggerFactory: { tagger })
```

Each product includes its matching model, tokenizer and labels. SwiftPM downloads the
selected release artifact during the build; the application runs offline. A release may contain All, English, or both; select a product included in that version.
Model and library versions are independent. Release metadata names the exact library version used for testing.

## Qualify an existing exported model

Run from this repository. Your exported model can be anywhere on disk:

```sh
python3 scripts/model.py qualify --all ../time-whisper/build/tagger-coreml
```

This tests the existing All model with system spelling and writes
`build/inputs/model-all-0.7.0.zip` when the checks pass. It does not require an English model,
retrain, or change the input folder. The default library checkout is `../time-whisper`;
the model version defaults to that checkout's Gradle version. Override either explicitly:

```sh
python3 scripts/model.py qualify \
  --version 0.7.1 \
  --library /path/to/time-whisper \
  --all /path/to/exported-all
```

Older exports without `export.json` are accepted. Their export settings and, unless supplied
with `--training-commit`, training commit are recorded as unknown rather than guessed.
The model, tokenizer, labels and `reference.json` must still be present and pass validation.

Local qualification can test an uncommitted working copy. Its source fingerprint is recorded,
and the resulting ZIP is marked local-only. To upload a release, qualify again against a clean
checkout of the exact published library tag. The release script refuses local-only results.

## Releasing a newly trained model

There is **one release path**:

**Train locally → export the variants you need → test locally → upload to a draft → run GitHub Actions → publish the draft.**

You run the local steps and start the workflow yourself. GitHub Actions then builds and tests
the distributable packages automatically. Pushing a commit does not release a model, and
Actions does not train it. You do not need to build or upload XCFrameworks yourself.

### 1. Choose the checkpoint and release versions

Use your normal macOS Terminal, with Xcode, Java 21 and Python installed. Sign in with `gh auth login` using an account with write
access to this repository.

Start in the root of this repository. Run the following steps in the **same Terminal session**.
Set these values for your release:

```sh
export DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer
model_version=0.7.0
library_version=0.7.0
library_src="$(cd ../time-whisper && pwd)"
checkpoint_dir="$library_src/tagger"
training_commit="PASTE_THE_FULL_40_CHARACTER_TRAINING_SOURCE_COMMIT"
```

- `model_version`: a new, unused version of this model package. All-only is supported.
- `library_version`: an already published Time Whisper library tag to test against.
- `library_src`: your local Time Whisper source checkout, containing `training/export.py`.
- `checkpoint_dir`: the trained checkpoint directory containing `model.pt` and `labels.json`;
  change it if you used a different training output path.
- `training_commit`: the Time Whisper source commit used to train that checkpoint. Use the
  recorded training commit, which may differ from the current checkout's `HEAD`.

Commit and push packaging changes in this repository before starting the workflow. The
training source commit must also be pushed to the Time Whisper repository so qualification
can look it up. The library tag must already be published. A new model does not
require a new library version when the existing version remains compatible and its checks pass.

### 2. Export the trained checkpoint

The packaging scripts in this repository use only Python's standard library; its
`requirements.txt` therefore has no third-party dependencies. Exporting the checkpoint runs
Time Whisper's Python code and needs its training/export dependencies. Install them in a
virtual environment here, after setting `library_src` above:

```sh
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt -r "$library_src/requirements.txt"
```

With that environment active, export All:

```sh
python3 "$library_src/training/export.py" \
  --model "$checkpoint_dir" \
  --out "$PWD/build/exports/all" \
  --languages en,pl
```

For an English variant, also run:

```sh
python3 "$library_src/training/export.py" \
  --model "$checkpoint_dir" \
  --out "$PWD/build/exports/en" \
  --languages en
```

Each export reads the trained checkpoint and keeps the appropriate vocabulary. It does not
train again. Skip the English export when releasing only All. Each folder contains the Core ML model, tokenizer, labels,
`export.json` and PyTorch reference outputs. All means all supported languages; it is unrelated
to the exporter's `--all-pieces` option.

### 3. Qualify the selected exports

Use a dedicated checkout of the published library so local development changes cannot affect
qualification. Clone it once; on subsequent releases, reuse `build/library` and run the fetch
and checkout commands:

```sh
git clone https://github.com/dayedge/time-whisper.git build/library
```

```sh
git -C build/library fetch origin --tags
git -C build/library checkout --detach "$library_version"

python3 scripts/model.py qualify \
  --version "$model_version" \
  --library-version "$library_version" \
  --library build/library \
  --training-commit "$training_commit" \
  --all build/exports/all
```

To include English, add `--en build/exports/en` to that command. At least one of `--all`
and `--en` is required. This runs `make check` with **system spelling on** for the selected
variants: English and Polish for All, English for English. It also checks tokenizer and inference parity against the export's PyTorch references.
The library checkout must be clean. Stop if qualification fails; review the failures in Time
Whisper rather than disabling spelling or editing the generated qualification metadata.

On success, the files to upload are:

```text
build/inputs/model-all-<version>.zip
build/inputs/model-en-<version>.zip  # only when --en was supplied
```

Only supplied variants produce ZIPs. A successful All-only qualification removes an older
English ZIP for the same version so it cannot be uploaded accidentally. The ZIPs contain the
exported models and qualification metadata. Weights stay out of Git.

### 4. Start release preparation

Run one command from this repository (or call the script by its path from another directory):

```sh
sh scripts/release.sh "$model_version"
```

For example, `sh scripts/release.sh 0.7.0`. It uploads whichever qualified variants exist for
that version; English is optional. To use ZIPs from another directory:

```sh
sh scripts/release.sh "$model_version" /path/to/qualified-zips
```

The default input directory is this repository's `build/inputs`. An optional relative path is
resolved from your current working directory. The script reads the available ZIPs and takes
its library version from their qualification metadata, calculates their checksums, and uses
this repository's current commit as the packaging source. No hashes or workflow fields need
to be copied by hand.

It checks that the source is clean and available on GitHub, creates the draft release,
uploads the selected archives, and starts **Prepare model release**. It does not push source or
publish the release. On retry, it can reuse an existing draft and replace its input archives;
it refuses published versions or versions whose release tag already exists.

To preview the commands without making any GitHub calls:

```sh
sh scripts/release.sh --dry-run "$model_version"
```

The preview still needs at least one release-ready qualified input archive and a clean local source checkout;
remote source, draft and tag checks run only during a real release.

The script prints the Actions link. Open it to follow the run, or use:

```sh
gh run watch --repo dayedge/time-whisper-model --exit-status
```

Select **Prepare model release** when prompted.

Actions verifies the uploaded files, reruns qualification, builds universal XCFrameworks for
the selected variants, and tests each in a standalone Swift consumer. It checks model loading and
release/reload, inference parity, and that the other variant is not linked.

After a successful run, the draft contains the selected artifacts:

```text
TimeWhisperModelAll-<version>.xcframework.zip
TimeWhisperModelEnglish-<version>.xcframework.zip  # only if included
```

It also contains their checksums and model metadata. The workflow creates the version tag
with a generated `Package.swift` pointing to those downloads. It leaves `main` unchanged and
**does not publish the draft**.

### 5. Review and publish

Review the successful Actions run and the draft's assets, versions and release notes:

```sh
gh release view "$model_version" --repo dayedge/time-whisper-model --web
```

When satisfied, publish it:

```sh
gh release edit "$model_version" --repo dayedge/time-whisper-model --draft=false
```

Consumers can now select that package version. Verify it in an application using the remote
package tag before announcing the release; draft asset URLs cannot be tested as ordinary
anonymous SwiftPM downloads. Never replace published assets or move a published tag.

## Packaging development and failed releases

The development manifest on `main` defaults to All. `scripts/model.py build` writes a local
package in `build/package` and a release manifest in `build/distributions/Package.swift`,
both exposing exactly the variants supplied. Use `build/package` for local integration;
release tags use download URLs and checksums. The `unpack`, `build` and `smoke.py` commands in the release workflow can also
be run locally when debugging packaging. They do not provide a separate publishing path.
Run `python3 scripts/test_model.py` for packaging-input tests that do not need model weights.
Run `python3 scripts/test_release.py` to test the release entry point with stubbed Git/GitHub
commands; it never contacts GitHub, creates commits or releases, or needs model weights.

If preparation fails before creating the version tag, fix the cause and rerun the workflow
against the draft. If the tag already exists, inspect and finish that draft manually; the
workflow refuses to move an existing tag or modify a published release. Release workflows
need permission to write repository contents.

## License

Packaging code is MPL-2.0. Model weights and tokenizer use Apache-2.0; see `MODEL_LICENSE.txt`
and `THIRD_PARTY_NOTICES.txt`. The app's selected framework includes its license notices.
