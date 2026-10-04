"""Deterministic digitization from verified pixel geometry.

Models can propose coordinates, but cannot supply accepted numerical graph values.
Unsupported geometry and unavailable calibration yield explicit abstentions.
"""

import math
import re
from pathlib import Path
from typing import Literal

import numpy as np
from PIL import Image, ImageDraw
from pydantic import BaseModel, Field


class Tick(BaseModel):
    pixel: float
    value: float
    label: str


class AxisSpec(BaseModel):
    scale: Literal["linear", "log"] = "linear"
    ticks: list[Tick]
    unit: str | None = None
    outcome: str | None = None


class PointSpec(BaseModel):
    x_pixel: float
    y_pixel: float
    sample_label: str
    origin: Literal["digitized", "curve_sample"] = "digitized"
    lower_y_pixel: float | None = None
    upper_y_pixel: float | None = None
    uncertainty_type: Literal["SD", "SE", "CI", "unknown", "none"] = "none"
    # An RGB color is optional; a local pixel component still has to exist.
    color: list[int] | None = None


class PlotPlan(BaseModel):
    figure_id: str
    kind: Literal["scatter", "line", "bar", "unsupported"]
    panel: str | None = None
    series: str
    outcome: str
    unit: str | None = None
    caption_excerpt: str
    plot_bounds: list[float]
    x_axis: AxisSpec
    y_axis: AxisSpec
    points: list[PointSpec] = Field(default_factory=list)
    abstention_reason: str | None = None


class MicroscopyPlan(BaseModel):
    figure_id: str
    sample_label: str
    panel: str | None = None
    caption_excerpt: str
    roi: list[float]
    scale_bar: list[float]
    scale_value: float = Field(gt=0)
    scale_unit: str
    scale_label: str
    threshold: int = Field(default=128, ge=0, le=255)
    polarity: Literal["dark", "light"] = "dark"
    min_area_pixels: int = Field(default=12, ge=3)
    max_area_pixels: int = Field(default=100000, ge=3)


class FigureReview(BaseModel):
    plots: list[PlotPlan] = Field(default_factory=list)
    microscopy: list[MicroscopyPlan] = Field(default_factory=list)
    abstentions: list[str] = Field(default_factory=list)


class DigitizedPoint(BaseModel):
    sample_label: str
    x: float
    y: float
    x_uncertainty: float
    y_uncertainty: float
    lower: float | None = None
    upper: float | None = None
    uncertainty_type: str
    origin: str
    pixel: list[float]


class DigitizationResult(BaseModel):
    figure_id: str
    status: Literal["verified_geometry", "abstained"]
    points: list[DigitizedPoint] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    calibration: dict = Field(default_factory=dict)
    overlay_path: str | None = None


def calibrate_axis(axis: AxisSpec, max_residual_pixels: float = 1.5) -> dict:
    if len(axis.ticks) < 3:
        raise ValueError("At least three labeled ticks are required for geometry verification")
    pixels = np.array([tick.pixel for tick in axis.ticks], dtype=float)
    values = np.array([tick.value for tick in axis.ticks], dtype=float)
    if not np.all(np.isfinite(pixels)) or not np.all(np.isfinite(values)):
        raise ValueError("Non-finite calibration ticks")
    if len(np.unique(pixels)) != len(pixels) or len(np.unique(values)) != len(values):
        raise ValueError("Tick coordinates and values must be distinct")
    order = np.argsort(pixels)
    differences = np.diff(values[order])
    if not (np.all(differences > 0) or np.all(differences < 0)):
        raise ValueError("Axis tick values are non-monotonic or contain a broken axis")
    if axis.scale == "log":
        if np.any(values <= 0):
            raise ValueError("Logarithmic tick values must be positive")
        values = np.log10(values)
    slope, intercept = np.polyfit(pixels, values, 1)
    if abs(slope) < 1e-15:
        raise ValueError("Degenerate calibration")
    residual = float(np.max(np.abs(values - (slope * pixels + intercept))) / abs(slope))
    if residual > max_residual_pixels:
        raise ValueError("Tick geometry fails calibration residual tolerance")
    return {"scale": axis.scale, "slope": float(slope), "intercept": float(intercept),
            "pixel_min": float(pixels.min()), "pixel_max": float(pixels.max()),
            "max_residual_pixels": residual, "ticks": [tick.model_dump() for tick in axis.ticks]}


