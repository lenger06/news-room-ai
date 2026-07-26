"""
Unit test for main.py's _cleanup_old_logs — the retention half of the log4j-style
rolling policy (rotation-within-a-day is handled by ConcurrentRotatingFileHandler
itself and is verified manually against the real library, not here, since that's
third-party behavior rather than logic of ours to unit test).
"""
import os
import sys
import time
from pathlib import Path

project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import main as main_module


def _touch_with_mtime(path: Path, age_days: float) -> None:
    path.write_text("log content")
    mtime = time.time() - age_days * 86400
    os.utime(path, (mtime, mtime))


def test_cleanup_deletes_files_older_than_retention(tmp_path):
    old_file = tmp_path / "newsroom_2026-06-01.log"
    recent_file = tmp_path / "newsroom_2026-07-25.log"
    _touch_with_mtime(old_file, age_days=40)
    _touch_with_mtime(recent_file, age_days=1)

    main_module._cleanup_old_logs(str(tmp_path), "newsroom_*.log*", retention_days=30)

    assert not old_file.exists()
    assert recent_file.exists()


def test_cleanup_handles_gzipped_rotated_backups(tmp_path):
    old_backup = tmp_path / "newsroom_2026-05-01.log.3.gz"
    _touch_with_mtime(old_backup, age_days=90)

    main_module._cleanup_old_logs(str(tmp_path), "newsroom_*.log*", retention_days=30)

    assert not old_backup.exists()


def test_cleanup_no_matching_files_does_not_raise(tmp_path):
    main_module._cleanup_old_logs(str(tmp_path), "newsroom_*.log*", retention_days=30)


def test_cleanup_ignores_unrelated_files(tmp_path):
    unrelated = tmp_path / "other_file.txt"
    _touch_with_mtime(unrelated, age_days=90)

    main_module._cleanup_old_logs(str(tmp_path), "newsroom_*.log*", retention_days=30)

    assert unrelated.exists()
