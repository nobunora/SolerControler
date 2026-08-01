import app.configuration.environment as canonical
import app.utils as legacy


def test_environment_helpers_keep_legacy_identity():
    for name in (
        "env",
        "env_bool",
        "env_float",
        "env_float_clamped",
        "env_int",
        "load_dotenv_if_present",
    ):
        assert getattr(legacy, name) is getattr(canonical, name)
