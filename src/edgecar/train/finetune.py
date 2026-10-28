"""LoRA fine-tuning of the command model on the synthetic training set.

    python -m edgecar.train.finetune --base models/hf/Qwen2.5-0.5B-Instruct --out models/tuned/edgecar-0.5b

The model sees the same short prompt it will see on the vehicle (``slm.TUNED_INSTRUCTIONS`` + the question) and is
trained only on the answer: the command and the end-of-turn token. After every epoch it is scored on the dev question
sets (exact command match, greedy decoding, no grammar) and the best epoch is kept. The held-out sets are never
looked at here — they are scored later, through llama.cpp and the full pipeline, like every other model.
"""

from __future__ import annotations

import argparse
import json
import math
import platform
import random
import subprocess
import time
from pathlib import Path

import torch
from peft import LoraConfig, get_peft_model
from transformers import AutoModelForCausalLM, AutoTokenizer

from edgecar.nlu import command
from edgecar.nlu.slm import messages

DEV_SETS = ("eval/questions.jsonl", "eval/refusal_dev.jsonl")
LORA_TARGETS = ("q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj")


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def dev_items() -> list[tuple[str, set[str]]]:
    """Dev questions with their accepted answers in the model's verb format."""
    out = []
    for p in DEV_SETS:
        for item in read_jsonl(Path(p)):
            golds = {command.render_model(command.parse(g)) for g in item["gold"]}
            out.append((item["q"], golds))
    return out


class Encoder:
    def __init__(self, tokenizer) -> None:
        self.tok = tokenizer
        self.eot = tokenizer.convert_tokens_to_ids("<|im_end|>")

    def prompt(self, question: str) -> list[int]:
        out = self.tok.apply_chat_template(messages(question, "tuned"), add_generation_prompt=True, tokenize=True)
        return list(out["input_ids"] if hasattr(out, "keys") else out)  # transformers ≥ 5 returns an encoding

    def example(self, question: str, answer: str) -> tuple[list[int], list[int]]:
        p = self.prompt(question)
        a = [*self.tok(answer, add_special_tokens=False)["input_ids"], self.eot]
        return p + a, [-100] * len(p) + a


def batches(rows: list[tuple[list[int], list[int]]], size: int, pad: int, rng: random.Random | None):
    order = list(range(len(rows)))
    if rng is not None:
        rng.shuffle(order)
    for i in range(0, len(order), size):
        chunk = [rows[j] for j in order[i : i + size]]
        n = max(len(ids) for ids, _ in chunk)
        ids = torch.full((len(chunk), n), pad)
        labels = torch.full((len(chunk), n), -100)
        mask = torch.zeros((len(chunk), n), dtype=torch.long)
        for k, (x, y) in enumerate(chunk):
            ids[k, : len(x)] = torch.tensor(x)
            labels[k, : len(y)] = torch.tensor(y)
            mask[k, : len(x)] = 1
        yield ids, labels, mask


def answer_loss(model, ids: torch.Tensor, labels: torch.Tensor, mask: torch.Tensor) -> torch.Tensor:
    """Cross-entropy on the answer tokens only. Projecting just those positions onto the 152k-token vocabulary,
    instead of every position, keeps the logits — by far the largest tensor here — small enough for a 6 GB GPU."""
    base = model.get_base_model()  # the LoRA layers are injected in place, so this is the adapted model
    hidden = base.model(input_ids=ids, attention_mask=mask).last_hidden_state
    target = labels[:, 1:]
    keep = target != -100
    logits = base.lm_head(hidden[:, :-1][keep]).float()
    return torch.nn.functional.cross_entropy(logits, target[keep])


@torch.no_grad()
def generate(model, enc: Encoder, questions: list[str], batch: int = 32, max_new: int = 10) -> list[str]:
    """Greedy answers, left-padded batches."""
    model.eval()
    pad = enc.tok.pad_token_id
    out: list[str] = []
    for i in range(0, len(questions), batch):
        prompts = [enc.prompt(q) for q in questions[i : i + batch]]
        n = max(map(len, prompts))
        ids = torch.full((len(prompts), n), pad)
        mask = torch.zeros((len(prompts), n), dtype=torch.long)
        for k, p in enumerate(prompts):
            ids[k, n - len(p) :] = torch.tensor(p)
            mask[k, n - len(p) :] = 1
        gen = model.generate(
            input_ids=ids.to(model.device),
            attention_mask=mask.to(model.device),
            max_new_tokens=max_new,
            do_sample=False,
            eos_token_id=enc.eot,
            pad_token_id=pad,
        )
        for row in gen[:, n:]:
            out.append(enc.tok.decode(row, skip_special_tokens=True).strip().split("\n")[0])
    model.train()
    return out


def score(model, enc: Encoder, items: list[tuple[str, set[str]]]) -> tuple[float, list[dict]]:
    preds = generate(model, enc, [q for q, _ in items])
    rows = [{"q": q, "pred": p, "gold": sorted(g), "ok": p in g} for (q, g), p in zip(items, preds, strict=True)]
    return sum(r["ok"] for r in rows) / len(rows), rows


