"""Deploy to a Hugging Face Docker Space.

Usage (after `hf auth login` with a write token):
  python deploy_hf.py [--space user/red-pen] [--password PASS]

API keys are read from .env and stored as encrypted Space secrets. If --password is omitted
and the Space is new, a staff password is generated and printed once.
"""
import argparse
import secrets
from pathlib import Path

from dotenv import dotenv_values
from huggingface_hub import CommitOperationAdd, HfApi

ROOT = Path(__file__).parent
FILES = ["Dockerfile", ".dockerignore", "requirements.txt", "server.py", "cli.py",
         *[str(p.relative_to(ROOT)).replace("\\", "/")
           for d in ("grader", "web") for p in (ROOT / d).rglob("*")
           if p.is_file() and "__pycache__" not in p.parts]]

SPACE_README = """---
title: Red Pen
emoji: 🖍️
colorFrom: red
colorTo: blue
sdk: docker
app_port: 8000
pinned: false
short_description: Marks handwritten assignments with Sarvam Vision and LLMs
---

Handwritten assignment marking: Sarvam Vision reads the scripts, an OpenRouter LLM marks them,
teachers review and approve, Bulbul v4 Flash reads feedback aloud.
Source: https://github.com/SuPythony/sarvam-buildathon
"""


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--space", help="repo id, default <your-username>/red-pen")
    p.add_argument("--password", help="staff sign-in password (APP_PASSWORD)")
    a = p.parse_args()

    api = HfApi()
    space = a.space or f"{api.whoami()['name']}/red-pen"
    env = dotenv_values(ROOT / ".env")
    for k in ("SARVAM_API_KEY", "OPENROUTER_API_KEY"):
        if not env.get(k):
            raise SystemExit(f"{k} missing from .env")

    new = not api.repo_exists(space, repo_type="space")
    api.create_repo(space, repo_type="space", space_sdk="docker", exist_ok=True)

    api.add_space_secret(space, "SARVAM_API_KEY", env["SARVAM_API_KEY"])
    api.add_space_secret(space, "OPENROUTER_API_KEY", env["OPENROUTER_API_KEY"])
    password = a.password or (secrets.token_urlsafe(9) if new else None)
    if password:
        api.add_space_secret(space, "APP_PASSWORD", password)
    api.add_space_variable(space, "OPENROUTER_MODEL",
                           env.get("OPENROUTER_MODEL") or "deepseek/deepseek-v4.1-flash")

    ops = [CommitOperationAdd(f, str(ROOT / f)) for f in FILES]
    ops.append(CommitOperationAdd("README.md", SPACE_README.encode()))
    api.create_commit(space, repo_type="space", operations=ops, commit_message="Deploy Red Pen")

    host = space.replace("/", "-").replace("_", "-").replace(".", "-").lower()
    print(f"Space: https://huggingface.co/spaces/{space}")
    print(f"App:   https://{host}.hf.space")
    if password:
        print(f"Staff password: {password}")
    else:
        print("Staff password unchanged (pass --password to set a new one).")


if __name__ == "__main__":
    main()
