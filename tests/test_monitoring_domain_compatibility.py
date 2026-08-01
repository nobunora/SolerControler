import app.domain.monitoring as canonical
import app.monitoring_csv as legacy


def test_monitoring_values_keep_legacy_identity():
    assert legacy.MonitoringPoint is canonical.MonitoringPoint
    assert legacy.validated_soc_percent is canonical.validated_soc_percent


def test_monitoring_csv_keeps_legacy_iterator_identity():
    from app.operations.monitoring_csv import iter_monitoring_points

    assert legacy.iter_monitoring_points is iter_monitoring_points