def git_commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
    except OSError:
        return "unknown"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", type=Path, required=True, help="Hugging Face model directory")
    ap.add_argument("--data", type=Path, default=Path("data/train"))
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--epochs", type=int, default=3)
    ap.add_argument("--batch", type=int, default=32)
    ap.add_argument("--lr", type=float, default=2e-4)
    ap.add_argument("--rank", type=int, default=16)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument(
        "--grad-ckpt", action="store_true", help="recompute activations in the backward pass (fits 1.5B in 6 GB)"
    )
    a = ap.parse_args()

    random.seed(a.seed)
    torch.manual_seed(a.seed)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    tok = AutoTokenizer.from_pretrained(a.base)
    model = AutoModelForCausalLM.from_pretrained(a.base, dtype=torch.bfloat16).to(device)
    if a.grad_ckpt:
        model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
        model.enable_input_require_grads()
        model.config.use_cache = False
    lora = LoraConfig(
        r=a.rank, lora_alpha=2 * a.rank, lora_dropout=0.05, target_modules=list(LORA_TARGETS), task_type="CAUSAL_LM"
    )
    model = get_peft_model(model, lora)
    trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
    total = sum(p.numel() for p in model.parameters())
    print(f"LoRA r={a.rank}: {trainable / 1e6:.1f}M trainable of {total / 1e6:.0f}M parameters on {device}")

    enc = Encoder(tok)
    train = [enc.example(r["q"], r["a"]) for r in read_jsonl(a.data / "train.jsonl")]
    val = [enc.example(r["q"], r["a"]) for r in read_jsonl(a.data / "val.jsonl")]
    val_items = [(r["q"], {r["a"]}) for r in read_jsonl(a.data / "val.jsonl")]
    dev = dev_items()

    steps_per_epoch = math.ceil(len(train) / a.batch)
    total_steps = steps_per_epoch * a.epochs
    warmup = max(1, int(0.05 * total_steps))
    opt = torch.optim.AdamW([p for p in model.parameters() if p.requires_grad], lr=a.lr, weight_decay=0.0)
    sched = torch.optim.lr_scheduler.LambdaLR(
        opt,
        lambda s: (
            (s + 1) / warmup
            if s < warmup
            else 0.5 * (1 + math.cos(math.pi * (s - warmup) / max(1, total_steps - warmup)))
        ),
    )

    dev_acc, _ = score(model, enc, dev)
    log: dict = {"epochs": [], "dev_before": dev_acc, "loss": []}
    print(f"dev accuracy before training: {dev_acc:.1%}")
    best, best_epoch = -1.0, 0
    rng = random.Random(a.seed)
    t_start = time.time()
    step = 0
    model.train()
    for epoch in range(1, a.epochs + 1):
        for ids, labels, mask in batches(train, a.batch, tok.pad_token_id, rng):
            loss = answer_loss(model, ids.to(device), labels.to(device), mask.to(device))
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
            sched.step()
            opt.zero_grad(set_to_none=True)
            step += 1
            log["loss"].append(round(loss.item(), 4))
            if step % 25 == 0:
                print(f"  step {step}/{total_steps}  loss {loss.item():.4f}  lr {sched.get_last_lr()[0]:.2e}")
        with torch.no_grad():
            model.eval()
            vl = [
                answer_loss(model, i.to(device), y.to(device), m.to(device)).item()
                for i, y, m in batches(val, a.batch, tok.pad_token_id, None)
            ]
            model.train()
        val_acc, _ = score(model, enc, val_items)
        dev_acc, dev_rows = score(model, enc, dev)
        entry = {
            "epoch": epoch,
            "val_loss": round(sum(vl) / len(vl), 4),
            "val_accuracy": round(val_acc, 4),
            "dev_accuracy": round(dev_acc, 4),
            "minutes": round((time.time() - t_start) / 60, 2),
        }
        log["epochs"].append(entry)
        print(f"epoch {epoch}: {entry}")
        if dev_acc > best:
            best, best_epoch = dev_acc, epoch
            a.out.mkdir(parents=True, exist_ok=True)
            model.save_pretrained(a.out / "adapter")
            (a.out / "dev_predictions.json").write_text(json.dumps(dev_rows, indent=2), encoding="utf-8")

    # Merge the best adapter into the base weights for export to GGUF.
    from peft import PeftModel

    base = AutoModelForCausalLM.from_pretrained(a.base, dtype=torch.bfloat16)
    merged = PeftModel.from_pretrained(base, a.out / "adapter").merge_and_unload()
    merged.save_pretrained(a.out / "merged", safe_serialization=True)
    tok.save_pretrained(a.out / "merged")
    log |= {
        "base": str(a.base),
        "best_epoch": best_epoch,
        "best_dev_accuracy": best,
        "config": {k: (str(v) if isinstance(v, Path) else v) for k, v in vars(a).items()},
        "lora_targets": LORA_TARGETS,
        "trainable_params": trainable,
        "train_examples": len(train),
        "data_stats": json.loads((a.data / "stats.json").read_text(encoding="utf-8")),
        "device": torch.cuda.get_device_name(0) if device == "cuda" else platform.processor(),
        "torch": torch.__version__,
        "commit": git_commit(),
        "train_minutes": round((time.time() - t_start) / 60, 2),
    }
    (a.out / "train_log.json").write_text(json.dumps(log, indent=2), encoding="utf-8")
    print(f"best epoch {best_epoch}: dev accuracy {best:.1%}, merged model in {a.out / 'merged'}")


if __name__ == "__main__":
    main()
