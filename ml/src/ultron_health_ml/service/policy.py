"""Load the bundled approved policy without consulting prior local artifacts."""

import hashlib
from importlib.resources import files
import json

from ultron_health_ml.features.config import FeatureConfig
from ultron_health_ml.forecast.baselines import FEATURES
from ultron_health_ml.forecast.contract import Targets
from ultron_health_ml.forecast.lifecycle import Point as ForecastPoint
from ultron_health_ml.hybrid.config import Settings
from ultron_health_ml.reduced.protocol import Point as DetectionPoint
from ultron_health_ml.service.schema import DETECTION_FP, FORECAST_FP

BUNDLE_SHA256 = "d2fdb2b227dd6ab5fcc40d40e38dec77742be7886bbfeeaa9fc56056afc29737"


def approved():
    bundle = json.loads(files(__package__).joinpath("approved_policy.json").read_text(encoding="utf-8"))
    canonical = json.dumps(bundle, sort_keys=True, separators=(",", ":")).encode()
    if hashlib.sha256(canonical).hexdigest() != BUNDLE_SHA256:
        raise ValueError("incompatible approved policy")
    detection = Settings.model_validate(bundle["detection"])
    point = DetectionPoint(**bundle["detection_point"])
    targets = Targets.model_validate(bundle["targets"])
    forecast = ForecastPoint(**bundle["forecast_point"])
    if ((FeatureConfig().fingerprint(), FEATURES.fingerprint()) != (DETECTION_FP, FORECAST_FP)
            or point != DetectionPoint("hybrid", 1.35, 2.) or forecast != ForecastPoint("process", "persistence", 1800, .65)
            or targets.targets["process"].min_lead_seconds != 240):
        raise ValueError("incompatible approved engine")
    return detection, point, targets, forecast, bundle["noise"]
