"""The Phase 4 revision must extend the complete, single migration chain."""
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


ROOT = Path(__file__).resolve().parents[1]


def test_phase4_is_the_only_head_and_no_revision_file_is_orphaned():
    scripts = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini")))
    revisions = list(scripts.walk_revisions())
    files = list((ROOT / "alembic" / "versions").glob("*.py"))
    assert scripts.get_heads() == ["f8137b4a2c91"]
    assert len(revisions) == len(files)
    assert all(current.down_revision == following.revision
               for current, following in zip(revisions, revisions[1:]))
    assert revisions[-1].down_revision is None
