"""
LOCOMO dataset split.

DEV set (conversations 0–4): used for tuning extraction prompts,
strategy thresholds, and retrieval parameters. Run freely.

TEST set (conversations 5–9): held out for final paper numbers.
Never use during development. Run once when reporting final results.

Enforced via --split dev|test flags in harness.py.
"""

DEV = ["conv-26", "conv-30", "conv-41", "conv-42", "conv-43"]
TEST = ["conv-44", "conv-47", "conv-48", "conv-49", "conv-50"]
ALL = DEV + TEST
