import app.domain.monitoring as canonical
import app.monitoring_csv as legacy


def test_monitoring_values_keep_legacy_identity():
    assert legacy.MonitoringPoint is canonical.MonitoringPoint
    assert legacy.validated_soc_percent is canonical.validated_soc_percent
