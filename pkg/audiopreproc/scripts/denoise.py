#!/usr/bin/env python3
"""Шумоподавление через Resemble Enhance (HF Space API)."""

import os
import shutil
import sys

from gradio_client import Client, handle_file


def main() -> None:
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <input_dir> <output_dir>", file=sys.stderr)
        sys.exit(1)

    in_dir, out_dir = sys.argv[1], sys.argv[2]
    os.makedirs(out_dir, exist_ok=True)

    client = Client("ResembleAI/resemble-enhance")
    chunks = sorted(f for f in os.listdir(in_dir) if f.endswith(".wav"))

    for i, chunk in enumerate(chunks):
        print(f"[{i + 1}/{len(chunks)}] {chunk}", file=sys.stderr, flush=True)
        result = client.predict(
            handle_file(os.path.join(in_dir, chunk)),
            "Midpoint",  # solver
            64,  # nfe (1-128)
            0.5,  # tau (0-1)
            True,  # denoising
            api_name="/predict",
        )
        # result = (denoised_path, enhanced_path)
        if result[0]:
            shutil.copy2(result[0], os.path.join(out_dir, chunk))
        else:
            shutil.copy2(os.path.join(in_dir, chunk), os.path.join(out_dir, chunk))
            print("  WARN: API вернул None, копируем оригинал", file=sys.stderr)

    print("OK", flush=True)


if __name__ == "__main__":
    main()
