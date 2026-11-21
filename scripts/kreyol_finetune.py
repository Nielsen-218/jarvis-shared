"""Train the Haitian Creole voice on the owner's own reading, on this PC, and write an A/B listen.

    python -m scripts.kreyol_finetune --dry-run      what would happen: the dataset is checked, nothing is written
    python -m scripts.kreyol_finetune                check, build the dataset, train, install, write the A/B files
    python -m scripts.kreyol_finetune --ab-only      only the A/B files (stock voice, and the tuned one when present)

The recordings come from app/voice-record.html (core/kreyol_record: data/users/<uid>/voice/kreyol). The training is
the ylacombe/finetune-hf-vits recipe for facebook/mms-tts-hat (a 16 kHz VITS model). That recipe is NOT part of this
repository and nothing here downloads or installs it: `check` says exactly what is missing and where it comes from.

Steps: 1. prerequisites  2. dataset (audio + text, a tenth held out)  3. `accelerate launch run_vits_finetuning.py`
in the recipe folder  4. the trained model is copied to models/kreyol-tuned (audio.kreyol.TUNED_DIR; audio/tts loads
it from there, JARVIS_KREYOL_TUNED=off goes back)  5. six sentences the model never saw, said by the stock and the
tuned voice, as WAV files side by side.

Licence: MMS checkpoints are CC-BY-NC 4.0 and a fine-tune inherits that (personal use only).
"""
import argparse
import csv
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from audio import kreyol                                  # noqa: E402
from core import kreyol_record, kreyol_script             # noqa: E402
from core.words import plural                             # noqa: E402

STOCK = "facebook/mms-tts-hat"
RECIPE_URL = "https://github.com/ylacombe/finetune-hf-vits"
RECIPE_DIR = Path(os.getenv("JARVIS_VITS_RECIPE", "") or Path.home() / "finetune-hf-vits")
WORK_DIR = ROOT / "runs" / "kreyol-finetune"
MIN_CLIPS, GOOD_CLIPS = 50, 80                           # the recipe's own report: 80 to 150 clips, about 20 minutes
HOLD_OUT_EVERY = 10
TOKENIZER_FILES = ("vocab.json", "tokenizer_config.json", "special_tokens_map.json", "added_tokens.json")


# -- 1. prerequisites ---------------------------------------------------------------------------------------------------
def aligner_built(recipe: Path) -> bool:
    """The recipe's Cython alignment module, compiled (monotonic_align/monotonic_align/core.*.pyd or .so)."""
    inner = recipe / "monotonic_align" / "monotonic_align"
    return inner.is_dir() and any(p.suffix in (".pyd", ".so") for p in inner.glob("core*"))


def checkpoint_ready(folder: Path) -> bool:
    """The training checkpoint: the MMS generator WITH its discriminator, in transformers format."""
    return (folder / "config.json").is_file() and any((folder / n).is_file() for n in ("model.safetensors", "pytorch_model.bin"))


