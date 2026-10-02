"""
Generate an access code for the Claude tier.

    python -m webapp.make_code            # random code, 15 questions in total
    python -m webapp.make_code 20         # random code, 20 questions in total
    python -m webapp.make_code 20 VIVA-2026
    python -m webapp.make_code 20 VIVA-2026 5     # ...and at most 5 questions per day

Prints the plain code (send this to the person) and the ACCESS_CODES entry
(append this to the env var on Render, comma-separated). Only the hash is
stored on the server.
"""
import secrets
import sys

from .config import hash_code

ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # no 0/O/1/I confusion

if __name__ == "__main__":
    quota = int(sys.argv[1]) if len(sys.argv) > 1 else 15
    code = sys.argv[2] if len(sys.argv) > 2 else "-".join(
        "".join(secrets.choice(ALPHABET) for _ in range(4)) for _ in range(3))
    print(f"Code to send:        {code.upper()}")
    daily = f":{int(sys.argv[3])}" if len(sys.argv) > 3 else ""
    print(f"ACCESS_CODES entry:  {hash_code(code)}:{quota}{daily}")
