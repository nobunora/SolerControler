import app.configuration.environment as canonical
import scripts.backup_drive as backup_script


def test_backup_drive_uses_canonical_dotenv_loader() -> None:
    assert backup_script.load_dotenv_if_present is canonical.load_dotenv_if_present
