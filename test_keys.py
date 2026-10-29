"""Quick check that both API keys work. Run: python test_keys.py"""
import os

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

PROVIDERS = {
    "Groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "key": os.getenv("GROQ_API_KEY"),
        "model": os.getenv("GROQ_MODEL", "openai/gpt-oss-120b"),
    },
    "Gemini": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "key": os.getenv("GEMINI_API_KEY"),
        "model": os.getenv("GEMINI_MODEL"),
    },
}

for name, cfg in PROVIDERS.items():
    print(f"\n=== {name} ===")
    if not cfg["key"]:
        print("  Missing key in .env")
        continue
    client = OpenAI(api_key=cfg["key"], base_url=cfg["base_url"], timeout=30, max_retries=0)
    try:
        if not cfg["model"]:
            # No model set yet: list what's available so you can pick a Flash model
            models = [m.id for m in client.models.list()]
            flash = [m for m in models if "flash" in m.lower()]
            print("  Key works. Flash models available:")
            for m in flash:
                print("   -", m)
            print("  -> Put one of these in .env as GEMINI_MODEL and rerun.")
            continue
        resp = client.chat.completions.create(
            model=cfg["model"],
            messages=[{"role": "user", "content": "Reply with exactly: OK"}],
        )
        print(f"  Model: {cfg['model']}")
        print(f"  Reply: {resp.choices[0].message.content.strip()}")
        print("  Working.")
    except Exception as e:
        print(f"  Failed: {e}")