import app.parsing.numbers as canonical
import app.utils as legacy


def test_number_helpers_keep_legacy_identity():
    for name in ("to_float", "to_int", "parse_csv_float"):
        assert getattr(legacy, name) is getattr(canonical, name)
