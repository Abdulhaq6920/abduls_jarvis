"""Probe the Hugging Face router with the token from .env. Never prints the token.

    python scripts/dev/hf_probe.py [model ...]

Lists which popular chat models are served and whether they advertise tool support, then sends
one tiny chat request to each model given on the command line (default: a short candidate list).
"""
import json
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
BASE = "https://router.huggingface.co/v1"


def token() -> str:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("JARVIS_HF_TOKEN="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("JARVIS_HF_TOKEN missing in .env")


def main() -> None:
    headers = {"Authorization": f"Bearer {token()}"}
    with httpx.Client(timeout=40) as client:
        who = client.get("https://huggingface.co/api/whoami-v2", headers=headers)
        print("whoami:", who.status_code, (who.json().get("name") if who.is_success else who.text[:120]), flush=True)

        models = client.get(f"{BASE}/models", headers=headers)
        print("models:", models.status_code, flush=True)
        if models.is_success:
            data = models.json().get("data", [])
            print("served models:", len(data), flush=True)
            wanted = ("gpt-oss", "Qwen3", "Qwen2.5", "Llama-3", "DeepSeek", "Mistral", "gemma", "GLM", "Kimi")
            for item in data:
                mid = item.get("id", "")
                if any(w.lower() in mid.lower() for w in wanted):
                    providers = [
                        f"{p.get('provider')}{'+tools' if p.get('supports_tools') else ''}"
                        for p in item.get("providers", [])
                    ]
                    print(" ", mid, "|", ", ".join(providers[:5]), flush=True)

        candidates = sys.argv[1:] or ["openai/gpt-oss-120b:fastest", "Qwen/Qwen3-8B:fastest"]
        for model in candidates:
            started = time.perf_counter()
            body = {"model": model, "max_tokens": 40, "messages": [{"role": "user", "content": "Reply with the single word: ready"}]}
            reply = client.post(f"{BASE}/chat/completions", headers=headers, json=body)
            ms = int((time.perf_counter() - started) * 1000)
            if reply.is_success:
                msg = reply.json()["choices"][0]["message"]
                print("chat", model, "->", reply.status_code, f"{ms}ms", json.dumps((msg.get("content") or "")[:60]), flush=True)
            else:
                print("chat", model, "->", reply.status_code, f"{ms}ms", reply.text[:200].replace("\n", " "), flush=True)


if __name__ == "__main__":
    main()
