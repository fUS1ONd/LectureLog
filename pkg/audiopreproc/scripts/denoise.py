#!/usr/bin/env python3
"""Шумоподавление через Resemble Enhance (HF Space API)."""

import os
import shutil
import sys
import time

from gradio_client import Client, handle_file

MAX_RETRIES = 3
RETRY_SLEEP_SEC = 2.0


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

        result = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                result = client.predict(
                    handle_file(os.path.join(in_dir, chunk)),
                    "Midpoint",  # solver
                    64,  # nfe (1-128)
                    0.5,  # tau (0-1)
                    True,  # denoising
                    api_name="/predict",
                )
                break
            except Exception as exc:  # noqa: BLE001
                print(
                    f"  WARN: ошибка API на попытке {attempt}/{MAX_RETRIES}: {exc}",
                    file=sys.stderr,
                    flush=True,
                )
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_SLEEP_SEC * attempt)

        # result = (denoised_path, enhanced_path)
        if result and result[0]:
            shutil.copy2(result[0], os.path.join(out_dir, chunk))
        else:
            shutil.copy2(os.path.join(in_dir, chunk), os.path.join(out_dir, chunk))
            print("  WARN: API недоступен, копируем оригинал", file=sys.stderr, flush=True)

    print("OK", flush=True)


if __name__ == "__main__":
    main()
