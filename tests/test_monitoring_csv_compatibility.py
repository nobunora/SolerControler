import app.monitoring_csv as legacy
import app.operations.monitoring_csv as canonical


def test_monitoring_csv_module_reexports_public_contract():
    assert legacy.__all__ == ["MonitoringPoint", "validated_soc_percent", "iter_monitoring_points"]
    assert legacy.iter_monitoring_points is canonical.iter_monitoring_points
