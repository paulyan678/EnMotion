from scripts.check_duplicate_filenames import tracked_conflict_copies


def test_repository_has_no_tracked_conflict_copy_filenames():
    assert tracked_conflict_copies() == []
