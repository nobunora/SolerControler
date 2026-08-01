import app.domain.constants as canonical
import app.utils as legacy


def test_percent_boundary_legacy_identity():
    assert legacy.clamp_percent is canonical.clamp_percent
