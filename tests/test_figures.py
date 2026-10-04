"""Synthetic calibrated image tests; no private publication figures included."""


import pytest
from PIL import Image, ImageDraw

from livingmeta.extraction.figures import (AxisSpec, MicroscopyPlan, PlotPlan, PointSpec, Tick,
                                         calibrate_axis, coordinate, digitize_plot, segment_microscopy)


def axis(log=False, reverse=False):
    values = [1, 10, 100] if log else [0, 5, 10]
    if reverse:
        values.reverse()
    return AxisSpec(scale="log" if log else "linear", ticks=[Tick(pixel=pixel, value=value, label=str(value))
                    for pixel, value in zip([20, 100, 180], values)])


def plot_image(path, errors=False):
    image = Image.new("RGB", (200, 200), "white")
    draw = ImageDraw.Draw(image)
    draw.line((20, 180, 180, 180), fill="black", width=2)
    draw.line((20, 20, 20, 180), fill="black", width=2)
    draw.ellipse((96, 96, 104, 104), fill="red")
    if errors:
        draw.line((100, 80, 100, 120), fill="red", width=2)
        draw.line((96, 80, 104, 80), fill="red", width=2)
        draw.line((96, 120, 104, 120), fill="red", width=2)
    image.save(path)


def plot_plan(**updates):
    data = dict(figure_id="Figure 1", kind="scatter", series="Synthetic A", outcome="Droplet_Size_um", unit="um",
                caption_excerpt="Synthetic plot", plot_bounds=[20, 20, 180, 180], x_axis=axis(), y_axis=axis(reverse=True),
                points=[PointSpec(x_pixel=100, y_pixel=100, sample_label="Synthetic A", color=[255, 0, 0])])
    data.update(updates)
    return PlotPlan(**data)


def test_linear_and_log_calibration():
    assert coordinate(calibrate_axis(axis()), 100) == pytest.approx(5)
    assert coordinate(calibrate_axis(axis(log=True)), 100) == pytest.approx(10)
    assert coordinate(calibrate_axis(axis(reverse=True)), 20) == pytest.approx(10)
    with pytest.raises(ValueError, match="outside"):
        coordinate(calibrate_axis(axis()), 190)


def test_broken_axis_and_insufficient_ticks_abstain():
    broken = axis()
    broken.ticks[1].value = 9
    with pytest.raises(ValueError, match="residual"):
        calibrate_axis(broken)
    insufficient = axis()
    insufficient.ticks.pop()
    with pytest.raises(ValueError, match="three"):
        calibrate_axis(insufficient)
    bad_log = axis(log=True)
    bad_log.ticks[0].value = 0
    with pytest.raises(ValueError, match="positive"):
        calibrate_axis(bad_log)


def test_marker_digitization_errorbars_and_overlay(tmp_path):
    path = tmp_path / "synthetic-plot.png"
    plot_image(path, errors=True)
    point = PointSpec(x_pixel=100, y_pixel=100, lower_y_pixel=120, upper_y_pixel=80,
                      sample_label="Synthetic A", color=[255, 0, 0], uncertainty_type="SD")
    result = digitize_plot(path, plot_plan(points=[point]), verified_tick_labels={"0", "5", "10"}, overlay_path=tmp_path / "overlay.png")
    assert result.status == "verified_geometry"
    assert result.points[0].y == pytest.approx(5)
    assert result.points[0].lower == pytest.approx(3.75)
    assert result.points[0].upper == pytest.approx(6.25)
    assert result.points[0].y_uncertainty > 0
    assert (tmp_path / "overlay.png").exists()


def test_unreadable_labels_unsupported_figures_and_fake_markers_abstain(tmp_path):
    path = tmp_path / "synthetic-plot.png"
    plot_image(path)
    result = digitize_plot(path, plot_plan(), verified_tick_labels={"0", "10"})
    assert result.status == "abstained"
    assert "Tick label" in result.notes[0]
    assert digitize_plot(path, plot_plan(kind="unsupported"), verified_tick_labels={"0", "5", "10"}).status == "abstained"
    fake = PointSpec(x_pixel=60, y_pixel=60, sample_label="Fake", color=[255, 0, 0])
    assert digitize_plot(path, plot_plan(points=[fake]), verified_tick_labels={"0", "5", "10"}).status == "abstained"


def test_calibration_values_cannot_disagree_with_tick_labels(tmp_path):
    path = tmp_path / "synthetic-plot.png"
    plot_image(path)
    plan = plot_plan()
    plan.y_axis.ticks[0].value = 100
    result = digitize_plot(path, plan, verified_tick_labels={"0", "5", "10"})
    assert result.status == "abstained"
    assert "disagrees" in result.notes[0]


def test_microscopy_scale_segmentation_and_technical_count(tmp_path):
    path = tmp_path / "synthetic-microscopy.png"
    image = Image.new("RGB", (160, 160), "white")
    draw = ImageDraw.Draw(image)
    draw.ellipse((30, 30, 50, 50), fill="black")
    draw.ellipse((80, 60, 100, 80), fill="black")
    draw.line((30, 140, 130, 140), fill="black", width=3)
    image.save(path)
    plan = MicroscopyPlan(figure_id="Figure 2", sample_label="Synthetic A", caption_excerpt="Synthetic microscopy",
                          roi=[10, 10, 140, 120], scale_bar=[30, 140, 130, 140], scale_value=10,
                          scale_unit="um", scale_label="10 um")
    result = segment_microscopy(path, plan, verified_scale_labels={"10 um"}, overlay_path=tmp_path / "overlay.png")
    assert result["status"] == "verified_geometry"
    assert result["n_technical"] == 2 and result["n_independent"] is None
    assert result["objects"][0]["diameter"] == pytest.approx(2.1, abs=0.2)
    assert "2D projected" in result["method"]
    plan.scale_bar = [30, 130, 130, 130]
    assert segment_microscopy(path, plan, verified_scale_labels={"10 um"})["status"] == "abstained"
    plan.scale_value = 100
    assert segment_microscopy(path, plan, verified_scale_labels={"10 um"})["status"] == "abstained"


def test_touching_microscopy_objects_are_excluded(tmp_path):
    path = tmp_path / "touching-objects.png"
    image = Image.new("RGB", (160, 160), "white")
    draw = ImageDraw.Draw(image)
    draw.ellipse((30, 30, 50, 50), fill="black")
    draw.ellipse((48, 30, 68, 50), fill="black")
    draw.line((30, 140, 130, 140), fill="black", width=3)
    image.save(path)
    plan = MicroscopyPlan(figure_id="Figure 3", sample_label="Synthetic touching objects", caption_excerpt="Synthetic",
                          roi=[10, 10, 140, 120], scale_bar=[30, 140, 130, 140], scale_value=10,
                          scale_unit="um", scale_label="10 um")
    result = segment_microscopy(path, plan, verified_scale_labels={"10 um"})
    assert result["status"] == "abstained"
    assert any("multiple centers" in note for note in result["notes"])
