# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""Rebuild or verify the public, semantic-only PWM software profile."""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from plc.pwm_fact_source import SOURCE_PATH, GENERATED_PATH, compile_source, canonical

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=SOURCE_PATH)
    parser.add_argument('--output', type=Path, default=GENERATED_PATH)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    generated = compile_source(json.loads(args.source.read_text(encoding='utf-8')))
    if args.check:
        if canonical(json.loads(args.output.read_text(encoding='utf-8'))) != canonical(generated):
            parser.exit(1, 'PWM generated contract has drifted\n')
    else:
        args.output.write_text(json.dumps(generated, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(generated['binding'])
if __name__ == '__main__':
    main()
