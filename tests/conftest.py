import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

# Force deterministic, offline behaviour for the unit-test suite:
# - keyword-only retrieval (no Qdrant network calls),
# - no LLM synthesis (template answers stay byte-for-byte stable).
os.environ["PARCELPILOT_TESTING"] = "1"