def prerequisites(recipe: Path, checkpoint: Path, python: str = sys.executable, have=None, cuda=None) -> list[str]:
    """Everything that stops a training run, each as what is missing and the exact way to get it. [] = ready."""
    have = have or (lambda name: importlib.util.find_spec(name) is not None)
    missing = []
    for module, fix in (("torch", "the GPU build of PyTorch"), ("transformers", f'"{python}" -m pip install transformers'),
                        ("accelerate", f'"{python}" -m pip install accelerate'),
                        ("datasets", f'"{python}" -m pip install datasets   (small; the recipe reads the clips with it)')):
        if not have(module):
            missing.append(f"Python package '{module}' is not installed. Get it with: {fix}")
    if cuda is None:
        try:
            import torch
            cuda = bool(torch.cuda.is_available())
        except Exception:
            cuda = False
    if not cuda:
        missing.append("No CUDA GPU is visible to PyTorch. Training on the CPU would take days; run this on the PC "
                       "with nothing heavy (a game, the brain model) holding the card.")
    if not (recipe / "run_vits_finetuning.py").is_file():
        missing.append(f"The training recipe is not at {recipe}. Fetch it (about 1 MB of code) with:\n"
                       f"      git clone {RECIPE_URL} \"{recipe}\"\n"
                       f"      \"{python}\" -m pip install -r \"{recipe / 'requirements.txt'}\"\n"
                       "    (or set JARVIS_VITS_RECIPE / --recipe to where it is)")
    elif not aligner_built(recipe):
        missing.append("The recipe's alignment module is not compiled. It needs Cython and the Microsoft C++ Build "
                       "Tools (Visual Studio Build Tools, 'Desktop development with C++', several GB). Then:\n"
                       f"      \"{python}\" -m pip install Cython\n"
                       f"      cd \"{recipe / 'monotonic_align'}\" && mkdir monotonic_align && "
                       f"\"{python}\" setup.py build_ext --inplace")
    if not checkpoint_ready(checkpoint):
        missing.append("The training checkpoint (the Creole model together with its discriminator; the published "
                       f"{STOCK} has the generator only) is not at {checkpoint}. The recipe makes it once, downloading "
                       "Meta's original 'hat' generator and discriminator from the facebook/mms-tts repository on "
                       "Hugging Face (several hundred MB; allow 1.5 GB of disk):\n"
                       f"      cd \"{recipe}\" && \"{python}\" convert_original_discriminator_checkpoint.py "
                       f"--language_code hat --pytorch_dump_folder_path \"{checkpoint}\"")
    return missing


# -- 2. the dataset -----------------------------------------------------------------------------------------------------
def inspect_clip(path: Path) -> tuple[float, str]:
    """(seconds, "") for a clip the model can train on, else (0, why not)."""
    try:
        with wave.open(str(path), "rb") as w:
            if w.getnchannels() != 1 or w.getsampwidth() != 2 or w.getframerate() != kreyol_record.RATE:
                return 0.0, f"not mono 16-bit {kreyol_record.RATE} Hz"
            seconds = w.getnframes() / w.getframerate()
    except (OSError, EOFError, wave.Error) as error:
        return 0.0, f"unreadable ({str(error)[:60] or type(error).__name__})"
    if not kreyol_record.MIN_SECONDS <= seconds <= kreyol_record.MAX_SECONDS + 1:
        return 0.0, f"{seconds:.1f} s is outside {kreyol_record.MIN_SECONDS} to {kreyol_record.MAX_SECONDS:.0f} s"
    return seconds, ""


def build_dataset(takes: list[dict], out: Path, write: bool = True) -> dict:
    """The kept takes as a dataset the recipe loads from a folder: train/ and test/ each with the WAVs and a
    metadata.csv (file_name, text). The text is what the voice is given at speaking time (audio.kreyol.prepare: lower
    case, numbers in words, only the model's letters), so training and speaking see the same spelling. Every tenth
    good clip is held out. With write=False nothing is written: the same report, as a plan.
    Returns {"train": [...], "test": [...], "skipped": [(take id, reason)], "seconds": float, "out": Path}."""
    rows, skipped = [], []
    for take in takes:
        text = kreyol.prepare(take.get("text", ""))
        seconds, problem = inspect_clip(Path(take["file"]))
        if not problem and len(text.split()) < 2:
            problem = "no text to read"
        if problem:
            skipped.append((take["id"], problem))
            continue
        rows.append({"id": take["id"], "source": Path(take["file"]), "file_name": f"{take['id']}.wav", "text": text,
                     "seconds": seconds})
    test = [r for i, r in enumerate(rows) if i % HOLD_OUT_EVERY == HOLD_OUT_EVERY - 1]
    if not test and len(rows) >= 5:
        test = [rows[-1]]
    held = {r["id"] for r in test}
    train = [r for r in rows if r["id"] not in held]
    if write:
        if out.exists():
            shutil.rmtree(out)                    # a rebuilt dataset never keeps a clip he has since deleted
        for split, part in (("train", train), ("test", test)):
            folder = out / split
            folder.mkdir(parents=True, exist_ok=True)
            with open(folder / "metadata.csv", "w", encoding="utf-8", newline="") as handle:
                table = csv.writer(handle)
                table.writerow(["file_name", "text"])
                for row in part:
                    shutil.copyfile(row["source"], folder / row["file_name"])
                    table.writerow([row["file_name"], row["text"]])
    return {"train": train, "test": test, "skipped": skipped, "seconds": sum(r["seconds"] for r in rows), "out": out}


