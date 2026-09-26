"""Produce one deterministic decision per canonical pair without provider calls."""
import argparse
import json
from pathlib import Path

from bot import compose


def generate(dataset: Path, output: Path):
    def load(folder, identifier):
        return json.loads((dataset / folder / f"{identifier}.json").read_text(encoding="utf-8"))

    pairs = json.loads((dataset / "test_pairs.json").read_text(encoding="utf-8"))["pairs"]
    rows = []
    for pair in pairs:
        merchant = load("merchants", pair["merchant_id"])
        category = load("categories", merchant["category_slug"])
        trigger = load("triggers", pair["trigger_id"])
        customer = load("customers", pair["customer_id"]) if pair.get("customer_id") else None
        result = compose(category, merchant, trigger, customer)
        rows.append({"test_id": pair["test_id"], "action": "send" if result["body"] else "wait", **result})
    output.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    return rows


if __name__ == "__main__":
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=root / "dataset/expanded")
    parser.add_argument("--out", type=Path, default=root / "submission.jsonl")
    args = parser.parse_args()
    rows = generate(args.dataset, args.out)
    print(f"Wrote {len(rows)} decisions ({sum(r['action'] == 'send' for r in rows)} messages); placeholders/unauthorized outreach are explicitly withheld.")
