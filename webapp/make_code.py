"""
Generate an access code for the Claude tier.

    python -m webapp.make_code            # random code, 25 questions
    python -m webapp.make_code 50         # random code, 50 questions
    python -m webapp.make_code 50 VIVA-2026

Prints the plain code (send this to the person) and the ACCESS_CODES entry
(append this to the env var on Render, comma-separated). Only the hash is
stored on the server.
"""
import secrets
import sys

from .config import hash_code

ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # no 0/O/1/I confusion

if __name__ == "__main__":
    quota = int(sys.argv[1]) if len(sys.argv) > 1 else 25
    code = sys.argv[2] if len(sys.argv) > 2 else "-".join(
        "".join(secrets.choice(ALPHABET) for _ in range(4)) for _ in range(3))
    print(f"Code to send:        {code.upper()}")
    print(f"ACCESS_CODES entry:  {hash_code(code)}:{quota}")