# -- 3. training --------------------------------------------------------------------------------------------------------
def training_config(dataset: Path, checkpoint: Path, output: Path, epochs: int = 200, batch: int = 16,
                    has_test: bool = True) -> dict:
    """The recipe's JSON config (its training_config_examples/finetune_mms.json, pointed at the local folders)."""
    return {
        "project_name": "jarvis_kreyol_voice", "push_to_hub": False, "report_to": "none",
        "overwrite_output_dir": True, "output_dir": str(output),
        "dataset_name": str(dataset), "audio_column_name": "audio", "text_column_name": "text",
        "train_split_name": "train", "eval_split_name": "test" if has_test else "train",
        "full_generation_sample_text": kreyol.prepare(kreyol_script.AB_SENTENCES[0]),
        "max_duration_in_seconds": 20, "min_duration_in_seconds": 1.0, "max_tokens_length": 500,
        "model_name_or_path": str(checkpoint),
        "preprocessing_num_workers": 1, "dataloader_num_workers": 0,          # Windows: no forked workers
        "do_train": True, "num_train_epochs": epochs, "gradient_accumulation_steps": 1,
        "gradient_checkpointing": False, "per_device_train_batch_size": batch, "learning_rate": 2e-5,
        "adam_beta1": 0.8, "adam_beta2": 0.99, "warmup_ratio": 0.01, "group_by_length": False,
        "do_eval": True, "eval_steps": 50, "per_device_eval_batch_size": batch, "max_eval_samples": 25,
        "do_step_schedule_per_epoch": True,
        "weight_disc": 3, "weight_fmaps": 1, "weight_gen": 1, "weight_kl": 1.5, "weight_duration": 1, "weight_mel": 35,
        "fp16": False, "seed": 456,
    }


def train(recipe: Path, config: Path, python: str = sys.executable, run=subprocess.run) -> int:
    return run([python, "-m", "accelerate.commands.launch", "run_vits_finetuning.py", str(config)], cwd=str(recipe)).returncode


# -- 4. install ---------------------------------------------------------------------------------------------------------
def install_model(trained: Path, target: Path, stock_tokenizer: Path | None = None) -> list[str]:
    """Copy the trained voice to where audio/tts loads it from. The previous tuned voice is kept beside it as
    <name>.previous. Returns what is still missing for the voice to load ([] = installed)."""
    weights = next((n for n in ("model.safetensors",) if (trained / n).is_file()), None)
    if weights is None or not (trained / "config.json").is_file():
        return [f"{trained} has no config.json + model.safetensors: the training did not finish or saved elsewhere."]
    staging = target.with_name(target.name + ".new")
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    for name in ("config.json", weights):
        shutil.copyfile(trained / name, staging / name)
    for name in TOKENIZER_FILES:
        source = trained / name if (trained / name).is_file() else stock_tokenizer / name if stock_tokenizer else None
        if source is not None and source.is_file():
            shutil.copyfile(source, staging / name)
    missing = [name for name in kreyol.TUNED_FILES if not (staging / name).is_file()]
    if missing:
        return [f"the trained folder lacks {', '.join(missing)} (the tokenizer files of {STOCK} belong beside the model)"]
    if target.exists():
        previous = target.with_name(target.name + ".previous")
        if previous.exists():
            shutil.rmtree(previous)
        target.replace(previous)
    staging.replace(target)
    return []


