import app.csv_merge as legacy
import app.operations.csv_merge as canonical


def test_csv_merge_legacy_exports_are_explicit_and_identical():
    assert legacy.__all__ == [
        "DEFAULT_EXCLUDED_DIR_NAMES",
        "CsvMergeResult",
        "discover_csv_files",
        "merge_csv_files",
    ]
    for name in legacy.__all__:
        assert getattr(legacy, name) is getattr(canonical, name)