def coordinate(calibration: dict, pixel: float, *, allow_extrapolation: bool = False) -> float:
    if not math.isfinite(pixel):
        raise ValueError("Non-finite point coordinate")
    if not allow_extrapolation and not calibration["pixel_min"] <= pixel <= calibration["pixel_max"]:
        raise ValueError("Point lies outside verified tick calibration")
    transformed = calibration["slope"] * pixel + calibration["intercept"]
    return 10 ** transformed if calibration["scale"] == "log" else transformed


def _uncertainty(calibration: dict, pixel: float, pixel_error: float = 1.0) -> float:
    center = coordinate(calibration, pixel)
    low = coordinate(calibration, pixel - pixel_error, allow_extrapolation=True)
    high = coordinate(calibration, pixel + pixel_error, allow_extrapolation=True)
    return max(abs(low - center), abs(high - center))


def _check_bounds(bounds: list[float], width: int, height: int) -> tuple[float, float, float, float]:
    if len(bounds) != 4 or not all(math.isfinite(v) for v in bounds):
        raise ValueError("A finite four-coordinate bounding box is required")
    x0, y0, x1, y1 = bounds
    if x0 < 0 or y0 < 0 or x1 > width or y1 > height or x1 <= x0 or y1 <= y0:
        raise ValueError("Figure bounding box is outside image bounds")
    return x0, y0, x1, y1


def _pixel_support(array: np.ndarray, x: float, y: float, color: list[int] | None = None) -> bool:
    xi, yi = int(round(x)), int(round(y))
    radius = 3
    patch = array[max(0, yi - radius):yi + radius + 1, max(0, xi - radius):xi + radius + 1, :3]
    if not patch.size:
        return False
    if color is not None:
        if len(color) != 3 or any(v < 0 or v > 255 for v in color):
            return False
        distance = np.linalg.norm(patch.astype(float) - np.array(color), axis=2)
        return bool(np.count_nonzero(distance < 55) >= 3)
    return bool(np.count_nonzero(np.min(patch, axis=2) < 180) >= 3)


def digitize_plot(image_path: Path, plan: PlotPlan, *, verified_tick_labels: set[str],
                  overlay_path: Path | None = None) -> DigitizationResult:
    """Calculate only after labels, bounds and visible pixel support are checked.

    A passing result verifies calibration and local image support, not semantic
    interpretation of series/sample identity. The pipeline retains that limit.
    """
    result = DigitizationResult(figure_id=plan.figure_id, status="abstained")
    try:
        if plan.kind == "unsupported":
            raise ValueError(plan.abstention_reason or "Unsupported plot type")
        if not plan.points:
            raise ValueError("No supported experimental markers or curve samples proposed")
        for axis in (plan.x_axis, plan.y_axis):
            if any(tick.label.strip() not in verified_tick_labels for tick in axis.ticks):
                raise ValueError("Tick label lacks PDF/OCR support")
            for tick in axis.ticks:
                cleaned = tick.label.strip().replace("−", "-").replace(",", "")
                try:
                    parsed = float(cleaned)
                except ValueError:
                    raise ValueError("Unsupported tick label notation; explicit verification required") from None
                if not math.isclose(parsed, tick.value, rel_tol=1e-8, abs_tol=1e-12):
                    raise ValueError("Proposed tick value disagrees with printed label")
        xcal = calibrate_axis(plan.x_axis)
        ycal = calibrate_axis(plan.y_axis)
        with Image.open(image_path) as source:
            image = source.convert("RGB")
        bounds = _check_bounds(plan.plot_bounds, image.width, image.height)
        if not (bounds[0] <= xcal["pixel_min"] < xcal["pixel_max"] <= bounds[2]
                and bounds[1] <= ycal["pixel_min"] < ycal["pixel_max"] <= bounds[3]):
            raise ValueError("Calibration ticks lie outside plot bounds")
        pixels = np.asarray(image)
        draw = ImageDraw.Draw(image)
        draw.rectangle(bounds, outline="#1565c0", width=2)
        for point in plan.points:
            if not bounds[0] <= point.x_pixel <= bounds[2] or not bounds[1] <= point.y_pixel <= bounds[3]:
                raise ValueError("Proposed marker lies outside plot bounds")
            if not _pixel_support(pixels, point.x_pixel, point.y_pixel, point.color):
                raise ValueError("Proposed marker has no local pixel support")
            x, y = coordinate(xcal, point.x_pixel), coordinate(ycal, point.y_pixel)
            low = high = None
            if (point.lower_y_pixel is None) != (point.upper_y_pixel is None):
                raise ValueError("Both error bar endpoints must be supplied")
            if point.lower_y_pixel is not None:
                for endpoint in (point.lower_y_pixel, point.upper_y_pixel):
                    if not _pixel_support(pixels, point.x_pixel, endpoint, point.color):
                        raise ValueError("Error bar endpoint has no local pixel support")
                low, high = sorted([coordinate(ycal, point.lower_y_pixel), coordinate(ycal, point.upper_y_pixel)])
                if not low <= y <= high:
                    raise ValueError("Error bars do not enclose the marker")
                draw.line((point.x_pixel, point.lower_y_pixel, point.x_pixel, point.upper_y_pixel), fill="#d32f2f", width=2)
            result.points.append(DigitizedPoint(sample_label=point.sample_label, x=x, y=y,
                                                 x_uncertainty=_uncertainty(xcal, point.x_pixel),
                                                 y_uncertainty=_uncertainty(ycal, point.y_pixel),
                                                 lower=low, upper=high, uncertainty_type=point.uncertainty_type,
                                                 origin=point.origin, pixel=[point.x_pixel, point.y_pixel]))
            draw.ellipse((point.x_pixel - 6, point.y_pixel - 6, point.x_pixel + 6, point.y_pixel + 6), outline="#d32f2f", width=2)
        result.status = "verified_geometry"
        result.calibration = {"x": xcal, "y": ycal, "plot_bounds": list(bounds),
                              "pixel_uncertainty": 1.0, "panel": plan.panel, "series": plan.series}
        result.notes.append("Pixel support does not independently establish series identity or marker semantics")
        if overlay_path is not None:
            overlay_path.parent.mkdir(parents=True, exist_ok=True)
            image.save(overlay_path)
            result.overlay_path = str(overlay_path)
    except (ValueError, OSError, OverflowError) as error:
        result.points = []
        result.notes.append(str(error))
    return result


