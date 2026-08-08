"""Drawing-level boundary graph and cross-entity closed-profile inference.

Entity topology in the scan database is intentionally local to one AutoCAD
handle.  Vision, however, perceives a contour even when it is drafted as four
independent LINE entities.  This module joins compatible line primitives at
nearby endpoints and promotes bounded planar faces into evidence-bearing shape
hypotheses.  The half-edge face walk deliberately handles shared edges and
branches without enumerating every graph cycle.  It is pure metadata analysis
and never touches the DWG.
"""

from __future__ import annotations

import heapq
import math
from bisect import bisect_left, bisect_right
from collections import defaultdict
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from src.cad_database import CADDatabase

from .common import (
    all_entities,
    all_topology_primitives,
    bbox_from_row,
    bbox_union,
    entity_geometry,
    entity_type,
    get_db,
    stable_id,
)

Point2 = Tuple[float, float]


def _public_handle(entity: Dict[str, Any]) -> str:
    return str(entity.get("native_handle") or entity.get("handle") or "")


def _boundary_family(entity: Dict[str, Any]) -> str:
    kind = entity_type(entity).replace(" ", "")
    if kind in {"line", "acdbline"}:
        return "line"
    if "polyline" in kind and not any(token in kind for token in ("dimension", "leader")):
        return "polyline"
    if kind in {"arc", "acdbarc"}:
        return "arc"
    if kind in {"circle", "acdbcircle"}:
        return "circle"
    if kind in {"ellipse", "acdbellipse"}:
        return "ellipse"
    if kind in {"spline", "acdbspline"}:
        return "spline"
    return ""


def _is_structural_linework(entity: Dict[str, Any]) -> bool:
    if entity.get("visible") in {False, 0, "0"}:
        return False
    kind = entity_type(entity)
    if not _boundary_family(entity):
        return False
    if any(token in kind for token in ("dimension", "leader", "xline", "ray")):
        return False
    style = " ".join(str(entity.get(key) or "").lower() for key in ("layer", "linetype", "name"))
    return not any(token in style for token in (
        "center", "centre", "hidden", "dimension", "dim-", "-dim", "hatch", "text",
    ))


def _point2(value: Any) -> Optional[Point2]:
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        return None
    try:
        point = (float(value[0]), float(value[1]))
        return point if all(math.isfinite(component) for component in point) else None
    except (TypeError, ValueError):
        return None


def _point_z(value: Any, default: float = 0.0) -> float:
    if isinstance(value, (list, tuple)) and len(value) > 2:
        try:
            result = float(value[2])
            if math.isfinite(result):
                return result
        except (TypeError, ValueError):
            pass
    try:
        result = float(default)
    except (TypeError, ValueError):
        return 0.0
    return result if math.isfinite(result) else 0.0


def _has_invalid_authored_z(value: Any) -> bool:
    if not isinstance(value, (list, tuple)) or len(value) <= 2:
        return False
    try:
        return not math.isfinite(float(value[2]))
    except (TypeError, ValueError):
        return True


def _has_invalid_optional_number(value: Any) -> bool:
    if value is None:
        return False
    try:
        return not math.isfinite(float(value))
    except (TypeError, ValueError):
        return True


def _adaptive_parametric_samples(point_at: Any,
                                 start_parameter: float,
                                 sweep: float,
                                 scale: float,
                                 max_segments: int = 2048,
                                 relative_chord_error: float = 1e-3,
                                 absolute_error_floor: Optional[float] = None) -> Tuple[
                                     List[Point2], List[float], float, float, bool
                                 ]:
    """Sample a parametric curve with a bounded midpoint chord error.

    A fixed angular step undersamples very shallow arcs: two segments can lose
    a large fraction of a thin lens's area even though the sweep is tiny.  The
    target below is relative to the curve's own visible deviation from its
    chord, with a numeric floor tied to the analytic scale.
    """
    if not all(math.isfinite(float(value)) for value in (
        start_parameter, sweep, scale, relative_chord_error
    )) or sweep == 0.0 or scale <= 0.0:
        raise ValueError("non-finite or degenerate parametric curve")
    initial_count = max(1, int(math.ceil(abs(sweep) / (math.pi / 2.0))))
    parameters = [
        start_parameter + sweep * index / initial_count
        for index in range(initial_count + 1)
    ]

    def chord_error(left: float, right: float) -> float:
        start = point_at(left)
        end = point_at(right)
        midpoint = point_at((left + right) / 2.0)
        chord_midpoint = ((start[0] + end[0]) / 2.0, (start[1] + end[1]) / 2.0)
        return math.dist(midpoint, chord_midpoint)

    def interval_error_bound(left: float, right: float) -> float:
        # Linear interpolation error is bounded by M*delta^2/8 when M bounds
        # ||p''||. `scale` is r for a circle and a conservative matrix norm
        # for the ellipse axes.
        return abs(float(scale)) * abs(right - left) ** 2 / 8.0

    characteristic_error = max(
        (chord_error(parameters[index], parameters[index + 1])
         for index in range(len(parameters) - 1)),
        default=0.0,
    )
    probe_points = [
        point_at(parameter)
        for left, right in zip(parameters, parameters[1:])
        for parameter in (left, (left + right) / 2.0, right)
    ]
    probe_magnitude = max(
        (abs(float(value)) for point in probe_points for value in point),
        default=1.0,
    )
    representable_floor = max(
        math.ulp(max(probe_magnitude, 1.0)) * 4.0,
        math.ulp(max(abs(float(scale)), 1.0)) * 8.0,
        1e-12,
    )
    numeric_error_floor = (
        representable_floor
        if absolute_error_floor is None
        else max(float(absolute_error_floor), representable_floor)
    )
    target_error = max(
        numeric_error_floor,
        characteristic_error * max(float(relative_chord_error), 1e-9),
        1e-12,
    )
    capped = False
    for _ in range(16):
        refined = [parameters[0]]
        split_count = 0
        for left, right in zip(parameters, parameters[1:]):
            if interval_error_bound(left, right) > target_error:
                if len(parameters) - 1 + split_count >= max_segments:
                    capped = True
                else:
                    refined.append((left + right) / 2.0)
                    split_count += 1
            refined.append(right)
        parameters = refined
        if not split_count or capped:
            break
    samples = [point_at(parameter) for parameter in parameters]
    if any(
        not all(math.isfinite(float(value)) for value in point)
        for point in samples
    ):
        raise ValueError("parametric evaluator returned non-finite samples")
    error_bound = max(
        (interval_error_bound(parameters[index], parameters[index + 1])
         for index in range(len(parameters) - 1)),
        default=0.0,
    )
    capped = capped or error_bound > target_error * (1.0 + 1e-9)
    normalized_parameters = [
        (parameter - start_parameter) / sweep for parameter in parameters
    ]
    return samples, normalized_parameters, target_error, error_bound, capped