# -- 5. the A/B listen --------------------------------------------------------------------------------------------------
def _speaker(source: str):
    """text -> (int16 samples, rate) with one VITS voice (the stock name or a local folder); seeded like audio/tts."""
    import numpy as np
    import torch
    from transformers import AutoTokenizer, VitsModel

    model = VitsModel.from_pretrained(source, local_files_only=True).eval()
    tokenizer = AutoTokenizer.from_pretrained(source, local_files_only=True)

    def say(text: str):
        parts = []
        for said, pause in kreyol.pieces(text):
            torch.manual_seed(7)
            with torch.no_grad():
                wave_ = model(**tokenizer(said, return_tensors="pt")).waveform[0].float().numpy()
            parts += [wave_, np.zeros(int(pause * model.config.sampling_rate), dtype=np.float32)]
        audio = np.concatenate(parts) if parts else np.zeros(1, dtype=np.float32)
        return (audio / max(float(np.abs(audio).max()), 1e-6) * 0.9 * 32767).astype("<i2"), model.config.sampling_rate
    return say


def ab_listen(out: Path, tuned: Path | None, speaker=_speaker) -> list[Path]:
    """The six test sentences as 1-stock.wav / 1-tuned.wav ... plus sentences.txt. Without a tuned voice, stock only."""
    out.mkdir(parents=True, exist_ok=True)
    written = []
    (out / "sentences.txt").write_text("\n".join(f"{n}. {s}" for n, s in enumerate(kreyol_script.AB_SENTENCES, 1)) + "\n",
                                       encoding="utf-8")
    for label, source in (("stock", STOCK), ("tuned", str(tuned) if tuned else "")):
        if not source:
            continue
        say = speaker(source)
        for n, sentence in enumerate(kreyol_script.AB_SENTENCES, 1):
            samples, rate = say(sentence)
            path = out / f"{n}-{label}.wav"
            with wave.open(str(path), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(int(rate))
                w.writeframes(samples.tobytes())
            written.append(path)
    return written


# -- the run ------------------------------------------------------------------------------------------------------------
def report(data: dict, pending: int) -> list[str]:
    clips = len(data["train"]) + len(data["test"])
    lines = [f"Recordings: {plural(clips, 'good clip')}, {data['seconds'] / 60:.1f} minutes "
             f"({len(data['train'])} to train on, {len(data['test'])} held out)."]
    if pending:
        lines.append(f"  {plural(pending, 'take')} still waiting to be decoded (uploaded through a machine with no decoder).")
    for take, why in data["skipped"]:
        lines.append(f"  skipped {take}: {why}")
    if clips < MIN_CLIPS:
        lines.append(f"  NOT ENOUGH: training needs at least {MIN_CLIPS} clips; {GOOD_CLIPS} to 150 (about 20 minutes) is the aim.")
    elif clips < GOOD_CLIPS:
        lines.append(f"  Enough to try, but {GOOD_CLIPS} or more clips give a steadier voice.")
    return lines


def listen(ab: Path, tuned: Path | None) -> int:
    """Write the A/B files and say where; a voice that will not load is reported, never a crash."""
    try:
        files = ab_listen(ab, tuned)
    except Exception as error:
        print(f"The A/B listen was not written: {str(error)[:200]}\n"
              f"  It needs the stock voice {STOCK} in this machine's Hugging Face cache (the PC has it once Jarvis has "
              "spoken Creole), and a tuned folder that loads. Then: python -m scripts.kreyol_finetune --ab-only")
        return 1
    print(f"Listen: {ab} ({plural(len(files), 'file')}{'' if tuned else '; no tuned voice yet, stock only'})")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--user", default="owner", help="whose recordings (default: owner)")
    ap.add_argument("--recipe", type=Path, default=RECIPE_DIR, help="the finetune-hf-vits checkout")
    ap.add_argument("--work", type=Path, default=WORK_DIR, help="dataset, checkpoints and A/B files go here")
    ap.add_argument("--target", "--out", dest="out", type=Path, default=None,
                    help="where the tuned voice is installed (default: models/kreyol-tuned of this checkout)")
    ap.add_argument("--epochs", type=int, default=200)
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--dry-run", action="store_true", help="check everything and print the plan; write nothing")
    ap.add_argument("--ab-only", action="store_true", help="only write the A/B listening files")
    args = ap.parse_args(argv)
    target = args.out or kreyol.tuned_dir()
    work, checkpoint = args.work, args.work / "mms-hat-train"
    dataset, trained, ab = work / "dataset", work / "trained", work / "ab"
    print(f"Installs to: {target}")
    print(f"Work folder: {work}")

    if args.ab_only:
        return listen(ab, target if all((target / n).is_file() for n in kreyol.TUNED_FILES) else None)
    if args.out is None and os.getenv("JARVIS_KREYOL_TUNED", "").strip().lower() in ("", "off", "on", "0", "1", "stock") \
            and (ROOT / ".git").is_file():
        # A git worktree (.git is a file there): the running Jarvis loads models/ of the LIVE checkout, not this one.
        print(f"Not started: {ROOT} is a git worktree, and a voice installed in its models folder would never be "
              "used by the running Jarvis. Run this from the live checkout, or name the folder with --target.")
        return 2

    if not args.dry_run:
        done = kreyol_record.convert_pending(args.user)
        if done["converted"] or done["rejected"]:
            print(f"Decoded waiting takes: {done['converted']} kept, {done['rejected']} rejected.")
    pending = sum(t["state"] == "pending" for t in kreyol_record.current_takes(args.user).values())
    data = build_dataset(kreyol_record.kept(args.user), dataset, write=False)
    missing = prerequisites(args.recipe, checkpoint)
    print("\n".join(report(data, pending)))
    print("Prerequisites: " + ("all present." if not missing else f"{plural(len(missing), 'thing')} missing."))
    for n, item in enumerate(missing, 1):
        print(f"  {n}. {item}")
    enough = len(data["train"]) + len(data["test"]) >= MIN_CLIPS
    print("Plan:")
    for step in (f"build the dataset in {dataset}",
                 f"train {args.epochs} epochs, batch {args.batch}, from {checkpoint} (recipe: {args.recipe})",
                 f"install the result in {target} (Jarvis uses it after a restart; JARVIS_KREYOL_TUNED=off goes back)",
                 f"write the A/B listen ({plural(len(kreyol_script.AB_SENTENCES), 'sentence')}, stock and tuned) to {ab}"):
        print(f"  - {step}")
    if args.dry_run:
        print("Dry run: nothing was written." + ("" if enough and not missing else " Not ready to train yet (see above)."))
        return 0 if enough and not missing else 1
    if missing or not enough:
        print("Not started: fix what is listed above, then run this again.")
        return 1

    data = build_dataset(kreyol_record.kept(args.user), dataset)
    config = work / "training_config.json"
    config.write_text(json.dumps(training_config(dataset, checkpoint, trained, args.epochs, args.batch,
                                                 has_test=bool(data["test"])), indent=1), encoding="utf-8")
    print(f"Training (this takes a while; the log is below). Config: {config}")
    code = train(args.recipe, config)
    if code != 0:
        print(f"The training stopped with exit code {code}. Nothing was installed; the stock voice is unchanged.")
        return code
    stock_folder = None
    try:
        from huggingface_hub import try_to_load_from_cache
        cached = try_to_load_from_cache(STOCK, "vocab.json")
        stock_folder = Path(cached).parent if isinstance(cached, str) else None
    except Exception:
        pass
    problems = install_model(trained, target, stock_folder)
    if problems:
        print("Trained, but not installed: " + "; ".join(problems))
        return 1
    print(f"Installed: {target}. If the tuned voice is worse, set JARVIS_KREYOL_TUNED=off (or delete the folder) "
          "and restart Jarvis.")
    listen(ab, target)                           # the voice is installed either way
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