def _components(mask: np.ndarray) -> list[list[tuple[int, int]]]:
    """Four-connected components without an OpenCV runtime dependency."""
    height, width = mask.shape
    seen = np.zeros(mask.shape, dtype=bool)
    components = []
    for y, x in zip(*np.where(mask)):
        if seen[y, x]:
            continue
        queue, component = [(int(y), int(x))], []
        seen[y, x] = True
        while queue:
            cy, cx = queue.pop()
            component.append((cy, cx))
            for ny, nx in ((cy - 1, cx), (cy + 1, cx), (cy, cx - 1), (cy, cx + 1)):
                if 0 <= ny < height and 0 <= nx < width and mask[ny, nx] and not seen[ny, nx]:
                    seen[ny, nx] = True
                    queue.append((ny, nx))
        components.append(component)
    return components


def segment_microscopy(image_path: Path, plan: MicroscopyPlan, *, verified_scale_labels: set[str],
                        overlay_path: Path | None = None) -> dict:
    """Measure separated 2D objects; exclude borders and unresolved touching objects.

    Outputs are projected equivalent-circle diameters, never hydrodynamic DLS
    diameters. The object count is a technical count, not independent replicate n.
    """
    result = {"figure_id": plan.figure_id, "status": "abstained", "objects": [], "notes": []}
    try:
        if plan.scale_label.strip() not in verified_scale_labels:
            raise ValueError("Scale bar label lacks PDF/OCR support")
        match = re.fullmatch(r"\s*([0-9]+(?:\.[0-9]+)?)\s*([^\d\s]+)\s*", plan.scale_label)
        if not match or not math.isclose(float(match.group(1)), plan.scale_value, rel_tol=1e-8):
            raise ValueError("Scale bar label disagrees with physical calibration")
        from .normalization import compatible_length_unit
        if not compatible_length_unit(plan.scale_unit, match.group(2)):
            raise ValueError("Scale bar units disagree with printed calibration")
        with Image.open(image_path) as source:
            image = source.convert("RGB")
        roi = _check_bounds(plan.roi, image.width, image.height)
        if len(plan.scale_bar) != 4:
            raise ValueError("Scale bar needs two endpoints")
        sx0, sy0, sx1, sy1 = plan.scale_bar
        if not (0 <= sx0 < image.width and 0 <= sx1 < image.width and 0 <= sy0 < image.height and 0 <= sy1 < image.height):
            raise ValueError("Scale bar lies outside the image")
        length = math.hypot(sx1 - sx0, sy1 - sy0)
        if length < 10:
            raise ValueError("Scale bar is too short for reliable calibration")
        grayscale = np.asarray(image.convert("L"))
        # Verify a continuous physical bar along the proposed segment.
        xs = np.linspace(sx0, sx1, max(10, int(length))).astype(int)
        ys = np.linspace(sy0, sy1, max(10, int(length))).astype(int)
        samples = grayscale[ys, xs]
        if not (np.mean(samples < 100) >= 0.85 or np.mean(samples > 220) >= 0.85):
            raise ValueError("Scale bar lacks continuous pixel support")
        perpendicular_x, perpendicular_y = -(sy1 - sy0) / length, (sx1 - sx0) / length
        nearby_x = np.clip((xs + perpendicular_x * 6).astype(int), 0, image.width - 1)
        nearby_y = np.clip((ys + perpendicular_y * 6).astype(int), 0, image.height - 1)
        if np.mean(np.abs(samples.astype(float) - grayscale[nearby_y, nearby_x].astype(float)) > 45) < 0.75:
            raise ValueError("Proposed scale bar lacks contrast against its surroundings")
        x0, y0, x1, y1 = map(int, roi)
        region = grayscale[y0:y1, x0:x1]
        if region.size > 4_000_000:
            raise ValueError("Microscopy ROI exceeds segmentation safety bound")
        mask = region < plan.threshold if plan.polarity == "dark" else region > plan.threshold
        draw = ImageDraw.Draw(image)
        for component in _components(mask):
            if not plan.min_area_pixels <= len(component) <= plan.max_area_pixels:
                continue
            yy, xx = np.array(component).T
            if xx.min() == 0 or yy.min() == 0 or xx.max() == region.shape[1] - 1 or yy.max() == region.shape[0] - 1:
                continue
            width, height = int(xx.max() - xx.min() + 1), int(yy.max() - yy.min() + 1)
            fill = len(component) / (width * height)
            # Conservative simple-object mask; touching/irregular components abstain.
            if min(width, height) / max(width, height) < 0.45 or fill < 0.55:
                result["notes"].append("Excluded an irregular or potentially touching component")
                continue
            from scipy.ndimage import distance_transform_edt, maximum_filter
            object_mask = np.zeros((height + 2, width + 2), dtype=bool)
            object_mask[yy - yy.min() + 1, xx - xx.min() + 1] = True
            distances = distance_transform_edt(object_mask)
            neighborhood = max(3, int(math.sqrt(len(component)) / 4) * 2 + 1)
            peaks = (distances == maximum_filter(distances, size=neighborhood)) & (distances > distances.max() * 0.55)
            if len(_components(peaks)) > 1:
                result["notes"].append("Excluded a component with multiple centers consistent with touching objects")
                continue
            diameter_pixels = math.sqrt(4 * len(component) / math.pi)
            size = diameter_pixels * plan.scale_value / length
            box = [int(xx.min()) + x0, int(yy.min()) + y0, int(xx.max()) + x0, int(yy.max()) + y0]
            result["objects"].append({"area_pixels": len(component), "diameter": size,
                                       "unit": plan.scale_unit, "box": box,
                                       "digitization_uncertainty": 2 * plan.scale_value / length})
            draw.rectangle(box, outline="#d32f2f", width=2)
        if not result["objects"]:
            raise ValueError("No separated objects pass conservative segmentation checks")
        result.update(status="verified_geometry", n_technical=len(result["objects"]), n_independent=None,
                      method="2D projected equivalent-circle diameter", scale_value=plan.scale_value,
                      scale_unit=plan.scale_unit, scale_bar_pixels=plan.scale_bar,
                      units_per_pixel=plan.scale_value / length)
        result["notes"].append("Threshold segmentation requires visual review; object count is not independent experimental n")
        if overlay_path:
            overlay_path.parent.mkdir(parents=True, exist_ok=True)
            draw.line(plan.scale_bar, fill="#1565c0", width=3)
            image.save(overlay_path)
            result["overlay_path"] = str(overlay_path)
    except (ValueError, OSError) as error:
        result["objects"] = []
        result["notes"].append(str(error))
    return result