def _sample_curve_primitive(primitive: Dict[str, Any],
                            entity: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Return directed samples for an open analytic or spline curve."""
    properties = primitive.get("properties") or {}
    geometry = entity_geometry(entity)
    role = str(primitive.get("role") or properties.get("curve_kind") or "").lower()
    normal = properties.get("normal") or geometry.get("normal")
    if normal is not None:
        if not isinstance(normal, (list, tuple)) or len(normal) < 3:
            return None
        try:
            normal_length = math.sqrt(sum(float(value) ** 2 for value in normal[:3]))
            if (
                not math.isfinite(normal_length)
                or normal_length <= 1e-12
                or float(normal[2]) / normal_length < 1.0 - 1e-6
            ):
                return None
        except (TypeError, ValueError):
            return None
    raw_start = properties.get("start_point") or geometry.get("start_point") or geometry.get("start")
    raw_end = properties.get("end_point") or geometry.get("end_point") or geometry.get("end")
    if (
        _has_invalid_authored_z(raw_start)
        or _has_invalid_authored_z(raw_end)
        or _has_invalid_authored_z(geometry.get("center"))
        or _has_invalid_optional_number(primitive.get("z"))
    ):
        return None
    start = _point2(raw_start)
    end = _point2(raw_end)
    center_z = _point_z([0.0, 0.0, primitive.get("z") or 0.0])
    start_z = _point_z(raw_start, center_z)
    end_z = _point_z(raw_end, center_z)
    z_values = [start_z, end_z]
    samples: List[Point2] = []
    sample_z: List[float] = []
    sampling_metadata = properties.get("sampling") or {}
    approximate = bool(sampling_metadata.get("approximate"))
    sampling_method = str(sampling_metadata.get("method") or "")
    sampling_error_target = 0.0
    sampling_error_bound: Optional[float] = 0.0
    sampling_certified = False
    sampling_capped = False
    endpoint_adjustment = 0.0
    endpoint_consistency_limit = 0.0
    sample_parameters: List[float] = []
    curve_center: Optional[Point2] = None
    curve_radius: Optional[float] = None
    curve_major_axis: Optional[Point2] = None
    curve_minor_axis: Optional[Point2] = None
    curve_start_parameter: Optional[float] = None
    curve_sweep: Optional[float] = None
    start_tangent: Optional[Point2] = None
    end_tangent: Optional[Point2] = None
    is_closed_curve = bool(primitive.get("is_closed"))

    if ("arc" in role and "ellipse" not in role) or "circle" in role:
        center = _point2([primitive.get("x"), primitive.get("y")])
        try:
            radius = float(primitive.get("radius"))
            start_parameter = float(
                properties.get("start_parameter")
                if properties.get("start_parameter") is not None else 0.0
            )
            sweep = float(
                properties.get("sweep")
                if properties.get("sweep") is not None
                else (2.0 * math.pi if "circle" in role else 0.0)
            )
        except (TypeError, ValueError):
            return None
        if (
            not center
            or radius <= 0.0
            or sweep <= 0.0
            or sweep > 2.0 * math.pi + 1e-9
            or not all(math.isfinite(value) for value in (
                radius, start_parameter, sweep
            ))
        ):
            return None
        def point_at(parameter: float) -> Point2:
            return (
                center[0] + radius * math.cos(parameter),
                center[1] + radius * math.sin(parameter),
            )
        try:
            (
                samples,
                sample_parameters,
                sampling_error_target,
                sampling_error_bound,
                sampling_capped,
            ) = _adaptive_parametric_samples(
                point_at, start_parameter, sweep, radius
            )
        except (OverflowError, ValueError):
            return None
        curve_center = center
        curve_radius = radius
        curve_start_parameter = start_parameter
        curve_sweep = sweep
        start_tangent = (
            -sweep * radius * math.sin(start_parameter),
            sweep * radius * math.cos(start_parameter),
        )
        end_parameter = start_parameter + sweep
        end_tangent = (
            -sweep * radius * math.sin(end_parameter),
            sweep * radius * math.cos(end_parameter),
        )
        endpoint_adjustment = max(
            math.dist(start, samples[0]) if start is not None else 0.0,
            math.dist(end, samples[-1]) if end is not None else 0.0,
        )
        analytic_midpoint = point_at(start_parameter + sweep / 2.0)
        analytic_chord_midpoint = (
            (samples[0][0] + samples[-1][0]) / 2.0,
            (samples[0][1] + samples[-1][1]) / 2.0,
        )
        visible_curve_deviation = math.dist(
            analytic_midpoint, analytic_chord_midpoint
        )
        endpoint_consistency_limit = max(
            radius * 1e-8,
            visible_curve_deviation * 0.005,
            1e-9,
        )
        if endpoint_adjustment > endpoint_consistency_limit:
            return None
        if start is None:
            start = samples[0]
        else:
            samples[0] = start
        if end is None:
            end = samples[-1]
        else:
            samples[-1] = end
        sampling_error_bound = max(sampling_error_bound, endpoint_adjustment)
        approximate = endpoint_adjustment > max(radius * 1e-8, 1e-10)
        sampling_method = (
            "analytic_adaptive_authored_endpoints"
            if approximate else (sampling_method or "analytic_adaptive")
        )
        sampling_certified = True
    elif "ellipse" in role:
        ellipse_arc_flag = properties.get("is_arc")
        if bool(primitive.get("is_closed")) or ellipse_arc_flag is False:
            is_closed_curve = True
        elif ellipse_arc_flag is True:
            is_closed_curve = False
        else:
            return None
        center = _point2([primitive.get("x"), primitive.get("y")])
        major = _point2(properties.get("major_axis"))
        minor = _point2(properties.get("minor_axis"))
        try:
            ratio = float(properties.get("radius_ratio"))
            start_parameter = float(
                properties.get("start_parameter")
                if properties.get("start_parameter") is not None else 0.0
            )
            sweep = float(
                properties.get("sweep")
                if properties.get("sweep") is not None
                else (2.0 * math.pi if is_closed_curve else 0.0)
            )
        except (TypeError, ValueError):
            return None
        if (
            not center
            or not major
            or ratio <= 0.0
            or sweep <= 0.0
            or sweep > 2.0 * math.pi + 1e-9
            or not all(math.isfinite(value) for value in (
                ratio, start_parameter, sweep
            ))
        ):
            return None
        major_length = math.hypot(*major)
        if major_length <= 1e-12:
            return None
        minor = minor or (-major[1] * ratio, major[0] * ratio)
        minor_length = math.hypot(*minor)
        if (
            minor_length <= 1e-12
            or not all(math.isfinite(value) for value in (*major, *minor))
        ):
            return None
        def point_at(parameter: float) -> Point2:
            return (
                center[0] + major[0] * math.cos(parameter)
                + minor[0] * math.sin(parameter),
                center[1] + major[1] * math.cos(parameter)
                + minor[1] * math.sin(parameter),
            )
        try:
            (
                samples,
                sample_parameters,
                sampling_error_target,
                sampling_error_bound,
                sampling_capped,
            ) = _adaptive_parametric_samples(
                point_at, start_parameter, sweep,
                math.hypot(major_length, minor_length),
            )
        except (OverflowError, ValueError):
            return None
        curve_center = center
        curve_major_axis = major
        curve_minor_axis = minor
        curve_start_parameter = start_parameter
        curve_sweep = sweep
        start_tangent = (
            sweep * (-major[0] * math.sin(start_parameter)
                     + minor[0] * math.cos(start_parameter)),
            sweep * (-major[1] * math.sin(start_parameter)
                     + minor[1] * math.cos(start_parameter)),
        )
        end_parameter = start_parameter + sweep
        end_tangent = (
            sweep * (-major[0] * math.sin(end_parameter)
                     + minor[0] * math.cos(end_parameter)),
            sweep * (-major[1] * math.sin(end_parameter)
                     + minor[1] * math.cos(end_parameter)),
        )
        endpoint_scale = max(major_length, minor_length)
        endpoint_adjustment = max(
            math.dist(start, samples[0]) if start is not None else 0.0,
            math.dist(end, samples[-1]) if end is not None else 0.0,
        )
        analytic_midpoint = point_at(start_parameter + sweep / 2.0)
        analytic_chord_midpoint = (
            (samples[0][0] + samples[-1][0]) / 2.0,
            (samples[0][1] + samples[-1][1]) / 2.0,
        )
        visible_curve_deviation = math.dist(
            analytic_midpoint, analytic_chord_midpoint
        )
        endpoint_consistency_limit = max(
            endpoint_scale * 1e-8,
            visible_curve_deviation * 0.005,
            1e-9,
        )
        if endpoint_adjustment > endpoint_consistency_limit:
            return None
        if start is None:
            start = samples[0]
        else:
            samples[0] = start
        if end is None:
            end = samples[-1]
        else:
            samples[-1] = end
        sampling_error_bound = max(sampling_error_bound, endpoint_adjustment)
        approximate = endpoint_adjustment > max(endpoint_scale * 1e-8, 1e-10)
        sampling_method = (
            "analytic_adaptive_authored_endpoints"
            if approximate else (sampling_method or "analytic_adaptive")
        )
        sampling_certified = True
    elif "spline" in role:
        if is_closed_curve:
            # A closed spline needs a certified cyclic evaluator; an open fit
            # chain plus a closure flag is contradictory topology evidence.
            return None
        raw_samples = (
            properties.get("sampled_points")
            or properties.get("fit_points")
            or geometry.get("sampled_points")
            or geometry.get("fit_points")
            or geometry.get("points")
        )
        if isinstance(raw_samples, (list, tuple)):
            sampled_points = [
                (point, _point_z(value, start_z))
                for value in raw_samples
                for point in [_point2(value)]
                if point
            ]
            sampled_points = [
                item for index, item in enumerate(sampled_points)
                if index == 0 or math.dist(item[0], sampled_points[index - 1][0]) > 1e-12
            ]
            samples = [item[0] for item in sampled_points]
            sample_z = [item[1] for item in sampled_points]
            z_values = sample_z
            if sample_z:
                start_z, end_z = sample_z[0], sample_z[-1]
        if len(samples) < 2:
            # Endpoints establish connectivity but do not reveal a spline's
            # visible path. Never promote their chord as a drawn boundary.
            return None
        approximate = True
        sampling_method = sampling_method or "fit_point_polyline"
        declared_error = sampling_metadata.get("error_bound")
        try:
            sampling_error_bound = (
                float(declared_error) if declared_error is not None else None
            )
        except (TypeError, ValueError):
            sampling_error_bound = None
        sampling_certified = bool(
            sampling_metadata.get("certified")
            and sampling_error_bound is not None
            and math.isfinite(sampling_error_bound)
            and sampling_error_bound >= 0.0
        )
        start_tangent = (
            samples[1][0] - samples[0][0], samples[1][1] - samples[0][1]
        )
        end_tangent = (
            samples[-1][0] - samples[-2][0],
            samples[-1][1] - samples[-2][1],
        )
    else:
        return None

    if (
        len(samples) < 2
        or (
            not is_closed_curve
            and math.dist(samples[0], samples[-1]) <= 1e-12
        )
    ):
        return None
    length = primitive.get("length")
    if curve_radius is not None and curve_sweep is not None:
        length_value = abs(curve_radius * curve_sweep)
    elif (
        curve_major_axis is not None
        and curve_minor_axis is not None
        and curve_start_parameter is not None
        and curve_sweep is not None
    ):
        length_value = _ellipse_arc_length(
            curve_major_axis,
            curve_minor_axis,
            curve_start_parameter,
            curve_sweep,
        )
    else:
        try:
            length_value = float(length) if length is not None else sum(
                math.dist(samples[index], samples[index + 1])
                for index in range(len(samples) - 1)
            )
        except (TypeError, ValueError):
            length_value = sum(
                math.dist(samples[index], samples[index + 1])
                for index in range(len(samples) - 1)
            )
    if not math.isfinite(length_value) or length_value <= 0.0:
        length_value = sum(
            math.dist(samples[index], samples[index + 1])
            for index in range(len(samples) - 1)
        )
    if not math.isfinite(length_value) or length_value <= 0.0:
        return None
    if not sample_parameters:
        cumulative = [0.0]
        for left, right in zip(samples, samples[1:]):
            cumulative.append(cumulative[-1] + math.dist(left, right))
        total_sampled_length = cumulative[-1]
        if total_sampled_length <= 1e-300:
            return None
        sample_parameters = [value / total_sampled_length for value in cumulative]
    if not sample_z:
        sample_z = [
            start_z + (end_z - start_z) * parameter
            for parameter in sample_parameters
        ]
    if curve_radius is not None:
        curve_model = {
            "kind": "circle" if is_closed_curve else "circle_arc",
            "center": curve_center,
            "radius": curve_radius,
            "start_parameter": curve_start_parameter,
            "sweep": curve_sweep,
        }
    elif curve_major_axis is not None and curve_minor_axis is not None:
        curve_model = {
            "kind": "ellipse" if is_closed_curve else "ellipse_arc",
            "center": curve_center,
            "major_axis": curve_major_axis,
            "minor_axis": curve_minor_axis,
            "start_parameter": curve_start_parameter,
            "sweep": curve_sweep,
        }
    else:
        curve_model = {
            "kind": "sampled_spline",
            "sampling_certified": sampling_certified,
        }
    return {
        "start": samples[0],
        "end": samples[-1],
        "samples": samples,
        "sample_parameters": sample_parameters,
        "sample_z": sample_z,
        "length": length_value,
        "primitive_type": "curve",
        "curve_kind": str(properties.get("curve_kind") or role),
        "approximate": approximate,
        "sampling_method": sampling_method,
        "sampling_segment_count": len(samples) - 1,
        "sampling_error_target": sampling_error_target,
        "sampling_error_bound": sampling_error_bound,
        "sampling_certified": sampling_certified,
        "sampling_capped": sampling_capped,
        "endpoint_adjustment": endpoint_adjustment,
        "endpoint_consistency_limit": endpoint_consistency_limit,
        "start_z": start_z,
        "end_z": end_z,
        "z_span": max(z_values, default=0.0) - min(z_values, default=0.0),
        "curve_center": curve_center,
        "curve_radius": curve_radius,
        "curve_major_axis": curve_major_axis,
        "curve_minor_axis": curve_minor_axis,
        "curve_start_parameter": curve_start_parameter,
        "curve_sweep": curve_sweep,
        "source_parameter_range": (0.0, 1.0),
        "start_tangent": start_tangent,
        "end_tangent": end_tangent,
        "is_closed_curve": is_closed_curve,
        "curve_model": curve_model,
    }


def _drawing_endpoint_tolerance(entities: Sequence[Dict[str, Any]],
                                explicit: Optional[float] = None,
                                segments: Optional[Sequence[Dict[str, Any]]] = None) -> float:
    extent = bbox_union(bbox_from_row(entity) for entity in entities)
    finite_extent = bool(
        extent and all(math.isfinite(float(value)) for value in extent)
    )
    diagonal = (
        math.hypot(extent[2] - extent[0], extent[3] - extent[1])
        if finite_extent else 0.0
    )
    max_abs_coordinate = (
        max(abs(float(value)) for value in extent) if finite_extent else 1.0
    )
    numeric_floor = max(
        math.ulp(max(max_abs_coordinate, 1.0)) * 16.0,
        1e-12,
    )
    if explicit is not None:
        try:
            requested = float(explicit)
            if math.isfinite(requested) and requested >= 0.0:
                return max(requested, numeric_floor)
        except (TypeError, ValueError):
            pass
    if not finite_extent:
        return max(1e-9, numeric_floor)
    lengths = sorted(
        float(segment.get("length") or 0.0)
        for segment in (segments or [])
        if float(segment.get("length") or 0.0) > numeric_floor
    )
    # A lower-quartile source length keeps one enormous construction tail from
    # defining the endpoint scale. The explicit-tolerance path above remains
    # governed only by representable coordinate precision.
    local_length = lengths[(len(lengths) - 1) // 4] if lengths else diagonal
    # The drawing diagonal provides unit-scale invariance; the local median
    # length prevents a far-away outlier from turning a visible gap into a snap.
    relative = min(diagonal * 1e-6, local_length * 1e-4)
    return max(relative, numeric_floor)


def _line_segments(database: CADDatabase,
                   entities: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    entity_map = {
        _public_handle(entity): entity
        for entity in entities
        if _public_handle(entity) and _is_structural_linework(entity)
    }
    segments: List[Dict[str, Any]] = []
    seen = set()
    for primitive in all_topology_primitives(database):
        handle = str(primitive.get("entity_handle") or "")
        if handle not in entity_map:
            continue
        family = _boundary_family(entity_map[handle])
        primitive_type = str(primitive.get("primitive_type") or "").lower()
        boundary: Optional[Dict[str, Any]] = None
        if primitive_type == "line" and family in {"line", "polyline"}:
            entity_line_geometry = entity_geometry(entity_map[handle])
            entity_raw_start = (
                entity_line_geometry.get("start_point")
                or entity_line_geometry.get("start")
            )
            entity_raw_end = (
                entity_line_geometry.get("end_point")
                or entity_line_geometry.get("end")
            )
            if (
                _has_invalid_authored_z(entity_raw_start)
                or _has_invalid_authored_z(entity_raw_end)
            ):
                continue
            try:
                start = (float(primitive["x"]), float(primitive["y"]))
                end = (float(primitive["x2"]), float(primitive["y2"]))
            except (KeyError, TypeError, ValueError):
                continue
            if (
                _has_invalid_optional_number(primitive.get("z"))
                or _has_invalid_optional_number(primitive.get("z2"))
            ):
                continue
            start_z = _point_z([
                start[0], start[1], primitive.get("z") or 0.0
            ])
            end_z = _point_z([
                end[0], end[1], primitive.get("z2") or 0.0
            ])
            if not all(math.isfinite(value) for value in (*start, *end)):
                continue
            boundary = {
                "start": start,
                "end": end,
                "samples": [start, end],
                "sample_parameters": [0.0, 1.0],
                "sample_z": [start_z, end_z],
                "length": math.dist(start, end),
                "primitive_type": "line",
                "curve_kind": "",
                "approximate": False,
                "sampling_method": "exact_segment",
                "sampling_error_bound": 0.0,
                "sampling_certified": True,
                "start_z": start_z,
                "end_z": end_z,
                "z_span": abs(end_z - start_z),
                "source_parameter_range": (0.0, 1.0),
                "start_tangent": (
                    end[0] - start[0], end[1] - start[1]
                ),
                "end_tangent": (
                    end[0] - start[0], end[1] - start[1]
                ),
            }
        elif primitive_type == "curve" and family in {
            "arc", "circle", "ellipse", "spline",
        }:
            boundary = _sample_curve_primitive(primitive, entity_map[handle])
        if not boundary:
            continue
        start = boundary["start"]
        end = boundary["end"]
        if (
            math.dist(start, end) <= 1e-12
            and not boundary.get("is_closed_curve")
        ):
            continue
        primitive_key = str(primitive.get("primitive_key") or "")
        member_key = (handle, primitive_key)
        if member_key in seen:
            continue
        seen.add(member_key)
        segments.append({
            "segment_id": f"{handle}:{primitive_key or len(segments)}",
            "handle": handle,
            "primitive_key": primitive_key,
            "start": start,
            "end": end,
            "source_start": start,
            "source_end": end,
            "source_bbox": bbox_from_row(entity_map[handle]),
            "samples": boundary["samples"],
            "sample_parameters": boundary.get("sample_parameters", [0.0, 1.0]),
            "sample_z": boundary.get("sample_z", [
                boundary.get("start_z", 0.0), boundary.get("end_z", 0.0)
            ]),
            "layer": entity_map[handle].get("layer") or "0",
            "length": boundary["length"],
            "primitive_type": boundary["primitive_type"],
            "curve_kind": boundary["curve_kind"],
            "approximate": boundary["approximate"],
            "sampling_method": boundary["sampling_method"],
            "sampling_segment_count": boundary.get("sampling_segment_count", 1),
            "sampling_error_target": boundary.get("sampling_error_target", 0.0),
            "sampling_error_bound": boundary.get("sampling_error_bound", 0.0),
            "sampling_certified": bool(boundary.get("sampling_certified", True)),
            "sampling_capped": boundary.get("sampling_capped", False),
            "endpoint_adjustment": boundary.get("endpoint_adjustment", 0.0),
            "endpoint_consistency_limit": boundary.get(
                "endpoint_consistency_limit", 0.0
            ),
            "start_z": boundary.get("start_z", 0.0),
            "end_z": boundary.get("end_z", 0.0),
            "z_span": boundary.get("z_span", 0.0),
            "plane_z": (
                float(boundary.get("start_z", 0.0))
                + float(boundary.get("end_z", 0.0))
            ) / 2.0,
            "curve_center": boundary.get("curve_center"),
            "curve_radius": boundary.get("curve_radius"),
            "curve_major_axis": boundary.get("curve_major_axis"),
            "curve_minor_axis": boundary.get("curve_minor_axis"),
            "curve_start_parameter": boundary.get("curve_start_parameter"),
            "curve_sweep": boundary.get("curve_sweep"),
            "source_parameter_range": boundary.get(
                "source_parameter_range", (0.0, 1.0)
            ),
            "start_tangent": boundary.get("start_tangent"),
            "end_tangent": boundary.get("end_tangent"),
            "is_closed_curve": bool(boundary.get("is_closed_curve")),
            "curve_model": boundary.get("curve_model"),
        })

    # Summary-only scans have no primitive rows. Preserve useful behavior for
    # simple LINE entities by falling back to their structured geometry.
    handles_with_segments = {segment["handle"] for segment in segments}
    for handle, entity in entity_map.items():
        family = _boundary_family(entity)
        if handle in handles_with_segments or family != "line":
            continue
        # Never infer a boundary from a bbox diagonal: it is wrong for
        # negative-slope lines and dangerous in a face graph.  Summary-only
        # fallback requires authored start/end geometry.
        geometry = entity_geometry(entity)
        raw_start = geometry.get("start_point") or geometry.get("start")
        raw_end = geometry.get("end_point") or geometry.get("end")
        if _has_invalid_authored_z(raw_start) or _has_invalid_authored_z(raw_end):
            continue
        start = _point2(raw_start)
        end = _point2(raw_end)
        if not start or not end:
            continue
        if math.dist(start, end) <= 1e-12:
            continue
        segments.append({
            "segment_id": f"{handle}:fallback",
            "handle": handle,
            "primitive_key": "",
            "start": start,
            "end": end,
            "source_start": start,
            "source_end": end,
            "source_bbox": bbox_from_row(entity),
            "samples": [start, end],
            "sample_parameters": [0.0, 1.0],
            "sample_z": [_point_z(raw_start), _point_z(raw_end)],
            "layer": entity.get("layer") or "0",
            "length": math.dist(start, end),
            "primitive_type": "line",
            "curve_kind": "",
            "approximate": False,
            "sampling_method": "exact_segment",
            "sampling_error_bound": 0.0,
            "sampling_certified": True,
            "start_z": _point_z(raw_start),
            "end_z": _point_z(raw_end),
            "z_span": abs(_point_z(raw_end) - _point_z(raw_start)),
            "plane_z": (_point_z(raw_start) + _point_z(raw_end)) / 2.0,
            "source_parameter_range": (0.0, 1.0),
            "start_tangent": (
                end[0] - start[0], end[1] - start[1]
            ),
            "end_tangent": (
                end[0] - start[0], end[1] - start[1]
            ),
        })
    return segments


def _cross(a: Point2, b: Point2) -> float:
    return a[0] * b[1] - a[1] * b[0]


def _planarize_line_segments(segments: Sequence[Dict[str, Any]],
                             tolerance: float,
                             max_pair_checks: int = 250000) -> List[Dict[str, Any]]:
    """Split straight segments at crossings, T-junctions, and overlaps.

    Curves pass through unchanged here and are handled by the line/curve
    planarizer after straight-edge crossings have been resolved. An adaptive
    x/y sweep chooses the axis with fewer interval overlaps. The
    cap counts broad-phase pairs, so even orthogonal worst cases cannot bypass
    the work budget.
    """
    line_indices = [
        index for index, segment in enumerate(segments)
        if str(segment.get("primitive_type") or "line") == "line"
    ]
    split_parameters: Dict[int, set] = {
        index: {0.0, 1.0} for index in line_indices
    }
    def interval_overlap_estimate(axis: int) -> int:
        heap: List[Tuple[float, int]] = []
        estimate = 0
        ordered_indices = sorted(
            line_indices,
            key=lambda item: min(
                segments[item]["start"][axis], segments[item]["end"][axis]
            ),
        )
        for item in ordered_indices:
            interval_min = min(
                segments[item]["start"][axis], segments[item]["end"][axis]
            ) - tolerance
            while heap and heap[0][0] < interval_min:
                heapq.heappop(heap)
            estimate += len(heap)
            if estimate > max_pair_checks:
                return estimate
            interval_max = max(
                segments[item]["start"][axis], segments[item]["end"][axis]
            ) + tolerance
            heapq.heappush(heap, (interval_max, item))
        return estimate

    sweep_axis = min((0, 1), key=interval_overlap_estimate)
    secondary_axis = 1 - sweep_axis
    ordered = sorted(
        line_indices,
        key=lambda index: min(
            segments[index]["start"][sweep_axis],
            segments[index]["end"][sweep_axis],
        ),
    )
    active: List[int] = []
    pair_checks = 0
    capped = False

    for index in ordered:
        segment = segments[index]
        a = segment["start"]
        b = segment["end"]
        primary_min = min(a[sweep_axis], b[sweep_axis]) - tolerance
        active = [
            other for other in active
            if max(
                segments[other]["start"][sweep_axis],
                segments[other]["end"][sweep_axis],
            ) + tolerance >= primary_min
        ]
        secondary_min = min(a[secondary_axis], b[secondary_axis]) - tolerance
        secondary_max = max(a[secondary_axis], b[secondary_axis]) + tolerance
        for other_index in active:
            pair_checks += 1
            if pair_checks > max_pair_checks:
                capped = True
                break
            other = segments[other_index]
            if abs(
                float(segment.get("plane_z") or 0.0)
                - float(other.get("plane_z") or 0.0)
            ) > tolerance:
                continue
            c = other["start"]
            d = other["end"]
            if (
                max(c[secondary_axis], d[secondary_axis]) + tolerance < secondary_min
                or min(c[secondary_axis], d[secondary_axis]) - tolerance > secondary_max
            ):
                continue

            r = (b[0] - a[0], b[1] - a[1])
            s = (d[0] - c[0], d[1] - c[1])
            r_cross_s = _cross(r, s)
            c_minus_a = (c[0] - a[0], c[1] - a[1])
            scale = max(math.hypot(*r), math.hypot(*s), tolerance, 1e-300)
            cross_epsilon = tolerance * scale
            if abs(r_cross_s) > cross_epsilon:
                t = _cross(c_minus_a, s) / r_cross_s
                u = _cross(c_minus_a, r) / r_cross_s
                parameter_epsilon = tolerance / max(min(math.hypot(*r), math.hypot(*s)), tolerance)
                if -parameter_epsilon <= t <= 1.0 + parameter_epsilon and -parameter_epsilon <= u <= 1.0 + parameter_epsilon:
                    split_parameters[index].add(min(1.0, max(0.0, t)))
                    split_parameters[other_index].add(min(1.0, max(0.0, u)))
            elif abs(_cross(c_minus_a, r)) <= cross_epsilon:
                r_length_sq = r[0] * r[0] + r[1] * r[1]
                s_length_sq = s[0] * s[0] + s[1] * s[1]
                if r_length_sq <= 1e-300 or s_length_sq <= 1e-300:
                    continue
                for point in (c, d):
                    parameter = (
                        (point[0] - a[0]) * r[0] + (point[1] - a[1]) * r[1]
                    ) / r_length_sq
                    if -1e-12 <= parameter <= 1.0 + 1e-12:
                        split_parameters[index].add(min(1.0, max(0.0, parameter)))
                for point in (a, b):
                    parameter = (
                        (point[0] - c[0]) * s[0] + (point[1] - c[1]) * s[1]
                    ) / s_length_sq
                    if -1e-12 <= parameter <= 1.0 + 1e-12:
                        split_parameters[other_index].add(min(1.0, max(0.0, parameter)))
        active.append(index)
        if capped:
            break

    result: List[Dict[str, Any]] = []
    for index, segment in enumerate(segments):
        if index not in split_parameters:
            result.append(segment)
            continue
        start = segment["start"]
        end = segment["end"]
        parameters = sorted(split_parameters[index])
        for part_index, (t0, t1) in enumerate(zip(parameters, parameters[1:])):
            part_start = (
                start[0] + (end[0] - start[0]) * t0,
                start[1] + (end[1] - start[1]) * t0,
            )
            part_end = (
                start[0] + (end[0] - start[0]) * t1,
                start[1] + (end[1] - start[1]) * t1,
            )
            length = math.dist(part_start, part_end)
            if length <= max(tolerance * 1e-3, 1e-12):
                continue
            source_z0 = float(segment.get("start_z") or 0.0)
            source_z1 = float(segment.get("end_z") or 0.0)
            part_start_z = source_z0 + (source_z1 - source_z0) * t0
            part_end_z = source_z0 + (source_z1 - source_z0) * t1
            source_parameter_range = segment.get(
                "source_parameter_range", (0.0, 1.0)
            )
            source_u0 = float(source_parameter_range[0])
            source_u1 = float(source_parameter_range[1])
            result.append({
                **segment,
                "segment_id": f"{segment['segment_id']}@{part_index}",
                "source_segment_id": segment.get(
                    "source_segment_id", segment["segment_id"]
                ),
                "start": part_start,
                "end": part_end,
                "samples": [part_start, part_end],
                "sample_parameters": [0.0, 1.0],
                "sample_z": [part_start_z, part_end_z],
                "length": length,
                "start_z": part_start_z,
                "end_z": part_end_z,
                "z_span": abs(part_end_z - part_start_z),
                "plane_z": (part_start_z + part_end_z) / 2.0,
                "source_parameter_range": (
                    source_u0 + (source_u1 - source_u0) * t0,
                    source_u0 + (source_u1 - source_u0) * t1,
                ),
                "planarized": len(parameters) > 2,
                "planarization_capped": capped,
            })
    return [{
        **segment,
        "line_line_pair_checks": pair_checks,
    } for segment in result]


def _dot(a: Point2, b: Point2) -> float:
    return a[0] * b[0] + a[1] * b[1]


def _curve_fraction_for_angle(theta: float,
                              start_parameter: float,
                              sweep: float,
                              angle_tolerance: float) -> Optional[float]:
    """Map a periodic angle onto one open curve's directed source domain."""
    if not math.isfinite(theta) or not math.isfinite(sweep) or sweep <= 0.0:
        return None
    two_pi = 2.0 * math.pi
    if sweep > two_pi + angle_tolerance:
        return None
    center_turn = round((start_parameter - theta) / two_pi)
    candidates = [
        (theta + two_pi * (center_turn + offset) - start_parameter) / sweep
        for offset in (-1, 0, 1, 2)
    ]
    accepted = [
        value for value in candidates
        if -angle_tolerance / sweep <= value <= 1.0 + angle_tolerance / sweep
    ]
    if not accepted:
        return None
    value = min(accepted, key=lambda item: abs(item - min(1.0, max(0.0, item))))
    return min(1.0, max(0.0, value))


def _analytic_curve_point_tangent(curve: Dict[str, Any],
                                  fraction: float) -> Tuple[Point2, Point2]:
    center = tuple(curve["curve_center"])
    start_parameter = float(curve["curve_start_parameter"])
    sweep = float(curve["curve_sweep"])
    parameter = start_parameter + sweep * fraction
    kind = str(curve.get("curve_kind") or "").lower()
    if ("arc" in kind and "ellipse" not in kind) or "circle" in kind:
        radius = float(curve["curve_radius"])
        point = (
            center[0] + radius * math.cos(parameter),
            center[1] + radius * math.sin(parameter),
        )
        tangent = (
            -sweep * radius * math.sin(parameter),
            sweep * radius * math.cos(parameter),
        )
        return point, tangent
    major = tuple(curve["curve_major_axis"])
    minor = tuple(curve["curve_minor_axis"])
    point = (
        center[0] + major[0] * math.cos(parameter) + minor[0] * math.sin(parameter),
        center[1] + major[1] * math.cos(parameter) + minor[1] * math.sin(parameter),
    )
    tangent = (
        sweep * (-major[0] * math.sin(parameter) + minor[0] * math.cos(parameter)),
        sweep * (-major[1] * math.sin(parameter) + minor[1] * math.cos(parameter)),
    )
    return point, tangent


def _line_arc_contacts(line: Dict[str, Any],
                       curve: Dict[str, Any],
                       tolerance: float) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    a = tuple(line["start"])
    b = tuple(line["end"])
    center = tuple(curve["curve_center"])
    radius = float(curve["curve_radius"])
    direction = (b[0] - a[0], b[1] - a[1])
    length_sq = _dot(direction, direction)
    if length_sq <= 1e-300:
        return [], None
    length = math.sqrt(length_sq)
    unit_direction = (direction[0] / length, direction[1] / length)
    relative = (a[0] - center[0], a[1] - center[1])
    foot_distance = -_dot(relative, unit_direction)
    closest = (
        relative[0] + foot_distance * unit_direction[0],
        relative[1] + foot_distance * unit_direction[1],
    )
    gap_sq = radius * radius - _dot(closest, closest)
    coordinate_magnitude = max(
        abs(a[0]), abs(a[1]), abs(b[0]), abs(b[1]),
        abs(center[0]), abs(center[1]), 1.0,
    )
    geometry_scale = max(radius, 1.0)
    numeric_tolerance = max(
        math.ulp(coordinate_magnitude) * 8.0,
        geometry_scale * 1e-13,
        1e-12,
    )
    gap_tolerance = (
        2.0 * radius * numeric_tolerance + numeric_tolerance * numeric_tolerance
    )
    if gap_sq < -gap_tolerance:
        return [], None
    strict_tangent_gap = max(
        math.ulp(max(radius * radius, 1.0)) * 64.0,
        radius * radius * 1e-14,
        1e-14,
    )
    if strict_tangent_gap < abs(gap_sq) <= gap_tolerance:
        return [], "line_curve_tangency_uncertain"
    tangent_root = abs(gap_sq) <= strict_tangent_gap
    if tangent_root:
        root_distances = [foot_distance]
        roots_below_resolution = False
    else:
        offset_distance = math.sqrt(max(0.0, gap_sq))
        root_distances = [
            foot_distance - offset_distance,
            foot_distance + offset_distance,
        ]
        roots_below_resolution = (
            root_distances[1] - root_distances[0] <= 2.0 * tolerance
        )
    parameter_tolerance = tolerance / max(length, tolerance)
    angle_tolerance = max(
        numeric_tolerance / max(radius, numeric_tolerance), 1e-13
    )
    contacts: List[Dict[str, Any]] = []
    for root_distance in root_distances:
        line_u = root_distance / length
        if not -parameter_tolerance <= line_u <= 1.0 + parameter_tolerance:
            continue
        line_u = min(1.0, max(0.0, line_u))
        point = (
            a[0] + min(length, max(0.0, root_distance)) * unit_direction[0],
            a[1] + min(length, max(0.0, root_distance)) * unit_direction[1],
        )
        theta = math.atan2(point[1] - center[1], point[0] - center[0])
        curve_u = _curve_fraction_for_angle(
            theta,
            float(curve["curve_start_parameter"]),
            float(curve["curve_sweep"]),
            angle_tolerance,
        )
        if curve_u is None:
            continue
        curve_length = max(float(curve.get("length") or 0.0), radius, tolerance)
        curve_parameter_tolerance = tolerance / curve_length
        at_endpoint = (
            line_u <= parameter_tolerance
            or line_u >= 1.0 - parameter_tolerance
            or curve_u <= curve_parameter_tolerance
            or curve_u >= 1.0 - curve_parameter_tolerance
        )
        if at_endpoint:
            contact_kind = "endpoint"
        elif tangent_root:
            contact_kind = "tangent"
        else:
            contact_kind = "crossing"
        radial_residual = abs(math.dist(point, center) - radius)
        if radial_residual > tolerance + numeric_tolerance:
            continue
        contacts.append({
            "line_u": line_u,
            "curve_u": curve_u,
            "point": point,
            "kind": contact_kind,
            "method": "analytic_line_arc",
            "residual": radial_residual,
        })
    if roots_below_resolution and contacts:
        for line_u, point in ((0.0, a), (1.0, b)):
            radial = (point[0] - center[0], point[1] - center[1])
            radial_length = math.hypot(*radial)
            if (
                abs(radial_length - radius) <= numeric_tolerance
                and abs(_dot(unit_direction, radial))
                <= max(radius, numeric_tolerance) * 1e-11
            ):
                curve_u = _curve_fraction_for_angle(
                    math.atan2(radial[1], radial[0]),
                    float(curve["curve_start_parameter"]),
                    float(curve["curve_sweep"]),
                    angle_tolerance,
                )
                if curve_u is not None:
                    return [{
                        "line_u": line_u,
                        "curve_u": curve_u,
                        "point": point,
                        "kind": "endpoint",
                        "method": "analytic_line_arc_tangent_endpoint",
                        "residual": abs(radial_length - radius),
                    }], None
        return [], "line_curve_intersections_below_resolution"
    if sum(contact["kind"] == "crossing" for contact in contacts) >= 2:
        normalized_distance = min(
            1.0, math.hypot(*closest) / max(radius, 1e-300)
        )
        minor_angle = 2.0 * math.acos(normalized_distance)
        minor_segment_area = 0.5 * radius * radius * (
            minor_angle - math.sin(minor_angle)
        )
        if minor_segment_area <= tolerance * tolerance:
            return [], "line_curve_face_below_resolution"
    return contacts, None


def _line_ellipse_contacts(line: Dict[str, Any],
                           curve: Dict[str, Any],
                           tolerance: float) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    a = tuple(line["start"])
    b = tuple(line["end"])
    center = tuple(curve["curve_center"])
    major = tuple(curve["curve_major_axis"])
    minor = tuple(curve["curve_minor_axis"])
    major_length = math.hypot(*major)
    minor_length = math.hypot(*minor)
    determinant = _cross(major, minor)
    if (
        major_length <= 1e-300
        or minor_length <= 1e-300
        or abs(determinant) <= major_length * minor_length * 1e-12
    ):
        return [], "ill_conditioned_ellipse_intersection"

    def inverse_map(point: Point2) -> Point2:
        relative = (point[0] - center[0], point[1] - center[1])
        return (
            (minor[1] * relative[0] - minor[0] * relative[1]) / determinant,
            (-major[1] * relative[0] + major[0] * relative[1]) / determinant,
        )

    def inverse_vector(vector: Point2) -> Point2:
        return (
            (minor[1] * vector[0] - minor[0] * vector[1]) / determinant,
            (-major[1] * vector[0] + major[0] * vector[1]) / determinant,
        )

    q0 = inverse_map(a)
    direction = (b[0] - a[0], b[1] - a[1])
    line_length = math.hypot(*direction)
    if line_length <= 1e-300:
        return [], None
    unit_direction = (
        direction[0] / line_length, direction[1] / line_length
    )
    q_unit = inverse_vector(unit_direction)
    q_length_sq = _dot(q_unit, q_unit)
    if q_length_sq <= 1e-300 or line_length <= 1e-300:
        return [], None
    foot_distance = -_dot(q0, q_unit) / q_length_sq
    closest = (
        q0[0] + foot_distance * q_unit[0],
        q0[1] + foot_distance * q_unit[1],
    )
    gap_sq = 1.0 - _dot(closest, closest)
    coordinate_magnitude = max(
        *(abs(value) for value in (*a, *b, *center)), 1.0
    )
    geometry_scale = max(major_length, minor_length, 1.0)
    numeric_tolerance = max(
        math.ulp(coordinate_magnitude) * 8.0,
        geometry_scale * 1e-13,
        1e-12,
    )
    inverse_norm = math.sqrt(
        minor[1] ** 2 + minor[0] ** 2 + major[1] ** 2 + major[0] ** 2
    ) / abs(determinant)
    matrix_norm = math.sqrt(major_length ** 2 + minor_length ** 2)
    normalized_tolerance = max(inverse_norm * numeric_tolerance, 1e-13)
    gap_tolerance = 2.0 * normalized_tolerance + normalized_tolerance ** 2
    if gap_sq < -gap_tolerance:
        return [], None
    strict_tangent_gap = max(math.ulp(1.0) * 64.0, 1e-14)
    if strict_tangent_gap < abs(gap_sq) <= gap_tolerance:
        return [], "line_curve_tangency_uncertain"
    tangent_root = abs(gap_sq) <= strict_tangent_gap
    if tangent_root:
        root_distances = [foot_distance]
        roots_below_resolution = False
    else:
        offset_distance = math.sqrt(max(0.0, gap_sq) / q_length_sq)
        root_distances = [
            foot_distance - offset_distance,
            foot_distance + offset_distance,
        ]
        roots_below_resolution = (
            root_distances[1] - root_distances[0] <= 2.0 * tolerance
        )
    parameter_tolerance = tolerance / max(line_length, tolerance)
    angle_tolerance = max(normalized_tolerance, 1e-13)
    contacts: List[Dict[str, Any]] = []
    for root_distance in root_distances:
        line_u = root_distance / line_length
        if not -parameter_tolerance <= line_u <= 1.0 + parameter_tolerance:
            continue
        line_u = min(1.0, max(0.0, line_u))
        point = (
            a[0] + min(line_length, max(0.0, root_distance)) * unit_direction[0],
            a[1] + min(line_length, max(0.0, root_distance)) * unit_direction[1],
        )
        q = inverse_map(point)
        curve_u = _curve_fraction_for_angle(
            math.atan2(q[1], q[0]),
            float(curve["curve_start_parameter"]),
            float(curve["curve_sweep"]),
            angle_tolerance,
        )
        if curve_u is None:
            continue
        curve_length = max(
            float(curve.get("length") or 0.0), major_length, minor_length, tolerance
        )
        curve_parameter_tolerance = tolerance / curve_length
        at_endpoint = (
            line_u <= parameter_tolerance
            or line_u >= 1.0 - parameter_tolerance
            or curve_u <= curve_parameter_tolerance
            or curve_u >= 1.0 - curve_parameter_tolerance
        )
        contact_kind = (
            "endpoint" if at_endpoint
            else "tangent" if tangent_root
            else "crossing"
        )
        # ||A(q-q_on_unit_circle)|| <= ||A|| * ||q-q_on_unit_circle||.
        # The previous inverse-norm division was a lower bound and could make
        # an ill-conditioned ellipse intersection look more accurate than it
        # was.
        physical_residual = matrix_norm * abs(math.hypot(*q) - 1.0)
        if physical_residual > tolerance + numeric_tolerance:
            continue
        contacts.append({
            "line_u": line_u,
            "curve_u": curve_u,
            "point": point,
            "kind": contact_kind,
            "method": "analytic_line_ellipse",
            "residual": physical_residual,
        })
    if roots_below_resolution and contacts:
        for line_u, point in ((0.0, a), (1.0, b)):
            q = inverse_map(point)
            q_radius = math.hypot(*q)
            if (
                abs(q_radius - 1.0) <= normalized_tolerance
                and abs(_dot(q, q_unit))
                <= math.sqrt(q_length_sq) * max(q_radius, normalized_tolerance) * 1e-11
            ):
                curve_u = _curve_fraction_for_angle(
                    math.atan2(q[1], q[0]),
                    float(curve["curve_start_parameter"]),
                    float(curve["curve_sweep"]),
                    angle_tolerance,
                )
                if curve_u is not None:
                    return [{
                        "line_u": line_u,
                        "curve_u": curve_u,
                        "point": point,
                        "kind": "endpoint",
                        "method": "analytic_line_ellipse_tangent_endpoint",
                        "residual": matrix_norm * abs(q_radius - 1.0),
                    }], None
        return [], "line_curve_intersections_below_resolution"
    if sum(contact["kind"] == "crossing" for contact in contacts) >= 2:
        normalized_distance = min(1.0, math.hypot(*closest))
        minor_angle = 2.0 * math.acos(normalized_distance)
        minor_segment_area = 0.5 * abs(determinant) * (
            minor_angle - math.sin(minor_angle)
        )
        if minor_segment_area <= tolerance * tolerance:
            return [], "line_curve_face_below_resolution"
    return contacts, None


def _interpolate_samples(values: Sequence[Any],
                         parameters: Sequence[float],
                         fraction: float) -> Any:
    if not values:
        return None
    if len(values) == 1 or fraction <= parameters[0]:
        return values[0]
    if fraction >= parameters[-1]:
        return values[-1]
    right_index = bisect_right(parameters, fraction)
    left_index = max(0, right_index - 1)
    right_index = min(right_index, len(parameters) - 1)
    left_parameter = float(parameters[left_index])
    right_parameter = float(parameters[right_index])
    local = (
        0.0 if right_parameter <= left_parameter
        else (fraction - left_parameter) / (right_parameter - left_parameter)
    )
    left = values[left_index]
    right = values[right_index]
    if isinstance(left, (list, tuple)):
        return tuple(
            float(left[axis]) + (float(right[axis]) - float(left[axis])) * local
            for axis in range(min(len(left), len(right)))
        )
    return float(left) + (float(right) - float(left)) * local


def _sampled_curve_contacts(line: Dict[str, Any],
                            curve: Dict[str, Any],
                            tolerance: float,
                            max_span_checks: int) -> Tuple[
                                List[Dict[str, Any]], Optional[str], int, bool
                            ]:
    """Find only certified endpoint contacts; flag interior sample hits unknown.

    Fit/sample polylines do not carry a bound against the real spline. Their
    authored endpoints are exact connectivity evidence, but an interior chord
    crossing can only veto a high-confidence topology result until a NURBS
    evaluator supplies a certified root.
    """
    raw_samples = curve.get("samples", [])
    if len(raw_samples) - 1 > max_span_checks:
        return [], "line_curve_span_budget", max_span_checks + 1, True
    samples = [tuple(point) for point in raw_samples]
    sample_parameters = [
        float(value) for value in curve.get("sample_parameters", [])
    ]
    if len(samples) < 2 or len(sample_parameters) != len(samples):
        return [], "uncertified_curve_samples", 0, False
    a = tuple(line["start"])
    b = tuple(line["end"])
    direction = (b[0] - a[0], b[1] - a[1])
    line_length = math.hypot(*direction)
    if line_length <= 1e-300:
        return [], None, 0, False
    line_parameter_tolerance = tolerance / max(line_length, tolerance)
    sampling_certified = bool(curve.get("sampling_certified"))
    sampling_error_bound = curve.get("sampling_error_bound")
    curve_error = (
        max(0.0, float(sampling_error_bound))
        if sampling_certified and sampling_error_bound is not None else 0.0
    )
    closing_endpoint_pair = (
        _point_on_line_distance(tuple(curve["start"]), a, b) <= tolerance
        and _point_on_line_distance(tuple(curve["end"]), a, b) <= tolerance
    )
    signed_distances = [
        _cross(direction, (point[0] - a[0], point[1] - a[1])) / line_length
        for point in samples
    ]
    contacts: List[Dict[str, Any]] = []
    checks = 0
    error_tube_overlap = False
    for sample_index, (start, end) in enumerate(zip(samples, samples[1:])):
        checks += 1
        if checks > max_span_checks:
            return [], "line_curve_span_budget", checks, True
        topology_padding = tolerance + curve_error
        tube_overlaps = not (
            max(start[0], end[0]) + topology_padding < min(a[0], b[0])
            or max(a[0], b[0]) + topology_padding < min(start[0], end[0])
            or max(start[1], end[1]) + topology_padding < min(a[1], b[1])
            or max(a[1], b[1]) + topology_padding < min(start[1], end[1])
        )
        error_tube_overlap = error_tube_overlap or tube_overlaps
        if not tube_overlaps:
            continue
        if (
            max(start[0], end[0]) + tolerance < min(a[0], b[0])
            or max(a[0], b[0]) + tolerance < min(start[0], end[0])
            or max(start[1], end[1]) + tolerance < min(a[1], b[1])
            or max(a[1], b[1]) + tolerance < min(start[1], end[1])
        ):
            continue
        span = (end[0] - start[0], end[1] - start[1])
        span_length = math.hypot(*span)
        if span_length <= 1e-300:
            continue
        denominator = _cross(direction, span)
        start_from_line = (start[0] - a[0], start[1] - a[1])
        cross_tolerance = tolerance * max(line_length, span_length, tolerance)
        if abs(denominator) <= cross_tolerance:
            if abs(_cross(start_from_line, direction)) > cross_tolerance:
                continue
            line_length_sq = line_length * line_length
            span_parameters = sorted(
                _dot((point[0] - a[0], point[1] - a[1]), direction)
                / line_length_sq
                for point in (start, end)
            )
            overlap_start = max(0.0, span_parameters[0])
            overlap_end = min(1.0, span_parameters[1])
            if (overlap_end - overlap_start) * line_length > tolerance:
                return [], "uncertified_curve_overlap", checks, False
            continue
        line_u = _cross(start_from_line, span) / denominator
        span_u = _cross(start_from_line, direction) / denominator
        span_parameter_tolerance = tolerance / max(span_length, tolerance)
        if not (
            -line_parameter_tolerance <= line_u <= 1.0 + line_parameter_tolerance
            and -span_parameter_tolerance <= span_u <= 1.0 + span_parameter_tolerance
        ):
            continue
        line_u = min(1.0, max(0.0, line_u))
        span_u = min(1.0, max(0.0, span_u))
        curve_u = (
            sample_parameters[sample_index]
            + (sample_parameters[sample_index + 1] - sample_parameters[sample_index])
            * span_u
        )
        curve_length = max(float(curve.get("length") or 0.0), span_length, tolerance)
        curve_parameter_tolerance = tolerance / curve_length
        curve_endpoint = (
            curve_u <= curve_parameter_tolerance
            or curve_u >= 1.0 - curve_parameter_tolerance
        )
        line_endpoint = (
            line_u <= line_parameter_tolerance
            or line_u >= 1.0 - line_parameter_tolerance
        )
        if curve_endpoint:
            curve_u = 0.0 if curve_u <= curve_parameter_tolerance else 1.0
            authored_point = tuple(curve["start"] if curve_u == 0.0 else curve["end"])
            if _point_on_line_distance(authored_point, a, b) > tolerance:
                continue
            contacts.append({
                "line_u": line_u,
                "curve_u": curve_u,
                "point": authored_point,
                "kind": "endpoint",
                "method": "authored_spline_endpoint",
                "residual": _point_on_line_distance(authored_point, a, b),
            })
            continue
        if line_endpoint:
            return [], "uncertified_curve_interior_contact", checks, False

        left_sign: Optional[float] = None
        for value in reversed(signed_distances[:sample_index + 1]):
            if abs(value) > tolerance:
                left_sign = value
                break
        right_sign: Optional[float] = None
        for value in signed_distances[sample_index + 1:]:
            if abs(value) > tolerance:
                right_sign = value
                break
        if left_sign is None or right_sign is None:
            return [], "uncertified_curve_contact_band", checks, False
        if left_sign * right_sign < 0.0:
            return [], "uncertified_curve_interior_crossing", checks, False
        return [], "uncertified_curve_interior_contact", checks, False
    if not closing_endpoint_pair:
        if not sampling_certified:
            return [], "uncertified_curve_possible_contact", checks, False
        if error_tube_overlap:
            return [], "certified_curve_error_tube_contact", checks, False
    return contacts, None, checks, False


def _point_on_line_distance(point: Point2, start: Point2, end: Point2) -> float:
    direction = (end[0] - start[0], end[1] - start[1])
    length_sq = _dot(direction, direction)
    if length_sq <= 1e-300:
        return math.dist(point, start)
    length = math.sqrt(length_sq)
    unit = (direction[0] / length, direction[1] / length)
    relative = (point[0] - start[0], point[1] - start[1])
    along = _dot(relative, unit)
    if along <= 0.0:
        return math.dist(point, start)
    if along >= length:
        return math.dist(point, end)
    # The perpendicular determinant avoids reconstructing a projection from a
    # huge authored tail, which can lose the local offset through cancellation.
    return abs(_cross(unit, relative))


def _segment_z_at(segment: Dict[str, Any], fraction: float) -> float:
    parameters = [float(value) for value in segment.get("sample_parameters", [])]
    sample_z = [float(value) for value in segment.get("sample_z", [])]
    if parameters and len(parameters) == len(sample_z):
        value = _interpolate_samples(sample_z, parameters, fraction)
        if value is not None:
            return float(value)
    return float(segment.get("start_z") or 0.0) + (
        float(segment.get("end_z") or 0.0)
        - float(segment.get("start_z") or 0.0)
    ) * fraction


def _segment_xy_bbox(segment: Dict[str, Any], tolerance: float) -> Tuple[float, float, float, float]:
    samples = [tuple(point) for point in segment.get("samples", [])]
    if not samples:
        samples = [tuple(segment["start"]), tuple(segment["end"])]
    error_bound = segment.get("sampling_error_bound")
    padding = tolerance + (
        max(0.0, float(error_bound)) if error_bound is not None else 0.0
    )
    bounds = (
        min(point[0] for point in samples),
        min(point[1] for point in samples),
        max(point[0] for point in samples),
        max(point[1] for point in samples),
    )
    source_bbox = segment.get("source_bbox")
    if error_bound is None and source_bbox is not None:
        bounds = (
            min(bounds[0], float(source_bbox[0])),
            min(bounds[1], float(source_bbox[1])),
            max(bounds[2], float(source_bbox[2])),
            max(bounds[3], float(source_bbox[3])),
        )
    return (
        bounds[0] - padding,
        bounds[1] - padding,
        bounds[2] + padding,
        bounds[3] + padding,
    )


def _audit_circle_circle_pair(
    left: Dict[str, Any], right: Dict[str, Any], tolerance: float
) -> Tuple[bool, Optional[str]]:
    """Exactly audit whether two circular arc domains need planarization."""
    left_kind = str(left.get("curve_kind") or "").lower()
    right_kind = str(right.get("curve_kind") or "").lower()
    if not (
        (("arc" in left_kind and "ellipse" not in left_kind) or "circle" in left_kind)
        and (("arc" in right_kind and "ellipse" not in right_kind) or "circle" in right_kind)
        and left.get("curve_center") is not None
        and right.get("curve_center") is not None
        and left.get("curve_radius") is not None
        and right.get("curve_radius") is not None
        and left.get("curve_start_parameter") is not None
        and right.get("curve_start_parameter") is not None
        and left.get("curve_sweep") is not None
        and right.get("curve_sweep") is not None
    ):
        return False, None
    c0 = tuple(left["curve_center"])
    c1 = tuple(right["curve_center"])
    r0 = float(left["curve_radius"])
    r1 = float(right["curve_radius"])
    delta = (c1[0] - c0[0], c1[1] - c0[1])
    center_distance = math.hypot(*delta)
    coordinate_magnitude = max(
        abs(c0[0]), abs(c0[1]), abs(c1[0]), abs(c1[1]), 1.0
    )
    numeric_tolerance = max(
        math.ulp(coordinate_magnitude) * 8.0,
        math.ulp(max(r0, r1, 1.0)) * 16.0,
        1e-12,
    )
    contact_band = tolerance + numeric_tolerance
    if center_distance <= numeric_tolerance:
        radius_gap = abs(r0 - r1)
        if radius_gap <= numeric_tolerance:
            two_pi = 2.0 * math.pi
            angle_tolerance = max(
                contact_band / max(min(r0, r1), contact_band), 1e-13
            )

            def angular_intervals(curve: Dict[str, Any]) -> List[Tuple[float, float]]:
                start = float(curve["curve_start_parameter"]) % two_pi
                sweep = min(two_pi, float(curve["curve_sweep"]))
                if sweep >= two_pi - angle_tolerance:
                    return [(0.0, two_pi)]
                end = start + sweep
                if end <= two_pi:
                    return [(start, end)]
                return [(start, two_pi), (0.0, end - two_pi)]

            overlap = math.fsum(
                max(0.0, min(left_end, right_end) - max(left_start, right_start))
                for left_start, left_end in angular_intervals(left)
                for right_start, right_end in angular_intervals(right)
            )
            if overlap > angle_tolerance:
                return True, "overlapping_circular_curve_domains"
            return True, None
        if radius_gap <= contact_band:
            return True, "near_curve_curve_contact"
        return True, None
    radius_sum = r0 + r1
    radius_difference = abs(r0 - r1)
    if (
        center_distance > radius_sum + contact_band
        or center_distance < radius_difference - contact_band
    ):
        return True, None
    if (
        center_distance > radius_sum
        or center_distance < radius_difference
    ):
        return True, "near_curve_curve_contact"
    along = (
        r0 * r0 - r1 * r1 + center_distance * center_distance
    ) / (2.0 * center_distance)
    height_sq = r0 * r0 - along * along
    height_tolerance = max(
        contact_band * max(r0, r1, 1.0) * 2.0,
        math.ulp(max(r0 * r0, r1 * r1, 1.0)) * 32.0,
    )
    if height_sq < -height_tolerance:
        return True, "near_curve_curve_contact"
    unit = (delta[0] / center_distance, delta[1] / center_distance)
    base = (c0[0] + along * unit[0], c0[1] + along * unit[1])
    height = math.sqrt(max(0.0, height_sq))
    perpendicular = (-unit[1], unit[0])
    roots = [(
        base[0] + height * perpendicular[0],
        base[1] + height * perpendicular[1],
    )]
    if height > contact_band:
        roots.append((
            base[0] - height * perpendicular[0],
            base[1] - height * perpendicular[1],
        ))
    angle_tolerance = max(
        contact_band / max(min(r0, r1), contact_band), 1e-13
    )
    for point in roots:
        left_u = _curve_fraction_for_angle(
            math.atan2(point[1] - c0[1], point[0] - c0[0]),
            float(left["curve_start_parameter"]),
            float(left["curve_sweep"]),
            angle_tolerance,
        )
        right_u = _curve_fraction_for_angle(
            math.atan2(point[1] - c1[1], point[0] - c1[0]),
            float(right["curve_start_parameter"]),
            float(right["curve_sweep"]),
            angle_tolerance,
        )
        if left_u is None or right_u is None:
            continue
        left_parameter_tolerance = contact_band / max(
            float(left.get("length") or 0.0), r0, contact_band
        )
        right_parameter_tolerance = contact_band / max(
            float(right.get("length") or 0.0), r1, contact_band
        )
        left_endpoint = (
            left_u <= left_parameter_tolerance
            or left_u >= 1.0 - left_parameter_tolerance
        )
        right_endpoint = (
            right_u <= right_parameter_tolerance
            or right_u >= 1.0 - right_parameter_tolerance
        )
        if not (left_endpoint and right_endpoint):
            return True, "unresolved_curve_curve_contact"
    return True, None


def _ellipse_locus_matrix(curve: Dict[str, Any]) -> Optional[Tuple[float, float, float]]:
    if curve.get("curve_radius") is not None:
        radius_sq = float(curve["curve_radius"]) ** 2
        return (radius_sq, 0.0, radius_sq)
    if (
        curve.get("curve_major_axis") is None
        or curve.get("curve_minor_axis") is None
    ):
        return None
    major = tuple(curve["curve_major_axis"])
    minor = tuple(curve["curve_minor_axis"])
    return (
        major[0] * major[0] + minor[0] * minor[0],
        major[0] * major[1] + minor[0] * minor[1],
        major[1] * major[1] + minor[1] * minor[1],
    )


def _curves_are_same_analytic_path(
    left: Dict[str, Any], right: Dict[str, Any], tolerance: float
) -> bool:
    """Compare an analytic locus/path independently of seam or traversal."""
    if left.get("curve_center") is None or right.get("curve_center") is None:
        return False
    left_center = tuple(left["curve_center"])
    right_center = tuple(right["curve_center"])
    coordinate_magnitude = max(
        *(abs(value) for value in (*left_center, *right_center)), 1e-300
    )
    geometry_scale = max(
        float(left.get("curve_radius") or 0.0),
        float(right.get("curve_radius") or 0.0),
        *(
            abs(float(value))
            for curve in (left, right)
            for field in ("curve_major_axis", "curve_minor_axis")
            for value in (curve.get(field) or ())
        ),
        1e-300,
    )
    metric_tolerance = max(
        math.ulp(geometry_scale) * 8.0,
        1e-300,
    )
    path_tolerance = max(
        math.ulp(coordinate_magnitude) * 8.0,
        metric_tolerance,
    )
    if math.dist(left_center, right_center) > path_tolerance:
        return False
    left_z = float(left.get("plane_z") or 0.0)
    right_z = float(right.get("plane_z") or 0.0)
    z_scale = max(abs(left_z), abs(right_z), 1e-300)
    z_tolerance = max(math.ulp(z_scale) * 8.0, 1e-300)
    if (
        abs(left_z - right_z) > z_tolerance
        or abs(
            float(left.get("z_span") or 0.0)
            - float(right.get("z_span") or 0.0)
        ) > z_tolerance
    ):
        return False
    left_radius = left.get("curve_radius")
    right_radius = right.get("curve_radius")
    same_locus = False
    if left_radius is not None and right_radius is not None:
        same_locus = abs(float(left_radius) - float(right_radius)) <= metric_tolerance
    else:
        left_matrix = _ellipse_locus_matrix(left)
        right_matrix = _ellipse_locus_matrix(right)
        if left_matrix is not None and right_matrix is not None:
            matrix_scale = max(
                *(abs(value) for value in (*left_matrix, *right_matrix)), 1.0
            )
            matrix_tolerance = max(
                math.ulp(matrix_scale) * 32.0,
                metric_tolerance * math.sqrt(matrix_scale) * 4.0,
            )
            same_locus = all(
                abs(left_value - right_value) <= matrix_tolerance
                for left_value, right_value in zip(left_matrix, right_matrix)
            )
    if not same_locus:
        return False
    left_full = bool(left.get("is_closed_curve")) or abs(
        float(left.get("curve_sweep") or 0.0) - 2.0 * math.pi
    ) <= 1e-10
    right_full = bool(right.get("is_closed_curve")) or abs(
        float(right.get("curve_sweep") or 0.0) - 2.0 * math.pi
    ) <= 1e-10
    if left_full or right_full:
        return left_full and right_full
    left_endpoints = sorted((tuple(left["start"]), tuple(left["end"])))
    right_endpoints = sorted((tuple(right["start"]), tuple(right["end"])))
    if any(
        math.dist(left_point, right_point) > path_tolerance
        for left_point, right_point in zip(left_endpoints, right_endpoints)
    ):
        return False
    left_length = float(left.get("length") or 0.0)
    right_length = float(right.get("length") or 0.0)
    length_tolerance = max(
        math.ulp(max(abs(left_length), abs(right_length), 1e-300)) * 32.0,
        metric_tolerance * 16.0,
    )
    if abs(left_length - right_length) > length_tolerance:
        return False
    return math.dist(
        _analytic_curve_point_tangent(left, 0.5)[0],
        _analytic_curve_point_tangent(right, 0.5)[0],
    ) <= path_tolerance


def _coalesce_duplicate_analytic_segments(
    segments: Sequence[Dict[str, Any]], tolerance: float
) -> List[Dict[str, Any]]:
    """Collapse coincident analytic paths before seam-specific graph splitting."""
    result: List[Dict[str, Any]] = []
    buckets: Dict[Tuple[Any, ...], List[Dict[str, Any]]] = defaultdict(list)
    for source in segments:
        is_analytic = bool(
            str(source.get("primitive_type") or "") == "curve"
            and source.get("curve_center") is not None
            and source.get("curve_start_parameter") is not None
            and source.get("curve_sweep") is not None
            and (
                source.get("curve_radius") is not None
                or (
                    source.get("curve_major_axis") is not None
                    and source.get("curve_minor_axis") is not None
                )
            )
        )
        if not is_analytic:
            result.append(source)
            continue
        center = tuple(source["curve_center"])
        locus_matrix = _ellipse_locus_matrix(source)
        is_full = bool(source.get("is_closed_curve")) or abs(
            float(source.get("curve_sweep") or 0.0) - 2.0 * math.pi
        ) <= 1e-10
        if is_full:
            domain_key: Tuple[Any, ...] = ("full",)
        else:
            endpoints = sorted((tuple(source["start"]), tuple(source["end"])))
            domain_key = (
                "open",
                *(
                    format(float(value), ".10g")
                    for point in endpoints
                    for value in point
                ),
                format(float(source.get("length") or 0.0), ".10g"),
            )
        bucket_key = (
            *(format(float(value), ".12g") for value in center),
            format(float(source.get("plane_z") or 0.0), ".12g"),
            *(
                format(float(value), ".12g")
                for value in (locus_matrix or ())
            ),
            *domain_key,
        )
        representative = next((
            candidate for candidate in buckets[bucket_key]
            if _curves_are_same_analytic_path(candidate, source, tolerance)
        ), None)
        identity = {
            "handle": source.get("handle"),
            "primitive_key": source.get("primitive_key"),
            "segment_id": source.get("segment_id"),
            "source_start": source.get("source_start"),
            "source_end": source.get("source_end"),
            "layer": source.get("layer"),
            "authored_curve_kind": source.get("curve_kind"),
            "authored_curve_model_kind": str(
                (source.get("curve_model") or {}).get("kind") or ""
            ),
            "start_z": source.get("start_z"),
            "end_z": source.get("end_z"),
            "plane_z": source.get("plane_z"),
            "z_span": source.get("z_span"),
        }
        if representative is None:
            representative = {
                **source,
                "coincident_source_members": [identity],
            }
            result.append(representative)
            buckets[bucket_key].append(representative)
        else:
            representative.setdefault("coincident_source_members", []).append(
                identity
            )
    return result


def _audit_curve_curve_contacts(
    segments: Sequence[Dict[str, Any]],
    curve_indices: Sequence[int],
    tolerance: float,
    max_checks: int,
) -> Tuple[int, Optional[str]]:
    """Veto any uncertified or error-tube-plausible interior curve contact.

    Curve/curve roots are not yet inserted into the graph. Certified adaptive
    chords can nevertheless prove two curves disjoint: every chord pair must
    be farther apart than the sum of both interpolation bounds and the topology
    tolerance. Authored endpoint/endpoint joints and exact duplicate paths are
    the only permitted contacts; everything else fails closed.
    """
    if len(curve_indices) < 2:
        return 0, None
    bounds = {
        index: _segment_xy_bbox(segments[index], tolerance)
        for index in curve_indices
    }
    ordered = sorted(
        curve_indices,
        key=lambda index: (
            bounds[index][0], str(segments[index].get("segment_id") or index)
        ),
    )
    active: Dict[int, None] = {}
    end_heap: List[Tuple[float, int]] = []
    checks = 0
    contact_tolerance = max(
        tolerance,
        math.ulp(max((
            abs(value)
            for index in curve_indices
            for point in segments[index].get("samples", [])
            for value in point
        ), default=1.0)) * 8.0,
        1e-12,
    )
    for right_index in ordered:
        right_bounds = bounds[right_index]
        while end_heap and end_heap[0][0] < right_bounds[0]:
            _, expired = heapq.heappop(end_heap)
            active.pop(expired, None)
        right = segments[right_index]
        right_samples = [tuple(point) for point in right.get("samples", [])]
        for left_index in active:
            checks += 1
            if checks > max_checks:
                return checks, "curve_curve_contact_budget"
            left_bounds = bounds[left_index]
            if (
                left_bounds[3] < right_bounds[1]
                or right_bounds[3] < left_bounds[1]
            ):
                continue
            left = segments[left_index]
            if abs(
                float(left.get("plane_z") or 0.0)
                - float(right.get("plane_z") or 0.0)
            ) > tolerance:
                continue
            left_samples = [tuple(point) for point in left.get("samples", [])]
            if len(left_samples) < 2 or len(right_samples) < 2:
                continue
            duplicate_path = _curves_are_same_analytic_path(
                left, right, tolerance
            ) or (
                len(left_samples) == len(right_samples)
                and (
                    all(
                        math.dist(a, b) <= tolerance
                        for a, b in zip(left_samples, right_samples)
                    )
                    or all(
                        math.dist(a, b) <= tolerance
                        for a, b in zip(left_samples, reversed(right_samples))
                    )
                )
            )
            if duplicate_path:
                continue
            analytic_handled, analytic_unresolved = _audit_circle_circle_pair(
                left, right, tolerance
            )
            if analytic_handled:
                if analytic_unresolved:
                    return checks, analytic_unresolved
                continue
            left_error_value = left.get("sampling_error_bound")
            right_error_value = right.get("sampling_error_bound")
            certified = bool(
                left.get("sampling_certified")
                and right.get("sampling_certified")
                and left_error_value is not None
                and right_error_value is not None
            )
            if not certified:
                return checks, "uncertified_curve_curve_overlap"
            left_error = max(0.0, float(left_error_value))
            right_error = max(0.0, float(right_error_value))
            if not math.isfinite(left_error + right_error):
                return checks, "uncertified_curve_curve_overlap"
            tube_radius = contact_tolerance + left_error + right_error
            shared_endpoints = [
                left_point
                for left_point in (tuple(left["start"]), tuple(left["end"]))
                for right_point in (tuple(right["start"]), tuple(right["end"]))
                if math.dist(left_point, right_point) <= contact_tolerance
            ]
            for left_start, left_end in zip(left_samples, left_samples[1:]):
                for right_start, right_end in zip(right_samples, right_samples[1:]):
                    checks += 1
                    if checks > max_checks:
                        return checks, "curve_curve_contact_budget"
                    if (
                        max(left_start[0], left_end[0]) + tube_radius
                        < min(right_start[0], right_end[0])
                        or max(right_start[0], right_end[0]) + tube_radius
                        < min(left_start[0], left_end[0])
                        or max(left_start[1], left_end[1]) + tube_radius
                        < min(right_start[1], right_end[1])
                        or max(right_start[1], right_end[1]) + tube_radius
                        < min(left_start[1], left_end[1])
                    ):
                        continue
                    contacts = _segment_contact_points(
                        left_start,
                        left_end,
                        right_start,
                        right_end,
                        contact_tolerance,
                    )
                    if contacts and all(
                        any(
                            math.dist(point, shared) <= contact_tolerance
                            for shared in shared_endpoints
                        )
                        for point in contacts
                    ):
                        continue
                    chord_distance = min(
                        _point_on_line_distance(
                            left_start, right_start, right_end
                        ),
                        _point_on_line_distance(
                            left_end, right_start, right_end
                        ),
                        _point_on_line_distance(
                            right_start, left_start, left_end
                        ),
                        _point_on_line_distance(
                            right_end, left_start, left_end
                        ),
                    )
                    if contacts or chord_distance <= tube_radius:
                        return checks, "unresolved_curve_curve_contact"
        active[right_index] = None
        heapq.heappush(end_heap, (right_bounds[2], right_index))
    return checks, None


def _ellipse_arc_length(major: Point2,
                        minor: Point2,
                        start_parameter: float,
                        sweep: float) -> float:
    """Numerically integrate an affine unit-circle arc to near machine scale."""
    end_parameter = start_parameter + sweep

    def speed(parameter: float) -> float:
        return math.hypot(
            -major[0] * math.sin(parameter) + minor[0] * math.cos(parameter),
            -major[1] * math.sin(parameter) + minor[1] * math.cos(parameter),
        )

    def simpson(left: float, right: float) -> float:
        midpoint = (left + right) / 2.0
        return (right - left) * (
            speed(left) + 4.0 * speed(midpoint) + speed(right)
        ) / 6.0

    whole = simpson(start_parameter, end_parameter)
    scale = max(math.hypot(*major), math.hypot(*minor), 1.0)
    target = max(abs(sweep) * scale * 1e-11, 1e-12)

    def refine(left: float,
               right: float,
               estimate: float,
               local_target: float,
               depth: int) -> float:
        midpoint = (left + right) / 2.0
        left_estimate = simpson(left, midpoint)
        right_estimate = simpson(midpoint, right)
        correction = left_estimate + right_estimate - estimate
        if depth <= 0 or abs(correction) <= 15.0 * local_target:
            return left_estimate + right_estimate + correction / 15.0
        return refine(
            left, midpoint, left_estimate, local_target / 2.0, depth - 1
        ) + refine(
            midpoint, right, right_estimate, local_target / 2.0, depth - 1
        )

    return abs(refine(start_parameter, end_parameter, whole, target, 16))


def _normalized_intersection_events(segment: Dict[str, Any],
                                    events: Sequence[Dict[str, Any]],
                                    tolerance: float) -> Tuple[
                                        List[Dict[str, Any]], Optional[str]
                                    ]:
    if not events:
        return [], None
    scale = max(float(segment.get("length") or 0.0), tolerance, 1e-300)
    ordered = sorted(
        events,
        key=lambda item: (
            float(item["u"]),
            float(item["point"][0]),
            float(item["point"][1]),
            str(item.get("method") or ""),
        ),
    )
    normalized: List[Dict[str, Any]] = []
    for event in ordered:
        candidate = {**event, "u": min(1.0, max(0.0, float(event["u"])))}
        if not normalized:
            normalized.append(candidate)
            continue
        previous = normalized[-1]
        parameter_distance = abs(float(candidate["u"]) - float(previous["u"])) * scale
        spatial_distance = math.dist(candidate["point"], previous["point"])
        if parameter_distance <= tolerance and spatial_distance <= tolerance:
            previous["residual"] = max(
                float(previous.get("residual") or 0.0),
                float(candidate.get("residual") or 0.0),
            )
            previous["methods"] = sorted(set(
                previous.get("methods", [previous.get("method")])
                + candidate.get("methods", [candidate.get("method")])
            ))
            continue
        if parameter_distance <= 2.0 * tolerance or spatial_distance <= 2.0 * tolerance:
            return [], "line_curve_intersections_below_resolution"
        normalized.append(candidate)
    return normalized, None


def _split_line_curve_segments(segments: Sequence[Dict[str, Any]],
                               events_by_index: Dict[int, List[Dict[str, Any]]],
                               tolerance: float) -> Tuple[
                                   List[Dict[str, Any]], Optional[str]
                               ]:
    result: List[Dict[str, Any]] = []
    for segment_index, segment in enumerate(segments):
        events, unresolved = _normalized_intersection_events(
            segment, events_by_index.get(segment_index, []), tolerance
        )
        if unresolved:
            return [], unresolved
        internal_events = [
            event for event in events
            if tolerance < float(event["u"]) * max(
                float(segment.get("length") or 0.0), tolerance
            )
            and tolerance < (1.0 - float(event["u"])) * max(
                float(segment.get("length") or 0.0), tolerance
            )
        ]
        if not internal_events:
            result.append(segment)
            continue
        parameters = [0.0, *[float(event["u"]) for event in internal_events], 1.0]
        source_range = segment.get("source_parameter_range", (0.0, 1.0))
        source_u0 = float(source_range[0])
        source_u1 = float(source_range[1])
        is_curve = str(segment.get("primitive_type") or "line") == "curve"
        segment_scale = max(float(segment.get("length") or 0.0), tolerance)
        endpoint_start_event = next((
            event for event in events
            if float(event["u"]) * segment_scale <= tolerance
        ), None)
        endpoint_end_event = next((
            event for event in reversed(events)
            if (1.0 - float(event["u"])) * segment_scale <= tolerance
        ), None)
        for part_index, (u0, u1) in enumerate(zip(parameters, parameters[1:])):
            child_sampling_target = segment.get("sampling_error_target", 0.0)
            child_sampling_bound = segment.get("sampling_error_bound")
            child_sampling_capped = bool(segment.get("sampling_capped"))
            start_event = (
                internal_events[part_index - 1]
                if part_index > 0 else endpoint_start_event
            )
            end_event = (
                internal_events[part_index]
                if part_index < len(internal_events) else endpoint_end_event
            )
            if is_curve:
                analytic_kind = str(segment.get("curve_kind") or "").lower()
                analytic = (
                    segment.get("curve_center") is not None
                    and segment.get("curve_start_parameter") is not None
                    and segment.get("curve_sweep") is not None
                    and (
                        segment.get("curve_radius") is not None
                        or (
                            segment.get("curve_major_axis") is not None
                            and segment.get("curve_minor_axis") is not None
                        )
                    )
                )
                if not analytic:
                    return [], "uncertified_curve_interior_split"
                analytic_start, start_tangent = _analytic_curve_point_tangent(
                    segment, u0
                )
                analytic_end, end_tangent = _analytic_curve_point_tangent(
                    segment, u1
                )
                part_start = tuple(start_event["point"]) if start_event else (
                    tuple(segment["start"]) if u0 == 0.0 else analytic_start
                )
                part_end = tuple(end_event["point"]) if end_event else (
                    tuple(segment["end"]) if u1 == 1.0 else analytic_end
                )
                parent_start_parameter = float(segment["curve_start_parameter"])
                parent_sweep = float(segment["curve_sweep"])
                child_start_parameter = parent_start_parameter + parent_sweep * u0
                child_sweep = parent_sweep * (u1 - u0)
                if (
                    ("arc" in analytic_kind and "ellipse" not in analytic_kind)
                    or "circle" in analytic_kind
                ):
                    center = tuple(segment["curve_center"])
                    radius = float(segment["curve_radius"])

                    def point_at(parameter: float) -> Point2:
                        return (
                            center[0] + radius * math.cos(parameter),
                            center[1] + radius * math.sin(parameter),
                        )

                    sampling_scale = radius
                    part_length = abs(
                        radius * child_sweep
                    )
                else:
                    center = tuple(segment["curve_center"])
                    major = tuple(segment["curve_major_axis"])
                    minor = tuple(segment["curve_minor_axis"])

                    def point_at(parameter: float) -> Point2:
                        return (
                            center[0] + major[0] * math.cos(parameter)
                            + minor[0] * math.sin(parameter),
                            center[1] + major[1] * math.cos(parameter)
                            + minor[1] * math.sin(parameter),
                        )

                    sampling_scale = math.hypot(
                        math.hypot(*major), math.hypot(*minor)
                    )
                    part_length = _ellipse_arc_length(
                        major, minor, child_start_parameter, child_sweep,
                    )
                (
                    part_samples,
                    part_sample_parameters,
                    child_sampling_target,
                    child_sampling_bound,
                    child_sampling_capped,
                ) = _adaptive_parametric_samples(
                    point_at,
                    child_start_parameter,
                    child_sweep,
                    sampling_scale,
                    relative_chord_error=2.5e-4,
                    absolute_error_floor=max(
                        tolerance * 1e-3,
                        math.ulp(max(
                            abs(value)
                            for point in (part_start, part_end, center)
                            for value in point
                        )) * 2.0,
                        math.ulp(max(abs(sampling_scale), 1.0)) * 8.0,
                        1e-12,
                    ),
                )
                analytic_sample_start = part_samples[0]
                analytic_sample_end = part_samples[-1]
                part_samples[0] = part_start
                part_samples[-1] = part_end
                child_sampling_bound = max(
                    float(child_sampling_bound),
                    math.dist(part_start, analytic_sample_start),
                    math.dist(part_end, analytic_sample_end),
                )
            else:
                source_start = tuple(segment["start"])
                source_end = tuple(segment["end"])
                part_start = tuple(start_event["point"]) if start_event else (
                    source_start[0] + (source_end[0] - source_start[0]) * u0,
                    source_start[1] + (source_end[1] - source_start[1]) * u0,
                )
                part_end = tuple(end_event["point"]) if end_event else (
                    source_start[0] + (source_end[0] - source_start[0]) * u1,
                    source_start[1] + (source_end[1] - source_start[1]) * u1,
                )
                part_samples = [part_start, part_end]
                part_sample_parameters = [0.0, 1.0]
                part_length = math.dist(part_start, part_end)
                tangent = (
                    source_end[0] - source_start[0],
                    source_end[1] - source_start[1],
                )
                start_tangent = tangent
                end_tangent = tangent
            if part_length <= max(tolerance * 1e-3, 1e-12):
                continue
            part_start_z = (
                float(start_event["z"]) if start_event
                else _segment_z_at(segment, u0)
            )
            part_end_z = (
                float(end_event["z"]) if end_event
                else _segment_z_at(segment, u1)
            )
            part_sample_z = [
                _segment_z_at(segment, u0 + (u1 - u0) * parameter)
                for parameter in part_sample_parameters
            ]
            part_sample_z[0] = part_start_z
            part_sample_z[-1] = part_end_z
            boundary_events = [
                event for event in (start_event, end_event) if event is not None
            ]
            methods = sorted({
                str(event.get("method") or "")
                for event in boundary_events if event.get("method")
            })
            intersection_kinds = sorted({
                str(event.get("kind") or "")
                for event in boundary_events if event.get("kind")
            })
            residual = max((
                float(event.get("residual") or 0.0)
                for event in boundary_events
            ), default=0.0)
            child = {
                **segment,
                "segment_id": f"{segment['segment_id']}@lc{part_index}",
                "source_segment_id": segment.get(
                    "source_segment_id", segment["segment_id"]
                ),
                "start": part_start,
                "end": part_end,
                "samples": part_samples,
                "sample_parameters": part_sample_parameters,
                "sample_z": part_sample_z,
                "length": part_length,
                "start_z": part_start_z,
                "end_z": part_end_z,
                "z_span": max(part_sample_z) - min(part_sample_z),
                "plane_z": sum(part_sample_z) / len(part_sample_z),
                "source_parameter_range": (
                    source_u0 + (source_u1 - source_u0) * u0,
                    source_u0 + (source_u1 - source_u0) * u1,
                ),
                "start_tangent": start_tangent,
                "end_tangent": end_tangent,
                "sampling_segment_count": len(part_samples) - 1,
                "sampling_error_target": child_sampling_target,
                "sampling_error_bound": child_sampling_bound,
                "sampling_capped": child_sampling_capped,
                "planarized": True,
                "line_curve_planarized": True,
                "planarization_capped": False,
                "intersection_methods": methods,
                "intersection_kinds": intersection_kinds,
                "intersection_error_bound": residual,
            }
            if is_curve:
                parent_start_parameter = float(segment["curve_start_parameter"])
                parent_sweep = float(segment["curve_sweep"])
                child["curve_start_parameter"] = (
                    parent_start_parameter + parent_sweep * u0
                )
                child["curve_sweep"] = parent_sweep * (u1 - u0)
                child["is_closed_curve"] = False
                child_curve_model = dict(segment.get("curve_model") or {})
                child_curve_model["kind"] = (
                    "circle_arc"
                    if child_curve_model.get("kind") == "circle"
                    else "ellipse_arc"
                    if child_curve_model.get("kind") == "ellipse"
                    else child_curve_model.get("kind")
                )
                child_curve_model["start_parameter"] = child[
                    "curve_start_parameter"
                ]
                child_curve_model["sweep"] = child["curve_sweep"]
                child["curve_model"] = child_curve_model
            result.append(child)
    return result, None


def _incomplete_line_curve_planarization(
    segments: Sequence[Dict[str, Any]], reason: str
) -> List[Dict[str, Any]]:
    return [{
        **segment,
        "planarization_capped": True,
        "line_curve_planarization_complete": False,
        "planarization_unresolved": reason,
    } for segment in segments]


def _planarize_line_curve_segments(
    segments: Sequence[Dict[str, Any]],
    tolerance: float,
    max_pair_checks: int = 250000,
) -> List[Dict[str, Any]]:
    """Atomically split LINE/ARC and LINE/ELLIPSE contacts.

    Circular and elliptic roots are solved analytically in their native
    parameter domains. Uncertified spline samples may expose an ambiguous
    interior contact, but never create topology; such a drawing fails closed.
    """
    line_indices = [
        index for index, segment in enumerate(segments)
        if str(segment.get("primitive_type") or "line") == "line"
    ]
    curve_indices = [
        index for index, segment in enumerate(segments)
        if str(segment.get("primitive_type") or "line") == "curve"
    ]
    if not curve_indices:
        return [{
            **segment,
            "line_curve_planarization_complete": True,
            "line_curve_pair_checks": 0,
            "line_curve_span_checks": 0,
            "curve_curve_contact_checks": 0,
            "analytic_intersection_count": 0,
            "ambiguous_contact_count": 0,
        } for segment in segments]
    curve_curve_checks, curve_curve_unresolved = _audit_curve_curve_contacts(
        segments, curve_indices, tolerance, max_pair_checks
    )
    if curve_curve_unresolved:
        return _incomplete_line_curve_planarization(
            segments, curve_curve_unresolved
        )
    line_curve_check_budget = max(0, max_pair_checks - curve_curve_checks)
    if not line_indices:
        return [{
            **segment,
            "line_curve_planarization_complete": True,
            "line_curve_pair_checks": 0,
            "line_curve_span_checks": 0,
            "curve_curve_contact_checks": curve_curve_checks,
            "analytic_intersection_count": 0,
            "ambiguous_contact_count": 0,
        } for segment in segments]
    bounds = {
        index: _segment_xy_bbox(segments[index], tolerance)
        for index in [*line_indices, *curve_indices]
    }

    def overlap_estimate(axis: int) -> int:
        min_offset = axis
        max_offset = axis + 2
        curve_mins = sorted(bounds[index][min_offset] for index in curve_indices)
        curve_maxs = sorted(bounds[index][max_offset] for index in curve_indices)
        estimate = 0
        for index in line_indices:
            line_min = bounds[index][min_offset]
            line_max = bounds[index][max_offset]
            estimate += max(
                0,
                bisect_right(curve_mins, line_max)
                - bisect_left(curve_maxs, line_min),
            )
            if estimate > line_curve_check_budget:
                break
        return estimate

    sweep_axis = min((0, 1), key=overlap_estimate)
    secondary_axis = 1 - sweep_axis
    candidate_curves_by_line: Dict[int, List[int]] = defaultdict(list)
    active_lines: Dict[int, None] = {}
    active_curves: Dict[int, None] = {}
    line_end_heap: List[Tuple[float, int]] = []
    curve_end_heap: List[Tuple[float, int]] = []
    line_index_set = set(line_indices)
    sweep_events = sorted(
        [
            (
                bounds[index][sweep_axis],
                "line" if index in line_index_set else "curve",
                str(segments[index].get("segment_id") or index),
                index,
            )
            for index in [*line_indices, *curve_indices]
        ]
    )
    primary_pair_count = 0
    for event_start, event_kind, _, event_index in sweep_events:
        while line_end_heap and line_end_heap[0][0] < event_start:
            _, expired = heapq.heappop(line_end_heap)
            active_lines.pop(expired, None)
        while curve_end_heap and curve_end_heap[0][0] < event_start:
            _, expired = heapq.heappop(curve_end_heap)
            active_curves.pop(expired, None)
        if event_kind == "line":
            for curve_index in active_curves:
                candidate_curves_by_line[event_index].append(curve_index)
                primary_pair_count += 1
            active_lines[event_index] = None
            heapq.heappush(
                line_end_heap,
                (bounds[event_index][sweep_axis + 2], event_index),
            )
        else:
            for line_index in active_lines:
                candidate_curves_by_line[line_index].append(event_index)
                primary_pair_count += 1
            active_curves[event_index] = None
            heapq.heappush(
                curve_end_heap,
                (bounds[event_index][sweep_axis + 2], event_index),
            )
        if primary_pair_count > line_curve_check_budget:
            return _incomplete_line_curve_planarization(
                segments, "line_curve_pair_budget"
            )
    ordered_lines = sorted(
        line_indices,
        key=lambda index: (
            bounds[index][sweep_axis],
            str(segments[index].get("segment_id") or index),
        ),
    )
    events_by_index: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    pair_checks = 0
    span_checks = 0
    analytic_intersection_count = 0
    endpoint_intersection_count = 0
    tangent_contact_count = 0
    for line_index in ordered_lines:
        line = segments[line_index]
        line_bounds = bounds[line_index]
        for curve_index in candidate_curves_by_line.get(line_index, []):
            curve_bounds = bounds[curve_index]
            pair_checks += 1
            if pair_checks + span_checks > line_curve_check_budget:
                return _incomplete_line_curve_planarization(
                    segments, "line_curve_pair_budget"
                )
            if (
                curve_bounds[secondary_axis + 2] < line_bounds[secondary_axis]
                or line_bounds[secondary_axis + 2] < curve_bounds[secondary_axis]
            ):
                continue
            curve = segments[curve_index]
            if abs(
                float(line.get("plane_z") or 0.0)
                - float(curve.get("plane_z") or 0.0)
            ) > tolerance:
                continue
            kind = str(curve.get("curve_kind") or "").lower()
            if (
                (("arc" in kind and "ellipse" not in kind) or "circle" in kind)
                and curve.get("curve_center") is not None
                and curve.get("curve_radius") is not None
                and curve.get("curve_start_parameter") is not None
                and curve.get("curve_sweep") is not None
            ):
                contacts, unresolved = _line_arc_contacts(
                    line, curve, tolerance
                )
                consumed_spans = 0
                span_capped = False
            elif (
                "ellipse" in kind
                and curve.get("curve_center") is not None
                and curve.get("curve_major_axis") is not None
                and curve.get("curve_minor_axis") is not None
                and curve.get("curve_start_parameter") is not None
                and curve.get("curve_sweep") is not None
            ):
                contacts, unresolved = _line_ellipse_contacts(
                    line, curve, tolerance
                )
                consumed_spans = 0
                span_capped = False
            else:
                contacts, unresolved, consumed_spans, span_capped = (
                    _sampled_curve_contacts(
                        line,
                        curve,
                        tolerance,
                        max(
                            0,
                            line_curve_check_budget - pair_checks - span_checks,
                        ),
                    )
                )
                span_checks += consumed_spans
            if (
                span_capped
                or pair_checks + span_checks > line_curve_check_budget
            ):
                return _incomplete_line_curve_planarization(
                    segments, unresolved or "line_curve_span_budget"
                )
            if unresolved:
                return _incomplete_line_curve_planarization(segments, unresolved)
            for contact in contacts:
                if contact["kind"] == "tangent":
                    tangent_contact_count += 1
                line_u = float(contact["line_u"])
                curve_u = float(contact["curve_u"])
                point = tuple(contact["point"])
                line_length = max(float(line.get("length") or 0.0), tolerance)
                curve_length = max(float(curve.get("length") or 0.0), tolerance)
                if curve_u * curve_length <= tolerance:
                    authored = tuple(curve["start"])
                    line_direction = (
                        float(line["end"][0]) - float(line["start"][0]),
                        float(line["end"][1]) - float(line["start"][1]),
                    )
                    direction_length = math.hypot(*line_direction)
                    line_unit = (
                        line_direction[0] / max(direction_length, 1e-300),
                        line_direction[1] / max(direction_length, 1e-300),
                    )
                    projected_line_u = (
                        _dot((
                            authored[0] - float(line["start"][0]),
                            authored[1] - float(line["start"][1]),
                        ), line_unit) / max(direction_length, 1e-300)
                    )
                    if (
                        not -tolerance / line_length <= projected_line_u
                        <= 1.0 + tolerance / line_length
                        or _point_on_line_distance(
                            authored, tuple(line["start"]), tuple(line["end"])
                        ) > tolerance
                    ):
                        continue
                    point = authored
                    line_u = min(1.0, max(0.0, projected_line_u))
                    curve_u = 0.0
                elif (1.0 - curve_u) * curve_length <= tolerance:
                    authored = tuple(curve["end"])
                    line_direction = (
                        float(line["end"][0]) - float(line["start"][0]),
                        float(line["end"][1]) - float(line["start"][1]),
                    )
                    direction_length = math.hypot(*line_direction)
                    line_unit = (
                        line_direction[0] / max(direction_length, 1e-300),
                        line_direction[1] / max(direction_length, 1e-300),
                    )
                    projected_line_u = (
                        _dot((
                            authored[0] - float(line["start"][0]),
                            authored[1] - float(line["start"][1]),
                        ), line_unit) / max(direction_length, 1e-300)
                    )
                    if (
                        not -tolerance / line_length <= projected_line_u
                        <= 1.0 + tolerance / line_length
                        or _point_on_line_distance(
                            authored, tuple(line["start"]), tuple(line["end"])
                        ) > tolerance
                    ):
                        continue
                    point = authored
                    line_u = min(1.0, max(0.0, projected_line_u))
                    curve_u = 1.0
                if line_u * line_length <= tolerance:
                    line_u = 0.0
                    point = tuple(line["start"])
                elif (1.0 - line_u) * line_length <= tolerance:
                    line_u = 1.0
                    point = tuple(line["end"])
                line_z = _segment_z_at(line, line_u)
                curve_z = _segment_z_at(curve, curve_u)
                if abs(line_z - curve_z) > tolerance:
                    continue
                event_z = (line_z + curve_z) / 2.0
                common = {
                    "point": point,
                    "z": event_z,
                    "method": contact["method"],
                    "residual": float(contact.get("residual") or 0.0),
                    "kind": contact["kind"],
                }
                events_by_index[line_index].append({**common, "u": line_u})
                events_by_index[curve_index].append({**common, "u": curve_u})
                if str(contact["method"]).startswith("analytic_"):
                    analytic_intersection_count += 1
                if contact["kind"] == "endpoint":
                    endpoint_intersection_count += 1
    split_segments, unresolved = _split_line_curve_segments(
        segments, events_by_index, tolerance
    )
    if unresolved:
        return _incomplete_line_curve_planarization(segments, unresolved)
    return [{
        **segment,
        "line_curve_planarization_complete": True,
        "line_curve_pair_checks": pair_checks,
        "line_curve_span_checks": span_checks,
        "curve_curve_contact_checks": curve_curve_checks,
        "analytic_intersection_count": analytic_intersection_count,
        "endpoint_intersection_count": endpoint_intersection_count,
        "tangent_contact_count": tangent_contact_count,
        "ambiguous_contact_count": 0,
    } for segment in split_segments]


def _snap_segment_endpoints(segments: List[Dict[str, Any]],
                            tolerance: float) -> Tuple[List[Dict[str, Any]], Dict[int, Point2], Dict[int, float]]:
    grid: Dict[Tuple[int, int, int], List[int]] = defaultdict(list)
    node_samples: Dict[int, List[Tuple[float, float, float]]] = {}
    node_sample_counts: Dict[int, Dict[Tuple[float, float, float], int]] = {}
    node_sums: Dict[int, List[float]] = {}
    node_counts: Dict[int, int] = {}
    node_diameters: Dict[int, float] = {}
    exact_point_nodes: Dict[Tuple[float, float, float], int] = {}
    endpoint_nodes: Dict[Tuple[int, str], int] = {}
    endpoints = sorted(
        (
            float(point[0]), float(point[1]),
            float(segment.get(f"{endpoint_name}_z") or 0.0),
            str(segment.get("segment_id") or segment_index),
            endpoint_name, segment_index,
        )
        for segment_index, segment in enumerate(segments)
        for endpoint_name, point in (("start", segment["start"]), ("end", segment["end"]))
    )
    for x, y, z, _, endpoint_name, segment_index in endpoints:
        point = (x, y, z)
        exact_node = exact_point_nodes.get(point)
        if exact_node is not None:
            node_sample_counts[exact_node][point] += 1
            node_counts[exact_node] += 1
            for axis in range(3):
                node_sums[exact_node][axis] += point[axis]
            endpoint_nodes[(segment_index, endpoint_name)] = exact_node
            continue
        cell = (
            math.floor(x / tolerance),
            math.floor(y / tolerance),
            math.floor(z / tolerance),
        )
        best_id: Optional[int] = None
        best_max_distance = float("inf")
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    for node_id in grid.get(
                        (cell[0] + dx, cell[1] + dy, cell[2] + dz), []
                    ):
                        distances = [
                            math.dist(point, sample) for sample in node_samples[node_id]
                        ]
                        max_distance = max(distances, default=float("inf"))
                        # Complete-link membership prevents transitive A~B~C
                        # chains from joining when A and C exceed tolerance.
                        if max_distance <= tolerance and max_distance < best_max_distance:
                            best_id = node_id
                            best_max_distance = max_distance
        if best_id is None:
            best_id = len(node_samples)
            node_samples[best_id] = [point]
            node_sample_counts[best_id] = {point: 1}
            node_sums[best_id] = [point[0], point[1], point[2]]
            node_counts[best_id] = 1
            node_diameters[best_id] = 0.0
            grid[cell].append(best_id)
        else:
            node_samples[best_id].append(point)
            node_sample_counts[best_id][point] = 1
            node_counts[best_id] += 1
            for axis in range(3):
                node_sums[best_id][axis] += point[axis]
            node_diameters[best_id] = max(
                node_diameters[best_id], best_max_distance
            )
        exact_point_nodes[point] = best_id
        endpoint_nodes[(segment_index, endpoint_name)] = best_id

    centers3 = {
        node_id: (
            node_sums[node_id][0] / node_counts[node_id],
            node_sums[node_id][1] / node_counts[node_id],
            node_sums[node_id][2] / node_counts[node_id],
        )
        for node_id in node_samples
    }
    centers = {
        node_id: (center[0], center[1])
        for node_id, center in centers3.items()
    }
    snapped: List[Dict[str, Any]] = []
    for segment_index, segment in enumerate(segments):
        start_node = endpoint_nodes[(segment_index, "start")]
        end_node = endpoint_nodes[(segment_index, "end")]
        if start_node == end_node:
            continue
        snapped.append({**segment, "start_node": start_node, "end_node": end_node})
    spreads = dict(node_diameters)
    return snapped, centers, spreads


def _signed_polygon_area(points: Sequence[Point2]) -> float:
    if len(points) < 3:
        return 0.0
    anchor_x, anchor_y = points[0]
    return math.fsum(
        (points[index][0] - anchor_x)
        * (points[(index + 1) % len(points)][1] - anchor_y)
        - (points[(index + 1) % len(points)][0] - anchor_x)
        * (points[index][1] - anchor_y)
        for index in range(len(points))
    ) / 2.0


def _canonical_ring(points: Sequence[Point2],
                    quantum: float) -> Tuple[Tuple[int, int], ...]:
    quantized = [
        (round(point[0] / quantum), round(point[1] / quantum))
        for point in points
    ]
    if not quantized:
        return ()

    def minimal_rotation(values: List[Tuple[int, int]]) -> Tuple[Tuple[int, int], ...]:
        # Booth's algorithm: lexicographically minimal cyclic rotation in O(n).
        count = len(values)
        doubled = values + values
        first, second, offset = 0, 1, 0
        while first < count and second < count and offset < count:
            left = doubled[first + offset]
            right = doubled[second + offset]
            if left == right:
                offset += 1
                continue
            if left > right:
                first = first + offset + 1
                if first == second:
                    first += 1
            else:
                second = second + offset + 1
                if first == second:
                    second += 1
            offset = 0
        start = min(first, second)
        return tuple(doubled[start:start + count])

    forward = minimal_rotation(quantized)
    reverse = minimal_rotation(list(reversed(quantized)))
    return min(forward, reverse)


def _coverage_segment_point(segment: Dict[str, Any], fraction: float) -> Point2:
    """Evaluate a boundary child by physical-length fraction."""
    fraction = min(1.0, max(0.0, float(fraction)))
    if (
        str(segment.get("primitive_type") or "line") == "curve"
        and segment.get("curve_center") is not None
        and segment.get("curve_start_parameter") is not None
        and segment.get("curve_sweep") is not None
    ):
        kind = str(segment.get("curve_kind") or "").lower()
        if (
            "ellipse" in kind
            and segment.get("curve_major_axis") is not None
            and segment.get("curve_minor_axis") is not None
            and 0.0 < fraction < 1.0
        ):
            major = tuple(segment["curve_major_axis"])
            minor = tuple(segment["curve_minor_axis"])
            start_parameter = float(segment["curve_start_parameter"])
            sweep = float(segment["curve_sweep"])
            target = float(segment.get("length") or 0.0) * fraction
            low, high = 0.0, 1.0
            for _ in range(36):
                middle = (low + high) / 2.0
                length = _ellipse_arc_length(
                    major, minor, start_parameter, sweep * middle
                )
                if length < target:
                    low = middle
                else:
                    high = middle
            fraction = (low + high) / 2.0
        return _analytic_curve_point_tangent(segment, fraction)[0]
    samples = [tuple(point) for point in segment.get("samples", [])]
    if len(samples) >= 2:
        span_lengths = [
            math.dist(left, right) for left, right in zip(samples, samples[1:])
        ]
        total = math.fsum(span_lengths)
        target = total * fraction
        traversed = 0.0
        for index, span_length in enumerate(span_lengths):
            if traversed + span_length >= target or index == len(span_lengths) - 1:
                local = (
                    0.0 if span_length <= 1e-300
                    else (target - traversed) / span_length
                )
                return (
                    samples[index][0]
                    + (samples[index + 1][0] - samples[index][0]) * local,
                    samples[index][1]
                    + (samples[index + 1][1] - samples[index][1]) * local,
                )
            traversed += span_length
    start = tuple(segment["start"])
    end = tuple(segment["end"])
    return (
        start[0] + (end[0] - start[0]) * fraction,
        start[1] + (end[1] - start[1]) * fraction,
    )


def _analytic_curve_extrema(segment: Dict[str, Any]) -> List[Point2]:
    if (
        segment.get("curve_center") is None
        or segment.get("curve_start_parameter") is None
        or segment.get("curve_sweep") is None
    ):
        return []
    kind = str(segment.get("curve_kind") or "").lower()
    if ("arc" in kind and "ellipse" not in kind) or "circle" in kind:
        candidates = [0.0, math.pi / 2.0, math.pi, 3.0 * math.pi / 2.0]
    elif (
        "ellipse" in kind
        and segment.get("curve_major_axis") is not None
        and segment.get("curve_minor_axis") is not None
    ):
        major = tuple(segment["curve_major_axis"])
        minor = tuple(segment["curve_minor_axis"])
        x_extreme = math.atan2(minor[0], major[0])
        y_extreme = math.atan2(minor[1], major[1])
        candidates = [
            x_extreme, x_extreme + math.pi,
            y_extreme, y_extreme + math.pi,
        ]
    else:
        return []
    start_parameter = float(segment["curve_start_parameter"])
    sweep = float(segment["curve_sweep"])
    angle_tolerance = max(1e-13, math.ulp(abs(start_parameter) + abs(sweep)) * 16.0)
    points: List[Point2] = []
    for parameter in candidates:
        fraction = _curve_fraction_for_angle(
            parameter, start_parameter, sweep, angle_tolerance
        )
        if fraction is not None:
            points.append(_analytic_curve_point_tangent(segment, fraction)[0])
    return points


def _face_boundary_bbox(
    graph_edges: Sequence[Dict[str, Any]], polygon: Sequence[Point2]
) -> Tuple[float, float, float, float]:
    bounds = [
        min(point[0] for point in polygon),
        min(point[1] for point in polygon),
        max(point[0] for point in polygon),
        max(point[1] for point in polygon),
    ]
    for edge in graph_edges:
        if str(edge.get("primitive_type") or "") != "curve":
            continue
        extrema = _analytic_curve_extrema(edge)
        if extrema:
            bounds[0] = min(bounds[0], *(point[0] for point in extrema))
            bounds[1] = min(bounds[1], *(point[1] for point in extrema))
            bounds[2] = max(bounds[2], *(point[0] for point in extrema))
            bounds[3] = max(bounds[3], *(point[1] for point in extrema))
            continue
        error_bound = edge.get("sampling_error_bound")
        if edge.get("sampling_certified") and error_bound is not None:
            padding = max(0.0, float(error_bound))
            samples = [tuple(point) for point in edge.get("samples", [])]
            if samples:
                bounds[0] = min(bounds[0], min(point[0] for point in samples) - padding)
                bounds[1] = min(bounds[1], min(point[1] for point in samples) - padding)
                bounds[2] = max(bounds[2], max(point[0] for point in samples) + padding)
                bounds[3] = max(bounds[3], max(point[1] for point in samples) + padding)
    return tuple(bounds)  # type: ignore[return-value]


def _canonical_source_coverage(
    segments: Sequence[Dict[str, Any]],
) -> Tuple[Tuple[Any, ...], ...]:
    """Describe face coverage in physical space, independent of source tails.

    Normalized source parameters change when a LINE is lengthened and when a
    closed ellipse chooses another seam. Instead, contiguous children of one
    authored primitive are chained by their physical endpoints. Each run is
    identified by its unordered endpoints, physical midpoint, and exact
    covered length. Thus an inserted tangent vertex or reversed traversal does
    not change identity, while complementary arc halves remain distinct.
    """
    coverage_origin = min(
        (
            tuple(point)
            for segment in segments
            for point in (segment["start"], segment["end"])
        ),
        default=(0.0, 0.0),
    )
    grouped: Dict[Tuple[str, str, str, str], List[Dict[str, Any]]] = defaultdict(list)
    for segment in segments:
        key = (
            str(segment.get("handle") or ""),
            str(segment.get("primitive_key") or ""),
            str(segment.get("primitive_type") or "line").lower(),
            str(
                segment.get("authored_curve_kind")
                or segment.get("curve_kind")
                or ""
            ).lower(),
        )
        grouped[key].append(segment)

    coverage: List[Tuple[Any, ...]] = []
    for key, members in sorted(grouped.items()):
        member_lengths = [
            max(0.0, float(member.get("length") or 0.0))
            for member in members
        ]
        group_scale = max(math.fsum(member_lengths), 1e-300)
        coordinate_magnitude = max(
            (
                abs(float(value))
                for member in members
                for point in (member["start"], member["end"])
                for value in point
            ),
            default=1.0,
        )
        connection_tolerance = max(
            group_scale * 1e-10,
            math.ulp(max(coordinate_magnitude, 1.0)) * 16.0,
            1e-15,
        )
        node_points: List[List[Point2]] = []
        edge_nodes: List[Tuple[int, int]] = []
        exact_nodes: Dict[Point2, int] = {}
        endpoint_grid: Dict[Tuple[int, int], List[int]] = defaultdict(list)

        def endpoint_node(point: Point2) -> int:
            exact = exact_nodes.get(point)
            if exact is not None:
                node_points[exact].append(point)
                return exact
            cell = (
                math.floor(point[0] / connection_tolerance),
                math.floor(point[1] / connection_tolerance),
            )
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for node_id in endpoint_grid.get(
                        (cell[0] + dx, cell[1] + dy), []
                    ):
                        if math.dist(point, node_points[node_id][0]) <= connection_tolerance:
                            node_points[node_id].append(point)
                            exact_nodes[point] = node_id
                            return node_id
            node_points.append([point])
            node_id = len(node_points) - 1
            exact_nodes[point] = node_id
            endpoint_grid[cell].append(node_id)
            return node_id

        for member in members:
            edge_nodes.append((
                endpoint_node(tuple(member["start"])),
                endpoint_node(tuple(member["end"])),
            ))
        node_centers = {
            node_id: (
                math.fsum(point[0] for point in points) / len(points),
                math.fsum(point[1] for point in points) / len(points),
            )
            for node_id, points in enumerate(node_points)
        }
        adjacency: Dict[int, List[int]] = defaultdict(list)
        for edge_index, (start_node, end_node) in enumerate(edge_nodes):
            adjacency[start_node].append(edge_index)
            adjacency[end_node].append(edge_index)
        unseen = set(range(len(members)))
        runs: List[Tuple[Any, ...]] = []
        while unseen:
            seed = min(unseen)
            component = {seed}
            queue = [seed]
            while queue:
                edge_index = queue.pop()
                for node_id in edge_nodes[edge_index]:
                    for neighbor in adjacency[node_id]:
                        if neighbor not in component:
                            component.add(neighbor)
                            queue.append(neighbor)
            unseen.difference_update(component)
            component_nodes = {
                node_id for edge_index in component for node_id in edge_nodes[edge_index]
            }
            terminal_nodes = [
                node_id for node_id in component_nodes
                if sum(
                    edge_index in component
                    for edge_index in adjacency[node_id]
                ) == 1
            ]
            is_cycle = not terminal_nodes
            if any(
                sum(edge_index in component for edge_index in adjacency[node_id]) > 2
                for node_id in component_nodes
            ):
                # A single authored primitive should never branch. Preserve a
                # deterministic fail-safe signature without pretending it is
                # one continuous run.
                ordered_edges = sorted(component)
                ordered: List[Tuple[int, bool]] = [
                    (edge_index, True) for edge_index in ordered_edges
                ]
            else:
                start_node = min(
                    terminal_nodes or list(component_nodes),
                    key=lambda node_id: node_centers[node_id],
                )
                ordered = []
                current_node = start_node
                remaining = set(component)
                while remaining:
                    candidates = sorted(
                        edge_index
                        for edge_index in adjacency[current_node]
                        if edge_index in remaining
                    )
                    if not candidates:
                        break
                    edge_index = candidates[0]
                    edge_start, edge_end = edge_nodes[edge_index]
                    forward = edge_start == current_node
                    ordered.append((edge_index, forward))
                    remaining.remove(edge_index)
                    current_node = edge_end if forward else edge_start
            total_length = math.fsum(member_lengths[index] for index in component)
            if total_length <= 0.0:
                continue
            if is_cycle:
                run_start = min(
                    (node_centers[node_id] for node_id in component_nodes),
                    default=(0.0, 0.0),
                )
                run_end = run_start
                run_midpoint = run_start
            else:
                first_index, first_forward = ordered[0]
                last_index, last_forward = ordered[-1]
                run_start = tuple(
                    members[first_index]["start" if first_forward else "end"]
                )
                run_end = tuple(
                    members[last_index]["end" if last_forward else "start"]
                )
                target = total_length / 2.0
                traversed = 0.0
                run_midpoint = run_start
                for edge_index, forward in ordered:
                    edge_length = member_lengths[edge_index]
                    if traversed + edge_length >= target:
                        local_fraction = (
                            0.0 if edge_length <= 1e-300
                            else (target - traversed) / edge_length
                        )
                        if not forward:
                            local_fraction = 1.0 - local_fraction
                        run_midpoint = _coverage_segment_point(
                            members[edge_index], local_fraction
                        )
                        break
                    traversed += edge_length
            anchor, other = sorted((run_start, run_end))
            local_scale = max(
                total_length,
                math.dist(anchor, other),
                math.dist(anchor, run_midpoint),
                1e-300,
            )
            # Five decimal digits relative to the covered run are deliberate:
            # they absorb representable-coordinate jitter after a rigid MOVE
            # while retaining the physical midpoint/length distinction between
            # complementary paths. The face-wide origin preserves the layout
            # between separate primitive runs without hashing absolute position.
            quantum_exponent = int(math.floor(math.log10(local_scale))) - 5
            quantum = 10.0 ** quantum_exponent

            def quantize(value: float) -> int:
                return int(round(float(value) / quantum))

            runs.append((
                "cycle" if is_cycle else "run",
                quantum_exponent,
                (
                    quantize(anchor[0] - coverage_origin[0]),
                    quantize(anchor[1] - coverage_origin[1]),
                ),
                (
                    quantize(other[0] - anchor[0]),
                    quantize(other[1] - anchor[1]),
                ),
                (
                    quantize(run_midpoint[0] - anchor[0]),
                    quantize(run_midpoint[1] - anchor[1]),
                ),
                quantize(total_length),
            ))
        coverage.append((*key, tuple(sorted(runs))))
    return tuple(coverage)


def _polygon_has_self_intersection(points: Sequence[Point2],
                                   tolerance: float,
                                   max_pair_checks: int = 100000) -> bool:
    """Conservatively reject non-adjacent boundary segment intersections."""
    count = len(points)
    if count < 4:
        return False
    edges = [
        (points[index], points[(index + 1) % count], index)
        for index in range(count)
    ]
    ordered = sorted(edges, key=lambda item: min(item[0][0], item[1][0]))
    active: List[Tuple[Point2, Point2, int]] = []
    checks = 0

    def intersects(a: Point2, b: Point2, c: Point2, d: Point2) -> bool:
        ab = (b[0] - a[0], b[1] - a[1])
        cd = (d[0] - c[0], d[1] - c[1])
        epsilon_ab = tolerance * max(math.hypot(*ab), 1e-12)
        epsilon_cd = tolerance * max(math.hypot(*cd), 1e-12)
        o1 = _cross(ab, (c[0] - a[0], c[1] - a[1]))
        o2 = _cross(ab, (d[0] - a[0], d[1] - a[1]))
        o3 = _cross(cd, (a[0] - c[0], a[1] - c[1]))
        o4 = _cross(cd, (b[0] - c[0], b[1] - c[1]))
        if (
            ((o1 > epsilon_ab and o2 < -epsilon_ab) or (o1 < -epsilon_ab and o2 > epsilon_ab))
            and ((o3 > epsilon_cd and o4 < -epsilon_cd) or (o3 < -epsilon_cd and o4 > epsilon_cd))
        ):
            return True

        def on_segment(point: Point2, start: Point2, end: Point2,
                       orientation: float, epsilon: float) -> bool:
            return (
                abs(orientation) <= epsilon
                and min(start[0], end[0]) - tolerance <= point[0] <= max(start[0], end[0]) + tolerance
                and min(start[1], end[1]) - tolerance <= point[1] <= max(start[1], end[1]) + tolerance
            )

        return (
            on_segment(c, a, b, o1, epsilon_ab)
            or on_segment(d, a, b, o2, epsilon_ab)
            or on_segment(a, c, d, o3, epsilon_cd)
            or on_segment(b, c, d, o4, epsilon_cd)
        )

    for a, b, index in ordered:
        min_x = min(a[0], b[0]) - tolerance
        active = [
            edge for edge in active
            if max(edge[0][0], edge[1][0]) + tolerance >= min_x
        ]
        for c, d, other_index in active:
            if (
                abs(index - other_index) in {0, 1}
                or {index, other_index} == {0, count - 1}
            ):
                continue
            if (
                max(a[1], b[1]) + tolerance < min(c[1], d[1])
                or max(c[1], d[1]) + tolerance < min(a[1], b[1])
            ):
                continue
            checks += 1
            if checks > max_pair_checks:
                return True
            if intersects(a, b, c, d):
                return True
        active.append((a, b, index))
    return False


def _point_on_polygon_boundary(point: Point2,
                               polygon: Sequence[Point2],
                               tolerance: float) -> bool:
    for index, start in enumerate(polygon):
        end = polygon[(index + 1) % len(polygon)]
        dx, dy = end[0] - start[0], end[1] - start[1]
        length_sq = dx * dx + dy * dy
        if length_sq <= 1e-24:
            distance = math.dist(point, start)
        else:
            parameter = min(1.0, max(0.0, (
                (point[0] - start[0]) * dx + (point[1] - start[1]) * dy
            ) / length_sq))
            projection = (start[0] + parameter * dx, start[1] + parameter * dy)
            distance = math.dist(point, projection)
        if distance <= tolerance:
            return True
    return False


def _point_strictly_inside_polygon(point: Point2,
                                   polygon: Sequence[Point2],
                                   tolerance: float) -> bool:
    if _point_on_polygon_boundary(point, polygon, tolerance):
        return False
    inside = False
    x, y = point
    for start, end in zip(polygon, [*polygon[1:], polygon[0]]):
        if (start[1] > y) == (end[1] > y):
            continue
        denominator = end[1] - start[1]
        if abs(denominator) <= 1e-300:
            continue
        crossing_x = start[0] + (y - start[1]) * (end[0] - start[0]) / denominator
        if crossing_x > x:
            inside = not inside
    return inside


def _segment_contact_points(a: Point2,
                            b: Point2,
                            c: Point2,
                            d: Point2,
                            tolerance: float) -> List[Point2]:
    """Return finite XY contacts between two sampled boundary segments."""
    r = (b[0] - a[0], b[1] - a[1])
    s = (d[0] - c[0], d[1] - c[1])
    r_cross_s = _cross(r, s)
    c_minus_a = (c[0] - a[0], c[1] - a[1])
    scale = max(math.hypot(*r), math.hypot(*s), tolerance, 1e-12)
    cross_epsilon = tolerance * scale
    contacts: List[Point2] = []
    if abs(r_cross_s) > cross_epsilon:
        t = _cross(c_minus_a, s) / r_cross_s
        u = _cross(c_minus_a, r) / r_cross_s
        parameter_epsilon = tolerance / scale
        if (
            -parameter_epsilon <= t <= 1.0 + parameter_epsilon
            and -parameter_epsilon <= u <= 1.0 + parameter_epsilon
        ):
            contacts.append((a[0] + t * r[0], a[1] + t * r[1]))
        return contacts
    if abs(_cross(c_minus_a, r)) > cross_epsilon:
        return contacts

    def on_segment(point: Point2, start: Point2, end: Point2) -> bool:
        direction = (end[0] - start[0], end[1] - start[1])
        length_sq = direction[0] ** 2 + direction[1] ** 2
        if length_sq <= 1e-24:
            return math.dist(point, start) <= tolerance
        parameter = (
            (point[0] - start[0]) * direction[0]
            + (point[1] - start[1]) * direction[1]
        ) / length_sq
        projection = (
            start[0] + parameter * direction[0],
            start[1] + parameter * direction[1],
        )
        return -tolerance <= parameter * math.sqrt(length_sq) <= math.sqrt(length_sq) + tolerance and math.dist(point, projection) <= tolerance

    for point in (a, b, c, d):
        if on_segment(point, a, b) and on_segment(point, c, d):
            contacts.append(point)
    return contacts


def _bridge_component_splits_face(bridge_edges: Sequence[Dict[str, Any]],
                                  polygon: Sequence[Point2],
                                  node_centers: Dict[int, Point2],
                                  tolerance: float,
                                  max_contact_checks: int = 100000) -> bool:
    """Detect a bridge path that actually enters the face between contacts.

    Bridge edges form a forest. Each edge is augmented with its sampled
    boundary-contact vertices, then non-contact leaves are pruned. This keeps
    only paths connecting distinct boundary contacts, so an unrelated interior
    dangling branch attached to an exterior tangent cannot suppress the face.
    """
    if not bridge_edges:
        return False
    edges_by_node: Dict[int, List[int]] = defaultdict(list)
    for edge_index, edge in enumerate(bridge_edges):
        edges_by_node[int(edge["start_node"])].append(edge_index)
        edges_by_node[int(edge["end_node"])].append(edge_index)
    unseen = set(range(len(bridge_edges)))
    quantum = max(tolerance, 1e-12)
    polygon_segments = list(zip(polygon, [*polygon[1:], polygon[0]]))
    contact_checks = 0
    while unseen:
        seed = unseen.pop()
        component = [seed]
        stack = [seed]
        while stack:
            edge_index = stack.pop()
            edge = bridge_edges[edge_index]
            for node_id in (int(edge["start_node"]), int(edge["end_node"])):
                for neighbor in edges_by_node[node_id]:
                    if neighbor in unseen:
                        unseen.remove(neighbor)
                        component.append(neighbor)
                        stack.append(neighbor)
        adjacency: Dict[Tuple[Any, ...], List[Tuple[Tuple[Any, ...], bool]]] = defaultdict(list)
        terminals = set()

        def add_augmented_edge(left: Tuple[Any, ...],
                               right: Tuple[Any, ...],
                               inside: bool) -> None:
            if left == right:
                return
            adjacency[left].append((right, inside))
            adjacency[right].append((left, inside))

        for edge_index in component:
            edge = bridge_edges[edge_index]
            samples = edge.get("_snapped_samples") or _directed_samples(
                edge, int(edge["start_node"]), int(edge["end_node"]), node_centers
            )
            for sample_index, (start, end) in enumerate(zip(samples, samples[1:])):
                direction = (end[0] - start[0], end[1] - start[1])
                length_sq = direction[0] ** 2 + direction[1] ** 2
                if length_sq <= 1e-300:
                    continue
                contacts_by_parameter: Dict[float, Tuple[Any, ...]] = {}
                for boundary_start, boundary_end in polygon_segments:
                    if (
                        max(start[0], end[0]) + tolerance < min(boundary_start[0], boundary_end[0])
                        or max(boundary_start[0], boundary_end[0]) + tolerance < min(start[0], end[0])
                        or max(start[1], end[1]) + tolerance < min(boundary_start[1], boundary_end[1])
                        or max(boundary_start[1], boundary_end[1]) + tolerance < min(start[1], end[1])
                    ):
                        continue
                    contact_checks += 1
                    if contact_checks > max_contact_checks:
                        # A capped unresolved arrangement must not be promoted
                        # as a confident outer face.
                        return True
                    found_contacts = _segment_contact_points(
                        start, end, boundary_start, boundary_end, tolerance
                    )
                    for point in found_contacts:
                        parameter = min(1.0, max(0.0, (
                            (point[0] - start[0]) * direction[0]
                            + (point[1] - start[1]) * direction[1]
                        ) / length_sq))
                        terminal = (
                            "contact",
                            round(point[0] / quantum),
                            round(point[1] / quantum),
                        )
                        contacts_by_parameter[round(parameter, 14)] = terminal
                        terminals.add(terminal)

                if sample_index == 0:
                    start_vertex: Tuple[Any, ...] = (
                        "node", int(edge["start_node"])
                    )
                else:
                    start_vertex = ("sample", edge_index, sample_index)
                if sample_index == len(samples) - 2:
                    end_vertex: Tuple[Any, ...] = (
                        "node", int(edge["end_node"])
                    )
                else:
                    end_vertex = ("sample", edge_index, sample_index + 1)

                ordered_vertices: List[Tuple[float, Tuple[Any, ...]]] = [
                    (0.0, contacts_by_parameter.get(0.0, start_vertex)),
                    *[
                        (parameter, vertex)
                        for parameter, vertex in sorted(contacts_by_parameter.items())
                        if 0.0 < parameter < 1.0
                    ],
                    (1.0, contacts_by_parameter.get(1.0, end_vertex)),
                ]
                for (left_parameter, left_vertex), (right_parameter, right_vertex) in zip(
                    ordered_vertices, ordered_vertices[1:]
                ):
                    if right_parameter - left_parameter <= 1e-12:
                        continue
                    parameter = (left_parameter + right_parameter) / 2.0
                    probe = (
                        start[0] + parameter * direction[0],
                        start[1] + parameter * direction[1],
                    )
                    add_augmented_edge(
                        left_vertex,
                        right_vertex,
                        _point_strictly_inside_polygon(probe, polygon, tolerance),
                    )

        if len(terminals) < 2:
            continue
        remaining = set(adjacency)
        degrees = {
            vertex: len({neighbor for neighbor, _ in adjacency[vertex]})
            for vertex in adjacency
        }
        queue = [
            vertex for vertex in remaining
            if vertex not in terminals and degrees.get(vertex, 0) <= 1
        ]
        while queue:
            vertex = queue.pop()
            if vertex not in remaining:
                continue
            remaining.remove(vertex)
            for neighbor, _ in adjacency.get(vertex, []):
                if neighbor not in remaining:
                    continue
                degrees[neighbor] = max(0, degrees.get(neighbor, 0) - 1)
                if neighbor not in terminals and degrees[neighbor] <= 1:
                    queue.append(neighbor)
        if any(
            inside and vertex in remaining and neighbor in remaining
            for vertex in remaining
            for neighbor, inside in adjacency.get(vertex, [])
        ):
            return True
    return False


def _coalesce_graph_edges(
    segments: Sequence[Dict[str, Any]], tolerance: float = 1e-6
) -> List[Dict[str, Any]]:
    """Collapse coincident snapped edges while preserving source membership.

    Duplicate CAD entities are common in imported drawings.  Treating two
    identical segments as separate graph edges creates zero-width faces and
    makes the angular successor ambiguous.  The coalesced edge retains every
    contributing primitive so semantic grounding can still expose all handles.
    """
    by_signature: Dict[Tuple[Any, ...], Dict[str, Any]] = {}
    for segment in segments:
        a = int(segment["start_node"])
        b = int(segment["end_node"])
        samples = [tuple(point) for point in segment.get("samples", [segment["start"], segment["end"]])]
        coordinate_magnitude = max(
            (abs(value) for point in samples for value in point), default=1.0
        )
        path_quantum = max(
            float(tolerance) * 1e-3,
            math.ulp(max(coordinate_magnitude, 1.0)) * 8.0,
            1e-18,
        )
        forward = tuple((
            int(round(point[0] / path_quantum)),
            int(round(point[1] / path_quantum)),
        ) for point in samples)
        reverse = tuple(reversed(forward))
        path_signature = min(forward, reverse)
        primitive_type = str(segment.get("primitive_type") or "line")
        key = (
            min(a, b), max(a, b),
            primitive_type,
            str(segment.get("curve_kind") or ""),
            # A straight edge is fully determined by its snapped endpoints;
            # retain a path signature only for curved multiedges such as a D
            # profile's arc and diameter.
            path_signature if primitive_type == "curve" else (),
        )
        coincident_members = list(segment.get("coincident_source_members", []))
        if coincident_members:
            source_segments = [{
                **segment,
                **member,
                "coincident_source_members": [],
            } for member in coincident_members]
        else:
            source_segments = list(segment.get("source_segments", [segment]))
        if key not in by_signature:
            by_signature[key] = {
                **segment,
                "start_node": a,
                "end_node": b,
                "source_segments": source_segments,
            }
        else:
            representative = by_signature[key]
            representative["source_segments"].extend(source_segments)
            representative["approximate"] = bool(
                representative.get("approximate") or segment.get("approximate")
            )
            representative["sampling_capped"] = bool(
                representative.get("sampling_capped")
                or segment.get("sampling_capped")
            )
            representative["sampling_certified"] = bool(
                representative.get("sampling_certified", True)
                and segment.get("sampling_certified", True)
            )
            if (
                representative.get("sampling_error_bound") is None
                or segment.get("sampling_error_bound") is None
            ):
                representative["sampling_error_bound"] = None
            else:
                representative["sampling_error_bound"] = max(
                    float(representative.get("sampling_error_bound") or 0.0),
                    float(segment.get("sampling_error_bound") or 0.0),
                )
            representative["planarized"] = bool(
                representative.get("planarized") or segment.get("planarized")
            )
            representative["line_curve_planarized"] = bool(
                representative.get("line_curve_planarized")
                or segment.get("line_curve_planarized")
            )
            for field in (
                "sampling_error_target",
                "endpoint_adjustment",
                "endpoint_consistency_limit",
                "intersection_error_bound",
                "analytic_intersection_count",
                "endpoint_intersection_count",
                "tangent_contact_count",
                "line_curve_pair_checks",
                "line_curve_span_checks",
                "curve_curve_contact_checks",
            ):
                representative[field] = max(
                    float(representative.get(field) or 0.0),
                    float(segment.get(field) or 0.0),
                )
            representative["intersection_methods"] = sorted(set(
                representative.get("intersection_methods", [])
                + segment.get("intersection_methods", [])
            ))
            representative["intersection_kinds"] = sorted(set(
                representative.get("intersection_kinds", [])
                + segment.get("intersection_kinds", [])
            ))
    return list(by_signature.values())


def _directed_samples(segment: Dict[str, Any],
                      start_node: int,
                      end_node: int,
                      node_centers: Dict[int, Point2]) -> List[Point2]:
    samples = [tuple(point) for point in segment.get("samples", [])]
    if len(samples) < 2:
        samples = [node_centers[start_node], node_centers[end_node]]
    elif (
        int(segment["start_node"]) != start_node
        or int(segment["end_node"]) != end_node
    ):
        samples.reverse()
    samples[0] = node_centers[start_node]
    samples[-1] = node_centers[end_node]
    return samples


def _departure_angle(segment: Dict[str, Any],
                     start_node: int,
                     end_node: int,
                     node_centers: Dict[int, Point2]) -> float:
    samples = _directed_samples(segment, start_node, end_node, node_centers)
    tangent: Optional[Point2]
    if (
        int(segment["start_node"]) == start_node
        and int(segment["end_node"]) == end_node
    ):
        raw_tangent = segment.get("start_tangent")
        tangent = _point2(raw_tangent)
    else:
        raw_tangent = segment.get("end_tangent")
        parsed_tangent = _point2(raw_tangent)
        tangent = (
            (-parsed_tangent[0], -parsed_tangent[1])
            if parsed_tangent is not None else None
        )
    if tangent is not None and math.hypot(*tangent) > 1e-12:
        tangent_angle = math.atan2(tangent[1], tangent[0])
        origin = samples[0]
        for point in samples[1:]:
            chord = (point[0] - origin[0], point[1] - origin[1])
            if math.hypot(*chord) <= 1e-12:
                continue
            chord_angle = math.atan2(chord[1], chord[0])
            deviation = (chord_angle - tangent_angle + math.pi) % (
                2.0 * math.pi
            ) - math.pi
            # Preserve the analytic tangent as the primary order, but use the
            # curve's infinitesimal side as a deterministic tie-break at an
            # exact tangent junction. A bounded perturbation cannot reorder
            # resolvably distinct crossings.
            return tangent_angle + max(-1e-9, min(1e-9, deviation))
        return tangent_angle
    origin = samples[0]
    for point in samples[1:]:
        if math.dist(origin, point) > 1e-12:
            return math.atan2(point[1] - origin[1], point[0] - origin[0])
    destination = node_centers[end_node]
    return math.atan2(destination[1] - origin[1], destination[0] - origin[0])


def _bridge_edge_indices(segments: Sequence[Dict[str, Any]]) -> set:
    """Return graph bridges using iterative edge-aware Tarjan DFS.

    The explicit stack is multigraph-safe and avoids Python recursion limits on
    long imported chains with thousands of line entities.
    """
    adjacency: Dict[int, List[Tuple[int, int]]] = defaultdict(list)
    for edge_index, segment in enumerate(segments):
        a = int(segment["start_node"])
        b = int(segment["end_node"])
        adjacency[a].append((edge_index, b))
        adjacency[b].append((edge_index, a))

    discovery: Dict[int, int] = {}
    low: Dict[int, int] = {}
    bridges = set()
    parent_node: Dict[int, Optional[int]] = {}
    parent_edge: Dict[int, Optional[int]] = {}
    counter = 0
    for root in adjacency:
        if root in discovery:
            continue
        discovery[root] = low[root] = counter
        counter += 1
        parent_node[root] = None
        parent_edge[root] = None
        stack: List[Tuple[int, int]] = [(root, 0)]
        while stack:
            node, next_index = stack[-1]
            if next_index < len(adjacency[node]):
                edge_index, neighbor = adjacency[node][next_index]
                stack[-1] = (node, next_index + 1)
                if edge_index == parent_edge[node]:
                    continue
                if neighbor not in discovery:
                    parent_node[neighbor] = node
                    parent_edge[neighbor] = edge_index
                    discovery[neighbor] = low[neighbor] = counter
                    counter += 1
                    stack.append((neighbor, 0))
                else:
                    low[node] = min(low[node], discovery[neighbor])
                continue

            stack.pop()
            parent = parent_node[node]
            if parent is None:
                continue
            low[parent] = min(low[parent], low[node])
            incoming_edge = parent_edge[node]
            if incoming_edge is not None and low[node] > discovery[parent]:
                bridges.add(incoming_edge)
    return bridges


def _bounded_planar_faces(segments: Sequence[Dict[str, Any]],
                          node_centers: Dict[int, Point2],
                          area_tolerance: float) -> List[Dict[str, Any]]:
    """Walk all directed half-edges and return counter-clockwise faces.

    Outgoing edges are sorted counter-clockwise.  At each vertex the successor
    immediately clockwise from the reverse edge keeps the traversed face on the
    left.  In ordinary world XY coordinates bounded faces are counter-clockwise
    (positive signed area); the unbounded face is clockwise and is discarded.
    Bridges may occur twice in the exterior walk but cannot create a positive
    non-degenerate face on their own.
    """
    outgoing: Dict[int, List[Tuple[float, int, int]]] = defaultdict(list)
    for edge_index, segment in enumerate(segments):
        a = int(segment["start_node"])
        b = int(segment["end_node"])
        if a == b or a not in node_centers or b not in node_centers:
            continue
        outgoing[a].append((
            _departure_angle(segment, a, b, node_centers), edge_index, b,
        ))
        outgoing[b].append((
            _departure_angle(segment, b, a, node_centers), edge_index, a,
        ))
    for node_id in outgoing:
        outgoing[node_id].sort(key=lambda item: (item[0], item[1], item[2]))

    successor: Dict[Tuple[int, int, int], Tuple[int, int, int]] = {}
    for node_id, options in outgoing.items():
        for position, (_, edge_index, neighbor) in enumerate(options):
            # This option is node_id -> neighbor.  For the half-edge arriving
            # neighbor -> node_id, turn to the previous (clockwise) option.
            _, next_edge, next_node = options[(position - 1) % len(options)]
            successor[(neighbor, node_id, edge_index)] = (
                node_id, next_node, next_edge,
            )

    visited = set()
    faces: List[Dict[str, Any]] = []
    max_steps = max(1, len(segments) * 2 + 1)
    for edge_index, segment in enumerate(segments):
        a = int(segment["start_node"])
        b = int(segment["end_node"])
        for start in ((a, b, edge_index), (b, a, edge_index)):
            if start in visited or start not in successor:
                continue
            current = start
            half_edges: List[Tuple[int, int, int]] = []
            local_seen = set()
            for _ in range(max_steps):
                if current in local_seen:
                    break
                local_seen.add(current)
                visited.add(current)
                half_edges.append(current)
                current = successor.get(current)  # type: ignore[assignment]
                if current is None:
                    break
                if current == start:
                    break
            if current != start or len(half_edges) < 2:
                continue
            ordered_nodes = [half_edge[0] for half_edge in half_edges]
            polygon: List[Point2] = []
            for from_node, to_node, walked_edge_index in half_edges:
                directed = _directed_samples(
                    segments[walked_edge_index], from_node, to_node, node_centers
                )
                polygon.extend(directed[:-1])
            if len(polygon) < 3:
                continue
            local_extent = max(
                max(point[0] for point in polygon)
                - min(point[0] for point in polygon),
                max(point[1] for point in polygon)
                - min(point[1] for point in polygon),
                1e-12,
            )
            self_intersection_tolerance = max(
                math.ulp(local_extent) * 8.0,
                math.sqrt(max(area_tolerance, 1e-24)) * 1e-3,
                1e-12,
            )
            if _polygon_has_self_intersection(
                polygon, self_intersection_tolerance
            ):
                continue
            signed_area = _signed_polygon_area(polygon)
            if signed_area <= area_tolerance:
                continue
            edge_indices = [half_edge[2] for half_edge in half_edges]
            # A valid bounded face never needs the same undirected edge twice.
            # This also rejects self-touching exterior walks around bridges.
            if len(set(edge_indices)) != len(edge_indices):
                continue
            faces.append({
                "ordered_nodes": ordered_nodes,
                "edge_indices": edge_indices,
                "polygon": polygon,
                "signed_area": signed_area,
            })
    return faces


def _authored_primitive_ids(segment: Dict[str, Any]) -> set:
    """Return stable authored primitive identities represented by an edge."""
    sources = segment.get("source_segments")
    if not isinstance(sources, list) or not sources:
        sources = [segment]
    return {
        (
            str(source.get("handle") or ""),
            str(source.get("primitive_key") or ""),
        )
        for source in sources
        if str(source.get("handle") or "")
    }


def _authored_endpoint_cycle_contexts(
    raw_segments: Sequence[Dict[str, Any]],
    planar_graph_edges: Sequence[Dict[str, Any]],
    tolerance: float,
) -> List[Dict[str, Any]]:
    """Recover only unbranched cycles explicitly authored before planarizing.

    Atomic planar faces remain the primary hypotheses.  Two independently
    authored straight contours can cross at their interiors, however, and
    planarization then replaces each source contour with smaller cells.  A raw
    LINE endpoint component is safe to retain when every snapped endpoint has
    degree two.

    The planar graph supplies one further fail-closed check: candidate
    continuity must still contribute exactly two incident child edges at every
    contact, and every foreign edge there must belong to another independently
    closed degree-two component.  Thus a closed-contour crossing (2 + 2)
    preserves both authored contours, while an open divider, a T contact, a
    shared edge, or another branch cannot promote a cell union into a profile.
    """
    if not raw_segments or not planar_graph_edges:
        return []

    snapped, node_centers, node_spreads = _snap_segment_endpoints(
        [dict(segment) for segment in raw_segments], tolerance
    )
    raw_edges = _coalesce_graph_edges(snapped, tolerance)
    if not raw_edges:
        return []

    raw_adjacency: Dict[int, List[int]] = defaultdict(list)
    raw_degree: Dict[int, int] = defaultdict(int)
    for edge_index, edge in enumerate(raw_edges):
        start_node = int(edge["start_node"])
        end_node = int(edge["end_node"])
        raw_adjacency[start_node].append(edge_index)
        raw_adjacency[end_node].append(edge_index)
        raw_degree[start_node] += 1
        raw_degree[end_node] += 1

    components: List[Dict[str, Any]] = []
    unseen = set(range(len(raw_edges)))
    while unseen:
        seed = min(unseen)
        component_edges = {seed}
        queue = [seed]
        while queue:
            edge_index = queue.pop()
            edge = raw_edges[edge_index]
            for node_id in (int(edge["start_node"]), int(edge["end_node"])):
                for neighbor in raw_adjacency[node_id]:
                    if neighbor not in component_edges:
                        component_edges.add(neighbor)
                        queue.append(neighbor)
        unseen.difference_update(component_edges)
        component_nodes = {
            node_id
            for edge_index in component_edges
            for node_id in (
                int(raw_edges[edge_index]["start_node"]),
                int(raw_edges[edge_index]["end_node"]),
            )
        }
        if not component_nodes or any(
            raw_degree[node_id] != 2 for node_id in component_nodes
        ):
            continue
        if any(
            str(raw_edges[edge_index].get("primitive_type") or "line")
            != "line"
            for edge_index in component_edges
        ):
            # This recovery path is intentionally narrower than the analytic
            # face walker. Curved authored contours keep atomic-face semantics
            # until source-continuity contacts are certified equivalently.
            continue
        source_ids = set().union(*(
            _authored_primitive_ids(raw_edges[edge_index])
            for edge_index in component_edges
        ))
        if len({source_id[0] for source_id in source_ids}) < 2:
            continue
        components.append({
            "edge_indices": component_edges,
            "node_ids": component_nodes,
            "source_ids": source_ids,
        })
    if not components:
        return []

    source_component: Dict[Tuple[str, str], int] = {}
    invalid_components = set()
    for component_index, component in enumerate(components):
        for source_id in component["source_ids"]:
            previous = source_component.get(source_id)
            if previous is not None and previous != component_index:
                invalid_components.update((previous, component_index))
            source_component[source_id] = component_index

    planar_degree: Dict[int, int] = defaultdict(int)
    planar_incident_edges: Dict[int, List[int]] = defaultdict(list)
    planar_source_ids: Dict[int, set] = {}
    component_planar_edges: Dict[int, List[int]] = defaultdict(list)
    component_seen_sources: Dict[int, set] = defaultdict(set)
    for edge_index, edge in enumerate(planar_graph_edges):
        start_node = int(edge["start_node"])
        end_node = int(edge["end_node"])
        planar_degree[start_node] += 1
        planar_degree[end_node] += 1
        planar_incident_edges[start_node].append(edge_index)
        planar_incident_edges[end_node].append(edge_index)
        source_ids = _authored_primitive_ids(edge)
        planar_source_ids[edge_index] = source_ids
        mapped_components = {
            source_component[source_id]
            for source_id in source_ids
            if source_id in source_component
        }
        if not mapped_components:
            continue
        if len(mapped_components) != 1:
            invalid_components.update(mapped_components)
            continue
        component_index = next(iter(mapped_components))
        if any(
            source_component.get(source_id) != component_index
            for source_id in source_ids
        ):
            # A coalesced planar edge contains both candidate and foreign
            # authored geometry.  Treat shared/overlapping edges as ambiguous.
            invalid_components.add(component_index)
            continue
        component_planar_edges[component_index].append(edge_index)
        component_seen_sources[component_index].update(source_ids)

    provisional_components = set()
    component_dependencies: Dict[int, set] = defaultdict(set)
    component_contact_nodes: Dict[int, int] = {}
    for component_index, component in enumerate(components):
        if component_index in invalid_components:
            continue
        if component_seen_sources[component_index] != component["source_ids"]:
            continue
        candidate_degree: Dict[int, int] = defaultdict(int)
        for edge_index in component_planar_edges[component_index]:
            edge = planar_graph_edges[edge_index]
            candidate_degree[int(edge["start_node"])] += 1
            candidate_degree[int(edge["end_node"])] += 1
        if not candidate_degree or any(
            degree != 2 for degree in candidate_degree.values()
        ):
            continue
        foreign_degrees = {
            node_id: planar_degree[node_id] - degree
            for node_id, degree in candidate_degree.items()
        }
        if any(
            degree < 0 or degree % 2
            for degree in foreign_degrees.values()
        ):
            continue
        contact_nodes = {
            node_id for node_id, degree in foreign_degrees.items()
            if degree > 0
        }
        # Without a contact, the ordinary atomic face already represents the
        # authored cycle. Avoid emitting a second identity under loose snapping.
        if not contact_nodes:
            continue
        candidate_edge_indices = set(
            component_planar_edges[component_index]
        )
        dependencies = set()
        foreign_contact_is_closed = True
        for node_id in contact_nodes:
            for edge_index in planar_incident_edges[node_id]:
                if edge_index in candidate_edge_indices:
                    continue
                source_ids = planar_source_ids[edge_index]
                mapped = {
                    source_component[source_id]
                    for source_id in source_ids
                    if source_id in source_component
                }
                if (
                    not mapped
                    or component_index in mapped
                    or len(mapped) != 1
                    or any(source_id not in source_component for source_id in source_ids)
                ):
                    foreign_contact_is_closed = False
                    break
                dependencies.update(mapped)
            if not foreign_contact_is_closed:
                break
        if not foreign_contact_is_closed or not dependencies:
            continue
        provisional_components.add(component_index)
        component_dependencies[component_index] = dependencies
        component_contact_nodes[component_index] = len(contact_nodes)

    # Shared-path ambiguity can invalidate a component after another component
    # has already named it as crossing evidence. Propagate that uncertainty.
    valid_components = set(provisional_components)
    while True:
        rejected = {
            component_index
            for component_index in valid_components
            if not component_dependencies[component_index].issubset(
                valid_components
            )
        }
        if not rejected:
            break
        valid_components.difference_update(rejected)
    if not valid_components:
        return []

    eligible_raw_indices = sorted(set().union(*(
        components[component_index]["edge_indices"]
        for component_index in valid_components
    )))
    eligible_edges = [raw_edges[index] for index in eligible_raw_indices]
    eligible_component_by_edge = {
        eligible_index: next(
            component_index
            for component_index in valid_components
            if raw_index in components[component_index]["edge_indices"]
        )
        for eligible_index, raw_index in enumerate(eligible_raw_indices)
    }
    eligible_degree: Dict[int, int] = defaultdict(int)
    for edge in eligible_edges:
        eligible_degree[int(edge["start_node"])] += 1
        eligible_degree[int(edge["end_node"])] += 1

    contexts: List[Dict[str, Any]] = []
    for face in _bounded_planar_faces(
        eligible_edges,
        node_centers,
        tolerance * tolerance,
    ):
        component_indices = {
            eligible_component_by_edge[edge_index]
            for edge_index in face["edge_indices"]
        }
        if len(component_indices) != 1:
            continue
        component_index = next(iter(component_indices))
        contexts.append({
            "face": face,
            "segments": eligible_edges,
            "node_centers": node_centers,
            "node_spreads": node_spreads,
            "degree_by_node": eligible_degree,
            "authored_cycle_recovered": True,
            "authored_crossing_node_count": component_contact_nodes.get(
                component_index, 0
            ),
        })
    return contexts


def infer_cross_entity_closed_profiles(database: Optional[CADDatabase] = None,
                                        tolerance: Optional[float] = None,
                                        handles: Optional[Iterable[str]] = None,
                                        max_pair_checks: int = 250000) -> List[Dict[str, Any]]:
    """Return simple closed contours assembled from two or more CAD handles."""
    db = get_db(database)
    allowed_handles = {str(handle) for handle in handles or [] if str(handle)}
    entities = [
        entity for entity in all_entities(db)
        if not allowed_handles or _public_handle(entity) in allowed_handles
    ]
    raw_segments = _line_segments(db, entities)
    endpoint_tolerance = _drawing_endpoint_tolerance(
        entities, tolerance, raw_segments
    )
    raw_segments = _coalesce_duplicate_analytic_segments(
        raw_segments, endpoint_tolerance
    )
    raw_segments = [
        segment for segment in raw_segments
        if float(segment.get("z_span") or 0.0) <= endpoint_tolerance
    ]
    planar_segments = _planarize_line_segments(
        raw_segments, endpoint_tolerance, max_pair_checks=max_pair_checks
    )
    # A partially planarized arrangement can turn an intended divider into a
    # bridge and promote the wrong outer face. Prefer no automatic profile over
    # a confident but topologically incomplete one.
    if any(segment.get("planarization_capped") for segment in planar_segments):
        return []
    line_line_pair_checks = max((
        int(segment.get("line_line_pair_checks") or 0)
        for segment in planar_segments
    ), default=0)
    planar_segments = _planarize_line_curve_segments(
        planar_segments,
        endpoint_tolerance,
        max_pair_checks=max(0, max_pair_checks - line_line_pair_checks),
    )
    if any(segment.get("planarization_capped") for segment in planar_segments):
        return []
    snapped_segments, node_centers, node_spreads = _snap_segment_endpoints(
        planar_segments, endpoint_tolerance
    )
    all_graph_edges = _coalesce_graph_edges(
        snapped_segments, endpoint_tolerance
    )
    bridge_indices = _bridge_edge_indices(all_graph_edges)
    bridge_edges = [
        edge for index, edge in enumerate(all_graph_edges)
        if index in bridge_indices
    ]
    segments = [
        edge for index, edge in enumerate(all_graph_edges)
        if index not in bridge_indices
    ]
    degree_by_node: Dict[int, int] = defaultdict(int)
    for edge in all_graph_edges:
        degree_by_node[int(edge["start_node"])] += 1
        degree_by_node[int(edge["end_node"])] += 1
    bridge_indices_by_node: Dict[int, set] = defaultdict(set)
    for bridge_index, edge in enumerate(bridge_edges):
        bridge_indices_by_node[int(edge["start_node"])].add(bridge_index)
        bridge_indices_by_node[int(edge["end_node"])].add(bridge_index)
    bridge_spatial_grid: Dict[Tuple[int, int], set] = defaultdict(set)
    global_bridge_indices = set()
    if node_centers and bridge_edges:
        graph_min_x = min(point[0] for point in node_centers.values())
        graph_min_y = min(point[1] for point in node_centers.values())
        graph_max_x = max(point[0] for point in node_centers.values())
        graph_max_y = max(point[1] for point in node_centers.values())
        graph_diagonal = math.hypot(
            graph_max_x - graph_min_x, graph_max_y - graph_min_y
        )
        bridge_cell_size = max(
            endpoint_tolerance * 16.0,
            graph_diagonal / max(math.sqrt(len(bridge_edges)), 1.0),
            math.ulp(max(abs(graph_min_x), abs(graph_min_y), abs(graph_max_x), abs(graph_max_y), 1.0)) * 16.0,
        )
        for bridge_index, edge in enumerate(bridge_edges):
            samples = _directed_samples(
                edge,
                int(edge["start_node"]),
                int(edge["end_node"]),
                node_centers,
            )
            edge["_snapped_samples"] = samples
            bounds = (
                min(point[0] for point in samples),
                min(point[1] for point in samples),
                max(point[0] for point in samples),
                max(point[1] for point in samples),
            )
            edge["_xy_bbox"] = bounds
            min_cell_x = math.floor(bounds[0] / bridge_cell_size)
            min_cell_y = math.floor(bounds[1] / bridge_cell_size)
            max_cell_x = math.floor(bounds[2] / bridge_cell_size)
            max_cell_y = math.floor(bounds[3] / bridge_cell_size)
            cell_count = (
                (max_cell_x - min_cell_x + 1)
                * (max_cell_y - min_cell_y + 1)
            )
            if cell_count > 4096:
                global_bridge_indices.add(bridge_index)
                continue
            for cell_x in range(min_cell_x, max_cell_x + 1):
                for cell_y in range(min_cell_y, max_cell_y + 1):
                    bridge_spatial_grid[(cell_x, cell_y)].add(bridge_index)
    else:
        bridge_cell_size = max(endpoint_tolerance * 16.0, 1e-12)
    profiles: List[Dict[str, Any]] = []
    seen_profile_ids = set()
    faces = _bounded_planar_faces(
        segments,
        node_centers,
        endpoint_tolerance * endpoint_tolerance,
    )
    face_contexts: List[Dict[str, Any]] = [{
        "face": face,
        "segments": segments,
        "node_centers": node_centers,
        "node_spreads": node_spreads,
        "degree_by_node": degree_by_node,
        "authored_cycle_recovered": False,
        "authored_crossing_node_count": 0,
    } for face in faces]
    face_contexts.extend(_authored_endpoint_cycle_contexts(
        raw_segments,
        all_graph_edges,
        endpoint_tolerance,
    ))
    for face_context in face_contexts:
        face = face_context["face"]
        context_segments = face_context["segments"]
        context_node_centers = face_context["node_centers"]
        context_node_spreads = face_context["node_spreads"]
        context_degree_by_node = face_context["degree_by_node"]
        authored_cycle_recovered = bool(
            face_context.get("authored_cycle_recovered")
        )
        ordered_nodes = face["ordered_nodes"]
        polygon = face["polygon"]
        graph_edges = [
            context_segments[index] for index in face["edge_indices"]
        ]
        face_z_values = [
            float(edge.get(key) or 0.0)
            for edge in graph_edges
            for key in ("start_z", "end_z")
        ]
        if (
            face_z_values
            and max(face_z_values) - min(face_z_values) > endpoint_tolerance
        ):
            continue
        face_plane_z = sum(face_z_values) / max(len(face_z_values), 1)
        face_bbox = _face_boundary_bbox(graph_edges, polygon)
        min_face_cell_x = math.floor(
            (face_bbox[0] - endpoint_tolerance) / bridge_cell_size
        )
        min_face_cell_y = math.floor(
            (face_bbox[1] - endpoint_tolerance) / bridge_cell_size
        )
        max_face_cell_x = math.floor(
            (face_bbox[2] + endpoint_tolerance) / bridge_cell_size
        )
        max_face_cell_y = math.floor(
            (face_bbox[3] + endpoint_tolerance) / bridge_cell_size
        )
        face_cell_count = (
            (max_face_cell_x - min_face_cell_x + 1)
            * (max_face_cell_y - min_face_cell_y + 1)
        )
        if face_cell_count > 4096:
            candidate_bridge_indices = set(range(len(bridge_edges)))
        else:
            candidate_bridge_indices = set(global_bridge_indices)
            for cell_x in range(min_face_cell_x, max_face_cell_x + 1):
                for cell_y in range(min_face_cell_y, max_face_cell_y + 1):
                    candidate_bridge_indices.update(
                        bridge_spatial_grid.get((cell_x, cell_y), set())
                    )
        coplanar_bridge_edges = [
            bridge_edges[bridge_index]
            for bridge_index in sorted(candidate_bridge_indices)
            for edge in [bridge_edges[bridge_index]]
            if abs(float(edge.get("plane_z") or 0.0) - face_plane_z) <= endpoint_tolerance
            and abs(float(edge.get("start_z") or 0.0) - face_plane_z) <= endpoint_tolerance
            and abs(float(edge.get("end_z") or 0.0) - face_plane_z) <= endpoint_tolerance
            and not (
                edge["_xy_bbox"][2] + endpoint_tolerance < face_bbox[0]
                or edge["_xy_bbox"][0] - endpoint_tolerance > face_bbox[2]
                or edge["_xy_bbox"][3] + endpoint_tolerance < face_bbox[1]
                or edge["_xy_bbox"][1] - endpoint_tolerance > face_bbox[3]
            )
        ]
        unresolved_curve_junction = (
            False
            if authored_cycle_recovered
            else _bridge_component_splits_face(
                coplanar_bridge_edges,
                polygon,
                context_node_centers,
                endpoint_tolerance,
            )
        )
        if unresolved_curve_junction:
            continue
        member_segments = [
            source
            for edge in graph_edges
            for source in edge.get("source_segments", [edge])
        ]
        member_handles = sorted({segment["handle"] for segment in member_segments})
        if len(member_handles) < 2:
            continue
        area = float(face["signed_area"])
        bbox = face_bbox
        perimeter = sum(float(segment["length"]) for segment in graph_edges)
        max_gap = max((
            context_node_spreads.get(node_id, 0.0)
            for node_id in ordered_nodes
        ), default=0.0)
        closure_quality = max(0.0, 1.0 - max_gap / max(endpoint_tolerance, 1e-12))
        layer_count = len({str(segment.get("layer") or "0") for segment in member_segments})
        curve_count = sum(
            1 for edge in graph_edges
            if str(edge.get("primitive_type") or "") == "curve"
        )
        approximate_curve_count = sum(
            1 for edge in graph_edges
            if str(edge.get("primitive_type") or "") == "curve" and edge.get("approximate")
        )
        curve_sampling_capped = any(
            bool(edge.get("sampling_capped")) for edge in graph_edges
        )
        curve_sampling_certified = all(
            bool(edge.get("sampling_certified"))
            for edge in graph_edges
            if str(edge.get("primitive_type") or "") == "curve"
        )
        curve_bbox_certified = all(
            (
                str(edge.get("primitive_type") or "") != "curve"
                or bool(_analytic_curve_extrema(edge))
                or bool(edge.get("sampling_certified"))
            )
            for edge in graph_edges
        )
        certified_curve_sampling_errors = [
            float(edge.get("sampling_error_bound") or 0.0)
            for edge in graph_edges
            if (
                str(edge.get("primitive_type") or "") == "curve"
                and edge.get("sampling_certified")
                and edge.get("sampling_error_bound") is not None
            )
        ]
        max_certified_curve_sampling_error = max(
            certified_curve_sampling_errors, default=0.0
        )
        max_curve_sampling_error: Optional[float] = (
            max_certified_curve_sampling_error
            if not curve_count or curve_sampling_certified else None
        )
        max_curve_endpoint_adjustment = max(
            (
                float(edge.get("endpoint_adjustment") or 0.0)
                for edge in graph_edges
                if str(edge.get("primitive_type") or "") == "curve"
            ),
            default=0.0,
        )
        branch_nodes = sum(
            1
            for node_id in ordered_nodes
            if context_degree_by_node.get(node_id, 0) > 2
        )
        adjacent_bridge_count = (
            0
            if authored_cycle_recovered
            else len(set().union(*(
                bridge_indices_by_node.get(node_id, set())
                for node_id in ordered_nodes
            ))) if ordered_nodes else 0
        )
        planarization_capped = any(
            bool(edge.get("planarization_capped")) for edge in graph_edges
        )
        line_curve_planarized_boundary_edges = sum(
            bool(edge.get("line_curve_planarized")) for edge in graph_edges
        )
        analytic_intersection_count = max((
            int(edge.get("analytic_intersection_count") or 0)
            for edge in graph_edges
        ), default=0)
        max_intersection_error = max((
            float(edge.get("intersection_error_bound") or 0.0)
            for edge in graph_edges
        ), default=0.0)
        line_line_pair_checks = max((
            int(edge.get("line_line_pair_checks") or 0)
            for edge in graph_edges
        ), default=0)
        line_curve_pair_checks = max((
            int(edge.get("line_curve_pair_checks") or 0)
            for edge in graph_edges
        ), default=0)
        line_curve_span_checks = max((
            int(edge.get("line_curve_span_checks") or 0)
            for edge in graph_edges
        ), default=0)
        curve_curve_contact_checks = max((
            int(edge.get("curve_curve_contact_checks") or 0)
            for edge in graph_edges
        ), default=0)
        confidence = min(
            0.94,
            0.78 + 0.12 * closure_quality
            - 0.03 * max(0, layer_count - 1)
            - 0.01 * branch_nodes
            - 0.06 * approximate_curve_count
            - 0.02 * int(curve_count > 0)
            - 0.08 * int(curve_sampling_capped)
            - 0.04 * int(curve_count > 0 and not curve_sampling_certified)
            - 0.12 * int(planarization_capped),
        )
        source_coverage = _canonical_source_coverage(member_segments)
        profile_id = stable_id(
            "shape", "closed_profile", member_handles,
            sorted({
                (segment["handle"], segment.get("primitive_key") or "")
                for segment in member_segments
            }),
            source_coverage,
        )
        if profile_id in seen_profile_ids:
            continue
        seen_profile_ids.add(profile_id)
        profiles.append({
            "profile_id": profile_id,
            "shape_type": "closed_profile",
            "source_coverage": source_coverage,
            "entity_handles": member_handles,
            "member_primitives": [
                {
                    "handle": segment["handle"],
                    "primitive_key": segment.get("primitive_key") or "",
                    "segment_id": segment["segment_id"],
                    "primitive_type": segment.get("primitive_type") or "line",
                    "curve_kind": (
                        segment.get("authored_curve_kind")
                        or segment.get("curve_kind")
                        or ""
                    ),
                    "curve_model_kind": str(
                        segment.get("authored_curve_model_kind")
                        or (segment.get("curve_model") or {}).get("kind")
                        or ""
                    ),
                    "approximate": bool(segment.get("approximate")),
                    "sampling_method": segment.get("sampling_method") or "",
                    "sampling_segment_count": int(
                        segment.get("sampling_segment_count") or 1
                    ),
                    "sampling_error_target": float(
                        segment.get("sampling_error_target") or 0.0
                    ),
                    "sampling_error_bound": (
                        None
                        if segment.get("sampling_error_bound") is None
                        else float(segment.get("sampling_error_bound") or 0.0)
                    ),
                    "sampling_certified": bool(
                        segment.get("sampling_certified")
                    ),
                    "endpoint_adjustment": float(
                        segment.get("endpoint_adjustment") or 0.0
                    ),
                    "endpoint_consistency_limit": float(
                        segment.get("endpoint_consistency_limit") or 0.0
                    ),
                    "source_parameter_range": list(
                        segment.get("source_parameter_range", (0.0, 1.0))
                    ),
                    "intersection_methods": list(
                        segment.get("intersection_methods", [])
                    ),
                    "intersection_kinds": list(
                        segment.get("intersection_kinds", [])
                    ),
                    "intersection_error_bound": float(
                        segment.get("intersection_error_bound") or 0.0
                    ),
                }
                for segment in member_segments
            ],
            "vertices": [[point[0], point[1]] for point in polygon],
            "junction_vertices": [
                [
                    context_node_centers[node_id][0],
                    context_node_centers[node_id][1],
                ]
                for node_id in ordered_nodes
            ],
            "bbox": bbox,
            "bbox_certified": curve_bbox_certified,
            "area": area,
            "perimeter": perimeter,
            "segment_count": len(member_segments),
            "boundary_edge_count": len(graph_edges),
            "branch_node_count": branch_nodes,
            "adjacent_bridge_count": adjacent_bridge_count,
            "curve_count": curve_count,
            "approximate_curve_count": approximate_curve_count,
            "sampled_curve_count": curve_count,
            "max_curve_sampling_error": max_curve_sampling_error,
            "max_certified_curve_sampling_error": (
                max_certified_curve_sampling_error
            ),
            "max_curve_endpoint_adjustment": max_curve_endpoint_adjustment,
            "endpoint_tolerance": endpoint_tolerance,
            "max_endpoint_gap": max_gap,
            "closure_quality": round(closure_quality, 4),
            "confidence": round(max(0.0, confidence), 4),
            "layer_count": layer_count,
            "topology_evidence": {
                "method": (
                    "authored_endpoint_cycle_recovery"
                    if authored_cycle_recovered
                    else "planar_half_edge_face_walk"
                ),
                "bounded_face": True,
                "orientation": "counter_clockwise",
                "authored_cycle_recovered": authored_cycle_recovered,
                "authored_crossing_node_count": int(
                    face_context.get("authored_crossing_node_count") or 0
                ),
                "coincident_sources_preserved": len(member_segments) != len(graph_edges),
                "removed_bridge_count": adjacent_bridge_count,
                "planarized_boundary_edges": sum(
                    bool(edge.get("planarized")) for edge in graph_edges
                ),
                "line_curve_planarized_boundary_edges": (
                    line_curve_planarized_boundary_edges
                ),
                "line_curve_planarization_complete": all(
                    edge.get("line_curve_planarization_complete", True)
                    for edge in graph_edges
                ),
                "drawing_analytic_intersection_count": (
                    analytic_intersection_count
                ),
                "max_intersection_error_bound": max_intersection_error,
                "ambiguous_contact_count": 0,
                "primitive_pair_checks": (
                    line_line_pair_checks
                    + line_curve_pair_checks
                    + curve_curve_contact_checks
                ),
                "sampled_curve_span_checks": line_curve_span_checks,
                "curve_curve_contact_checks": curve_curve_contact_checks,
                "planarization_capped": planarization_capped,
                "curve_measurements_are_sampled": bool(curve_count),
                "curve_sampling_capped": curve_sampling_capped,
                "curve_sampling_certified": curve_sampling_certified,
                "curve_bbox_certified": curve_bbox_certified,
                "profile_identity_basis": source_coverage,
            },
            "source": (
                "drawing_topology:authored_endpoint_cycle"
                if authored_cycle_recovered
                else "drawing_topology:bounded_face"
            ),
        })
    profiles.sort(key=lambda item: (-float(item["area"]), item["profile_id"]))
    return profiles


__all__ = ["infer_cross_entity_closed_profiles"]
