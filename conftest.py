import sys
from pathlib import Path

# リポジトリ直下を import path に入れて `import core` などを解決する
sys.path.insert(0, str(Path(__file__).resolve().parent))
