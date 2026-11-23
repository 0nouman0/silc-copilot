"""Deterministic evaluation over the golden set and the red-team set. No model is called: it checks the parts that must be right in code.

    python -m eval.run_eval [--live]        # --live builds the catalog from the configured database; default uses the unit-test replica

Per item (JSONL): {"q": text, "skill": expected skill, "prerouter": expected prerouter intent or null, "tool": name, "args": {...},
"must_contain": [...], "must_not_contain": [...]}. `skill` and `prerouter` are routing checks; `tool` runs a tool against the catalog and
checks its JSON output text. The questions in golden_set.jsonl are a SEED written from the blueprint's categories: replace them with the
~500 anonymised genuine enquiries and admissions' reference answers (Phase 0) before using the numbers as a launch bar.
Launch targets: routing >= 95%, personal cases routed >= 95%, wrong fee/date/seat/verdict = 0, ineligible recommended = 0.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from app.agent.prerouter import prerouter
from app.agent.skills import select_skill
from app.tools import ToolContext, execute

HERE = Path(__file__).parent


def load(name: str) -> list[dict]:
    p = HERE / name
    return [json.loads(x) for x in p.read_text(encoding="utf-8").split("\n") if x.strip() and not x.startswith("#")]


def check(item: dict) -> list[str]:
    fails: list[str] = []
    hist = [{"role": "user", "content": item["q"]}]
    pre = prerouter(hist)
    if "prerouter" in item and (pre.intent if pre else None) != item["prerouter"]:
        fails.append(f"prerouter: expected {item['prerouter']!r}, got {pre.intent if pre else None!r}")
    if "skill" in item and pre is None and select_skill(hist) != item["skill"]:
        fails.append(f"skill: expected {item['skill']!r}, got {select_skill(hist)!r}")
    out_text = pre.reply if pre else ""
    if "tool" in item:
        out_text = json.dumps(execute(item["tool"], json.dumps(item.get("args", {})), ToolContext()).data, default=str)
    for s in item.get("must_contain", []):
        if s.lower() not in out_text.lower():
            fails.append(f"missing {s!r}")
    for s in item.get("must_not_contain", []):
        if s.lower() in out_text.lower():
            fails.append(f"forbidden {s!r}")
    return fails


def main(argv: list[str]) -> int:
    if "--live" in argv:
        from dotenv import load_dotenv
        load_dotenv()
        from app.catalog import refresh
        print("building catalog from the database:", refresh(force=True, who="eval").reason)
    else:
        from app.catalog import set_catalog, set_reader
        from tests.fake_catalog import FakeReader, build
        rd = FakeReader()                      # the unit-test replica for the catalog AND for fee lookups (it stores no fees), never the real database
        set_reader(rd)
        set_catalog(build(rd))
    total = failed = 0
    for fname in ("golden_set.jsonl", "redteam.jsonl"):
        items = load(fname)
        bad = 0
        for it in items:
            fails = check(it)
            total += 1
            if fails:
                bad += 1
                failed += 1
                print(f"FAIL [{fname}] {it['q']!r}: {'; '.join(fails)}")
        print(f"{fname}: {len(items) - bad}/{len(items)} passed")
    print(f"overall: {total - failed}/{total} ({100 * (total - failed) / max(total, 1):.0f}%)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
