from __future__ import annotations

from app.categories import Detector
from app.detectors.accessibility import AccessibilityDetector
from app.detectors.backend import BackendDetector
from app.detectors.documentation import DocumentationDetector
from app.detectors.performance import PerformanceDetector
from app.detectors.security import SecurityDetector
from app.detectors.ui import UiDetector

_DETECTORS: dict[str, Detector] = {
    "ui": UiDetector(),
    "backend": BackendDetector(),
    "security": SecurityDetector(),
    "performance": PerformanceDetector(),
    "documentation": DocumentationDetector(),
    "accessibility": AccessibilityDetector(),
}


def get_detector(category_key: str) -> Detector:
    return _DETECTORS[category_key]
