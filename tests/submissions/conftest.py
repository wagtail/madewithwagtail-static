import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts" / "submissions"
sys.path.insert(0, str(SCRIPTS))
