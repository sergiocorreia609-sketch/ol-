# -*- coding: utf-8 -*-
from __future__ import unicode_literals

"""Tudo32f | Fólio: busca, composição visual e criação de folhas."""
__title__ = "Fólio"
__author__ = "Tudo32f"
__version__ = "2.8"
__doc__ = "Pesquise vistas e tabelas, componha folhas, analise o resultado e confirme a montagem."

from Autodesk.Revit import DB
from pyrevit import revit, forms, script
import math
import re
import json

doc = revit.doc
uidoc = revit.uidoc
TITLE = "Fólio v2.8 | Tudo32f"
CM_TO_FT = 1.0 / 30.48
LABEL_GAP_FT = 1.5 * CM_TO_FT
LAYOUTS = [
    "Automático por tamanho (recomendado)",
    "2 colunas equilibradas — plantas",
    "3 colunas compactas — detalhes",
    "Faixas horizontais — cortes e alçados",
    "Prancha principal + detalhes",
    "Manual — arrastar para posicionar"
]
MANUAL_LAYOUT = LAYOUTS[-1]
AUTO_LAYOUTS = LAYOUTS[:-1]
SCALE_AUTO = "Automática — maior escala que cabe"
SCALE_KEEP = "Manter escalas atuais"
SCALE_MANUAL = "Original — alterar apenas na prévia"
SCALE_CUSTOM = "Personalizada"
# Revit's standard metric scale selector order. Revit 2024 does not expose a
# public API to enumerate a user's customized scale dropdown; View.Scale uses
# the matching predefined entry when one exists and otherwise sets a custom scale.
SCALE_PRESETS = [1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000]
# Denominators are starting preferences only. If the view footprint does not
# fit the usable sheet rectangle, choose_auto_scale moves to the next smaller
# printed scale (larger denominator) that fits.
PREFERRED_SCALE_BY_VIEW = {
    "floorplan": 100, "engineeringplan": 100, "ceilingplan": 100,
    "area plan": 100, "structuralplan": 100,
    "section": 100, "elevation": 100,
    "detail": 20, "draftingview": 20, "legend": 20
}
ANNOTATION_TEXT_MIN_MM = 2.0
ANNOTATION_TEXT_MAX_MM = 5.0
MAX_VIEW_SCALE = 24000  # Limite documentado para denominador no Revit 2024.
GRID_STEP_CM = 2.0
GRID_MAJOR_EVERY = 5
SCALE_OPTIONS = [SCALE_AUTO, SCALE_MANUAL, SCALE_CUSTOM] + ["1:{}".format(value)
                                                          for value in SCALE_PRESETS]
BUILT_IN_PROFILES = {
    "Plantas — grelha equilibrada": {
        "layout": LAYOUTS[1], "scale_mode": SCALE_KEEP,
        "single_sheet": False, "margin_cm": 2.5, "gap_cm": 2.0
    },
    "Cortes e alçados — faixas": {
        "layout": LAYOUTS[3], "scale_mode": SCALE_KEEP,
        "single_sheet": False, "margin_cm": 2.5, "gap_cm": 2.5
    },
    "Detalhes — grelha compacta": {
        "layout": LAYOUTS[2], "scale_mode": SCALE_KEEP,
        "single_sheet": False, "margin_cm": 1.5, "gap_cm": 1.5
    },
    "Vista principal + apoios": {
        "layout": LAYOUTS[4], "scale_mode": SCALE_KEEP,
        "single_sheet": False, "margin_cm": 2.5, "gap_cm": 2.0
    },
    "Compacta — uma prancha": {
        "layout": LAYOUTS[0], "scale_mode": SCALE_KEEP,
        "single_sheet": True, "margin_cm": 1.5, "gap_cm": 1.0
    }
}
TITLEBLOCK_SCAN_INFO = {}
SCHEDULE_METRICS_CACHE = {}


def id_value(element_id):
    try:
        return str(element_id.Value)
    except Exception:
        try:
            return str(element_id.IntegerValue)
        except Exception:
            return str(element_id)


def try_get_property(element, property_name):
    try:
        return getattr(element, property_name)
    except Exception:
        return None


def category_display_name(category):
    name = try_get_property(category, "Name") if category else None
    if name:
        return safe_text(name)
    try:
        return "Category ID {}".format(id_value(category.Id))
    except Exception:
        return "(sem categoria)"


def symbol_display_names(symbol):
    """Resolve family/type labels without letting a missing .Name hide a valid type."""
    family = try_get_property(symbol, "Family")
    family_name = try_get_property(family, "Name") if family else None
    type_name = try_get_property(symbol, "Name")
    if not family_name:
        try:
            parameter = symbol.get_Parameter(DB.BuiltInParameter.SYMBOL_FAMILY_NAME_PARAM)
            if parameter:
                family_name = parameter.AsString()
        except Exception:
            pass
    if not type_name:
        try:
            parameter = symbol.get_Parameter(DB.BuiltInParameter.SYMBOL_NAME_PARAM)
            if parameter:
                type_name = parameter.AsString()
        except Exception:
            pass
    symbol_id = id_value(try_get_property(symbol, "Id"))
    return (safe_text(family_name) if family_name else "Família de carimbo",
            safe_text(type_name) if type_name else "Tipo {}".format(symbol_id))


def cm_to_ft(value):
    return float(value) * CM_TO_FT


def ft_to_cm(value):
    return float(value) / CM_TO_FT


def safe_text(value):
    try:
        return str(value)
    except Exception:
        try:
            return unicode(value)
        except Exception:
            return ""


def xml_escape(value):
    return (safe_text(value).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def view_type_text(view):
    try:
        return safe_text(view.ViewType)
    except Exception:
        return "Vista"


def paper_size_with_provenance(view):
    """Return sheet-paper extents plus the source and confidence of the measurement."""
    try:
        outline = view.Outline
        width = abs(outline.Max.U - outline.Min.U)
        height = abs(outline.Max.V - outline.Min.V)
        if width > 1e-6 and height > 1e-6:
            return width, height, "view_outline", "measured"
    except Exception:
        pass
    try:
        box = view.CropBox
        transform = box.Transform
        right = view.RightDirection
        up = view.UpDirection
        xs, ys = [], []
        for x in (box.Min.X, box.Max.X):
            for y in (box.Min.Y, box.Max.Y):
                for z in (box.Min.Z, box.Max.Z):
                    point = transform.OfPoint(DB.XYZ(x, y, z))
                    xs.append(point.DotProduct(right))
                    ys.append(point.DotProduct(up))
        scale = max(1, int(view.Scale))
        width, height = (max(xs) - min(xs)) / scale, (max(ys) - min(ys)) / scale
        if width > 1e-6 and height > 1e-6:
            return width, height, "cropbox_projected_by_scale", "estimated"
    except Exception:
        pass
    return None, None, "unavailable", "unavailable"


def paper_size(view):
    """Compatibility wrapper for callers that need only width and height."""
    width, height, unused_source, unused_confidence = paper_size_with_provenance(view)
    return width, height


def default_document_metadata():
    """Planning-only fields; these do not claim a detected document relationship."""
    return {
        "document_group_id": None,
        "document_relations": [],
        "position_locked": False,
        "hierarchy_locked": False,
        "group_locked": False
    }


def placed_view_ids():
    ids = set()
    try:
        for viewport in DB.FilteredElementCollector(doc).OfClass(DB.Viewport):
            ids.add(id_value(viewport.ViewId))
    except Exception:
        pass
    try:
        for instance in DB.FilteredElementCollector(doc).OfClass(DB.ScheduleSheetInstance):
            ids.add(id_value(instance.ScheduleId))
    except Exception:
        pass
    return ids


def get_view_area(item):
    """Return the estimated paper-space area occupied by a view item."""
    return item.get("layout_width", 0.0) * item.get("layout_height", 0.0)


def inferred_layout_role(item):
    """Classify supporting documentation without treating a schedule as a main view."""
    kind = item.get("kind", "").lower()
    if item.get("is_schedule") or any(token in kind for token in (
            "schedule", "tabela", "legend", "draftingview", "detail")):
        return "support"
    return "secondary"


def suggest_principal_view(items):
    """Choose the dominant architectural view from type first, then paper footprint."""
    candidates = [item for item in items if inferred_layout_role(item) != "support"]
    if not candidates:
        return None

    preferred_types = (
        "floorplan", "engineeringplan", "ceilingplan", "area plan",
        "structuralplan", "section", "elevation", "threeD"
    )

    def ranking(item):
        kind = item.get("kind", "").lower()
        type_priority = 0
        for index, token in enumerate(preferred_types):
            if token.lower() in kind:
                type_priority = len(preferred_types) - index
                break
        # The footprint breaks ties within a view type; it does not let a table
        # or a detail outrank an architectural plan.
        return (type_priority, get_view_area(item), item.get("layout_width", 0.0))

    return max(candidates, key=ranking)


def apply_auto_hierarchy(items):
    """Assign one principal view, secondary views, and explicit support items."""
    if not items:
        return items
    principal = suggest_principal_view(items)
    if principal is None:
        return items
    principal_id = principal.get("id")
    for item in items:
        if item.get("id") == principal_id:
            item["layout_role"] = "principal"
        else:
            item["layout_role"] = inferred_layout_role(item)
        item["hierarchy_source"] = "automatic"
    return items


def get_principal_view(items):
    """Return the assigned principal view, or suggest one when none is assigned."""
    for item in items:
        if item.get("layout_role") == "principal":
            return item
    return suggest_principal_view(items)

def make_view_item(view, existing_viewport=None):
    item_id = id_value(view.Id)
    if existing_viewport:
        try:
            box = existing_viewport.GetBoxOutline()
            min_x, min_y = box.MinimumPoint.X, box.MinimumPoint.Y
            max_x, max_y = box.MaximumPoint.X, box.MaximumPoint.Y
            label_measured = False
            try:
                label = existing_viewport.GetLabelOutline()
                if label:
                    label_measured = True
                    min_x = min(min_x, label.MinimumPoint.X)
                    min_y = min(min_y, label.MinimumPoint.Y)
                    max_x = max(max_x, label.MaximumPoint.X)
                    max_y = max(max_y, label.MaximumPoint.Y)
            except Exception:
                pass
            box_center = existing_viewport.GetBoxCenter()
            union_x, union_y = (min_x + max_x) / 2.0, (min_y + max_y) / 2.0
            width, height = max_x - min_x, max_y - min_y
            item = {
                "id": item_id, "view": view, "viewport": existing_viewport,
                "name": safe_text(view.Name), "kind": view_type_text(view),
                "width": width, "height": height, "layout_width": width,
                "layout_height": height, "label_gap": 0.0,
                "current_center": (union_x, union_y),
                "box_offset": (box_center.X - union_x, box_center.Y - union_y),
                "is_existing": True, "layout_role": "secondary",
                "measurement_source": ("viewport_box_and_label" if label_measured
                                        else "viewport_box"),
                "measurement_confidence": ("measured" if label_measured else "partial"),
                "scale_locked": False
            }
            item.update(default_document_metadata())
            return item
        except Exception:
            pass
    width, height, measurement_source, measurement_confidence = (
        paper_size_with_provenance(view))
    if width is None or height is None:
        return None
    scale_text = ""
    try:
        scale_text = " | 1:{}".format(view.Scale)
    except Exception:
        pass
    label_gap = LABEL_GAP_FT
    item = {
        "id": item_id, "view": view, "viewport": None,
        "name": safe_text(view.Name), "kind": view_type_text(view),
        "width": width, "height": height, "layout_width": width,
        "layout_height": height + label_gap, "label_gap": label_gap,
        "current_center": None, "box_offset": (0.0, label_gap / 2.0),
        "is_existing": False, "scale_text": scale_text, "layout_role": "secondary",
        "measurement_source": measurement_source,
        "measurement_confidence": measurement_confidence,
        "scale_locked": False
    }
    item.update(default_document_metadata())
    return item


def schedule_table_dimensions(schedule):
    """Measure regular schedule geometry and cell text without changing the schedule."""
    cache_key = id_value(schedule.Id)
    cached = SCHEDULE_METRICS_CACHE.get(cache_key)
    if cached is not None:
        return cached
    column_widths = {}
    total_height = 0.0
    text_sizes = []
    cell_count = 0
    truncated = False
    table_data = schedule.GetTableData()
    section_names = ("Title", "Header", "Body", "Summary", "Footer")
    for section_name in section_names:
        section_type = getattr(DB.SectionType, section_name, None)
        if section_type is None:
            continue
        try:
            section = table_data.GetSectionData(section_type)
            first_column = int(section.FirstColumnNumber)
            last_column = int(section.LastColumnNumber)
            first_row = int(section.FirstRowNumber)
            last_row = int(section.LastRowNumber)
        except Exception:
            continue
        columns = range(first_column, last_column + 1) if last_column >= first_column else []
        rows = range(first_row, last_row + 1) if last_row >= first_row else []
        for column in columns:
            try:
                width = float(section.GetColumnWidth(column))
                if width > column_widths.get(column, 0.0):
                    column_widths[column] = width
            except Exception:
                continue
        for row in rows:
            try:
                row_height = float(section.GetRowHeight(row))
                if row_height > 0:
                    total_height += row_height
            except Exception:
                pass
            for column in columns:
                if cell_count >= 5000:
                    truncated = True
                    break
                cell_count += 1
                try:
                    style = section.GetTableCellStyle(row, column)
                    size_ft = float(style.TextSize)
                    size_mm = size_ft * 304.8
                    if size_mm > 0:
                        text_sizes.append(size_mm)
                except Exception:
                    continue
            if truncated:
                break
        if truncated:
            break
    width = sum(column_widths.values())
    try:
        table_width = float(table_data.Width)
        if table_width > width:
            width = table_width
    except Exception:
        pass
    if width <= 1e-6 or total_height <= 1e-6:
        raise ValueError("A tabela não forneceu dimensões de linhas e colunas válidas.")
    diagnostics = {
        "available": bool(text_sizes),
        "counts": {"ScheduleCell": len(text_sizes)},
        "small": sum(1 for size in text_sizes if size < ANNOTATION_TEXT_MIN_MM),
        "large": sum(1 for size in text_sizes if size > ANNOTATION_TEXT_MAX_MM),
        "unmeasured": max(0, cell_count - len(text_sizes)),
        "range_mm": (min(text_sizes), max(text_sizes)) if text_sizes else None,
        "truncated": truncated
    }
    result = (width, total_height, diagnostics)
    SCHEDULE_METRICS_CACHE[cache_key] = result
    return result


def make_schedule_item(schedule, existing_instance=None):
    """Represent a Revit schedule as a measured, non-scalable sheet rectangle."""
    width, height, diagnostics = schedule_table_dimensions(schedule)
    current_center = None
    measurement_source = "table_section_data_estimate"
    measurement_confidence = "estimated"
    if existing_instance is not None:
        owner_sheet = None
        try:
            owner_sheet = doc.GetElement(existing_instance.OwnerViewId)
        except Exception:
            pass
        box = None
        for view in (owner_sheet, None):
            try:
                box = existing_instance.get_BoundingBox(view)
                if box is not None:
                    break
            except Exception:
                continue
        if box is not None:
            actual_width = abs(box.Max.X - box.Min.X)
            actual_height = abs(box.Max.Y - box.Min.Y)
            if actual_width > 1e-6 and actual_height > 1e-6:
                width, height = actual_width, actual_height
                current_center = ((box.Min.X + box.Max.X) / 2.0,
                                  (box.Min.Y + box.Max.Y) / 2.0)
                measurement_source = "schedule_instance_bounding_box"
                measurement_confidence = "measured"
    item = {
        "id": id_value(schedule.Id), "view": schedule,
        "schedule_instance": existing_instance,
        "name": safe_text(schedule.Name), "kind": "Tabela",
        "width": width, "height": height,
        "layout_width": width, "layout_height": height,
        "label_gap": 0.0, "current_center": current_center,
        "box_offset": (0.0, 0.0),
        "is_existing": existing_instance is not None,
        "is_schedule": True, "scale_locked": True,
        "current_scale": None, "planned_scale": None,
        "annotation_diagnostics": diagnostics,
        "schedule_diagnostics": diagnostics,
        "layout_role": "support",
        "measurement_source": measurement_source,
        "measurement_confidence": measurement_confidence
    }
    item.update(default_document_metadata())
    item["label"] = item_label(item)
    return item


def choose_auto_scale(item, current_scale, bounds, margin):
    """Prefer a view-type scale, adapting only when the safe sheet area requires it."""
    min_x, min_y, max_x, max_y = bounds
    inset_left, inset_bottom, inset_right, inset_top = margin_insets(margin)
    usable_w = max(0.0, max_x - min_x - inset_left - inset_right)
    usable_h = max(0.0, max_y - min_y - inset_bottom - inset_top)
    if usable_w <= 1e-6 or usable_h <= 1e-6:
        return current_scale
    width = max(item.get("layout_width", item["width"]), 1e-6)
    label_gap = max(0.0, item.get("label_gap", 0.0))
    content_h = max(item["height"], item.get("layout_height", item["height"]) - label_gap)
    usable_content_h = max(1e-6, usable_h - label_gap)
    required = current_scale * max(width / usable_w, content_h / usable_content_h)
    kind = item.get("kind", "").lower()
    preferred = None
    preferences = item.get("preferred_scales", PREFERRED_SCALE_BY_VIEW)
    for token, denominator in preferences.items():
        if token in kind:
            preferred = denominator
            break
    if preferred is None:
        preferred = current_scale
    required = max(required, preferred)
    return next_preferred_scale(required)


def next_preferred_scale(required_scale):
    """Prefer Revit's common metric scales; use a rounded custom value only past them."""
    required_scale = max(1.0, float(required_scale))
    for denominator in SCALE_PRESETS:
        if denominator + 1e-6 >= required_scale:
            return denominator
    return min(MAX_VIEW_SCALE, int(math.ceil(required_scale / 1000.0) * 1000.0))


def view_scale_lock_reason(view):
    """Report a scale lock only when Revit or its template actually enforces one."""
    try:
        parameter = view.get_Parameter(DB.BuiltInParameter.VIEW_SCALE)
    except Exception:
        parameter = None
    if parameter is None:
        return "parâmetro de escala indisponível"
    try:
        is_read_only = bool(parameter.IsReadOnly)
    except Exception:
        is_read_only = None

    template_present = False
    template_state_inspected = False
    template_name = None
    try:
        template_id = view.ViewTemplateId
        invalid_id = id_value(DB.ElementId.InvalidElementId)
        if template_id and id_value(template_id) != invalid_id:
            template = doc.GetElement(template_id)
            if template:
                template_present = True
                template_name = safe_text(template.Name)
                possible_ids = set(id_value(value)
                                   for value in template.GetTemplateParameterIds())
                noncontrolled_ids = set(
                    id_value(value)
                    for value in template.GetNonControlledTemplateParameterIds())
                template_state_inspected = True
                scale_ids = []
                for parameter_name in (
                        "VIEW_SCALE", "VIEW_SCALE_PULLDOWN_IMPERIAL",
                        "VIEW_SCALE_PULLDOWN_METRIC"):
                    try:
                        built_in = getattr(DB.BuiltInParameter, parameter_name)
                        scale_ids.append(id_value(DB.ElementId(built_in)))
                    except Exception:
                        pass
                parameter_id = id_value(parameter.Id)
                if parameter_id not in scale_ids:
                    scale_ids.append(parameter_id)
                relevant_ids = [value for value in scale_ids
                                if value in possible_ids]
                controlled_ids = [value for value in relevant_ids
                                  if value not in noncontrolled_ids]
                if controlled_ids:
                    return "controlada pelo modelo de vista '{}'".format(template_name)
                # A checked scale is authoritative: do not let a stale or
                # template-related IsReadOnly flag override the template's
                # explicit non-controlled state.
                if (parameter_id in noncontrolled_ids or
                        (relevant_ids and not controlled_ids)):
                    return None
    except Exception:
        template_state_inspected = False

    if is_read_only:
        if template_present and not template_state_inspected and template_name:
            return "parâmetro de escala somente de leitura no modelo de vista '{}'".format(
                template_name)
        return "parâmetro de escala somente de leitura no Revit"
    # An unavailable IsReadOnly flag is not proof of a template lock. Let the
    # scale edit proceed; Revit remains the final authority during execution.
    return None



def diagnose_annotation_text(view):
    """Read visible dimension, text-note, and tag type sizes without modifying them."""
    sizes = []
    counts = {"Dimension": 0, "TextNote": 0, "IndependentTag": 0}
    unmeasured = 0
    try:
        elements = DB.FilteredElementCollector(doc, view.Id).WhereElementIsNotElementType()
        for element in elements:
            if isinstance(element, DB.Dimension):
                key = "Dimension"
            elif isinstance(element, DB.TextNote):
                key = "TextNote"
            elif isinstance(element, DB.IndependentTag):
                key = "IndependentTag"
            else:
                continue
            counts[key] += 1
            try:
                element_type = doc.GetElement(element.GetTypeId())
                parameter = element_type.get_Parameter(DB.BuiltInParameter.TEXT_SIZE)
                if parameter and parameter.HasValue:
                    size_mm = ft_to_cm(parameter.AsDouble()) * 10.0
                    if size_mm > 0:
                        sizes.append((key, size_mm))
                    else:
                        unmeasured += 1
                else:
                    unmeasured += 1
            except Exception:
                unmeasured += 1
    except Exception:
        return {"available": False, "counts": counts, "small": 0, "large": 0,
                "unmeasured": unmeasured, "range_mm": None}
    measured = [entry[1] for entry in sizes]
    return {
        "available": True,
        "counts": counts,
        "small": sum(1 for size in measured if size < ANNOTATION_TEXT_MIN_MM),
        "large": sum(1 for size in measured if size > ANNOTATION_TEXT_MAX_MM),
        "unmeasured": unmeasured,
        "measured_count": len(measured),
        "range_mm": (min(measured), max(measured)) if measured else None
    }


def summarize_annotation_diagnostics(items):
    """Attach read-only annotation/schedule text findings to every layout item."""
    small = large = unavailable = unmeasured = measured = 0
    for item in items:
        result = item.get("annotation_diagnostics")
        if result is None:
            result = diagnose_annotation_text(item["view"])
        item["annotation_diagnostics"] = result
        if not result["available"]:
            unavailable += 1
        small += result["small"]
        large += result["large"]
        unmeasured += result.get("unmeasured", 0)
        measured += result.get("measured_count", result.get("counts", {}).get("ScheduleCell", 0))
    parts = []
    if small:
        parts.append("{} ocorrência(s) abaixo de {:.1f} mm".format(
            small, ANNOTATION_TEXT_MIN_MM))
    if large:
        parts.append("{} ocorrência(s) acima de {:.1f} mm".format(
            large, ANNOTATION_TEXT_MAX_MM))
    if unavailable:
        parts.append("análise indisponível em {} vista(s)".format(unavailable))
    if unmeasured:
        parts.append("{} tipo(s)/célula(s) de anotação sem dimensão mensurável".format(unmeasured))
    if not measured and not unavailable:
        parts.append("sem anotações com tamanho mensurável; legibilidade não avaliada")
    elif not small and not large and not unmeasured and not unavailable:
        parts.append("{} elemento(s) medido(s) dentro da faixa de referência".format(measured))
    return "Legibilidade (diagnóstico, sem alterações): " + "; ".join(parts) + "."


def margin_insets(margin):
    """Return left, bottom, right and top insets; scalar margins remain supported."""
    if isinstance(margin, (tuple, list)) and len(margin) == 4:
        return tuple(max(0.0, float(value)) for value in margin)
    value = max(0.0, float(margin))
    return value, value, value, value


def plan_scale_items(items, bounds, margin, scale_mode, custom_scale=None,
                     preferred_scales=None):
    """Prepare paper-space footprints and scale labels without changing the Revit model."""
    planned = []
    changed = 0
    scalable = 0
    locked = []
    for source in items:
        item = dict(source)
        if preferred_scales:
            item["preferred_scales"] = preferred_scales
        if item.get("is_schedule"):
            item["current_scale"] = None
            item["planned_scale"] = None
            item["scale_changed"] = False
            item["scale_locked"] = True
            item["adaptive_scale_enabled"] = False
            item["scale_text"] = ""
            item["label"] = item_label(item)
            planned.append(item)
            continue
        view = item["view"]
        scalable += 1
        try:
            current_scale = max(1, int(view.Scale))
        except Exception:
            current_scale = 0
        target_scale = current_scale
        is_perspective = False
        try:
            is_perspective = isinstance(view, DB.View3D) and bool(view.IsPerspective)
        except Exception:
            pass
        lock_reason = view_scale_lock_reason(view) if current_scale else "escala indisponível"
        if current_scale and not is_perspective and not lock_reason:
            if scale_mode == SCALE_AUTO:
                target_scale = choose_auto_scale(item, current_scale, bounds, margin)
            elif scale_mode == SCALE_CUSTOM and custom_scale:
                target_scale = int(custom_scale)
            elif scale_mode.startswith("1:"):
                try:
                    target_scale = int(scale_mode.split(":", 1)[1])
                except Exception:
                    target_scale = current_scale
        if is_perspective or not current_scale or lock_reason:
            target_scale = current_scale
            if is_perspective:
                lock_reason = "vista em perspectiva"
        if lock_reason:
            locked.append("{} ({})".format(item["name"], lock_reason))
        item["scale_lock_reason"] = lock_reason
        item["scale_locked"] = bool(is_perspective or not current_scale or lock_reason)
        item["adaptive_scale_enabled"] = bool(
            scale_mode == SCALE_AUTO and not item["scale_locked"])
        if current_scale and target_scale and target_scale != current_scale:
            factor = current_scale / float(target_scale)
            item["width"] *= factor
            item["height"] *= factor
            item["layout_width"] *= factor
            label_gap = max(0.0, item.get("label_gap", 0.0))
            body_height = max(0.0, item["layout_height"] - label_gap)
            item["layout_height"] = body_height * factor + label_gap
            offset = item.get("box_offset", (0.0, 0.0))
            item["box_offset"] = (offset[0] * factor, offset[1] * factor)
            changed += 1
        item["current_scale"] = current_scale
        item["planned_scale"] = target_scale
        item["scale_changed"] = bool(current_scale and target_scale != current_scale)
        item["scale_text"] = " | 1:{}".format(target_scale) if target_scale else ""
        item["label"] = item_label(item)
        planned.append(item)
    if scale_mode == SCALE_AUTO:
        summary = "Ajuste automático: {} de {} vista(s) terão a escala otimizada.".format(
            changed, scalable)
    elif scale_mode == SCALE_KEEP:
        summary = "Escalas atuais mantidas nas {} vista(s).".format(scalable)
    elif scale_mode == SCALE_MANUAL:
        summary = "Escalas originais preservadas inicialmente; a alteração fica disponível na prévia."
    elif scale_mode == SCALE_CUSTOM:
        summary = "Escala personalizada 1:{} aplicada às vistas compatíveis.".format(custom_scale)
    else:
        summary = "Escala {} aplicada às vistas compatíveis.".format(scale_mode)
    if locked:
        summary += " Escala bloqueada em {} vista(s): {}{}".format(
            len(locked), "; ".join(locked[:3]),
            "; …" if len(locked) > 3 else "")
    return planned, summary, locked


def fit_items_to_single_page(items, bounds, margin, gap, style):
    """Iteratively reduce view footprints until every selected view packs on one sheet."""
    if not items:
        raise ValueError("Selecione pelo menos uma vista para ajustar em uma prancha.")
    min_x, min_y, max_x, max_y = bounds
    inset_left, inset_bottom, inset_right, inset_top = margin_insets(margin)
    usable_w = max(0.0, max_x - min_x - inset_left - inset_right)
    usable_h = max(0.0, max_y - min_y - inset_bottom - inset_top)
    if usable_w <= 0 or usable_h <= 0:
        raise ValueError("A área segura do carimbo não tem espaço suficiente para as vistas.")
    for attempt in range(32):
        pages, unplaced = layout_pages(items, bounds, margin, gap, style, allow_multiple=True)
        placed_count = sum(len(page["placements"]) for page in pages)
        if len(pages) == 1 and not unplaced and placed_count == len(items):
            return items, "Todas as {} vistas cabem em uma prancha; escalas ajustadas automaticamente.".format(len(items))
        usable_area = max(1e-6, usable_w * usable_h)
        total_area = sum(item["layout_width"] * item["layout_height"] for item in items)
        factor = max(1.12, min(1.45, math.sqrt(total_area / (usable_area * 0.72))))
        if len(pages) > 1:
            factor = max(factor, min(1.35, 1.08 + 0.08 * len(pages)))
        changed = False
        for item in items:
            if item.get("scale_locked") or item.get("scale_user_override"):
                continue
            old_scale = item.get("planned_scale") or item.get("current_scale")
            if not old_scale:
                continue
            new_scale = next_preferred_scale(old_scale * factor)
            if new_scale <= old_scale:
                continue
            ratio = old_scale / float(new_scale)
            item["width"] *= ratio
            item["height"] *= ratio
            item["layout_width"] *= ratio
            label_gap = max(0.0, item.get("label_gap", 0.0))
            body_height = max(0.0, item["layout_height"] - label_gap)
            item["layout_height"] = body_height * ratio + label_gap
            offset = item.get("box_offset", (0.0, 0.0))
            item["box_offset"] = (offset[0] * ratio, offset[1] * ratio)
            item["planned_scale"] = new_scale
            item["scale_changed"] = new_scale != item.get("current_scale")
            item["scale_text"] = " | 1:{}".format(new_scale)
            item["label"] = item_label(item)
            changed = True
        if not changed:
            break
    raise ValueError(
        "Não foi possível encaixar todas as vistas em uma única prancha dentro do limite de escala. Use mais de uma prancha ou reduza a margem/entre-eixos.")


def item_label(item):
    return "{} | {}{} | {:.0f} × {:.0f} cm | ID {}".format(
        item["name"], item["kind"], item.get("scale_text", ""),
        ft_to_cm(item["width"]), ft_to_cm(item["height"]), item["id"])


def collect_view_candidates():
    occupied = placed_view_ids()
    candidates, diagnostics = [], []
    excluded_types = set(["DrawingSheet", "Schedule", "Internal", "ProjectBrowser",
                          "SystemBrowser", "Report", "Undefined", "PanelSchedule"])
    for view in DB.FilteredElementCollector(doc).OfClass(DB.View):
        try:
            if view.IsTemplate or isinstance(view, DB.ViewSheet) or isinstance(view, DB.ViewSchedule):
                continue
            type_name = view_type_text(view)
            if type_name in excluded_types or "Schedule" in type_name:
                continue
            if view.ViewSpecific:
                continue
            if id_value(view.Id) in occupied:
                continue
            item = make_view_item(view)
            if item is None:
                diagnostics.append("Sem dimensões em papel: {}".format(view.Name))
                continue
            search_terms = [item["name"], item["kind"]]
            try:
                level = view.GenLevel
                item["level_name"] = safe_text(level.Name) if level else ""
                if item["level_name"]:
                    search_terms.append(item["level_name"])
            except Exception:
                item["level_name"] = ""
            try:
                template_id = view.ViewTemplateId
                if template_id and template_id != DB.ElementId.InvalidElementId:
                    template = doc.GetElement(template_id)
                    item["template_name"] = safe_text(template.Name) if template else ""
                    if item["template_name"]:
                        search_terms.append(item["template_name"])
            except Exception:
                item["template_name"] = ""
            try:
                search_terms.append("1:{}".format(view.Scale))
            except Exception:
                pass
            item["search_text"] = " ".join(search_terms).lower()
            item["label"] = item_label(item)
            candidates.append(item)
        except Exception:
            continue
    for schedule in DB.FilteredElementCollector(doc).OfClass(DB.ViewSchedule):
        try:
            if schedule.IsTemplate or id_value(schedule.Id) in occupied:
                continue
            if try_get_property(schedule, "IsTitleblockRevisionSchedule"):
                continue
            if try_get_property(schedule, "IsInternalKeynoteSchedule"):
                continue
            try:
                if not schedule.CanBePrinted:
                    continue
            except Exception:
                pass
            item = make_schedule_item(schedule)
            item["search_text"] = (item["name"] + " tabela schedule").lower()
            candidates.append(item)
        except Exception as error:
            diagnostics.append("Tabela não mensurável: {} ({})".format(
                safe_text(try_get_property(schedule, "Name")), safe_text(error)))
    candidates.sort(key=lambda item: (item["name"].lower(), item["kind"].lower()))
    labels, seen = set(), set()
    for item in candidates:
        if item["label"] in labels:
            item["label"] += " [ID {}]".format(item["id"])
        labels.add(item["label"])
    return candidates, diagnostics


def active_sheet_viewports(sheet):
    items = []
    if sheet is None:
        return items
    for viewport in DB.FilteredElementCollector(doc, sheet.Id).OfClass(DB.Viewport):
        try:
            view = doc.GetElement(viewport.ViewId)
            item = make_view_item(view, viewport)
            if item:
                item["label"] = item_label(item)
                items.append(item)
        except Exception:
            continue
    for instance in DB.FilteredElementCollector(doc, sheet.Id).OfClass(DB.ScheduleSheetInstance):
        try:
            schedule = doc.GetElement(instance.ScheduleId)
            if schedule is None or try_get_property(schedule, "IsTitleblockRevisionSchedule"):
                continue
            item = make_schedule_item(schedule, instance)
            items.append(item)
        except Exception:
            continue
    items.sort(key=lambda item: item["name"].lower())
    return items


def refresh_layout_item(item, element=None):
    """Refresh actual paper extents for either a viewport or a schedule instance."""
    if item.get("is_schedule"):
        instance = element or item.get("schedule_instance")
        return make_schedule_item(item["view"], instance)
    viewport = element or item.get("viewport")
    return make_view_item(item["view"], viewport)


def move_layout_item(item, element, target_center, refreshed=None):
    """Move a viewport box center or a schedule bounding-box center on a sheet."""
    if item.get("is_schedule"):
        current = (refreshed or item).get("current_center")
        if current is None:
            raise ValueError("Não foi possível medir o centro real da tabela.")
        delta = DB.XYZ(target_center[0] - current[0],
                       target_center[1] - current[1], 0.0)
        DB.ElementTransformUtils.MoveElement(doc, element.Id, delta)
        return
    source = refreshed or item
    offset_x, offset_y = source.get("box_offset", item.get("box_offset", (0.0, 0.0)))
    element.SetBoxCenter(DB.XYZ(target_center[0] + offset_x,
                                target_center[1] + offset_y, 0.0))


def is_titleblock_category(category, target_category_id):
    if category is None:
        return False
    try:
        if id_value(category.Id) == target_category_id:
            return True
    except Exception:
        pass
    try:
        category_name = category_display_name(category).lower()
        return any(token in category_name for token in (
            "title block", "titleblock", "carimbo", "margens e carimbos",
            "bloco de margem", "blocos de margem"))
    except Exception:
        return False


def titleblock_types():
    """Find loaded titleblock FamilySymbols using several Revit API paths."""
    found = {}
    scan = {
        "all_family_symbols": 0,
        "titleblock_category_elements": 0,
        "matching_families": 0,
        "sheet_instances": 0,
        "family_categories": set(),
        "errors": []
    }
    try:
        category = DB.Category.GetCategory(doc, DB.BuiltInCategory.OST_TitleBlocks)
        target_category_id = id_value(category.Id) if category else ""
    except Exception:
        target_category_id = ""

    def add_symbol(symbol, family_category=None):
        if symbol is None:
            return
        try:
            category = try_get_property(symbol, "Category")
            family = try_get_property(symbol, "Family")
            if family_category is None and family is not None:
                family_category = try_get_property(family, "FamilyCategory")
            if not (is_titleblock_category(category, target_category_id) or
                    is_titleblock_category(family_category, target_category_id)):
                return
            key = id_value(symbol.Id)
            if key in found:
                return
            family_name, type_name = symbol_display_names(symbol)
            label = "{} | {} [ID {}]".format(family_name, type_name, key)
            found[key] = (label, symbol)
        except Exception as error:
            scan["errors"].append(safe_text(error))

    # Scan all symbols and compare the category IDs directly.
    try:
        for symbol in DB.FilteredElementCollector(doc).OfClass(DB.FamilySymbol):
            scan["all_family_symbols"] += 1
            add_symbol(symbol)
    except Exception as error:
        scan["errors"].append("FamilySymbol: {}".format(safe_text(error)))

    # Some Revit builds expose titleblock types more consistently through the category collector.
    try:
        category_collector = (DB.FilteredElementCollector(doc)
                              .OfCategory(DB.BuiltInCategory.OST_TitleBlocks)
                              .WhereElementIsElementType())
        for element_type in category_collector:
            scan["titleblock_category_elements"] += 1
            add_symbol(element_type)
    except Exception as error:
        scan["errors"].append("Categoria OST_TitleBlocks: {}".format(safe_text(error)))

    # Family.GetFamilySymbolIds is an independent route for loaded families.
    try:
        for family in DB.FilteredElementCollector(doc).OfClass(DB.Family):
            try:
                family_category = family.FamilyCategory
                category_name = category_display_name(family_category)
                scan["family_categories"].add(category_name)
                if not is_titleblock_category(family_category, target_category_id):
                    continue
                scan["matching_families"] += 1
                for symbol_id in family.GetFamilySymbolIds():
                    add_symbol(doc.GetElement(symbol_id), family_category)
            except Exception as error:
                scan["errors"].append("Family: {}".format(safe_text(error)))
    except Exception as error:
        scan["errors"].append("Famílias: {}".format(safe_text(error)))

    # Existing titleblock instances can still reveal their loaded types if a collector path differs.
    try:
        for sheet in DB.FilteredElementCollector(doc).OfClass(DB.ViewSheet):
            instances = (DB.FilteredElementCollector(doc, sheet.Id)
                         .OfCategory(DB.BuiltInCategory.OST_TitleBlocks)
                         .WhereElementIsNotElementType())
            for instance in instances:
                scan["sheet_instances"] += 1
                add_symbol(doc.GetElement(instance.GetTypeId()))
    except Exception as error:
        scan["errors"].append("Instâncias em folhas: {}".format(safe_text(error)))

    TITLEBLOCK_SCAN_INFO.clear()
    TITLEBLOCK_SCAN_INFO.update(scan)
    TITLEBLOCK_SCAN_INFO["found"] = len(found)
    result = list(found.values())
    result.sort(key=lambda pair: pair[0].lower())
    return result


def titleblock_scan_summary():
    info = TITLEBLOCK_SCAN_INFO
    categories = sorted(list(info.get("family_categories", set())))
    category_text = ", ".join(categories[:8]) if categories else "nenhuma categoria de família enumerada"
    if len(categories) > 8:
        category_text += " …"
    errors = info.get("errors", [])
    error_text = safe_text(errors[0]) if errors else "nenhum erro de enumeração"
    return ("FamilySymbols verificados: {}; elementos na categoria de carimbo: {}; "
            "famílias na categoria: {}; carimbos em folhas: {}; categorias vistas: {}".format(
                info.get("all_family_symbols", 0),
                info.get("titleblock_category_elements", 0),
                info.get("matching_families", 0),
                info.get("sheet_instances", 0),
                category_text) + ". Detalhe da API: {}".format(error_text))


def sheet_bounds(sheet):
    outline = sheet.Outline
    return (outline.Min.U, outline.Min.V, outline.Max.U, outline.Max.V)


def titleblock_bounds(symbol):
    """Resolve paper bounds from an existing sheet, type dimensions, or a rolled-back probe."""
    symbol_id = id_value(symbol.Id)
    for sheet in DB.FilteredElementCollector(doc).OfClass(DB.ViewSheet):
        try:
            instances = (DB.FilteredElementCollector(doc, sheet.Id)
                         .OfCategory(DB.BuiltInCategory.OST_TitleBlocks)
                         .WhereElementIsNotElementType())
            for instance in instances:
                if id_value(instance.GetTypeId()) == symbol_id:
                    bounds = sheet_bounds(sheet)
                    if bounds[2] > bounds[0] and bounds[3] > bounds[1]:
                        return bounds
        except Exception:
            continue

    width = height = None
    for owner in (symbol,):
        try:
            width_parameter = owner.get_Parameter(DB.BuiltInParameter.SHEET_WIDTH)
            height_parameter = owner.get_Parameter(DB.BuiltInParameter.SHEET_HEIGHT)
            if width_parameter and height_parameter:
                width, height = width_parameter.AsDouble(), height_parameter.AsDouble()
        except Exception:
            pass
        if width is None or height is None:
            try:
                width_parameter = owner.LookupParameter("Sheet Width")
                height_parameter = owner.LookupParameter("Sheet Height")
                if width_parameter and height_parameter:
                    width, height = width_parameter.AsDouble(), height_parameter.AsDouble()
            except Exception:
                pass
        if width and height and width > 0 and height > 0:
            return (0.0, 0.0, width, height)

    transaction = DB.Transaction(doc, "Fólio | Medir tipo de carimbo carregado")
    try:
        if transaction.Start() != DB.TransactionStatus.Started:
            raise Exception("Não foi possível medir o tipo de carimbo carregado.")
        probe_sheet = DB.ViewSheet.Create(doc, symbol.Id)
        if probe_sheet is None:
            raise Exception("O Revit não criou a folha de medição do tipo selecionado.")
        doc.Regenerate()
        bounds = sheet_bounds(probe_sheet)
        transaction.RollBack()
        if bounds[2] <= bounds[0] or bounds[3] <= bounds[1]:
            raise Exception("O tipo de carimbo carregado não forneceu dimensões válidas.")
        return bounds
    except Exception:
        try:
            if transaction.GetStatus() == DB.TransactionStatus.Started:
                transaction.RollBack()
        except Exception:
            pass
        raise


def placement_rect(item, point):
    cx, cy = point[0], point[1]
    return (cx - item["layout_width"] / 2.0,
            cy - item["layout_height"] / 2.0,
            cx + item["layout_width"] / 2.0,
            cy + item["layout_height"] / 2.0)


def sorted_by_area(items):
    return sorted(items, key=lambda item: (
        -(item["layout_width"] * item["layout_height"]),
        -item["layout_width"], item["name"].lower()))


def _is_wide_section(item):
    kind = item.get("kind", "").lower()
    width = max(0.0, item.get("layout_width", 0.0))
    height = max(1e-6, item.get("layout_height", 0.0))
    return ("section" in kind or "elevation" in kind) and width / height >= 1.6


def _scale_item_to_zone(item, zone_w, zone_h):
    """Adjust only the planned view scale so its paper footprint fits its assigned zone."""
    if not item.get("adaptive_scale_enabled") or item.get("scale_locked"):
        return
    current = item.get("planned_scale") or item.get("current_scale")
    if not current:
        return
    width = max(item.get("layout_width", item.get("width", 0.0)), 1e-6)
    label_gap = max(0.0, item.get("label_gap", 0.0))
    content_h = max(item.get("height", 0.0),
                     item.get("layout_height", item.get("height", 0.0)) - label_gap)
    required = current * max(width / max(zone_w, 1e-6),
                             content_h / max(zone_h - label_gap, 1e-6))
    kind = item.get("kind", "").lower()
    preferred = current
    for token, denominator in item.get("preferred_scales", PREFERRED_SCALE_BY_VIEW).items():
        if token in kind:
            preferred = denominator
            break
    # Permit up to a 2x enlargement beyond the preferred scale when the assigned
    # zone has room; keep the preference as a guard against implausible zoom-in.
    minimum_denominator = max(1, int(preferred / 2.0))
    threshold = max(required, minimum_denominator)
    target = next((value for value in SCALE_PRESETS if value + 1e-6 >= threshold), None)
    if target is None:
        target = int(math.ceil(threshold / 1000.0) * 1000.0)
    if target == current:
        return
    factor = current / float(target)
    item["width"] *= factor
    item["height"] *= factor
    body_height = max(0.0, item["layout_height"] - label_gap)
    item["layout_width"] *= factor
    item["layout_height"] = body_height * factor + label_gap
    offset = item.get("box_offset", (0.0, 0.0))
    item["box_offset"] = (offset[0] * factor, offset[1] * factor)
    item["planned_scale"] = target
    item["scale_changed"] = target != item.get("current_scale")
    item["scale_text"] = " | 1:{}".format(target)
    item["label"] = item_label(item)
    item["adaptive_zone_scale"] = True


def layout_wide_section_composition(items, left, bottom, right, top, gap):
    """Layout a dominant plan below a panoramic section band with a support column."""
    sections = sorted_by_area([item for item in items if _is_wide_section(item)])
    principal = get_principal_view(items)
    if not sections or principal is None or principal["id"] in [item["id"] for item in sections]:
        return None

    supports = [item for item in items
                if item["id"] != principal["id"] and
                item["id"] not in [section["id"] for section in sections]]
    usable_w, usable_h = right - left, top - bottom
    top_h = usable_h * 0.25
    lower_top = top - top_h - gap
    lower_h = lower_top - bottom
    if lower_h <= 0 or usable_w <= 0:
        return None

    trial = dict((item["id"], dict(item)) for item in items)
    placements = {}
    section_gap_total = gap * max(0, len(sections) - 1)
    section_cell_w = (usable_w - section_gap_total) / float(len(sections))
    for index, source in enumerate(sections):
        item = trial[source["id"]]
        _scale_item_to_zone(item, section_cell_w, top_h)
        if item["layout_width"] > section_cell_w or item["layout_height"] > top_h:
            return None
        if (item["layout_width"] < section_cell_w * 0.20 and
                item["layout_height"] < top_h * 0.20):
            item["zone_fit_warning"] = True
        cell_left = left + index * (section_cell_w + gap)
        placements[item["id"]] = (
            cell_left + section_cell_w / 2.0, top - top_h / 2.0)

    main_w = usable_w if not supports else usable_w * 0.64
    main = trial[principal["id"]]
    _scale_item_to_zone(main, main_w, lower_h)
    if main["layout_width"] > main_w or main["layout_height"] > lower_h:
        return None
    if main["layout_width"] < main_w * 0.20 and main["layout_height"] < lower_h * 0.20:
        main["zone_fit_warning"] = True
    placements[main["id"]] = (left + main_w / 2.0, bottom + lower_h / 2.0)

    if supports:
        support_left = left + main_w + gap
        support_w = right - support_left
        support_gap_total = gap * max(0, len(supports) - 1)
        support_cell_h = (lower_h - support_gap_total) / float(len(supports))
        for index, source in enumerate(sorted_by_area(supports)):
            item = trial[source["id"]]
            _scale_item_to_zone(item, support_w, support_cell_h)
            if item["layout_width"] > support_w or item["layout_height"] > support_cell_h:
                return None
            if (item["layout_width"] < support_w * 0.20 and
                    item["layout_height"] < support_cell_h * 0.20):
                item["zone_fit_warning"] = True
            cell_top = lower_top - index * (support_cell_h + gap)
            placements[item["id"]] = (
                support_left + support_w / 2.0, cell_top - support_cell_h / 2.0)

    for item in items:
        updated = trial[item["id"]]
        if updated.get("adaptive_zone_scale") or updated.get("zone_fit_warning"):
            item.update(updated)
    return placements


def auto_shelf_pages(items, left, bottom, right, top, gap, allow_multiple):
    """Try several deterministic shelf orders and keep the tightest valid packing."""
    role_order = {"principal": 0, "secondary": 1, "support": 2}
    def role_rank(item):
        return role_order.get(item.get("layout_role", "secondary"), 1)

    orders = [
        sorted_by_area(items),
        sorted(items, key=lambda item: (
            -max(item["layout_width"], item["layout_height"]),
            -(item["layout_width"] * item["layout_height"]), item["name"].lower())),
        sorted(items, key=lambda item: (
            -item["layout_height"], -item["layout_width"], item["name"].lower())),
        sorted(items, key=lambda item: (
            -item["layout_width"], -item["layout_height"], item["name"].lower())),
        sorted(items, key=lambda item: (
            -min(item["layout_width"], item["layout_height"]),
            -(item["layout_width"] * item["layout_height"]), item["name"].lower())),
        sorted(items, key=lambda item: (
            role_rank(item), -(item["layout_width"] * item["layout_height"]),
            item["name"].lower()))
    ]
    best_pages, best_unplaced, best_score = None, None, None
    by_id = dict((item["id"], item) for item in items)
    for sequence in orders:
        pages = [{"placements": {}}]
        unplaced = []
        page_index, x, y_top, row_h = 0, left, top, 0.0
        for item in sequence:
            width, height = item["layout_width"], item["layout_height"]
            if x > left + 1e-6 and x + width > right + 1e-6:
                x, y_top, row_h = left, y_top - row_h - gap, 0.0
            if y_top - height < bottom - 1e-6:
                if allow_multiple and pages[page_index]["placements"]:
                    pages.append({"placements": {}})
                    page_index = len(pages) - 1
                    x, y_top, row_h = left, top, 0.0
                else:
                    unplaced.append(item["id"])
                    continue
            if y_top - height < bottom - 1e-6 or x + width > right + 1e-6:
                unplaced.append(item["id"])
                continue
            pages[page_index]["placements"][item["id"]] = (
                x + width / 2.0, y_top - height / 2.0)
            x += width + gap
            row_h = max(row_h, height)
        pages = [page for page in pages if page["placements"]]
        if not pages:
            pages = [{"placements": {}}]
        page_scores = []
        for page in pages:
            page_placements = page["placements"]
            page_quality = composition_quality_metrics(
                by_id, page_placements, (left, bottom, right, top), gap)
            page_scores.append((page_quality["score"], len(page_placements)))
        total_count = sum(count for unused_score, count in page_scores)
        quality_score = (sum(score * count for score, count in page_scores) /
                         float(max(1, total_count)))
        occupied_area = sum(
            by_id[item_id]["layout_width"] * by_id[item_id]["layout_height"]
            for page in pages for item_id in page["placements"])
        score = (len(unplaced), len(pages), -quality_score, -occupied_area)
        if best_score is None or score < best_score:
            best_pages, best_unplaced, best_score = pages, unplaced, score
    return best_pages or [{"placements": {}}], best_unplaced or []


def layout_pages(items, bounds, margin, gap, style, allow_multiple=True):
    """Arrange view footprints using paper-space extents; returns pages and unplaced ids."""
    min_x, min_y, max_x, max_y = bounds
    inset_left, inset_bottom, inset_right, inset_top = margin_insets(margin)
    left, bottom = min_x + inset_left, min_y + inset_bottom
    right, top = max_x - inset_right, max_y - inset_top
    usable_w, usable_h = max(0.0, right - left), max(0.0, top - bottom)
    pages = [{"placements": {}}]
    unplaced = []
    if usable_w <= 0 or usable_h <= 0:
        return pages, [item["id"] for item in items]
    oversized = []
    usable = []
    for item in items:
        too_large = item["layout_width"] > usable_w or item["layout_height"] > usable_h
        if too_large:
            oversized.append(item["id"])
        else:
            usable.append(item)
    unplaced.extend(oversized)

    def next_page():
        pages.append({"placements": {}})
        return len(pages) - 1

    if style.startswith("2 colunas") or style.startswith("3 colunas"):
        columns = 2 if style.startswith("2") else 3
        cell_w = (usable_w - gap * (columns - 1)) / float(columns)
        sequence = sorted_by_area(usable)
        page_index, y_top, index = 0, top, 0
        while index < len(sequence):
            first = sequence[index]
            if first["layout_width"] > cell_w:
                group = [first]
                group_w = usable_w
            else:
                group, group_w = [], cell_w
                while index + len(group) < len(sequence) and len(group) < columns:
                    candidate = sequence[index + len(group)]
                    if candidate["layout_width"] > cell_w:
                        break
                    group.append(candidate)
                if not group:
                    group, group_w = [first], usable_w
            row_h = max(item["layout_height"] for item in group)
            if y_top - row_h < bottom and pages[page_index]["placements"]:
                if allow_multiple:
                    page_index = next_page()
                    y_top = top
                else:
                    unplaced.extend(item["id"] for item in group)
                    index += len(group)
                    continue
            if y_top - row_h < bottom:
                for item in group:
                    unplaced.append(item["id"])
                index += len(group)
                continue
            for column, item in enumerate(group):
                if len(group) == 1 and item["layout_width"] > cell_w:
                    cell_left, actual_cell_w = left, usable_w
                else:
                    actual_cell_w = cell_w
                    cell_left = left + column * (cell_w + gap)
                cx = cell_left + actual_cell_w / 2.0
                cy = y_top - row_h / 2.0
                pages[page_index]["placements"][item["id"]] = (cx, cy)
            y_top -= row_h + gap
            index += len(group)
    elif style.startswith("Faixas"):
        page_index, y_top = 0, top
        for item in sorted_by_area(usable):
            row_h = item["layout_height"]
            if y_top - row_h < bottom and pages[page_index]["placements"]:
                if allow_multiple:
                    page_index = next_page()
                    y_top = top
                else:
                    unplaced.append(item["id"])
                    continue
            if y_top - row_h < bottom:
                unplaced.append(item["id"])
                continue
            pages[page_index]["placements"][item["id"]] = (
                (left + right) / 2.0, y_top - row_h / 2.0)
            y_top -= row_h + gap
    elif style.startswith("Prancha principal"):
        principal = get_principal_view(usable)
        if principal:
            remaining = [item for item in usable if item["id"] != principal["id"]]
            secondary = sorted_by_area(
                [item for item in remaining if item.get("layout_role") != "support"])
            support = sorted_by_area(
                [item for item in remaining if item.get("layout_role") == "support"])
            sequence = [principal] + secondary + support
            main_w = usable_w * 0.62
            detail_left = left + main_w + gap
            detail_w = max(0.0, right - detail_left)
            if principal["layout_width"] <= main_w and principal["layout_height"] <= usable_h and detail_w > 0:
                pages[0]["placements"][principal["id"]] = (
                    left + main_w / 2.0, (bottom + top) / 2.0)
                detail_items = sequence[1:]
                detail_top = top
                overflow = []
                for item in detail_items:
                    if item["layout_width"] > detail_w or item["layout_height"] > usable_h:
                        overflow.append(item)
                        continue
                    if detail_top - item["layout_height"] < bottom:
                        overflow.append(item)
                        continue
                    pages[0]["placements"][item["id"]] = (
                        detail_left + detail_w / 2.0,
                        detail_top - item["layout_height"] / 2.0)
                    detail_top -= item["layout_height"] + gap
                if overflow and allow_multiple:
                    extra_pages, extra_unplaced = layout_pages(
                        overflow, bounds, margin, gap,
                        "Automático por tamanho", allow_multiple)
                    for page in extra_pages:
                        if page["placements"]:
                            pages.append(page)
                    unplaced.extend(extra_unplaced)
                elif overflow:
                    unplaced.extend(item["id"] for item in overflow)
            else:
                return layout_pages(items, bounds, margin, gap,
                                    "Automático por tamanho", allow_multiple)
        else:
            return layout_pages(items, bounds, margin, gap,
                                "Automático por tamanho", allow_multiple)
    else:
        composed = None
        if style.startswith("Automático"):
            composed = layout_wide_section_composition(
                usable, left, bottom, right, top, gap)
        if composed is not None:
            pages = [{"placements": composed}]
        else:
            pages, packed_unplaced = auto_shelf_pages(
                usable, left, bottom, right, top, gap, allow_multiple)
            unplaced.extend(packed_unplaced)

    pages = [page for page in pages if page["placements"]]
    if not pages:
        pages = [{"placements": {}}]
    return pages, unplaced


def layout_fixed_page_count(items, bounds, margin, gap, style, page_count,
                            partition_mode="role_area", packing_options=None):
    """Balance requested sheets while varying deterministic document partitions."""
    page_count = max(1, min(int(page_count), len(items))) if items else 1
    groups = [[] for unused in range(page_count)]
    min_x, min_y, max_x, max_y = bounds
    inset_left, inset_bottom, inset_right, inset_top = margin_insets(margin)
    usable_area = max(1e-6, (max_x - min_x - inset_left - inset_right) *
                      (max_y - min_y - inset_bottom - inset_top))
    loads = [0.0 for unused in range(page_count)]

    def burden(item):
        area = max(0.0, item["layout_width"] * item["layout_height"])
        span = max(item["layout_width"] / max(max_x - min_x - inset_left - inset_right, 1e-6),
                   item["layout_height"] / max(max_y - min_y - inset_bottom - inset_top, 1e-6))
        return area / usable_area + 0.12 * span

    def partition_key(item, include_role=True):
        role = _layout_role_order(item) if include_role else 0
        area = item["layout_width"] * item["layout_height"]
        name = item.get("name", "").lower()
        if partition_mode == "role_compact":
            return role, burden(item), name
        if partition_mode == "role_wide":
            return role, -item["layout_width"], -item["layout_height"], name
        if partition_mode == "role_tall":
            return role, -item["layout_height"], -item["layout_width"], name
        if partition_mode == "role_name":
            return role, name, -area
        return role, -burden(item), name

    principal = get_principal_view(items)
    if principal:
        groups[0].append(principal)
        loads[0] += burden(principal)
    remaining = [item for item in items if not principal or item["id"] != principal["id"]]
    # Seed otherwise-empty sheets with the strongest remaining non-support view
    # so a multi-sheet set does not leave later pages as collections of details.
    major_items = sorted(
        [item for item in remaining if item.get("layout_role") != "support"],
        key=lambda item: partition_key(item, False))
    for page_index in range(1, page_count):
        if page_index >= len(major_items):
            break
        item = major_items[page_index - 1]
        groups[page_index].append(item)
        loads[page_index] += burden(item)
        remaining = [candidate for candidate in remaining
                     if candidate["id"] != item["id"]]
    remaining.sort(key=lambda item: partition_key(item))
    for item in remaining:
        target = min(range(page_count), key=lambda index: (loads[index], len(groups[index])))
        groups[target].append(item)
        loads[target] += burden(item)
    pages, unplaced = [], []
    for group in groups:
        if not group:
            pages.append({"placements": {}})
            continue
        if packing_options:
            inset_left, inset_bottom, inset_right, inset_top = margin_insets(margin)
            placements = pack_items_in_region(
                group, min_x + inset_left, min_y + inset_bottom,
                max_x - inset_right, max_y - inset_top, gap,
                packing_options.get("columns", len(group)),
                packing_options.get("order_mode", "role_area"),
                packing_options.get("horizontal_alignment", "center"),
                packing_options.get("vertical_alignment", "center"))
            missing = [item["id"] for item in group
                       if not placements or item["id"] not in placements]
            placements = placements or {}
        else:
            packed, missing = layout_pages(
                group, bounds, margin, gap, style, allow_multiple=False)
            placements = {}
            for page in packed:
                placements.update(page.get("placements", {}))
        pages.append({"placements": placements})
        unplaced.extend(missing)
    return pages, unplaced


def page_diagnostics(items_by_id, placements, bounds, margin):
    min_x, min_y, max_x, max_y = bounds
    inset_left, inset_bottom, inset_right, inset_top = margin_insets(margin)
    left, bottom = min_x + inset_left, min_y + inset_bottom
    right, top = max_x - inset_right, max_y - inset_top
    rects, outside, overlaps = [], [], []
    for item_id, point in placements.items():
        item = items_by_id[item_id]
        rect = placement_rect(item, point)
        rects.append((item_id, rect))
        if rect[0] < left - 1e-5 or rect[1] < bottom - 1e-5 or rect[2] > right + 1e-5 or rect[3] > top + 1e-5:
            outside.append(item_id)
    for i in range(len(rects)):
        id_a, a = rects[i]
        for j in range(i + 1, len(rects)):
            id_b, b = rects[j]
            if a[0] < b[2] - 1e-4 and a[2] > b[0] + 1e-4 and a[1] < b[3] - 1e-4 and a[3] > b[1] + 1e-4:
                overlaps.append((id_a, id_b))
    return outside, overlaps


def composition_quality_metrics(items_by_id, placements, bounds, gap,
                                outside_count=0, overlap_count=0):
    """Score geometric hierarchy and balance; this is guidance, not print approval."""
    min_x, min_y, max_x, max_y = bounds
    usable_w = max(1e-6, max_x - min_x)
    usable_h = max(1e-6, max_y - min_y)
    usable_area = usable_w * usable_h
    page_items = [items_by_id[item_id] for item_id in placements
                  if item_id in items_by_id]
    if not page_items:
        return {"score": 0, "occupancy": 0, "balance": 0,
                "hierarchy": 0, "alignment": 0, "spacing": 0}

    rects = {}
    occupied_area = 0.0
    weighted_x = weighted_y = weight_total = 0.0
    for item in page_items:
        item_id = item["id"]
        center = placements[item_id]
        rect = placement_rect(item, center)
        rects[item_id] = rect
        area = max(1e-8, item.get("layout_width", 0.0) *
                   item.get("layout_height", 0.0))
        occupied_area += area
        role_weight = 1.25 if item.get("layout_role") == "principal" else 1.0
        if item.get("layout_role") == "support":
            role_weight = 0.75
        weight = area * role_weight
        weighted_x += center[0] * weight
        weighted_y += center[1] * weight
        weight_total += weight

    occupancy = min(1.0, occupied_area / usable_area)
    # A mid-range fill leaves room for margins and annotation while avoiding
    # both an over-packed sheet and a single tiny island of content.
    occupancy_score = max(0.0, 100.0 - abs(occupancy - 0.62) * 150.0)

    centroid_x = weighted_x / max(weight_total, 1e-8)
    centroid_y = weighted_y / max(weight_total, 1e-8)
    max_offset = max(1e-8, math.sqrt((usable_w / 2.0) ** 2 + (usable_h / 2.0) ** 2))
    offset = math.sqrt((centroid_x - (min_x + max_x) / 2.0) ** 2 +
                       (centroid_y - (min_y + max_y) / 2.0) ** 2)
    balance_score = 100.0 * (1.0 - min(1.0, offset / max_offset))

    principal = get_principal_view(page_items)
    non_support = [item for item in page_items
                   if item.get("layout_role") != "support" and
                   (not principal or item["id"] != principal["id"])]
    if principal is None:
        hierarchy_score = 30.0
    elif not non_support:
        hierarchy_score = 88.0
    else:
        main_area = max(1e-8, get_view_area(principal))
        next_area = max([get_view_area(item) for item in non_support] or [1e-8])
        ratio = main_area / max(next_area, 1e-8)
        hierarchy_score = max(35.0, min(100.0, 55.0 + ratio * 22.5))
    support_items = [item for item in page_items
                     if item.get("layout_role") == "support" and
                     not item.get("is_schedule")]
    tiny_supports = sum(1 for item in support_items
                        if get_view_area(item) / usable_area < 0.025)
    hierarchy_score = max(0.0, hierarchy_score - min(30.0, tiny_supports * 10.0))

    pairs = []
    alignment_tolerance = cm_to_ft(0.5)
    spacing_tolerance = max(cm_to_ft(1.0), gap * 0.5)
    actual_gaps = []
    item_ids = list(rects.keys())
    for index, item_id in enumerate(item_ids):
        a = rects[item_id]
        for other_id in item_ids[index + 1:]:
            b = rects[other_id]
            aligned = (abs(a[0] - b[0]) <= alignment_tolerance or
                       abs(a[2] - b[2]) <= alignment_tolerance or
                       abs(a[1] - b[1]) <= alignment_tolerance or
                       abs(a[3] - b[3]) <= alignment_tolerance)
            pairs.append(1.0 if aligned else 0.0)
            vertical_overlap = min(a[3], b[3]) - max(a[1], b[1])
            horizontal_overlap = min(a[2], b[2]) - max(a[0], b[0])
            if vertical_overlap > alignment_tolerance:
                distance = max(b[0] - a[2], a[0] - b[2])
                if distance >= 0:
                    actual_gaps.append(distance)
            elif horizontal_overlap > alignment_tolerance:
                distance = max(b[1] - a[3], a[1] - b[3])
                if distance >= 0:
                    actual_gaps.append(distance)
    alignment_score = (100.0 * sum(pairs) / len(pairs)) if pairs else 72.0
    if actual_gaps:
        spacing_score = 100.0 * sum(
            max(0.0, 1.0 - abs(actual_gap - gap) /
                max(spacing_tolerance * 3.0, 1e-6))
            for actual_gap in actual_gaps) / len(actual_gaps)
    else:
        spacing_score = 70.0

    weighted_score = (occupancy_score * 0.20 + balance_score * 0.20 +
                      hierarchy_score * 0.25 + alignment_score * 0.20 +
                      spacing_score * 0.15)
    weighted_score -= min(45.0, outside_count * 15.0 + overlap_count * 22.5)
    return {
        "score": int(round(max(0.0, min(100.0, weighted_score)))),
        "occupancy": int(round(occupancy_score)),
        "balance": int(round(balance_score)),
        "hierarchy": int(round(hierarchy_score)),
        "alignment": int(round(alignment_score)),
        "spacing": int(round(spacing_score)),
        "fill_ratio": occupancy
    }


def _layout_role_order(item):
    return {"principal": 0, "secondary": 1, "support": 2}.get(
        item.get("layout_role", "secondary"), 1)


def _composition_order_key(item, mode):
    role = _layout_role_order(item)
    area = item["layout_width"] * item["layout_height"]
    name = item.get("name", "").lower()
    if mode == "role_width":
        return role, -item["layout_width"], -item["layout_height"], name
    if mode == "role_height":
        return role, -item["layout_height"], -item["layout_width"], name
    if mode == "role_name":
        return role, name, -area
    if mode == "role_compact":
        return role, area, name
    return role, -area, -item["layout_width"], name


def _proposal_is_near_duplicate(signature, existing_signatures, bounds,
                                distance_ratio=0.02):
    """Treat layouts with only negligible center shifts as the same proposal."""
    if not signature:
        return True
    diagonal = math.sqrt(max(1e-8,
                             (bounds[2] - bounds[0]) ** 2 +
                             (bounds[3] - bounds[1]) ** 2))
    for existing in existing_signatures:
        if len(signature) != len(existing):
            continue
        current_by_id = dict((entry[0], entry[1:]) for entry in signature)
        existing_by_id = dict((entry[0], entry[1:]) for entry in existing)
        if set(current_by_id.keys()) != set(existing_by_id.keys()):
            continue
        same_partition = True
        maximum_distance = 0.0
        for item_id, current in current_by_id.items():
            previous = existing_by_id[item_id]
            if current[0] != previous[0] or current[3] != previous[3]:
                same_partition = False
                break
            dx = (current[1] - previous[1]) / 1000.0
            dy = (current[2] - previous[2]) / 1000.0
            maximum_distance = max(
                maximum_distance, math.sqrt(dx * dx + dy * dy))
        if same_partition and maximum_distance <= diagonal * distance_ratio:
            return True
    return False


def pack_items_in_region(items, left, bottom, right, top, gap, max_columns,
                         order_mode="role_area", horizontal_alignment="center",
                         vertical_alignment="center"):
    """Pack measured view rectangles into rows with selectable order and alignment."""
    usable_w, usable_h = max(0.0, right - left), max(0.0, top - bottom)
    if usable_w <= 0.0 or usable_h <= 0.0:
        return None
    if not items:
        return {}
    max_columns = max(1, int(max_columns))
    ordered = sorted(items, key=lambda item: _composition_order_key(item, order_mode))
    rows, row, row_width, row_height = [], [], 0.0, 0.0
    for item in ordered:
        width, height = item["layout_width"], item["layout_height"]
        if width > usable_w + 1e-6 or height > usable_h + 1e-6:
            return None
        proposed_width = row_width + (gap if row else 0.0) + width
        if row and (len(row) >= max_columns or proposed_width > usable_w + 1e-6):
            rows.append((row, row_width, row_height))
            row, row_width, row_height = [], 0.0, 0.0
        row.append(item)
        row_width += (gap if len(row) > 1 else 0.0) + width
        row_height = max(row_height, height)
    if row:
        rows.append((row, row_width, row_height))
    required_h = sum(entry[2] for entry in rows) + gap * max(0, len(rows) - 1)
    if required_h > usable_h + 1e-6:
        return None
    if vertical_alignment == "top":
        y_top = top
    elif vertical_alignment == "bottom":
        y_top = bottom + required_h
    else:
        y_top = top - max(0.0, usable_h - required_h) / 2.0
    placements = {}
    for row_items, used_w, row_h in rows:
        if horizontal_alignment == "left":
            x = left
        elif horizontal_alignment == "right":
            x = right - used_w
        else:
            x = left + (usable_w - used_w) / 2.0
        center_y = y_top - row_h / 2.0
        for item in row_items:
            width = item["layout_width"]
            placements[item["id"]] = (x + width / 2.0, center_y)
            x += width + gap
        y_top -= row_h + gap
    return placements


def layout_grid_pages(items, bounds, margin, gap, max_columns, order_mode,
                      horizontal_alignment, vertical_alignment, allow_multiple):
    """Build balanced grid/shelf pages from data-derived row and page breaks."""
    inset_left, inset_bottom, inset_right, inset_top = margin_insets(margin)
    left, bottom = bounds[0] + inset_left, bounds[1] + inset_bottom
    right, top = bounds[2] - inset_right, bounds[3] - inset_top
    usable_w, usable_h = right - left, top - bottom
    if usable_w <= 0.0 or usable_h <= 0.0:
        return [{"placements": {}}], [item["id"] for item in items]
    if not items:
        return [{"placements": {}}], []

    max_columns = max(1, int(max_columns))
    ordered = sorted(items, key=lambda item: _composition_order_key(item, order_mode))
    rows, row, row_width, row_height = [], [], 0.0, 0.0
    unplaced = []
    for item in ordered:
        width, height = item["layout_width"], item["layout_height"]
        if width > usable_w + 1e-6 or height > usable_h + 1e-6:
            unplaced.append(item["id"])
            continue
        proposed_width = row_width + (gap if row else 0.0) + width
        if row and (len(row) >= max_columns or proposed_width > usable_w + 1e-6):
            rows.append((row, row_width, row_height))
            row, row_width, row_height = [], 0.0, 0.0
        row.append(item)
        row_width += (gap if len(row) > 1 else 0.0) + width
        row_height = max(row_height, height)
    if row:
        rows.append((row, row_width, row_height))

    page_rows, current_rows, current_height = [], [], 0.0
    for row_items, used_width, row_height in rows:
        added_height = row_height + (gap if current_rows else 0.0)
        if current_height + added_height > usable_h + 1e-6 and current_rows:
            page_rows.append(current_rows)
            current_rows, current_height = [], 0.0
            added_height = row_height
        if current_height + added_height > usable_h + 1e-6:
            unplaced.extend(item["id"] for item in row_items)
            continue
        if current_rows:
            current_height += gap
        current_rows.append((row_items, used_width, row_height))
        current_height += row_height
    if current_rows:
        page_rows.append(current_rows)

    pages = []
    for rows_on_page in page_rows:
        required_h = sum(entry[2] for entry in rows_on_page) + gap * max(0, len(rows_on_page) - 1)
        if not allow_multiple and pages:
            unplaced.extend(item["id"] for row_items, unused_width, unused_height in rows_on_page
                            for item in row_items)
            continue
        if vertical_alignment == "top":
            y_top = top
        elif vertical_alignment == "bottom":
            y_top = bottom + required_h
        else:
            y_top = top - max(0.0, usable_h - required_h) / 2.0
        placements = {}
        for row_items, used_width, row_height in rows_on_page:
            if horizontal_alignment == "left":
                x = left
            elif horizontal_alignment == "right":
                x = right - used_width
            else:
                x = left + (usable_w - used_width) / 2.0
            center_y = y_top - row_height / 2.0
            for item in row_items:
                width = item["layout_width"]
                placements[item["id"]] = (x + width / 2.0, center_y)
                x += width + gap
            y_top -= row_height + gap
        pages.append({"placements": placements})
    if not pages:
        pages = [{"placements": {}}]
    if not allow_multiple and len(pages) > 1:
        unplaced.extend(item_id for page in pages[1:]
                        for item_id in page["placements"].keys())
        pages = pages[:1]
    return pages, unplaced


def layout_architectural_candidate(items, bounds, margin, gap, variant,
                                   hero_ratio=None, support_columns=None,
                                   order_mode="role_area",
                                   horizontal_alignment="center",
                                   vertical_alignment="center"):
    """Create role-led hero, column, or centered-grid compositions without changing scales."""
    inset_left, inset_bottom, inset_right, inset_top = margin_insets(margin)
    min_x, min_y, max_x, max_y = bounds
    left, bottom = min_x + inset_left, min_y + inset_bottom
    right, top = max_x - inset_right, max_y - inset_top
    usable_w, usable_h = right - left, top - bottom
    if usable_w <= 0.0 or usable_h <= 0.0 or not items:
        return None
    principal = get_principal_view(items)
    if principal is None:
        columns = 2 if variant.endswith("2") else 3
        return pack_items_in_region(
            items, left, bottom, right, top, gap, columns, order_mode,
            horizontal_alignment, vertical_alignment)

    supports = [item for item in items if item["id"] != principal["id"]]
    support_columns = max(1, int(support_columns or len(supports) or 1))
    placements = {}
    if variant in ("hero_left", "hero_right"):
        ratio = hero_ratio if hero_ratio is not None else 0.60
        hero_w = usable_w if not supports else usable_w * ratio
        side_w = usable_w - hero_w - (gap if supports else 0.0)
        if (principal["layout_width"] > hero_w + 1e-6 or
                principal["layout_height"] > usable_h + 1e-6):
            return None
        hero_left = left if variant == "hero_left" else right - hero_w
        placements[principal["id"]] = (hero_left + hero_w / 2.0,
                                        (bottom + top) / 2.0)
        if supports:
            side_left = left + hero_w + gap if variant == "hero_left" else left
            packed = pack_items_in_region(
                supports, side_left, bottom, side_left + side_w, top, gap,
                support_columns, order_mode, horizontal_alignment,
                vertical_alignment)
            if packed is None:
                return None
            placements.update(packed)
    elif variant in ("hero_top", "hero_bottom"):
        ratio = hero_ratio if hero_ratio is not None else 0.58
        hero_h = usable_h if not supports else usable_h * ratio
        support_h = usable_h - hero_h - (gap if supports else 0.0)
        if (principal["layout_width"] > usable_w + 1e-6 or
                principal["layout_height"] > hero_h + 1e-6):
            return None
        hero_bottom = bottom + support_h + gap if variant == "hero_top" and supports else bottom
        placements[principal["id"]] = (left + usable_w / 2.0,
                                        hero_bottom + hero_h / 2.0)
        if supports:
            support_bottom = bottom if variant == "hero_top" else bottom + hero_h + gap
            packed = pack_items_in_region(
                supports, left, support_bottom, right, support_bottom + support_h,
                gap, support_columns, order_mode, horizontal_alignment,
                vertical_alignment)
            if packed is None:
                return None
            placements.update(packed)
    elif variant == "hero_center":
        if not supports:
            placements[principal["id"]] = ((left + right) / 2.0, (bottom + top) / 2.0)
        else:
            side_w = (usable_w * 0.48 - 2.0 * gap) / 2.0
            hero_w = usable_w - 2.0 * side_w - 2.0 * gap
            if (side_w <= 0.0 or principal["layout_width"] > hero_w + 1e-6 or
                    principal["layout_height"] > usable_h + 1e-6):
                return None
            placements[principal["id"]] = ((left + right) / 2.0, (bottom + top) / 2.0)
            left_items = supports[::2]
            right_items = supports[1::2]
            left_pack = pack_items_in_region(
                left_items, left, bottom, left + side_w, top, gap, 1,
                order_mode, horizontal_alignment, vertical_alignment)
            right_start = left + side_w + gap + hero_w + gap
            right_pack = pack_items_in_region(
                right_items, right_start, bottom, right, top, gap, 1,
                order_mode, horizontal_alignment, vertical_alignment)
            if left_pack is None or right_pack is None:
                return None
            placements.update(left_pack)
            placements.update(right_pack)
    else:
        return None
    return placements if len(placements) == len(items) else None


def hero_split_ratios(items, bounds, margin, orientation):
    """Derive hero-zone proportions from the main view's measured paper footprint."""
    principal = get_principal_view(items)
    if principal is None:
        return []
    inset_left, inset_bottom, inset_right, inset_top = margin_insets(margin)
    usable_w = max(1e-6, bounds[2] - bounds[0] - inset_left - inset_right)
    usable_h = max(1e-6, bounds[3] - bounds[1] - inset_bottom - inset_top)
    if orientation in ("hero_left", "hero_right"):
        minimum = principal["layout_width"] / usable_w
    else:
        minimum = principal["layout_height"] / usable_h
    minimum = max(0.0, min(1.0, minimum))
    remaining = max(0.0, 0.92 - minimum)
    ratios = set()
    for portion in (0.12, 0.30, 0.52, 0.76, 1.0):
        ratio = min(0.92, minimum + max(0.025, remaining * portion))
        if ratio > minimum + 1e-6:
            ratios.add(round(ratio, 3))
    if not ratios and minimum < 1.0:
        ratios.add(round(min(0.92, minimum + 0.025), 3))
    return sorted(ratios)


COMPOSITION_SNAPSHOT_FIELDS = (
    "id", "name", "kind", "width", "height", "layout_width", "layout_height",
    "label_gap", "current_center", "box_offset", "is_existing", "is_schedule",
    "layout_role", "hierarchy_source", "current_scale", "planned_scale",
    "scale_text", "label", "scale_changed", "scale_locked", "scale_lock_reason",
    "adaptive_scale_enabled", "scale_user_override", "preferred_scales",
    "adaptive_zone_scale", "zone_fit_warning", "measurement_source",
    "measurement_confidence", "document_group_id", "document_relations",
    "position_locked", "hierarchy_locked", "group_locked"
)


def _snapshot_plain_value(value):
    """Copy only primitive geometry data; Revit objects never enter a planning snapshot."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    value_type = type(value).__name__
    if value_type in ("str", "unicode", "String"):
        return safe_text(value)
    if value_type in ("Double", "Single", "Decimal"):
        try:
            return float(value)
        except Exception:
            return None
    if value_type in ("Int16", "Int32", "Int64", "long"):
        try:
            return int(value)
        except Exception:
            return None
    if isinstance(value, dict):
        return dict((safe_text(key), _snapshot_plain_value(entry))
                    for key, entry in value.items())
    if isinstance(value, (tuple, list)):
        return tuple(_snapshot_plain_value(entry) for entry in value)
    return None


def _snapshot_signature_value(value):
    if isinstance(value, float):
        return round(value, 6)
    if isinstance(value, dict):
        return tuple((key, _snapshot_signature_value(value[key]))
                     for key in sorted(value.keys()))
    if isinstance(value, (tuple, list)):
        return tuple(_snapshot_signature_value(entry) for entry in value)
    return value


def make_composition_snapshot(items, bounds, margin, gap, setup):
    """Create an immutable-by-convention, versionable input for geometry planning."""
    snapshot_items = []
    for item in items:
        snapshot_item = {}
        for key in COMPOSITION_SNAPSHOT_FIELDS:
            if key in item:
                snapshot_item[key] = _snapshot_plain_value(item[key])
        snapshot_items.append(snapshot_item)
    snapshot_items.sort(key=lambda entry: entry.get("id", 0))
    snapshot = {
        "items": snapshot_items,
        "bounds": tuple(_snapshot_plain_value(value) for value in bounds),
        "margin": _snapshot_plain_value(margin),
        "gap": _snapshot_plain_value(gap),
        "requested_sheet_count": setup.get("requested_sheet_count"),
        "single_sheet": bool(setup.get("single_sheet")),
        "mode": safe_text(setup.get("mode", "")),
        "scale_mode": safe_text(setup.get("scale_mode", ""))
    }
    snapshot["input_signature"] = (
        tuple(tuple((key, _snapshot_signature_value(item.get(key)))
                    for key in sorted(item.keys())) for item in snapshot_items),
        _snapshot_signature_value(snapshot["bounds"]),
        _snapshot_signature_value(snapshot["margin"]),
        _snapshot_signature_value(snapshot["gap"]),
        snapshot["requested_sheet_count"], snapshot["single_sheet"],
        snapshot["mode"], snapshot["scale_mode"])
    return snapshot


def _proposal_signature(pages, items):
    signatures = []
    for page_index, page in enumerate(pages):
        for item_id, center in page.get("placements", {}).items():
            item = next((entry for entry in items if entry["id"] == item_id), None)
            scale = item.get("planned_scale") if item else None
            signatures.append((item_id, page_index,
                               int(round(center[0] * 1000.0)),
                               int(round(center[1] * 1000.0)), scale))
    return tuple(sorted(signatures))


def score_composition_proposal(items, pages, bounds, margin, gap, unplaced):
    items_by_id = dict((item["id"], item) for item in items)
    inset_left, inset_bottom, inset_right, inset_top = margin_insets(margin)
    safe_bounds = (bounds[0] + inset_left, bounds[1] + inset_bottom,
                   bounds[2] - inset_right, bounds[3] - inset_top)
    score_total, item_count = 0.0, 0
    outside_count = overlap_count = 0
    for page in pages:
        placements = page.get("placements", {})
        if not placements:
            continue
        outside, overlaps = page_diagnostics(items_by_id, placements, bounds, margin)
        quality = composition_quality_metrics(
            items_by_id, placements, safe_bounds, gap, len(outside), len(overlaps))
        count = len(placements)
        score_total += quality["score"] * count
        item_count += count
        outside_count += len(outside)
        overlap_count += len(overlaps)
    base_score = score_total / float(max(1, item_count))
    page_penalty = max(0, len([page for page in pages if page.get("placements")]) - 1) * 3.0
    penalty = (base_score - page_penalty - len(unplaced) * 45.0 -
               outside_count * 18.0 - overlap_count * 25.0)
    return int(round(max(0.0, min(100.0, penalty))))


def composition_candidate_specs(items, bounds, margin, gap, allow_multiple,
                                requested_count=None):
    """Yield finite, geometry-derived search instructions without evaluating them."""
    styles = [LAYOUTS[4], LAYOUTS[1], LAYOUTS[3], LAYOUTS[0], LAYOUTS[2]]
    partition_modes = ("role_area", "role_compact", "role_wide",
                       "role_tall", "role_name")
    order_modes = ("role_area", "role_width", "role_height",
                   "role_name", "role_compact")
    alignments = (
        ("center", "center", "centrada"),
        ("left", "top", "superior esquerda"),
        ("right", "top", "superior direita"),
        ("center", "top", "faixa superior"),
        ("center", "bottom", "faixa inferior")
    )
    for style in styles:
        if requested_count:
            for partition_mode in partition_modes:
                yield {"kind": "fixed_style", "style": style,
                       "partition_mode": partition_mode}
        else:
            yield {"kind": "style", "style": style}

    if items:
        inset_left, inset_bottom, inset_right, inset_top = margin_insets(margin)
        usable_w = max(0.0, bounds[2] - bounds[0] - inset_left - inset_right)
        positive_widths = [item["layout_width"] for item in items
                           if item.get("layout_width", 0.0) > 1e-6]
        min_width = min(positive_widths) if positive_widths else usable_w
        max_columns = min(len(items), max(1, int(
            (usable_w + gap) / max(min_width + gap, 1e-6))))
        grid_combo_index = 0
        for columns in range(1, max_columns + 1):
            for order_mode in order_modes:
                for horizontal, vertical, alignment_label in alignments:
                    if requested_count:
                        partition_mode = partition_modes[
                            grid_combo_index % len(partition_modes)]
                        spec = {"kind": "fixed_grid", "style": LAYOUTS[1],
                                "partition_mode": partition_mode,
                                "columns": columns, "order_mode": order_mode,
                                "horizontal_alignment": horizontal,
                                "vertical_alignment": vertical,
                                "alignment_label": alignment_label}
                    else:
                        spec = {"kind": "grid", "style": LAYOUTS[1],
                                "columns": columns, "order_mode": order_mode,
                                "horizontal_alignment": horizontal,
                                "vertical_alignment": vertical,
                                "alignment_label": alignment_label}
                    yield spec
                    grid_combo_index += 1

        if not requested_count or int(requested_count) == 1:
            principal = get_principal_view(items)
            principal_id = principal.get("id") if principal else None
            supports = [item for item in items if item["id"] != principal_id]
            for variant, orientation_label in (
                    ("hero_left", "principal à esquerda"),
                    ("hero_right", "principal à direita"),
                    ("hero_top", "principal acima"),
                    ("hero_bottom", "principal abaixo")):
                ratios = hero_split_ratios(items, bounds, margin, variant)
                for ratio in ratios:
                    if variant in ("hero_left", "hero_right"):
                        support_zone_w = usable_w * (1.0 - ratio) - gap
                    else:
                        support_zone_w = usable_w
                    support_widths = [item["layout_width"] for item in supports
                                      if item.get("layout_width", 0.0) > 1e-6]
                    min_support_width = (min(support_widths)
                                         if support_widths else support_zone_w)
                    max_support_columns = min(
                        len(supports), max(1, int(
                            (support_zone_w + gap) /
                            max(min_support_width + gap, 1e-6))))
                    for support_columns in range(1, max_support_columns + 1):
                        for order_mode in ("role_area", "role_width", "role_name"):
                            yield {"kind": "hero", "style": LAYOUTS[4],
                                   "variant": variant, "ratio": ratio,
                                   "support_columns": support_columns,
                                   "order_mode": order_mode,
                                   "orientation_label": orientation_label}
            yield {"kind": "center_hero", "style": LAYOUTS[4]}


def evaluate_composition_spec(spec, items, bounds, margin, gap, allow_multiple,
                              requested_count=None):
    """Evaluate one pure-geometry candidate and reject any unsafe/incomplete plan."""
    trial = [dict(item) for item in items]
    kind = spec["kind"]
    style = spec["style"]
    name = style
    unplaced = []
    if kind == "style":
        pages, unplaced = layout_pages(
            trial, bounds, margin, gap, style, allow_multiple)
    elif kind == "fixed_style":
        partition_mode = spec["partition_mode"]
        pages, unplaced = layout_fixed_page_count(
            trial, bounds, margin, gap, style, requested_count, partition_mode)
        name = "{} · partição {}".format(style, partition_mode.replace("role_", ""))
    elif kind in ("grid", "fixed_grid"):
        columns = spec["columns"]
        order_mode = spec["order_mode"]
        horizontal = spec["horizontal_alignment"]
        vertical = spec["vertical_alignment"]
        if kind == "fixed_grid":
            partition_mode = spec["partition_mode"]
            packing_options = {
                "columns": columns, "order_mode": order_mode,
                "horizontal_alignment": horizontal,
                "vertical_alignment": vertical
            }
            pages, unplaced = layout_fixed_page_count(
                trial, bounds, margin, gap, style, requested_count,
                partition_mode, packing_options)
            partition_text = " · distribuição {}".format(
                partition_mode.replace("role_", ""))
        else:
            pages, unplaced = layout_grid_pages(
                trial, bounds, margin, gap, columns, order_mode,
                horizontal, vertical, allow_multiple)
            partition_text = ""
        name = "Grelha {} col. · {} · {}{}".format(
            columns, order_mode.replace("role_", ""),
            spec["alignment_label"], partition_text)
    elif kind == "hero":
        placements = layout_architectural_candidate(
            trial, bounds, margin, gap, spec["variant"],
            hero_ratio=spec["ratio"],
            support_columns=spec["support_columns"],
            order_mode=spec["order_mode"])
        pages = [{"placements": placements}] if placements else []
        name = ("Composição hierárquica · {} · principal {:.0f}% · "
                "apoios {} col. · {}".format(
                    spec["orientation_label"], spec["ratio"] * 100.0,
                    spec["support_columns"],
                    spec["order_mode"].replace("role_", "")))
    elif kind == "center_hero":
        placements = layout_architectural_candidate(
            trial, bounds, margin, gap, "hero_center")
        pages = [{"placements": placements}] if placements else []
        name = "Vista principal central · apoios laterais"
    else:
        return None

    if unplaced or not pages:
        return None
    if requested_count and len(pages) != int(requested_count):
        return None
    if any(not page.get("placements") for page in pages):
        return None
    placed_ids = set(item_id for page in pages
                     for item_id in page.get("placements", {}).keys())
    if placed_ids != set(item["id"] for item in trial):
        return None
    trial_by_id = dict((item["id"], item) for item in trial)
    for page in pages:
        outside, overlaps = page_diagnostics(
            trial_by_id, page.get("placements", {}), bounds, margin)
        if outside or overlaps:
            return None
    signature = _proposal_signature(pages, trial)
    if not signature:
        return None
    return {
        "name": name, "style": style, "items": trial, "pages": pages,
        "unplaced": [], "signature": signature,
        "score": score_composition_proposal(
            trial, pages, bounds, margin, gap, [])
    }


class CompositionProposalSearch(object):
    """One-step-at-a-time search state for a responsive WPF preview."""

    def __init__(self, items, bounds, margin, gap, allow_multiple,
                 requested_count=None):
        self.items = items
        self.bounds = bounds
        self.margin = margin
        self.gap = gap
        self.allow_multiple = allow_multiple
        self.requested_count = requested_count
        self.specs = iter(composition_candidate_specs(
            items, bounds, margin, gap, allow_multiple, requested_count))
        self.exact_signatures = set()
        self.candidates = []
        self.evaluated = 0
        self.done = False

    def step(self):
        if self.done:
            return None
        try:
            spec = next(self.specs)
        except StopIteration:
            self.done = True
            return None
        self.evaluated += 1
        candidate = evaluate_composition_spec(
            spec, self.items, self.bounds, self.margin, self.gap,
            self.allow_multiple, self.requested_count)
        if candidate is None or candidate["signature"] in self.exact_signatures:
            return None
        self.exact_signatures.add(candidate["signature"])
        self.candidates.append(candidate)
        return candidate

    def final_candidates(self):
        ordered = sorted(self.candidates, key=lambda candidate: (
            -candidate["score"], len(candidate["pages"]),
            candidate["name"].lower()))
        diverse, signatures = [], []
        for candidate in ordered:
            if _proposal_is_near_duplicate(
                    candidate["signature"], signatures, self.bounds):
                continue
            diverse.append(candidate)
            signatures.append(candidate["signature"])
        return diverse


def build_composition_proposals(items, bounds, margin, gap, allow_multiple,
                                requested_count=None):
    """Synchronous compatibility wrapper used by offline geometry verification."""
    search = CompositionProposalSearch(
        items, bounds, margin, gap, allow_multiple, requested_count)
    while not search.done:
        search.step()
    return search.final_candidates()


def describe_outside_items(items_by_id, outside, bounds, margin):
    """Return view names, sides, measured excess and scale for actionable errors."""
    min_x, min_y, max_x, max_y = bounds
    inset_left, inset_bottom, inset_right, inset_top = margin_insets(margin)
    left, bottom = min_x + inset_left, min_y + inset_bottom
    right, top = max_x - inset_right, max_y - inset_top
    descriptions = []
    for item_id in outside:
        item = items_by_id.get(item_id)
        if not item:
            descriptions.append("Vista ID {}".format(item_id))
            continue
        center = item.get("current_center")
        if not center:
            continue
        rect = placement_rect(item, center)
        sides = []
        if rect[0] < left:
            sides.append("esquerda {:.1f} cm".format(ft_to_cm(left - rect[0])))
        if rect[1] < bottom:
            sides.append("inferior {:.1f} cm".format(ft_to_cm(bottom - rect[1])))
        if rect[2] > right:
            sides.append("direita {:.1f} cm".format(ft_to_cm(rect[2] - right)))
        if rect[3] > top:
            sides.append("superior {:.1f} cm".format(ft_to_cm(rect[3] - top)))
        try:
            scale_text = " | escala 1:{}".format(item["view"].Scale)
        except Exception:
            scale_text = ""
        descriptions.append("{}: {}{}".format(
            item["name"], ", ".join(sides) if sides else "fora da área segura", scale_text))
    return "; ".join(descriptions)


def load_wpf_api():
    import clr
    for assembly in ("WindowsBase", "PresentationCore", "PresentationFramework"):
        try:
            clr.AddReference(assembly)
        except Exception:
            pass
    from System.Windows import (Visibility, DragDrop, DragDropEffects,
                                DataObject, DataFormats)
    from System.Windows.Input import Mouse, MouseButtonState
    return (Visibility, DragDrop, DragDropEffects, DataObject,
            DataFormats, Mouse, MouseButtonState)


def canvas_attached_properties(canvas):
    """Resolve WPF Canvas attached properties across IronPython/Revit versions."""
    try:
        from System.Windows.Controls import Canvas
        return Canvas.LeftProperty, Canvas.TopProperty
    except Exception:
        try:
            from System.Reflection import BindingFlags
            flags = BindingFlags.Public | BindingFlags.Static | BindingFlags.FlattenHierarchy
            canvas_type = canvas.GetType()
            left_field = canvas_type.GetField("LeftProperty", flags)
            top_field = canvas_type.GetField("TopProperty", flags)
            if left_field is None or top_field is None:
                raise Exception("Campos anexados LeftProperty/TopProperty não encontrados.")
            return left_field.GetValue(None), top_field.GetValue(None)
        except Exception as error:
            raise Exception("Não foi possível inicializar o posicionamento do preview WPF: {}".format(
                safe_text(error)))


def parse_cm_input(text, label, maximum):
    try:
        value = float(safe_text(text).replace(",", "."))
        if value < 0 or value > maximum:
            raise ValueError()
        return value
    except Exception:
        raise ValueError("{} deve estar entre 0 e {} cm.".format(label, maximum))


def show_setup_window(active_sheet, candidates, active_items, titleblocks,
                      diagnostics, initial=None):
    """Collect view selection and sheet rules in one branded window."""
    Visibility = load_wpf_api()[0]
    initial = initial or {}
    config = script.get_config()
    xaml = '''<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="Tudo32f | Fólio v2.8" Width="1030" Height="760"
        MinWidth="900" MinHeight="650" ResizeMode="CanResizeWithGrip"
        WindowStartupLocation="CenterOwner" ShowInTaskbar="False"
        Background="#F3F6FA" FontFamily="Segoe UI">
      <Grid>
        <Grid.RowDefinitions><RowDefinition Height="104"/><RowDefinition Height="*"/><RowDefinition Height="74"/></Grid.RowDefinitions>
        <Border Grid.Row="0" Background="#142B45" Padding="26,15">
          <StackPanel VerticalAlignment="Center">
            <TextBlock Text="Tudo32f" Foreground="#8DD7D0" FontSize="12" FontWeight="Bold"/>
            <TextBlock Text="Fólio" Foreground="White" FontSize="25" FontWeight="SemiBold" Margin="0,2,0,0"/>
            <TextBlock Text="Pesquise vistas e tabelas, escolha uma composição e ajuste cada folha na prévia" Foreground="#D7E2ED" FontSize="12" Margin="0,3,0,0"/>
          </StackPanel>
        </Border>
        <Grid Grid.Row="1" Margin="20,16,20,12">
          <Grid.ColumnDefinitions><ColumnDefinition Width="1.05*"/><ColumnDefinition Width="0.95*"/></Grid.ColumnDefinitions>
          <ScrollViewer Grid.Column="0" VerticalScrollBarVisibility="Auto" Margin="0,0,10,0">
            <StackPanel>
              <TextBlock Text="AÇÃO" Foreground="#31506E" FontSize="11" FontWeight="Bold" Margin="0,0,0,7"/>
              <ComboBox x:Name="modeCombo" Height="32" Margin="0,0,0,13"/>
              <Border x:Name="viewSelectionCard" Background="White" BorderBrush="#D8E1EA" BorderThickness="1" CornerRadius="6" Padding="13">
                <StackPanel>
                  <TextBlock Text="VISTAS DISPONÍVEIS" Foreground="#31506E" FontSize="11" FontWeight="Bold" Margin="0,0,0,7"/>
                  <TextBlock Text="Só aparecem vistas e tabelas ainda não colocadas em folhas. Pesquise pelo nome ou tipo e escolha o que quer montar." Foreground="#52667A" FontSize="11" TextWrapping="Wrap" Margin="0,0,0,7"/>
                  <TextBox x:Name="searchInput" Height="30" Padding="7,4" ToolTip="Pesquisar nome ou tipo de vista"/>
                  <ListBox x:Name="viewsList" Height="315" Margin="0,8,0,6" SelectionMode="Extended"/>
                  <StackPanel Orientation="Horizontal" HorizontalAlignment="Left">
                    <Button x:Name="selectFilteredButton" Content="Selecionar filtradas" Height="28" Padding="9,2" Margin="0,0,7,0" Background="White" BorderBrush="#C7D2DE"/>
                    <Button x:Name="clearViewsButton" Content="Limpar seleção" Height="28" Padding="9,2" Background="White" BorderBrush="#C7D2DE"/>
                  </StackPanel>
                  <TextBlock x:Name="selectionCountText" Foreground="#087E83" FontSize="11" FontWeight="SemiBold" Margin="2,8,0,0"/>
                </StackPanel>
              </Border>
              <Border x:Name="activeSheetCard" Background="White" BorderBrush="#D8E1EA" BorderThickness="1" CornerRadius="6" Padding="13" Visibility="Collapsed">
                <StackPanel>
                  <TextBlock Text="FOLHA ATIVA" Foreground="#31506E" FontSize="11" FontWeight="Bold" Margin="0,0,0,7"/>
                  <TextBlock x:Name="activeSheetSummary" Foreground="#243B53" FontSize="13" FontWeight="SemiBold" TextWrapping="Wrap"/>
                  <TextBlock Text="As vistas e tabelas desta folha aparecerão no editor. Arraste-as para reposicionar ou use a organização automática." Foreground="#52667A" FontSize="11" TextWrapping="Wrap" Margin="0,7,0,0"/>
                </StackPanel>
              </Border>
              <Border x:Name="sheetCountCard" Background="White" BorderBrush="#D8E1EA" BorderThickness="1" CornerRadius="6" Padding="13" Margin="0,10,0,0">
                <StackPanel>
                  <TextBlock Text="QUANTIDADE DE PRANCHAS" Foreground="#31506E" FontSize="11" FontWeight="Bold" Margin="0,0,0,5"/>
                  <TextBlock Text="Automático distribui pelas folhas necessárias. Indica um número para controlar a quantidade." Foreground="#52667A" FontSize="10" TextWrapping="Wrap"/>
                  <TextBox x:Name="sheetCountInput" Height="30" Margin="0,7,0,0" Padding="7,4" ToolTip="Escreva Automático ou um número, por exemplo 3"/>
                </StackPanel>
              </Border>
            </StackPanel>
          </ScrollViewer>
          <ScrollViewer Grid.Column="1" VerticalScrollBarVisibility="Auto" Margin="10,0,0,0">
            <StackPanel>
              <Expander Header="Opções avançadas de composição" IsExpanded="False" Margin="0,0,0,8">
              <Border Background="White" BorderBrush="#D8E1EA" BorderThickness="1" CornerRadius="6" Padding="13">
                <StackPanel>
                  <TextBlock Text="Perfil de diagramação" Foreground="#52667A" FontSize="11" Margin="0,0,0,5"/>
                  <Grid>
                    <Grid.ColumnDefinitions><ColumnDefinition/><ColumnDefinition Width="Auto"/></Grid.ColumnDefinitions>
                    <ComboBox x:Name="profileCombo" Height="31" Margin="0,0,7,0"/>
                    <Button x:Name="saveProfileButton" Grid.Column="1" Content="Salvar perfil" Height="31" Padding="8,1" Background="White" BorderBrush="#C7D2DE"/>
                  </Grid>
                  <TextBlock Text="Estratégia de distribuição" Foreground="#52667A" FontSize="11" Margin="0,10,0,5"/>
                  <ComboBox x:Name="layoutCombo" Height="31"/>
                  <TextBlock Text="Escolha uma composição pronta. A opção recomendada organiza automaticamente por tamanho e cria folhas adicionais quando necessário." Foreground="#52667A" FontSize="11" TextWrapping="Wrap" Margin="0,7,0,0"/>
                  <CheckBox x:Name="singleSheetCheck" Content="Usar uma única prancha" Margin="0,10,0,0" Foreground="#243B53" FontSize="11"/>
                  <TextBlock Text="A composição preserva as escalas atuais. Se não couber, ajuste a escala manualmente ou permita mais folhas; o ajuste automático é opcional." Foreground="#52667A" FontSize="10" TextWrapping="Wrap" Margin="18,3,0,0"/>
                  <TextBlock Text="Escalas das vistas" Foreground="#52667A" FontSize="11" Margin="0,13,0,5"/>
                  <CheckBox x:Name="keepScalesCheck" Content="Manter as escalas atuais" Margin="0,0,0,5" Foreground="#243B53" FontSize="11" ToolTip="Marcado, o Fólio preserva as escalas e bloqueia alterações. Desmarcado, pode mudar as escalas na prévia."/>
                  <TextBlock Text="Ajuste inicial (se desmarcado)" Foreground="#52667A" FontSize="10" Margin="0,1,0,4"/>
                  <ComboBox x:Name="scaleModeCombo" Height="31"/>
                  <Grid Margin="0,8,0,0">
                    <Grid.ColumnDefinitions><ColumnDefinition Width="1.05*"/><ColumnDefinition Width="0.95*"/></Grid.ColumnDefinitions>
                    <StackPanel Grid.Column="0" Margin="0,0,8,0">
                      <TextBlock Text="Escala personalizada (1:x)" Foreground="#52667A" FontSize="10"/>
                      <TextBox x:Name="customScaleInput" Height="30" Margin="0,4,0,0" Padding="7,4" ToolTip="Informe o denominador, por exemplo 75 para 1:75"/>
                    </StackPanel>
                    <TextBlock x:Name="scaleHelpText" Grid.Column="1" VerticalAlignment="Bottom" Foreground="#52667A" FontSize="10" TextWrapping="Wrap" Margin="4,0,0,3"/>
                  </Grid>
                  <TextBlock Text="Escalas preferidas por grupo (denominador 1:x)" Foreground="#52667A" FontSize="11" Margin="0,12,0,4"/>
                  <Grid>
                    <Grid.ColumnDefinitions><ColumnDefinition/><ColumnDefinition/><ColumnDefinition/></Grid.ColumnDefinitions>
                    <StackPanel Grid.Column="0" Margin="0,0,5,0"><TextBlock Text="Plantas" Foreground="#52667A" FontSize="10"/><TextBox x:Name="planScaleInput" Height="28" Padding="6,3"/></StackPanel>
                    <StackPanel Grid.Column="1" Margin="3,0,3,0"><TextBlock Text="Cortes/alçados" Foreground="#52667A" FontSize="10"/><TextBox x:Name="sectionScaleInput" Height="28" Padding="6,3"/></StackPanel>
                    <StackPanel Grid.Column="2" Margin="5,0,0,0"><TextBlock Text="Detalhes" Foreground="#52667A" FontSize="10"/><TextBox x:Name="detailScaleInput" Height="28" Padding="6,3"/></StackPanel>
                  </Grid>
                  <TextBlock Text="A escala alterada é da vista do projeto e reflete em todas as folhas onde ela aparece. O preview valida a ocupação antes de confirmar." Foreground="#7A5A24" FontSize="10" TextWrapping="Wrap" Margin="0,8,0,0"/>
                  <Grid Margin="0,13,0,0">
                    <Grid.ColumnDefinitions><ColumnDefinition/><ColumnDefinition/></Grid.ColumnDefinitions>
                    <StackPanel Grid.Column="0" Margin="0,0,8,0">
                      <TextBlock Text="Margem segura (cm)" Foreground="#52667A" FontSize="11"/>
                      <TextBox x:Name="marginInput" Height="30" Margin="0,4,0,0" Padding="7,4"/>
                    </StackPanel>
                    <StackPanel Grid.Column="1" Margin="8,0,0,0">
                      <TextBlock Text="Espaço entre vistas (cm)" Foreground="#52667A" FontSize="11"/>
                      <TextBox x:Name="gapInput" Height="30" Margin="0,4,0,0" Padding="7,4"/>
                    </StackPanel>
                  </Grid>
                </StackPanel>
              </Border>
              </Expander>
              <Border x:Name="sheetCreationCard" Background="White" BorderBrush="#D8E1EA" BorderThickness="1" CornerRadius="6" Padding="13" Margin="0,12,0,0">
                <StackPanel>
                  <TextBlock Text="NOVAS PRANCHAS" Foreground="#31506E" FontSize="11" FontWeight="Bold" Margin="0,0,0,7"/>
                  <TextBlock Text="Família carregada em Blocos de Margens e Carimbos" Foreground="#52667A" FontSize="11" TextWrapping="Wrap"/>
                  <ComboBox x:Name="titleblockCombo" Height="31" Margin="0,4,0,10"/>
                  <TextBlock Text="As novas folhas usarão este tipo já carregado. O Fólio não cria nem carrega famílias." Foreground="#52667A" FontSize="10" TextWrapping="Wrap" Margin="0,0,0,8"/>
                  <TextBlock Text="Área reservada do carimbo além da margem geral (cm)" Foreground="#52667A" FontSize="10" TextWrapping="Wrap"/>
                  <Expander Header="Ajustar área livre do carimbo" IsExpanded="False" Margin="0,2,0,8">
                  <StackPanel>
                  <Grid Margin="0,4,0,8">
                    <Grid.ColumnDefinitions><ColumnDefinition/><ColumnDefinition/><ColumnDefinition/><ColumnDefinition/></Grid.ColumnDefinitions>
                    <StackPanel Grid.Column="0" Margin="0,0,3,0"><TextBlock Text="Esquerda" FontSize="9"/><TextBox x:Name="reserveLeftInput" Height="26" Padding="5,2"/></StackPanel>
                    <StackPanel Grid.Column="1" Margin="2,0,2,0"><TextBlock Text="Direita" FontSize="9"/><TextBox x:Name="reserveRightInput" Height="26" Padding="5,2"/></StackPanel>
                    <StackPanel Grid.Column="2" Margin="2,0,2,0"><TextBlock Text="Topo" FontSize="9"/><TextBox x:Name="reserveTopInput" Height="26" Padding="5,2"/></StackPanel>
                    <StackPanel Grid.Column="3" Margin="3,0,0,0"><TextBlock Text="Base" FontSize="9"/><TextBox x:Name="reserveBottomInput" Height="26" Padding="5,2"/></StackPanel>
                  </Grid>
                  </StackPanel>
                  </Expander>
                  <Grid>
                    <Grid.ColumnDefinitions><ColumnDefinition/><ColumnDefinition/></Grid.ColumnDefinitions>
                    <StackPanel Grid.Column="0" Margin="0,0,8,0">
                      <TextBlock Text="Prefixo do número" Foreground="#52667A" FontSize="11"/>
                      <TextBox x:Name="numberPrefixInput" Height="30" Margin="0,4,0,0" Padding="7,4"/>
                    </StackPanel>
                    <StackPanel Grid.Column="1" Margin="8,0,0,0">
                      <TextBlock Text="Primeiro número" Foreground="#52667A" FontSize="11"/>
                      <TextBox x:Name="numberStartInput" Height="30" Margin="0,4,0,0" Padding="7,4"/>
                    </StackPanel>
                  </Grid>
                  <TextBlock Text="Título das folhas" Foreground="#52667A" FontSize="11" Margin="0,10,0,0"/>
                  <TextBox x:Name="sheetTitleInput" Height="30" Margin="0,4,0,0" Padding="7,4"/>
                </StackPanel>
              </Border>
              <TextBlock x:Name="setupStatusText" Foreground="#52667A" FontSize="11" TextWrapping="Wrap" Margin="3,11,3,0"/>
            </StackPanel>
          </ScrollViewer>
        </Grid>
        <Border Grid.Row="2" Background="White" BorderBrush="#D8E1EA" BorderThickness="0,1,0,0" Padding="20,11">
          <Grid>
            <TextBlock Text="A próxima janela permite arrastar, distribuir e validar antes de aplicar no Revit." VerticalAlignment="Center" Foreground="#52667A" FontSize="11" Margin="0,0,215,0" TextWrapping="Wrap"/>
            <StackPanel Orientation="Horizontal" HorizontalAlignment="Right" VerticalAlignment="Center">
              <Button x:Name="cancelButton" Content="Cancelar" Width="84" Height="34" Margin="0,0,8,0" Background="White" BorderBrush="#C7D2DE"/>
              <Button x:Name="previewButton" Content="Abrir prévia" Width="112" Height="34" Background="#087E83" Foreground="White" FontWeight="SemiBold" BorderThickness="0"/>
            </StackPanel>
          </Grid>
        </Border>
      </Grid>
    </Window>'''

    window = forms.WPFWindow(xaml, literal_string=True, handle_esc=True, set_owner=True)
    mode_options = []
    if titleblocks:
        mode_options.append("Criar pranchas e distribuir vistas")
    if active_sheet is not None:
        mode_options.append("Organizar a folha ativa")
    if not mode_options:
        forms.alert("Não encontrei famílias/tipos de carimbo carregados em Blocos de Margens e Carimbos. Carregue a família no projeto e tente novamente.",
                    title=TITLE, warn_icon=True)
        return None
    for option in mode_options:
        window.modeCombo.Items.Add(option)
    initial_mode = initial.get("mode") or safe_text(getattr(config, "last_mode", mode_options[0]))
    if initial_mode not in mode_options:
        initial_mode = mode_options[0]
    window.modeCombo.SelectedItem = initial_mode

    for layout in LAYOUTS:
        window.layoutCombo.Items.Add(layout)
    default_layout = initial.get("layout") or safe_text(
        getattr(config, "layout_style", LAYOUTS[0]))
    window.layoutCombo.SelectedItem = default_layout if default_layout in LAYOUTS else LAYOUTS[0]
    for scale_option in SCALE_OPTIONS:
        window.scaleModeCombo.Items.Add(scale_option)
    saved_scale_mode = initial.get("scale_mode") or safe_text(
        getattr(config, "scale_mode", SCALE_KEEP))
    keep_default = initial.get("keep_scales")
    if keep_default is None:
        keep_default = getattr(config, "keep_scales", saved_scale_mode == SCALE_KEEP)
    window.keepScalesCheck.IsChecked = bool(keep_default)
    if saved_scale_mode == SCALE_KEEP:
        saved_scale_mode = SCALE_MANUAL
    window.scaleModeCombo.SelectedItem = (
        saved_scale_mode if saved_scale_mode in SCALE_OPTIONS else SCALE_MANUAL)
    window.customScaleInput.Text = safe_text(initial.get(
        "custom_scale", getattr(config, "custom_scale", "100")))
    preferred_defaults = {"plan": 100, "section": 100, "detail": 20}
    try:
        stored_preferences = json.loads(safe_text(getattr(config, "preferred_scales_json", "{}")))
        if isinstance(stored_preferences, dict):
            preferred_defaults.update(stored_preferences)
    except Exception:
        pass
    window.planScaleInput.Text = safe_text(initial.get("plan_scale", preferred_defaults["plan"]))
    window.sectionScaleInput.Text = safe_text(initial.get("section_scale", preferred_defaults["section"]))
    window.detailScaleInput.Text = safe_text(initial.get("detail_scale", preferred_defaults["detail"]))
    window.singleSheetCheck.IsChecked = bool(initial.get(
        "single_sheet", getattr(config, "single_sheet", False)))
    window.sheetCountInput.Text = safe_text(initial.get(
        "requested_sheet_count", getattr(config, "requested_sheet_count", "Automático")) or "Automático")

    profile_map = dict((name, dict(profile)) for name, profile in BUILT_IN_PROFILES.items())
    custom_profiles = {}
    profile_state = {"applying": False}
    try:
        stored_profiles = json.loads(safe_text(getattr(config, "layout_profiles_json", "{}")))
        if isinstance(stored_profiles, dict):
            for name, profile in stored_profiles.items():
                if isinstance(profile, dict) and profile.get("layout") in LAYOUTS:
                    custom_profiles[safe_text(name)] = profile
                    profile_map[safe_text(name)] = profile
    except Exception:
        custom_profiles = {}
    window.profileCombo.Items.Add("Personalizado")
    for profile_name in sorted(profile_map.keys()):
        window.profileCombo.Items.Add(profile_name)
    initial_profile = initial.get("profile_name") or safe_text(
        getattr(config, "last_profile", "Personalizado"))
    window.profileCombo.SelectedItem = initial_profile if initial_profile in profile_map else "Personalizado"

    def update_scale_controls(sender=None, args=None):
        keep_scales = bool(window.keepScalesCheck.IsChecked)
        selected_mode = safe_text(window.scaleModeCombo.SelectedItem)
        custom_selected = selected_mode == SCALE_CUSTOM
        auto_selected = selected_mode == SCALE_AUTO
        window.scaleModeCombo.IsEnabled = not keep_scales
        window.customScaleInput.IsEnabled = not keep_scales and custom_selected
        preferred_enabled = not keep_scales and auto_selected
        window.planScaleInput.IsEnabled = preferred_enabled
        window.sectionScaleInput.IsEnabled = preferred_enabled
        window.detailScaleInput.IsEnabled = preferred_enabled
        if keep_scales:
            window.scaleHelpText.Text = "As escalas atuais serão preservadas; desmarque para permitir alterações na prévia."
        elif custom_selected:
            window.scaleHelpText.Text = "Digite o denominador. Ex.: 50 = 1:50."
        elif auto_selected:
            window.scaleHelpText.Text = "Escolhe a maior escala métrica que cabe na área útil."
        elif selected_mode == SCALE_MANUAL:
            window.scaleHelpText.Text = "Mantém a escala inicial; use Alterar escala na prévia se quiser mudar."
        else:
            window.scaleHelpText.Text = "Aplica a escala escolhida às vistas compatíveis."
    def apply_profile(sender=None, args=None):
        profile_name = safe_text(window.profileCombo.SelectedItem)
        profile = profile_map.get(profile_name)
        if not profile:
            return
        profile_state["applying"] = True
        try:
            if profile.get("layout") in LAYOUTS:
                window.layoutCombo.SelectedItem = profile.get("layout")
            profile_scale_mode = profile.get("scale_mode", SCALE_KEEP)
            profile_keep_scales = profile.get(
                "keep_scales", profile_scale_mode == SCALE_KEEP)
            window.keepScalesCheck.IsChecked = bool(profile_keep_scales)
            if profile_scale_mode == SCALE_KEEP:
                profile_scale_mode = SCALE_MANUAL
            if profile_scale_mode in SCALE_OPTIONS:
                window.scaleModeCombo.SelectedItem = profile_scale_mode
            window.singleSheetCheck.IsChecked = bool(profile.get("single_sheet", False))
            window.marginInput.Text = safe_text(profile.get("margin_cm", 2.5))
            window.gapInput.Text = safe_text(profile.get("gap_cm", 1.5))
            window.customScaleInput.Text = safe_text(profile.get("custom_scale", 100))
            update_scale_controls()
        finally:
            profile_state["applying"] = False

    def mark_profile_custom(sender=None, args=None):
        if not profile_state["applying"] and safe_text(window.profileCombo.SelectedItem) != "Personalizado":
            window.profileCombo.SelectedItem = "Personalizado"

    def on_save_profile(sender, args):
        try:
            profile_name = forms.ask_for_string(
                default="", prompt="Nome para esta composição personalizada:", title=TITLE)
            if profile_name is None:
                return
            profile_name = safe_text(profile_name).strip()
            if not profile_name:
                return
            profile_data = {
                "layout": safe_text(window.layoutCombo.SelectedItem),
                "scale_mode": (SCALE_KEEP if bool(window.keepScalesCheck.IsChecked)
                               else safe_text(window.scaleModeCombo.SelectedItem)),
                "keep_scales": bool(window.keepScalesCheck.IsChecked),
                "single_sheet": bool(window.singleSheetCheck.IsChecked),
                "margin_cm": parse_cm_input(window.marginInput.Text, "Margem segura", 30),
                "gap_cm": parse_cm_input(window.gapInput.Text, "Espaço entre vistas", 20),
                "custom_scale": safe_text(window.customScaleInput.Text or "100")
            }
            custom_profiles[profile_name] = profile_data
            profile_map[profile_name] = profile_data
            config.layout_profiles_json = json.dumps(custom_profiles, sort_keys=True)
            config.last_profile = profile_name
            script.save_config()
            if profile_name not in [safe_text(window.profileCombo.Items[i])
                                    for i in range(window.profileCombo.Items.Count)]:
                window.profileCombo.Items.Add(profile_name)
            window.profileCombo.SelectedItem = profile_name
            window.setupStatusText.Text = "Perfil '{}' guardado para reutilizar neste Revit.".format(profile_name)
        except Exception as error:
            window.setupStatusText.Text = "Não foi possível guardar o perfil: {}".format(safe_text(error))

    window.profileCombo.SelectionChanged += apply_profile
    window.saveProfileButton.Click += on_save_profile
    update_scale_controls()
    window.marginInput.Text = safe_text(initial.get(
        "margin_cm", getattr(config, "safe_margin_cm", 2.5)))
    window.gapInput.Text = safe_text(initial.get(
        "gap_cm", getattr(config, "view_gap_cm", 1.5)))
    if initial_profile in profile_map:
        apply_profile()
    window.layoutCombo.SelectionChanged += mark_profile_custom
    window.scaleModeCombo.SelectionChanged += mark_profile_custom
    window.keepScalesCheck.Click += mark_profile_custom
    window.singleSheetCheck.Click += mark_profile_custom
    window.marginInput.TextChanged += mark_profile_custom
    window.gapInput.TextChanged += mark_profile_custom
    window.customScaleInput.TextChanged += mark_profile_custom
    window.numberPrefixInput.Text = safe_text(initial.get(
        "number_prefix", getattr(config, "sheet_prefix", "A-")))
    window.numberStartInput.Text = safe_text(initial.get(
        "number_start", getattr(config, "sheet_number_start", "101")))
    window.sheetTitleInput.Text = safe_text(initial.get(
        "sheet_title", getattr(config, "sheet_title", "Documentação")))

    titleblock_by_label = {}
    for label, symbol in titleblocks:
        window.titleblockCombo.Items.Add(label)
        titleblock_by_label[label] = symbol
    saved_type_id = safe_text(initial.get("titleblock_id", getattr(config, "titleblock_id", "")))
    selected_titleblock = next((label for label, symbol in titleblock_by_label.items()
        if id_value(symbol.Id) == saved_type_id), None)
    if selected_titleblock:
        window.titleblockCombo.SelectedItem = selected_titleblock
    elif window.titleblockCombo.Items.Count:
        window.titleblockCombo.SelectedIndex = 0

    safe_area_map = {}
    try:
        stored_safe_areas = json.loads(safe_text(getattr(config, "titleblock_safe_areas_json", "{}")))
        if isinstance(stored_safe_areas, dict):
            safe_area_map = stored_safe_areas
    except Exception:
        safe_area_map = {}
    safe_area_state = {"loading": False}

    def load_titleblock_safe_area(sender=None, args=None):
        label = safe_text(window.titleblockCombo.SelectedItem)
        symbol = titleblock_by_label.get(label)
        values = safe_area_map.get(id_value(symbol.Id), {}) if symbol else {}
        safe_area_state["loading"] = True
        try:
            window.reserveLeftInput.Text = safe_text(values.get("left", 0))
            window.reserveRightInput.Text = safe_text(values.get("right", 0))
            window.reserveTopInput.Text = safe_text(values.get("top", 0))
            window.reserveBottomInput.Text = safe_text(values.get("bottom", 0))
        finally:
            safe_area_state["loading"] = False

    def save_titleblock_safe_area(sender=None, args=None):
        if safe_area_state["loading"]:
            return
        label = safe_text(window.titleblockCombo.SelectedItem)
        symbol = titleblock_by_label.get(label)
        if not symbol:
            return
        try:
            safe_area_map[id_value(symbol.Id)] = {
                "left": parse_cm_input(window.reserveLeftInput.Text, "Reserva esquerda", 100),
                "right": parse_cm_input(window.reserveRightInput.Text, "Reserva direita", 100),
                "top": parse_cm_input(window.reserveTopInput.Text, "Reserva superior", 100),
                "bottom": parse_cm_input(window.reserveBottomInput.Text, "Reserva inferior", 100)
            }
        except Exception:
            return
    window.titleblockCombo.SelectionChanged += load_titleblock_safe_area
    for reserve_input in (window.reserveLeftInput, window.reserveRightInput,
                          window.reserveTopInput, window.reserveBottomInput):
        reserve_input.TextChanged += save_titleblock_safe_area
    load_titleblock_safe_area()

    items_by_mode = {"create": candidates, "active": active_items}
    labels_to_item = {"create": {}, "active": {}}
    chosen_ids = {
        "create": set(initial.get("selected_ids", [])),
        "active": set(item["id"] for item in active_items)
    }
    filter_state = {"rebuilding": False}

    def current_mode_key():
        return "active" if safe_text(window.modeCombo.SelectedItem).startswith("Organizar") else "create"

    def visible_items():
        key = current_mode_key()
        if key == "active":
            return active_items
        query = safe_text(window.searchInput.Text).strip().lower()
        if not query:
            return candidates
        return [item for item in candidates
                if query in item.get("search_text",
                    (item["name"] + " " + item["kind"]).lower())]

    def update_selection_count():
        key = current_mode_key()
        if key == "active":
            window.activeSheetSummary.Text = "{} | {} elemento(s) existente(s)".format(
                active_sheet.Name, len(active_items))
            return
        window.selectionCountText.Text = "Selecionados: {} de {} elemento(s) filtrado(s)".format(
            len(chosen_ids["create"]), len(visible_items()))

    def capture_visible_selection():
        if filter_state["rebuilding"] or current_mode_key() != "create":
            return
        visible = visible_items()
        visible_ids = set(item["id"] for item in visible)
        chosen_ids["create"].difference_update(visible_ids)
        for label in list(window.viewsList.SelectedItems):
            item = labels_to_item["create"].get(safe_text(label))
            if item:
                chosen_ids["create"].add(item["id"])

    def refresh_list():
        key = current_mode_key()
        filter_state["rebuilding"] = True
        try:
            window.viewsList.Items.Clear()
            labels_to_item[key] = {}
            if key == "active":
                window.viewSelectionCard.Visibility = Visibility.Collapsed
                window.activeSheetCard.Visibility = Visibility.Visible
                update_selection_count()
                return
            window.viewSelectionCard.Visibility = Visibility.Visible
            window.activeSheetCard.Visibility = Visibility.Collapsed
            for item in visible_items():
                label = item["label"]
                labels_to_item["create"][label] = item
                window.viewsList.Items.Add(label)
                if item["id"] in chosen_ids["create"]:
                    window.viewsList.SelectedItems.Add(label)
            update_selection_count()
        finally:
            filter_state["rebuilding"] = False

    def update_mode(sender, args):
        is_create = current_mode_key() == "create"
        window.sheetCreationCard.IsEnabled = is_create
        window.sheetCountCard.Visibility = (
            Visibility.Visible if is_create else Visibility.Collapsed)
        window.singleSheetCheck.IsEnabled = is_create
        window.sheetCreationCard.Visibility = (
            Visibility.Visible if is_create else Visibility.Collapsed)
        if is_create:
            window.setupStatusText.Text = (
                "{} elemento(s) pronto(s) para pesquisa.".format(len(candidates)) if candidates else
                "Não encontrei vistas ou tabelas não colocadas que possam ser organizadas.")
            if diagnostics:
                window.setupStatusText.Text += " {} vista(s) não entraram na lista porque não foi possível calcular dimensões em papel.".format(
                    len(diagnostics))
        else:
            schedule_count = 0
            try:
                schedule_count = len(list(DB.FilteredElementCollector(doc, active_sheet.Id)
                    .OfClass(DB.ScheduleSheetInstance)))
            except Exception:
                pass
            window.setupStatusText.Text = (
                "A organização inclui vistas e tabelas regulares. {} tabela(s) já estava(m) nesta folha."
                .format(schedule_count))
        refresh_list()

    def on_filter(sender, args):
        capture_visible_selection()
        refresh_list()

    def on_selection_changed(sender, args):
        capture_visible_selection()
        update_selection_count()

    def on_select_filtered(sender, args):
        if current_mode_key() != "create":
            return
        chosen_ids["create"].update(item["id"] for item in visible_items())
        refresh_list()

    def on_clear_views(sender, args):
        if current_mode_key() != "create":
            return
        chosen_ids["create"].clear()
        refresh_list()

    result = {"accepted": False, "action": "cancel"}

    def _on_preview(sender, args):
        key = current_mode_key()
        if key == "create":
            capture_visible_selection()
            selected = [item for item in candidates if item["id"] in chosen_ids["create"]]
            if not selected:
                report_setup_issue("Selecione pelo menos uma vista para montar a prévia.")
                return
            if not titleblocks:
                report_setup_issue("Não encontrei uma família de carimbo carregada no projeto.")
                return
            title_label = safe_text(window.titleblockCombo.SelectedItem)
            titleblock = titleblock_by_label.get(title_label)
            if titleblock is None:
                report_setup_issue("Escolha uma família/tipo de carimbo carregado no projeto.")
                return
        else:
            selected = list(active_items)
            if not selected:
                report_setup_issue("A folha ativa não contém viewports para organizar.")
                return
            titleblock = None
            active_titleblock_id = None
            try:
                titleblock_instances = (DB.FilteredElementCollector(doc, active_sheet.Id)
                                        .OfCategory(DB.BuiltInCategory.OST_TitleBlocks)
                                        .WhereElementIsNotElementType())
                for titleblock_instance in titleblock_instances:
                    active_titleblock_id = id_value(titleblock_instance.GetTypeId())
                    break
            except Exception:
                pass
        selected = apply_auto_hierarchy(selected)
        try:
            safe_area_id = id_value(titleblock.Id) if titleblock else active_titleblock_id
            if titleblock:
                reserve_values = {
                    "left": parse_cm_input(window.reserveLeftInput.Text, "Reserva esquerda", 100),
                    "right": parse_cm_input(window.reserveRightInput.Text, "Reserva direita", 100),
                    "top": parse_cm_input(window.reserveTopInput.Text, "Reserva superior", 100),
                    "bottom": parse_cm_input(window.reserveBottomInput.Text, "Reserva inferior", 100)
                }
                safe_area_map[safe_area_id] = reserve_values
            else:
                reserve_values = safe_area_map.get(safe_area_id, {}) if safe_area_id else {}
            margin_cm = parse_cm_input(window.marginInput.Text, "Margem segura", 30)
            gap_cm = parse_cm_input(window.gapInput.Text, "Espaço entre vistas", 20)
            margin_insets_cm = (
                margin_cm + float(reserve_values.get("left", 0)),
                margin_cm + float(reserve_values.get("bottom", 0)),
                margin_cm + float(reserve_values.get("right", 0)),
                margin_cm + float(reserve_values.get("top", 0)))
            preferred_scales = {
                "floorplan": int(safe_text(window.planScaleInput.Text)),
                "engineeringplan": int(safe_text(window.planScaleInput.Text)),
                "ceilingplan": int(safe_text(window.planScaleInput.Text)),
                "area plan": int(safe_text(window.planScaleInput.Text)),
                "structuralplan": int(safe_text(window.planScaleInput.Text)),
                "section": int(safe_text(window.sectionScaleInput.Text)),
                "elevation": int(safe_text(window.sectionScaleInput.Text)),
                "detail": int(safe_text(window.detailScaleInput.Text)),
                "draftingview": int(safe_text(window.detailScaleInput.Text)),
                "legend": int(safe_text(window.detailScaleInput.Text))
            }
            if any(value < 1 or value > MAX_VIEW_SCALE for value in preferred_scales.values()):
                raise ValueError("As escalas preferidas devem estar entre 1 e 24000 no Revit 2024.")
            keep_scales = bool(window.keepScalesCheck.IsChecked)
            scale_mode = (SCALE_KEEP if keep_scales else
                          safe_text(window.scaleModeCombo.SelectedItem))
            if not scale_mode:
                scale_mode = SCALE_MANUAL
            custom_scale = None
            if scale_mode == SCALE_CUSTOM:
                custom_text = safe_text(window.customScaleInput.Text).strip()
                if not custom_text.isdigit():
                    raise ValueError("Informe o denominador da escala personalizada usando um número inteiro.")
                custom_scale = int(custom_text)
                if custom_scale < 1 or custom_scale > MAX_VIEW_SCALE:
                    raise ValueError("O denominador personalizado deve estar entre 1 e 24000 no Revit 2024.")
            single_sheet = key == "create" and bool(window.singleSheetCheck.IsChecked)
            requested_sheet_count = None
            if key == "create":
                count_text = safe_text(window.sheetCountInput.Text).strip().lower()
                if count_text not in ("", "auto", "automático", "automatico"):
                    if not count_text.isdigit():
                        raise ValueError("Indique 'Automático' ou um número inteiro de folhas.")
                    requested_sheet_count = int(count_text)
                    if requested_sheet_count < 1 or requested_sheet_count > len(selected):
                        raise ValueError("O número de folhas deve estar entre 1 e o número de vistas selecionadas ({}).".format(
                            len(selected)))
                    single_sheet = requested_sheet_count == 1
            number_start = int(safe_text(window.numberStartInput.Text).strip())
            if number_start < 0:
                raise ValueError("O número inicial não pode ser negativo.")
            if key == "create" and not safe_text(window.sheetTitleInput.Text).strip():
                raise ValueError("Informe o título das folhas.")
        except Exception as error:
            report_setup_issue(safe_text(error))
            return
        if key == "create":
            prefix = safe_text(window.numberPrefixInput.Text).strip()
            sheet_title = safe_text(window.sheetTitleInput.Text).strip()
            try:
                config.titleblock_id = id_value(titleblock.Id)
                config.sheet_prefix = prefix
                config.sheet_number_start = str(number_start)
                config.sheet_title = sheet_title
                config.layout_style = safe_text(window.layoutCombo.SelectedItem)
                config.safe_margin_cm = str(margin_cm)
                config.view_gap_cm = str(gap_cm)
                config.scale_mode = scale_mode
                config.keep_scales = keep_scales
                config.custom_scale = str(custom_scale or 100)
                config.single_sheet = single_sheet
                config.requested_sheet_count = str(requested_sheet_count or "")
                config.preferred_scales_json = json.dumps({
                    "plan": preferred_scales["floorplan"],
                    "section": preferred_scales["section"],
                    "detail": preferred_scales["detail"]})
                config.titleblock_safe_areas_json = json.dumps(safe_area_map, sort_keys=True)
                config.last_profile = safe_text(window.profileCombo.SelectedItem)
                config.last_mode = safe_text(window.modeCombo.SelectedItem)
                script.save_config()
            except Exception:
                pass
        else:
            prefix, sheet_title = "", ""
            try:
                config.scale_mode = scale_mode
                config.keep_scales = keep_scales
                config.custom_scale = str(custom_scale or 100)
                config.preferred_scales_json = json.dumps({
                    "plan": preferred_scales["floorplan"],
                    "section": preferred_scales["section"],
                    "detail": preferred_scales["detail"]})
                script.save_config()
            except Exception:
                pass
        result.update({
            "accepted": True,
            "action": "confirm",
            "mode": "active" if key == "active" else "create",
            "items": selected,
            "active_sheet": active_sheet if key == "active" else None,
            "titleblock": titleblock,
            "titleblock_id": titleblock.Id if titleblock else None,
            "titleblock_label": safe_text(window.titleblockCombo.SelectedItem) if key == "create" else "",
            "layout": safe_text(window.layoutCombo.SelectedItem),
            "scale_mode": scale_mode,
            "keep_scales": keep_scales,
            "custom_scale": custom_scale,
            "single_sheet": single_sheet,
            "requested_sheet_count": requested_sheet_count,
            "profile_name": safe_text(window.profileCombo.SelectedItem),
            "margin_cm": margin_cm,
            "reserve_values": reserve_values if key == "create" else {},
            "margin_insets_cm": margin_insets_cm,
            "preferred_scales": preferred_scales,
            "gap_cm": gap_cm,
            "margin_ft": tuple(cm_to_ft(value) for value in margin_insets_cm),
            "gap_ft": cm_to_ft(gap_cm),
            "prefix": prefix,
            "number_start": number_start,
            "sheet_title": sheet_title,
            "selected_ids": [item["id"] for item in selected]
        })
        window.Close()

    def report_setup_issue(message):
        window.setupStatusText.Text = safe_text(message)
        try:
            forms.alert(safe_text(message), title=TITLE, warn_icon=True)
        except Exception:
            pass

    def on_preview(sender, args):
        window.previewButton.IsEnabled = False
        window.previewButton.Content = "Preparando..."
        try:
            _on_preview(sender, args)
            if not result["accepted"]:
                window.previewButton.IsEnabled = True
                window.previewButton.Content = "Abrir prévia"
        except Exception as error:
            window.previewButton.IsEnabled = True
            window.previewButton.Content = "Abrir prévia"
            report_setup_issue("Não foi possível preparar a prévia: {}".format(safe_text(error)))

    window.modeCombo.SelectionChanged += update_mode
    window.layoutCombo.SelectionChanged += lambda sender, args: None
    window.scaleModeCombo.SelectionChanged += update_scale_controls
    window.keepScalesCheck.Click += update_scale_controls
    window.searchInput.TextChanged += on_filter
    window.viewsList.SelectionChanged += on_selection_changed
    window.selectFilteredButton.Click += on_select_filtered
    window.clearViewsButton.Click += on_clear_views
    window.previewButton.Click += on_preview
    window.cancelButton.Click += lambda sender, args: window.Close()
    update_mode(None, None)
    window.ShowDialog()
    return result if result["accepted"] else None


def initial_preview_state(setup):
    items = setup["items"]
    if setup["mode"] == "active":
        placements = {}
        for item in items:
            center = item.get("current_center")
            if center:
                placements[item["id"]] = {"page": 0, "center": center}
        return {"pages": [{"placements": placements}], "page_count": 1,
                "unplaced": set(), "items": items}
    if setup["layout"] == MANUAL_LAYOUT:
        return {"pages": [{"placements": {}}],
                "page_count": setup.get("requested_sheet_count") or 1,
                "unplaced": set(item["id"] for item in items), "items": items}
    requested_count = setup.get("requested_sheet_count")
    if requested_count:
        pages, unplaced = layout_fixed_page_count(
            items, setup["bounds"], setup["margin_ft"], setup["gap_ft"],
            setup["layout"], requested_count)
    else:
        pages, unplaced = layout_pages(
            items, setup["bounds"], setup["margin_ft"], setup["gap_ft"],
            setup["layout"], allow_multiple=(setup["mode"] == "create" and
                                              not setup.get("single_sheet")))
    placements = {}
    for page_index, page in enumerate(pages):
        for item_id, center in page["placements"].items():
            placements[item_id] = {"page": page_index, "center": center}
    return {"pages": [{"placements": placements},], "page_count": max(1, len(pages)),
            "unplaced": set(unplaced), "items": items, "layout_pages": pages}


def analyze_preview_state(state, setup):
    items_by_id = dict((item["id"], item) for item in state["items"])
    by_page = []
    page_count = max([state.get("page_count", 1)] +
        [entry["page"] + 1 for entry in state["pages"][0]["placements"].values()])
    for unused_index in range(page_count):
        by_page.append({})
    for item_id, entry in state["pages"][0]["placements"].items():
        if entry["page"] < len(by_page):
            by_page[entry["page"]][item_id] = entry["center"]
    outside, overlaps = [], []
    for page_index, placements in enumerate(by_page):
        page_outside, page_overlaps = page_diagnostics(
            items_by_id, placements, setup["bounds"], setup["margin_ft"])
        outside.extend((page_index, item_id) for item_id in page_outside)
        overlaps.extend((page_index, pair) for pair in page_overlaps)
    unplaced = set(item["id"] for item in state["items"])
    unplaced.difference_update(state["pages"][0]["placements"].keys())
    return by_page, outside, overlaps, unplaced


def compose_page_diagnostic(items, placements, outside, overlaps, page_index,
                            setup, sheet_label):
    """Explain geometric fit and evidence limits without certifying print legibility."""
    items_by_id = dict((item["id"], item) for item in items)
    page_ids = set(placements.keys())
    page_outside = [item_id for index, item_id in outside if index == page_index]
    page_overlaps = [pair for index, pair in overlaps if index == page_index]
    usable_w = max(1e-6, setup["bounds"][2] - setup["bounds"][0] -
                   margin_insets(setup["margin_ft"])[0] - margin_insets(setup["margin_ft"])[2])
    usable_h = max(1e-6, setup["bounds"][3] - setup["bounds"][1] -
                   margin_insets(setup["margin_ft"])[1] - margin_insets(setup["margin_ft"])[3])
    usable_area = usable_w * usable_h
    page_items = [items_by_id[item_id] for item_id in page_ids if item_id in items_by_id]
    principal = next((item for item in page_items
                      if item.get("layout_role") == "principal"), None)
    principal_assigned = principal is not None
    if principal is None:
        principal = suggest_principal_view(page_items)
    panoramas = [item for item in page_items if _is_wide_section(item)]
    panorama_ids = set(item["id"] for item in panoramas)
    supports = [item for item in page_items
                if not item.get("is_schedule") and
                (item.get("layout_role") == "support" or
                 (item.get("layout_role") != "principal" and
                  (principal is None or item["id"] != principal["id"]) and
                  item["id"] not in panorama_ids))]
    small_supports = [item for item in supports
                      if item.get("layout_width", 0.0) * item.get("layout_height", 0.0)
                      / usable_area < 0.05]

    scale_items = [item for item in page_items if not item.get("is_schedule")]
    scale_missing = [item for item in scale_items
                     if not item.get("planned_scale") and not item.get("current_scale")]
    annotation_small = annotation_large = annotation_unavailable = 0
    annotation_unmeasured = measured = 0
    for item in page_items:
        info = item.get("annotation_diagnostics", {})
        annotation_small += info.get("small", 0)
        annotation_large += info.get("large", 0)
        annotation_unmeasured += info.get("unmeasured", 0)
        if not info.get("available", False):
            annotation_unavailable += 1
        else:
            measured += sum(info.get("counts", {}).values())

    insets = margin_insets(setup["margin_ft"])
    safe_left = setup["bounds"][0] + insets[0]
    safe_bottom = setup["bounds"][1] + insets[1]
    quality = composition_quality_metrics(
        items_by_id, placements,
        (safe_left, safe_bottom, safe_left + usable_w, safe_bottom + usable_h),
        setup.get("gap_ft", 0.0), len(page_outside), len(page_overlaps))

    if not page_outside and not page_overlaps:
        geometry = "Conforme (sem saídas da margem nem colisões geométricas previstas)."
    else:
        geometry = "Rever: {} fora da margem; {} colisão(ões) geométrica(s) prevista(s).".format(
            len(page_outside), len(page_overlaps))

    lines = ["Diagnóstico da folha {}".format(sheet_label),
             "Índice heurístico da composição: {score}/100 — hierarquia {hierarchy}, equilíbrio {balance}, alinhamento {alignment}, ocupação {occupancy}, espaçamento {spacing}.".format(**quality),
             "Composição geométrica: {}".format(geometry)]
    if principal:
        if principal_assigned:
            lines.append("Vista principal: posicionada — {}.".format(principal["name"]))
        else:
            lines.append("Vista dominante estimada por tipo/área: posicionada — {}.".format(
                principal["name"]))
    else:
        lines.append("Vista principal: não identificada nesta folha.")
    if panoramas:
        lines.append("Secção panorâmica: posicionada — {}.".format(
            ", ".join(item["name"] for item in panoramas[:3])))
    else:
        lines.append("Secção panorâmica: nenhuma identificada nesta folha.")
    if small_supports:
        lines.append("Vista(s) de apoio com ocupação reduzida: {}.".format(
            ", ".join(item["name"] for item in small_supports[:3])))
    elif supports:
        lines.append("Vistas de apoio: {} posicionada(s); ocupação acima do limiar de alerta.".format(
            len(supports)))
    else:
        lines.append("Vistas de apoio: nenhuma classificada nesta folha.")
    schedules = [item for item in page_items if item.get("is_schedule")]
    if schedules:
        lines.append("Tabelas: {} posicionada(s); dimensões e colisões são validadas separadamente das viewports.".format(
            len(schedules)))
    if page_overlaps:
        lines.append("Colisão entre áreas de vistas/rótulos: {} caso(s); verificar na folha.".format(
            len(page_overlaps)))
    else:
        lines.append("Colisões geométricas previstas: nenhuma.")
    if scale_missing:
        lines.append("Escalas: incompleto — {} vista(s) sem escala mensurável.".format(
            len(scale_missing)))
    else:
        lines.append("Escalas: {} vista(s) verificadas quanto à presença e aplicação; tabelas não têm escala de vista.".format(
            len(scale_items)))
    if annotation_small or annotation_large:
        annotation_text = "Anotações medidas: {} ocorrência(s) abaixo e {} acima da faixa de referência.".format(
            annotation_small, annotation_large)
    elif annotation_unavailable or annotation_unmeasured:
        annotation_text = "Dimensões de anotações: análise parcial; {} vista(s) indisponível(eis), {} tipo(s)/célula(s) sem tamanho mensurável.".format(
            annotation_unavailable, annotation_unmeasured)
    elif not measured:
        annotation_text = "Sem anotações com tamanho mensurável; legibilidade não avaliada."
    else:
        annotation_text = "Sem ocorrências fora da faixa de referência nos elementos medidos ({}).".format(
            measured)
    lines.append(annotation_text)
    lines.append("Legibilidade impressa: não verificada. O índice não substitui a inspeção do PDF/exportação à escala final.")

    recommendations = []
    if small_supports:
        recommendations.append("verificar o recorte da vista de apoio e considerar ampliá-la ou dar-lhe uma zona maior")
    if page_outside or page_overlaps:
        recommendations.append("reposicionar ou redimensionar as vistas assinaladas e rever os rótulos")
    if annotation_small or annotation_large or annotation_unavailable:
        recommendations.append("inspecionar os tamanhos de cotas e notas; a análise não altera tipos")
    if quality["score"] < 65:
        recommendations.append("comparar outra composição e rever equilíbrio, alinhamento e ocupação da área útil")
    if not recommendations:
        recommendations.append("confirmar o enquadramento das vistas e validar uma exportação PDF à escala de impressão")
    lines.append("Recomendação: {}.".format("; ".join(recommendations)))
    return "\n".join(lines)


def preview_window(setup, initial_state):
    """Dock-style drag-and-drop composition preview with add-sheet support."""
    Visibility, DragDrop, DragDropEffects, DataObject, DataFormats, Mouse, MouseButtonState = load_wpf_api()
    from System import TimeSpan
    from System.Windows import Thickness
    from System.Windows.Input import ModifierKeys, Keyboard
    from System.Windows.Media import BrushConverter
    from System.Windows.Shapes import Line as WpfLine
    from System.Windows.Threading import DispatcherTimer
    brush_converter = BrushConverter()
    state = initial_state
    items = setup["items"]
    items_by_id = dict((item["id"], item) for item in items)
    selected_ids = set()
    selection_ui_state = {"syncing": False}
    layout_ui_state = {"suppress_selection": False}
    proposal_state = {
        "candidates": [], "index": -1, "input_signature": None,
        "input_version": 0, "snapshot": None, "invalidation_reason": None,
        "search": None, "search_timer": None, "searching": False,
        "search_paused": False, "search_complete": False,
        "search_controls": None, "displayed_best_signature": None
    }
    layout_scale_keys = (
        "width", "height", "layout_width", "layout_height", "box_offset",
        "planned_scale", "scale_text", "scale_changed", "adaptive_scale_enabled", "label")
    layout_scale_baseline = dict(
        (item["id"], dict((key, item[key]) for key in layout_scale_keys if key in item))
        for item in items)

    def reset_automatic_layout_scales():
        for item in items:
            if item.get("scale_user_override"):
                continue
            baseline = layout_scale_baseline.get(item["id"], {})
            for key in layout_scale_keys:
                if key in baseline:
                    item[key] = baseline[key]
                else:
                    item.pop(key, None)
            item.pop("adaptive_zone_scale", None)
            item.pop("zone_fit_warning", None)
    bounds = setup["bounds"]
    min_x, min_y, max_x, max_y = bounds
    sheet_w, sheet_h = max_x - min_x, max_y - min_y
    if sheet_w <= 0 or sheet_h <= 0:
        raise Exception("Dimensões de folha inválidas para pré-visualização.")

    def refresh_composition_snapshot(reason=None):
        snapshot = make_composition_snapshot(
            items, bounds, setup["margin_ft"], setup["gap_ft"], setup)
        signature = snapshot["input_signature"]
        if signature != proposal_state["input_signature"]:
            had_candidates = bool(proposal_state["candidates"])
            had_search = bool(proposal_state.get("search") is not None or
                              proposal_state.get("searching") or
                              proposal_state.get("search_paused"))
            timer = proposal_state.get("search_timer")
            if timer is not None:
                try:
                    timer.Stop()
                except Exception:
                    pass
            proposal_state["search"] = None
            proposal_state["search_timer"] = None
            proposal_state["searching"] = False
            proposal_state["search_paused"] = False
            proposal_state["search_complete"] = False
            proposal_state["displayed_best_signature"] = None
            proposal_state["input_version"] += 1
            proposal_state["candidates"] = []
            proposal_state["index"] = -1
            proposal_state["input_signature"] = signature
            proposal_state["invalidation_reason"] = reason
            if had_search:
                restore_search_controls()
            if had_candidates and reason:
                window.proposalInfoText.Text = (
                    "A entrada da composição mudou ({}). As propostas antigas foram descartadas; "
                    "clique em Organizar automaticamente para recalculá-las."
                ).format(reason)
        proposal_state["snapshot"] = snapshot
        return snapshot

    canvas_base_width = 690.0
    zoom_state = {"factor": 1.0}
    canvas_width = canvas_base_width * zoom_state["factor"]
    canvas_height = canvas_width * sheet_h / sheet_w
    scale_state = {"value": canvas_width / sheet_w}
    xaml_tiles = []
    for index, item in enumerate(items):
        xaml_tiles.append(
            '<Border x:Name="viewTile{0}" Canvas.Left="0" Canvas.Top="0" '
            'Width="80" Height="50" Visibility="Collapsed" Padding="4" '
            'CornerRadius="3" BorderThickness="1.5" Background="#DDEBF3" '
            'BorderBrush="#31506E" ClipToBounds="True">'
            '<TextBlock x:Name="viewTileLabel{0}" TextWrapping="Wrap" '
            'TextAlignment="Center" VerticalAlignment="Center" '
            'HorizontalAlignment="Center" FontSize="9" Foreground="#243B53"/>'
            '</Border>'.format(index))
    for index, unused_item in enumerate(items):
        xaml_tiles.append(
            '<Rectangle x:Name="viewResizeHandle{0}" Width="12" Height="12" '
            'Fill="#087E83" Stroke="White" StrokeThickness="1" '
            'Visibility="Collapsed" IsHitTestVisible="True"/>'.format(index))
    xaml_tiles = "\n".join(xaml_tiles)
    xaml = '''<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="Tudo32f | Prévia do Fólio v2.8" Width="1160" Height="790"
        MinWidth="980" MinHeight="650" ResizeMode="CanResizeWithGrip"
        WindowStartupLocation="CenterOwner" ShowInTaskbar="False"
        Background="#F3F6FA" FontFamily="Segoe UI">
      <Grid>
        <Grid.RowDefinitions><RowDefinition Height="96"/><RowDefinition Height="*"/><RowDefinition Height="82"/></Grid.RowDefinitions>
        <Border Grid.Row="0" Background="#142B45" Padding="24,13">
          <Grid>
            <StackPanel VerticalAlignment="Center">
              <TextBlock Text="Tudo32f" Foreground="#8DD7D0" FontSize="11" FontWeight="Bold"/>
              <TextBlock Text="Composição da prancha" Foreground="White" FontSize="23" FontWeight="SemiBold" Margin="0,2,0,0"/>
              <TextBlock Text="Ctrl+clique seleciona vários elementos; arraste um selecionado para mover o grupo ou aplique escala comum às vistas" Foreground="#D7E2ED" FontSize="11" Margin="0,3,0,0"/>
            </StackPanel>
            <TextBlock x:Name="sheetSizeText" Grid.Column="1" HorizontalAlignment="Right" VerticalAlignment="Center" Foreground="#D7E2ED" FontSize="12"/>
          </Grid>
        </Border>
        <Grid Grid.Row="1" Margin="16,13,16,10">
          <Grid.ColumnDefinitions><ColumnDefinition Width="330"/><ColumnDefinition Width="*"/></Grid.ColumnDefinitions>
          <Border Grid.Column="0" Background="White" BorderBrush="#D8E1EA" BorderThickness="1" CornerRadius="7" Padding="12" Margin="0,0,10,0">
            <Grid>
              <Grid.RowDefinitions><RowDefinition Height="Auto"/><RowDefinition Height="*"/><RowDefinition Height="Auto"/><RowDefinition Height="Auto"/><RowDefinition Height="Auto"/></Grid.RowDefinitions>
              <StackPanel Grid.Row="0">
                <TextBlock Text="VISTAS PARA MONTAGEM" Foreground="#31506E" FontSize="11" FontWeight="Bold"/>
                <TextBlock Text="Grelha de 2 cm com encaixe ao mover. Use Ctrl+clique para selecionar vistas e tabelas; a escala aplica-se apenas às vistas compatíveis." Foreground="#52667A" FontSize="11" TextWrapping="Wrap" Margin="0,5,0,8"/>
              </StackPanel>
              <ListBox x:Name="dragList" Grid.Row="1" SelectionMode="Extended"/>
              <StackPanel Grid.Row="2" Margin="0,8,0,0">
                <TextBlock Text="DISTRIBUIÇÃO AUTOMÁTICA" Foreground="#31506E" FontSize="10" FontWeight="Bold" Margin="0,0,0,5"/>
                <ComboBox x:Name="layoutCombo" Height="30"/>
                <Button x:Name="autoArrangeButton" Content="Organizar automaticamente" Height="32" Margin="0,7,0,0" ToolTip="Gera e percorre propostas baseadas na hierarquia, dimensões e proporção das vistas. Compare-as na prévia." Background="#EAF2F7" BorderBrush="#C7D2DE"/>
                 <Button x:Name="cancelSearchButton" Content="Pausar procura" Height="27" Margin="0,5,0,0" Visibility="Collapsed" Background="White" BorderBrush="#C7D2DE"/>
                <TextBlock x:Name="proposalInfoText" Text="As propostas serão calculadas a partir das vistas selecionadas." Foreground="#52667A" FontSize="10" TextWrapping="Wrap" Margin="2,5,2,0"/>
                <TextBlock Text="PAPEL NA FOLHA — aplica ao escolher" Foreground="#31506E" FontSize="10" FontWeight="Bold" Margin="0,9,0,4"/>
                <ComboBox x:Name="hierarchyCombo" Height="28" ToolTip="Selecione uma vista e escolha Principal, Secundária ou Apoio; a alteração aplica-se imediatamente."/>
              </StackPanel>
              <StackPanel Grid.Row="3" Orientation="Horizontal" Margin="0,10,0,0">
                <Button x:Name="addSheetButton" Content="＋ Nova prancha" Height="32" Padding="11,2" Background="White" BorderBrush="#AFC3D2"/>
                <Button x:Name="removeSheetButton" Content="Remover vazia" Height="32" Padding="9,2" Margin="7,0,0,0" Background="White" BorderBrush="#C7D2DE"/>
              </StackPanel>
              <TextBlock x:Name="leftStatusText" Grid.Row="4" Foreground="#52667A" FontSize="11" TextWrapping="Wrap" Margin="2,9,2,0"/>
            </Grid>
          </Border>
          <Border Grid.Column="1" Background="White" BorderBrush="#D8E1EA" BorderThickness="1" CornerRadius="7" Padding="12">
            <Grid>
              <Grid.RowDefinitions><RowDefinition Height="Auto"/><RowDefinition Height="*"/><RowDefinition Height="Auto"/></Grid.RowDefinitions>
              <Grid Grid.Row="0" Margin="2,0,2,10">
                <Grid.ColumnDefinitions><ColumnDefinition/><ColumnDefinition Width="Auto"/></Grid.ColumnDefinitions>
                <StackPanel Orientation="Horizontal" VerticalAlignment="Center">
                  <TextBlock Text="PRÉVIA DA FOLHA" Foreground="#31506E" FontSize="11" FontWeight="Bold" VerticalAlignment="Center" Margin="0,0,10,0"/>
                  <ComboBox x:Name="sheetCombo" Width="150" Height="29"/>
                  <TextBlock Text="Escala" Foreground="#52667A" FontSize="10" VerticalAlignment="Center" Margin="10,0,5,0"/>
                  <Button x:Name="chooseScaleButton" Content="Alterar escala…" MinWidth="138" Height="29" Padding="9,1" Margin="5,0,0,0" IsEnabled="False" ToolTip="Selecione uma vista e escolha uma escala predefinida ou personalizada. A prévia atualiza imediatamente." Background="White" BorderBrush="#C7D2DE"/>
                </StackPanel>
                <StackPanel Grid.Column="1" Orientation="Horizontal" HorizontalAlignment="Right" VerticalAlignment="Center">
                  <TextBlock x:Name="sheetCountText" Foreground="#52667A" FontSize="11" VerticalAlignment="Center" Margin="0,0,9,0"/>
                  <Button x:Name="zoomOutButton" Content="−" Width="29" Height="29" ToolTip="Reduzir preview" Margin="0,0,3,0" Background="White" BorderBrush="#C7D2DE"/>
                  <TextBlock x:Name="zoomText" Text="100%" MinWidth="45" TextAlignment="Center" VerticalAlignment="Center" Foreground="#31506E" FontSize="11"/>
                  <Button x:Name="zoomInButton" Content="+" Width="29" Height="29" ToolTip="Ampliar preview" Margin="3,0,3,0" Background="White" BorderBrush="#C7D2DE"/>
                  <Button x:Name="zoomFitButton" Content="Ajustar" Height="29" Padding="7,1" ToolTip="Enquadrar a folha" Background="White" BorderBrush="#C7D2DE"/>
                </StackPanel>
              </Grid>
              <ScrollViewer x:Name="previewScroll" Grid.Row="1" HorizontalScrollBarVisibility="Auto" VerticalScrollBarVisibility="Auto" Background="#E8EEF3">
                <Border Background="#D5DEE6" Padding="12" HorizontalAlignment="Center" VerticalAlignment="Top">
                  <Canvas x:Name="previewCanvas" Width="690" Height="420" Background="White" AllowDrop="True" ClipToBounds="True">
                    <Rectangle x:Name="safeAreaRect" Canvas.Left="15" Canvas.Top="15" Width="660" Height="390" Fill="Transparent" Stroke="#4C9B95" StrokeThickness="1.4" StrokeDashArray="5,3"/>
                    __VIEW_TILES__
                  </Canvas>
                </Border>
              </ScrollViewer>
              <StackPanel Grid.Row="2" Margin="2,9,2,0">
                <TextBlock x:Name="previewStatusText" Foreground="#52667A" FontSize="11" TextWrapping="Wrap"/>
                <Border Background="#F5F8FA" BorderBrush="#D8E1EA" BorderThickness="1" CornerRadius="4" Height="112" Margin="0,6,0,2" Padding="7">
                  <ScrollViewer VerticalScrollBarVisibility="Auto" HorizontalScrollBarVisibility="Disabled">
                    <TextBlock x:Name="diagnosticText" Foreground="#243B53" FontSize="10.5" TextWrapping="Wrap"/>
                  </ScrollViewer>
                </Border>
                <CheckBox x:Name="ignoreUnplacedCheck" Content="Ignorar vistas que permanecerem sem prancha" Margin="0,6,0,0"/>
                <CheckBox x:Name="allowConflictsCheck" Content="Confirmo que aceito vistas fora da margem ou sobrepostas" Margin="0,4,0,0"/>
              </StackPanel>
            </Grid>
          </Border>
        </Grid>
        <Border Grid.Row="2" Background="White" BorderBrush="#D8E1EA" BorderThickness="0,1,0,0" Padding="18,12">
          <Grid>
            <TextBlock Text="A escala só muda quando escolhida. A prévia estima as dimensões; a verificação final mede viewports, rótulos e tabelas reais." VerticalAlignment="Center" Foreground="#52667A" FontSize="11" Margin="0,0,300,0" TextWrapping="Wrap"/>
            <StackPanel Orientation="Horizontal" HorizontalAlignment="Right" VerticalAlignment="Center">
              <Button x:Name="backButton" Content="Voltar" Width="82" Height="34" Margin="0,0,8,0" Background="White" BorderBrush="#C7D2DE"/>
              <Button x:Name="confirmButton" Content="Confirmar composição" Width="160" Height="34" Background="#087E83" Foreground="White" FontWeight="SemiBold" BorderThickness="0"/>
            </StackPanel>
          </Grid>
        </Border>
      </Grid>
    </Window>'''
    xaml = xaml.replace("__VIEW_TILES__", xaml_tiles)
    window = forms.WPFWindow(xaml, literal_string=True, handle_esc=True, set_owner=True)
    window.previewCanvas.Width = canvas_width
    window.previewCanvas.Height = canvas_height
    window.sheetSizeText.Text = "Folha: {:.0f} × {:.0f} cm".format(
        ft_to_cm(sheet_w), ft_to_cm(sheet_h))
    left_property, top_property = canvas_attached_properties(window.previewCanvas)
    scale = scale_state["value"]
    inset_left, inset_bottom, inset_right, inset_top = margin_insets(setup["margin_ft"])
    safe_left = inset_left * scale
    safe_top = inset_top * scale
    window.safeAreaRect.Width = max(0.0, sheet_w - inset_left - inset_right) * scale
    window.safeAreaRect.Height = max(0.0, sheet_h - inset_bottom - inset_top) * scale
    window.safeAreaRect.SetValue(left_property, safe_left)
    window.safeAreaRect.SetValue(top_property, safe_top)
    grid_step_ft = cm_to_ft(GRID_STEP_CM)
    grid_lines = []
    minor_grid_brush = brush_converter.ConvertFromString("#E6EBF0")
    major_grid_brush = brush_converter.ConvertFromString("#C7D2DC")

    def update_sheet_grid():
        pixel_scale = scale_state["value"]
        current_width = float(window.previewCanvas.Width)
        current_height = float(window.previewCanvas.Height)
        for line, axis, offset_ft, is_major in grid_lines:
            coordinate = offset_ft * pixel_scale
            if axis == "vertical":
                line.X1 = coordinate
                line.X2 = coordinate
                line.Y1 = 0.0
                line.Y2 = current_height
            else:
                line.X1 = 0.0
                line.X2 = current_width
                line.Y1 = coordinate
                line.Y2 = coordinate

    horizontal_steps = int(math.floor(sheet_w / grid_step_ft))
    vertical_steps = int(math.floor(sheet_h / grid_step_ft))
    for index in range(horizontal_steps + 1):
        line = WpfLine()
        is_major = index % GRID_MAJOR_EVERY == 0
        line.Stroke = major_grid_brush if is_major else minor_grid_brush
        line.StrokeThickness = 0.9 if is_major else 0.45
        line.IsHitTestVisible = False
        grid_lines.append((line, "vertical", index * grid_step_ft, is_major))
        window.previewCanvas.Children.Insert(0, line)
    for index in range(vertical_steps + 1):
        line = WpfLine()
        is_major = index % GRID_MAJOR_EVERY == 0
        line.Stroke = major_grid_brush if is_major else minor_grid_brush
        line.StrokeThickness = 0.9 if is_major else 0.45
        line.IsHitTestVisible = False
        grid_lines.append((line, "horizontal", index * grid_step_ft, is_major))
        window.previewCanvas.Children.Insert(0, line)
    update_sheet_grid()

    custom_scale_choice = "Personalizar…"
    hierarchy_labels = {
        "principal": "Principal",
        "secondary": "Secundária",
        "support": "Apoio"
    }
    hierarchy_roles = dict((label, role) for role, label in hierarchy_labels.items())
    for role in ("principal", "secondary", "support"):
        window.hierarchyCombo.Items.Add(hierarchy_labels[role])

    for layout in LAYOUTS:
        window.layoutCombo.Items.Add(layout)
    current_layout = setup["layout"]
    window.layoutCombo.SelectedItem = current_layout if current_layout in LAYOUTS else LAYOUTS[0]
    if setup["mode"] == "active":
        window.addSheetButton.Visibility = Visibility.Collapsed
        window.removeSheetButton.Visibility = Visibility.Collapsed
        window.ignoreUnplacedCheck.Visibility = Visibility.Collapsed
        window.confirmButton.Content = "Aplicar à folha"
    else:
        if setup.get("single_sheet"):
            window.addSheetButton.Visibility = Visibility.Collapsed
            window.removeSheetButton.Visibility = Visibility.Collapsed
            window.ignoreUnplacedCheck.Visibility = Visibility.Collapsed
        else:
            window.addSheetButton.Visibility = Visibility.Visible
            window.removeSheetButton.Visibility = Visibility.Visible
        window.confirmButton.Content = "Criar pranchas"
    if setup.get("requested_sheet_count"):
        window.addSheetButton.Visibility = Visibility.Collapsed
        window.removeSheetButton.Visibility = Visibility.Collapsed

    sheet_bounds = bounds
    drag_start = {"point": None}
    edit_state = {"selected_id": None, "mode": None, "item_id": None,
                  "start_point": None, "start_center": None,
                  "start_centers": {}, "snapshot": None}
    hierarchy_ui_state = {"loading": False}
    label_to_id = {}
    result = {"accepted": False, "action": "cancel"}
    preview_sheet_labels = {}
    if setup["mode"] == "create":
        used_numbers = set()
        try:
            for existing_sheet in DB.FilteredElementCollector(doc).OfClass(DB.ViewSheet):
                used_numbers.add(safe_text(existing_sheet.SheetNumber))
        except Exception:
            pass
        number_cursor = int(setup.get("number_start", 1))
        for page_number in range(max(1, len(items))):
            sheet_number, number_cursor = unique_sheet_number(
                safe_text(setup.get("prefix", "")), number_cursor, used_numbers)
            preview_sheet_labels[page_number] = sheet_number

    def page_count():
        return max([state.get("page_count", 1)] +
            [entry["page"] + 1 for entry in state["pages"][0]["placements"].values()])

    def all_page_placements():
        return analyze_preview_state(state, setup)[0]

    def page_has_placement(page_index):
        for entry in state["pages"][0]["placements"].values():
            if entry["page"] == page_index:
                return True
        return False

    def selected_scale_items():
        ids = selected_ids
        if not ids and edit_state.get("selected_id"):
            ids = set([edit_state["selected_id"]])
        return [item for item in items if item["id"] in ids]

    def selected_hierarchy_items():
        ids = selected_ids
        if not ids and edit_state.get("selected_id"):
            ids = set([edit_state["selected_id"]])
        return [item for item in items if item["id"] in ids]

    def sync_selected_scale():
        selected = selected_scale_items()
        scale_items = [item for item in selected
                       if not item.get("is_schedule") and
                       (item.get("planned_scale") or item.get("current_scale"))]
        editable = [item for item in scale_items if not item.get("scale_locked")]
        if not scale_items:
            window.chooseScaleButton.IsEnabled = False
            window.chooseScaleButton.Content = "Alterar escala…"
            window.chooseScaleButton.ToolTip = (
                "Selecione uma vista colocada na prévia ou na lista para alterar a escala.")
            return
        if setup.get("keep_scales", setup.get("scale_mode") == SCALE_KEEP):
            window.chooseScaleButton.IsEnabled = False
            window.chooseScaleButton.Content = "Escalas preservadas"
            window.chooseScaleButton.ToolTip = (
                "A opção Manter as escalas atuais está ativa. Desmarque-a na janela anterior "
                "para permitir alterações de escala na prévia.")
            return
        window.chooseScaleButton.IsEnabled = True
        denominators = set(int(item.get("planned_scale") or item.get("current_scale"))
                           for item in scale_items)
        if not editable:
            window.chooseScaleButton.IsEnabled = False
            window.chooseScaleButton.Content = "Escala bloqueada"
            lock_details = ["{}: {}".format(
                item["name"], item.get("scale_lock_reason", "restrição do Revit"))
                for item in scale_items]
            window.chooseScaleButton.ToolTip = (
                "O Revit não permite alterar a escala destas vistas: {}"
            ).format("; ".join(lock_details))
        else:
            if len(denominators) == 1:
                window.chooseScaleButton.Content = "Escala 1:{}".format(
                    list(denominators)[0])
            else:
                window.chooseScaleButton.Content = "Várias escalas"
            locked = ["{}: {}".format(
                item["name"], item.get("scale_lock_reason", "restrição do Revit"))
                for item in scale_items if item.get("scale_locked")]
            tooltip = (
                "Clique para escolher uma escala predefinida ou personalizada para {} vista(s). "
                "Com Ctrl, selecione várias; as vistas bloqueadas serão preservadas."
            ).format(len(editable))
            if locked:
                tooltip += " Ignoradas por bloqueio: {}.".format("; ".join(locked))
            window.chooseScaleButton.ToolTip = tooltip

    def sync_selected_hierarchy():
        selected = selected_hierarchy_items()
        hierarchy_ui_state["loading"] = True
        try:
            window.hierarchyCombo.IsEnabled = bool(selected)
            roles = set(item.get("layout_role", "secondary") for item in selected)
            if len(roles) == 1:
                role = list(roles)[0]
                window.hierarchyCombo.SelectedItem = hierarchy_labels.get(role)
            else:
                window.hierarchyCombo.SelectedIndex = -1
        finally:
            hierarchy_ui_state["loading"] = False

    def on_apply_hierarchy(sender=None, args=None):
        if hierarchy_ui_state["loading"]:
            return
        selected = selected_hierarchy_items()
        role_label = safe_text(window.hierarchyCombo.SelectedItem).strip()
        role = hierarchy_roles.get(role_label)
        if not selected or role is None:
            window.previewStatusText.Text = "Selecione vistas e escolha Principal, Secundária ou Apoio."
            return
        view_items = [item for item in selected if not item.get("is_schedule")]
        if role == "principal" and len(view_items) != 1:
            window.previewStatusText.Text = (
                "A folha só pode ter uma vista principal. Selecione exatamente uma vista.")
            sync_selected_hierarchy()
            return
        if role == "principal":
            principal_id = view_items[0]["id"]
            for item in items:
                if (not item.get("is_schedule") and item.get("layout_role") == "principal" and
                        item["id"] != principal_id):
                    item["layout_role"] = "secondary"
                    item["hierarchy_source"] = "manual"
        changed = 0
        for item in selected:
            if item.get("is_schedule"):
                item["layout_role"] = "support"
                item["hierarchy_source"] = "manual"
                continue
            item["layout_role"] = role
            item["hierarchy_source"] = "manual"
            changed += 1
        refresh_composition_snapshot("hierarquia documental alterada")
        draw_preview()
        window.previewStatusText.Text = (
            "Papel documental atualizado em {} vista(s). A composição mantém as posições atuais; "
            "use Organizar automaticamente para comparar uma nova proposta."
        ).format(changed)

    def on_choose_scale(sender=None, args=None):
        if setup.get("keep_scales", setup.get("scale_mode") == SCALE_KEEP):
            window.previewStatusText.Text = (
                "As escalas estão protegidas. Volte à janela anterior e desmarque "
                "Manter as escalas atuais para poder alterá-las.")
            return
        selected = selected_scale_items()
        scale_items = [item for item in selected
                       if not item.get("is_schedule") and
                       (item.get("planned_scale") or item.get("current_scale"))]
        if not scale_items:
            window.previewStatusText.Text = (
                "Selecione uma vista na prévia ou na lista antes de escolher a escala.")
            return
        editable = [item for item in scale_items if not item.get("scale_locked")]
        if not editable:
            details = ["{}: {}".format(
                item["name"], item.get("scale_lock_reason", "restrição do Revit"))
                for item in scale_items]
            window.previewStatusText.Text = (
                "O Revit não permite alterar a escala destas vistas: {}"
            ).format("; ".join(details))
            return
        denominators = set(SCALE_PRESETS)
        for item in editable:
            denominator = item.get("planned_scale") or item.get("current_scale")
            if denominator:
                denominators.add(int(denominator))
        options = ["1:{}".format(value) for value in sorted(denominators)]
        options.append(custom_scale_choice)
        if len(editable) == 1:
            dialog_title = "Alterar escala — {}".format(editable[0]["name"])
        else:
            dialog_title = "Aplicar escala a {} vistas".format(len(editable))
        try:
            choice = forms.SelectFromList.show(
                options, title=dialog_title, multiselect=False,
                button_name="Aplicar à prévia")
        except Exception as error:
            window.previewStatusText.Text = (
                "Não foi possível abrir o seletor de escala: {}"
            ).format(safe_text(error))
            return
        if choice is None:
            return
        try:
            apply_preview_scale(choice)
        except Exception as error:
            window.previewStatusText.Text = (
                "Não foi possível atualizar a escala na prévia: {}"
            ).format(safe_text(error))

    def apply_preview_scale(scale_choice):
        selected = selected_scale_items()
        if not selected:
            window.previewStatusText.Text = "Selecione uma ou mais vistas na prévia ou na lista."
            return
        targets = [item for item in selected if not item.get("scale_locked")]
        locked_count = len(selected) - len(targets)
        if not targets:
            window.previewStatusText.Text = "As vistas selecionadas têm escala bloqueada."
            sync_selected_scale()
            return
        text = safe_text(scale_choice).strip()
        if text == custom_scale_choice:
            current = (targets[0].get("planned_scale") or
                       targets[0].get("current_scale") or 100)
            entered_scale = forms.ask_for_string(
                default=safe_text(current),
                prompt="Indique o denominador que será aplicado a todas as vistas selecionadas. Ex.: 75 para 1:75.",
                title="Escala personalizada")
            if entered_scale is None:
                return
            text = safe_text(entered_scale).strip()
        match = re.match(r"^(?:1\s*:\s*)?(\d+)$", text)
        if not match:
            window.previewStatusText.Text = "Escolha uma escala comum no formato 1:100 ou use Personalizada."
            return
        target_scale = int(match.group(1))
        if target_scale < 1 or target_scale > MAX_VIEW_SCALE:
            window.previewStatusText.Text = "A escala deve estar entre 1:1 e 1:24000 no Revit 2024."
            return
        changed_count = 0
        for item in targets:
            current_scale = item.get("planned_scale") or item.get("current_scale")
            if not current_scale:
                continue
            if target_scale != current_scale:
                factor = current_scale / float(target_scale)
                label_gap = max(0.0, item.get("label_gap", 0.0))
                item["width"] *= factor
                item["height"] *= factor
                item["layout_width"] *= factor
                body_height = max(0.0, item["layout_height"] - label_gap)
                item["layout_height"] = body_height * factor + label_gap
                offset = item.get("box_offset", (0.0, 0.0))
                item["box_offset"] = (offset[0] * factor, offset[1] * factor)
                item["planned_scale"] = target_scale
                item["scale_text"] = " | 1:{}".format(target_scale)
                item["scale_changed"] = target_scale != item.get("current_scale")
                item["label"] = item_label(item)
                item.pop("adaptive_zone_scale", None)
                item.pop("zone_fit_warning", None)
            item["scale_user_override"] = True
            item["adaptive_scale_enabled"] = False
            changed_count += 1
        if not changed_count:
            window.previewStatusText.Text = "Não foi possível determinar a escala das vistas selecionadas."
            return
        refresh_composition_snapshot("escala alterada")
        sync_selected_scale()
        draw_preview()
        locked_note = (" {} vista(s) com escala bloqueada foram ignoradas.".format(locked_count)
                       if locked_count else "")
        window.previewStatusText.Text = (
            "Prévia atualizada: escala 1:{} aplicada a {} vista(s) selecionada(s).{} "
            "A escala do modelo será aplicada ao confirmar."
        ).format(target_scale, changed_count, locked_note)

    def page_summary():
        by_page, outside, overlaps, unplaced = analyze_preview_state(state, setup)
        current_page = max(0, window.sheetCombo.SelectedIndex)
        current_count = len(by_page[current_page]) if current_page < len(by_page) else 0
        window.sheetCountText.Text = "{} folha(s)".format(max(1, len(by_page)))
        window.leftStatusText.Text = "Vista(s) atribuída(s): {} de {} | pendentes: {}".format(
            len(state["pages"][0]["placements"]), len(items), len(unplaced))
        scale_summary = setup.get("scale_summary", "")
        warning_text = "{} | Folha atual: {} vista(s)".format(
            scale_summary, current_count) if scale_summary else "Folha atual: {} vista(s)".format(current_count)
        adapted_items = [item["name"] for item in items
                         if item.get("adaptive_zone_scale") and
                         item["id"] in by_page[current_page]] if current_page < len(by_page) else []
        if adapted_items:
            warning_text += " | Escala adaptada à zona em {} vista(s)".format(len(adapted_items))
        small_items = [item["name"] for item in items
                       if item.get("zone_fit_warning") and
                       item["id"] in by_page[current_page]] if current_page < len(by_page) else []
        if small_items:
            warning_text += " | Verificar recorte/escala: {} ocupa(m) pouca área da zona atribuída".format(
                ", ".join(small_items[:3]))
        if outside:
            warning_text += " | {} fora da margem".format(len(outside))
        if overlaps:
            warning_text += " | {} sobreposição(ões)".format(len(overlaps))
        if unplaced:
            warning_text += " | {} sem folha".format(len(unplaced))
        warning_text += " | Grelha {:.0f} cm; encaixe ao mover ativo".format(GRID_STEP_CM)
        current_layout = safe_text(window.layoutCombo.SelectedItem)
        if current_layout in AUTO_LAYOUTS:
            layout_number = AUTO_LAYOUTS.index(current_layout) + 1
            warning_text += " | Diagramação {}/{}: {}".format(
                layout_number, len(AUTO_LAYOUTS), current_layout)
        window.previewStatusText.Text = warning_text
        if setup["mode"] == "active" and setup.get("active_sheet"):
            sheet_label = safe_text(setup["active_sheet"].SheetNumber)
        else:
            sheet_label = "{} (prevista)".format(preview_sheet_labels.get(
                current_page, "Prancha {}".format(current_page + 1)))
        sync_selected_scale()
        diagnostic = compose_page_diagnostic(
            items, by_page[current_page] if current_page < len(by_page) else {},
            outside, overlaps, current_page, setup, sheet_label)
        if len(selected_ids) > 1:
            diagnostic += "\n{} vistas selecionadas; a escala escolhida será aplicada a todas as selecionadas compatíveis.".format(
                len(selected_ids))
        selected = items_by_id.get(edit_state.get("selected_id"))
        if selected and selected["id"] in (by_page[current_page] if current_page < len(by_page) else {}):
            resize_hint = ("escala bloqueada ({}); apenas mover".format(
                               selected.get("scale_lock_reason", "restrição do Revit"))
                           if selected.get("scale_locked") else
                           "arraste para mover ou o canto para redimensionar")
            diagnostic += "\nSelecionada: {} — {} × {} cm, escala prevista 1:{}; {}.".format(
                selected["name"], round(ft_to_cm(selected["width"]), 1),
                round(ft_to_cm(selected["height"]), 1), selected.get("planned_scale") or "—",
                resize_hint)
        window.diagnosticText.Text = diagnostic
        sync_selected_hierarchy()
        return by_page, outside, overlaps, unplaced

    def snap_item_center(item, center):
        width = max(0.0, item.get("layout_width", item.get("width", 0.0)))
        height = max(0.0, item.get("layout_height", item.get("height", 0.0)))
        left = center[0] - width / 2.0
        top_offset = max_y - (center[1] + height / 2.0)
        snapped_left = min_x + math.floor((left - min_x) / grid_step_ft + 0.5) * grid_step_ft
        snapped_top = math.floor(top_offset / grid_step_ft + 0.5) * grid_step_ft
        return (snapped_left + width / 2.0,
                max_y - snapped_top - height / 2.0)

    def on_view_mouse_down(item_id, resize, sender, args):
        try:
            entry = state["pages"][0]["placements"].get(item_id)
            current_page = max(0, window.sheetCombo.SelectedIndex)
            if entry is None or entry["page"] != current_page:
                return
            item = items_by_id[item_id]
            try:
                ctrl_pressed = bool(Keyboard.Modifiers & ModifierKeys.Control)
            except Exception:
                ctrl_pressed = False
            if resize:
                selected_ids.clear()
                selected_ids.add(item_id)
            elif ctrl_pressed:
                if item_id in selected_ids:
                    selected_ids.remove(item_id)
                    edit_state["mode"] = None
                    edit_state["item_id"] = None
                    if not selected_ids:
                        edit_state["selected_id"] = None
                        draw_preview()
                        args.Handled = True
                        return
                    edit_state["selected_id"] = item_id if item_id in selected_ids else next(
                        candidate["id"] for candidate in items if candidate["id"] in selected_ids)
                    draw_preview()
                    args.Handled = True
                    return
                selected_ids.add(item_id)
                edit_state["selected_id"] = item_id
                edit_state["mode"] = None
                edit_state["item_id"] = None
                draw_preview()
                args.Handled = True
                return
            elif item_id not in selected_ids or len(selected_ids) <= 1:
                selected_ids.clear()
                selected_ids.add(item_id)
            edit_state["selected_id"] = item_id
            if resize and setup.get("keep_scales", setup.get("scale_mode") == SCALE_KEEP):
                window.previewStatusText.Text = (
                    "As escalas estão protegidas. Desmarque Manter as escalas atuais "
                    "na janela anterior para redimensionar esta vista.")
                draw_preview()
                args.Handled = True
                return
            if resize and item.get("scale_locked"):
                window.previewStatusText.Text = "Esta vista mantém a escala e não pode ser redimensionada aqui; ainda pode ser movida."
                draw_preview()
                args.Handled = True
                return
            edit_state["mode"] = "resize" if resize else "move"
            edit_state["item_id"] = item_id
            edit_state["start_point"] = args.GetPosition(window.previewCanvas)
            edit_state["start_center"] = entry["center"]
            edit_state["start_centers"] = dict(
                (candidate_id, candidate_entry["center"])
                for candidate_id, candidate_entry in state["pages"][0]["placements"].items()
                if candidate_id in selected_ids and candidate_entry["page"] == current_page)
            edit_state["snapshot"] = {
                "width": item["width"], "height": item["height"],
                "layout_width": item["layout_width"],
                "layout_height": item["layout_height"],
                "planned_scale": item.get("planned_scale"),
                "box_offset": item.get("box_offset", (0.0, 0.0))}
            sender.CaptureMouse()
            args.Handled = True
            draw_preview()
        except Exception as error:
            window.previewStatusText.Text = "Não foi possível selecionar a vista: {}".format(safe_text(error))

    def on_view_mouse_move(sender, args):
        if edit_state.get("mode") is None or Mouse.LeftButton != MouseButtonState.Pressed:
            return
        try:
            item_id = edit_state["item_id"]
            item = items_by_id[item_id]
            start = edit_state["start_point"]
            point = args.GetPosition(window.previewCanvas)
            dx = (point.X - start.X) / max(scale_state["value"], 1e-6)
            dy = (point.Y - start.Y) / max(scale_state["value"], 1e-6)
            if edit_state["mode"] == "move":
                start_x, start_y = edit_state["start_center"]
                snapped_center = snap_item_center(
                    item, (start_x + dx, start_y - dy))
                snapped_dx = snapped_center[0] - start_x
                snapped_dy = snapped_center[1] - start_y
                for moved_id, center in edit_state.get("start_centers", {}).items():
                    moved_entry = state["pages"][0]["placements"].get(moved_id)
                    if moved_entry is not None:
                        moved_entry["center"] = (
                            center[0] + snapped_dx, center[1] + snapped_dy)
            else:
                start_x, start_y = edit_state["start_center"]
                entry = state["pages"][0]["placements"][item_id]
                original = edit_state["snapshot"]
                ratio_x = dx / max(original["layout_width"], 1e-6)
                ratio_y = dy / max(original["layout_height"], 1e-6)
                factor = max(0.15, 1.0 + max(ratio_x, ratio_y))
                original_scale = original.get("planned_scale") or item.get("current_scale")
                if not original_scale:
                    return
                target_scale = max(1, int(round(original_scale / factor)))
                applied_factor = original_scale / float(target_scale)
                label_gap = max(0.0, item.get("label_gap", 0.0))
                item["width"] = original["width"] * applied_factor
                item["height"] = original["height"] * applied_factor
                item["layout_width"] = original["layout_width"] * applied_factor
                item["layout_height"] = max(0.0, original["layout_height"] - label_gap) * applied_factor + label_gap
                item["box_offset"] = (original["box_offset"][0] * applied_factor,
                                      original["box_offset"][1] * applied_factor)
                item["planned_scale"] = target_scale
                item["scale_user_override"] = True
                item["adaptive_scale_enabled"] = False
                item["scale_text"] = " | 1:{}".format(target_scale)
                item["scale_changed"] = target_scale != item.get("current_scale")
                item["label"] = item_label(item)
                item.pop("adaptive_zone_scale", None)
                item.pop("zone_fit_warning", None)
                entry["center"] = (
                    start_x + (item["layout_width"] - original["layout_width"]) / 2.0,
                    start_y - (item["layout_height"] - original["layout_height"]) / 2.0)
            draw_preview()
            args.Handled = True
        except Exception as error:
            window.previewStatusText.Text = "Não foi possível editar a vista: {}".format(safe_text(error))

    def on_view_mouse_up(sender, args):
        completed_mode = edit_state.get("mode")
        try:
            sender.ReleaseMouseCapture()
        except Exception:
            pass
        if completed_mode == "resize":
            refresh_composition_snapshot("escala ajustada manualmente")
        elif completed_mode == "move":
            invalidate_proposal_cache("posição ajustada manualmente")
        edit_state["mode"] = None
        edit_state["item_id"] = None
        edit_state["start_centers"] = {}
        edit_state["snapshot"] = None

    def on_drag_list_selection_changed(sender, args):
        if selection_ui_state["syncing"]:
            return
        selected = set()
        for label_text in sender.SelectedItems:
            item_id = label_to_id.get(safe_text(label_text))
            if item_id:
                selected.add(item_id)
        selected_ids.clear()
        selected_ids.update(selected)
        if selected:
            active = label_to_id.get(safe_text(sender.SelectedItem))
            edit_state["selected_id"] = active if active in selected else next(
                item["id"] for item in items if item["id"] in selected)
        else:
            edit_state["selected_id"] = None
        draw_preview()

    def draw_preview(sender=None, args=None):
        by_page, outside, overlaps, unplaced = page_summary()
        current_page = max(0, window.sheetCombo.SelectedIndex)
        if current_page >= len(by_page):
            current_page = max(0, len(by_page) - 1)
        outside_ids = set(item_id for page, item_id in outside if page == current_page)
        overlap_ids = set()
        for page, pair in overlaps:
            if page == current_page:
                overlap_ids.update(pair)
        current_placements = by_page[current_page] if current_page < len(by_page) else {}
        current_labels = {}
        for item in items:
            item_id = item["id"]
            index = items.index(item)
            tile = getattr(window, "viewTile{}".format(index))
            label = getattr(window, "viewTileLabel{}".format(index))
            resize_handle = getattr(window, "viewResizeHandle{}".format(index))
            placement = current_placements.get(item_id)
            if placement is None:
                tile.Visibility = Visibility.Collapsed
                resize_handle.Visibility = Visibility.Collapsed
                continue
            tile.Visibility = Visibility.Visible
            width_px = max(36.0, item["layout_width"] * scale_state["value"])
            height_px = max(26.0, item["layout_height"] * scale_state["value"])
            tile.Width = width_px
            tile.Height = height_px
            left_px = (placement[0] - min_x) * scale_state["value"] - width_px / 2.0
            top_px = (max_y - placement[1]) * scale_state["value"] - height_px / 2.0
            tile.SetValue(left_property, left_px)
            tile.SetValue(top_property, top_px)
            label.Text = "{}{}\n{:.0f} × {:.0f} cm".format(
                item["name"], item.get("scale_text", ""),
                ft_to_cm(item["width"]), ft_to_cm(item["height"]))
            label.FontSize = 8.0 if width_px < 110 else 9.0
            role_name = hierarchy_labels.get(item.get("layout_role", "secondary"), "Secundária")
            tile.ToolTip = "{} | {}".format(role_name, item["name"])
            if item_id in outside_ids:
                tile.Background = brush_converter.ConvertFromString("#FCE4E4")
                tile.BorderBrush = brush_converter.ConvertFromString("#C0392B")
            elif item_id in overlap_ids:
                tile.Background = brush_converter.ConvertFromString("#FFF0D2")
                tile.BorderBrush = brush_converter.ConvertFromString("#C27A16")
            elif item.get("layout_role") == "principal":
                tile.Background = brush_converter.ConvertFromString("#DCE5F5")
                tile.BorderBrush = brush_converter.ConvertFromString("#596D9A")
            elif item.get("layout_role") == "support":
                tile.Background = brush_converter.ConvertFromString("#EDF0F2")
                tile.BorderBrush = brush_converter.ConvertFromString("#8493A0")
            else:
                tile.Background = brush_converter.ConvertFromString("#DDEFEA")
                tile.BorderBrush = brush_converter.ConvertFromString("#26847D")
            is_selected = item_id in selected_ids
            tile.BorderThickness = Thickness(3.0 if is_selected else 1.5)
            if is_selected:
                tile.BorderBrush = brush_converter.ConvertFromString("#087E83")
            resize_handle.Visibility = (Visibility.Visible if
                edit_state.get("selected_id") == item_id and
                not setup.get("keep_scales", setup.get("scale_mode") == SCALE_KEEP) and
                not item.get("scale_locked") else Visibility.Collapsed)
            resize_handle.SetValue(left_property, left_px + width_px - resize_handle.Width / 2.0)
            resize_handle.SetValue(top_property, top_px + height_px - resize_handle.Height / 2.0)
            resize_handle.ToolTip = "Arraste para redimensionar mantendo a proporção; altera a escala da vista no projeto."
            assigned_page = state["pages"][0]["placements"][item_id]["page"]
            current_labels[item["label"]] = item_id
        selection_ui_state["syncing"] = True
        try:
            label_to_id.clear()
            window.dragList.Items.Clear()
            for item in items:
                item_id = item["id"]
                if item_id not in state["pages"][0]["placements"]:
                    prefix = "○ Sem prancha"
                else:
                    prefix = "✓ Prancha {}".format(
                        state["pages"][0]["placements"][item_id]["page"] + 1)
                label_text = "{} | {}".format(prefix, item["label"])
                label_to_id[label_text] = item_id
                window.dragList.Items.Add(label_text)
                if item_id in selected_ids:
                    window.dragList.SelectedItems.Add(label_text)
        finally:
            selection_ui_state["syncing"] = False

    def set_zoom(factor, redraw=True):
        factor = max(0.30, min(2.50, float(factor)))
        zoom_state["factor"] = factor
        canvas_width = canvas_base_width * factor
        canvas_height = canvas_width * sheet_h / sheet_w
        scale_state["value"] = canvas_width / sheet_w
        window.previewCanvas.Width = canvas_width
        window.previewCanvas.Height = canvas_height
        update_sheet_grid()
        safe_scale = scale_state["value"]
        inset_left, inset_bottom, inset_right, inset_top = margin_insets(setup["margin_ft"])
        window.safeAreaRect.Width = max(0.0, sheet_w - inset_left - inset_right) * safe_scale
        window.safeAreaRect.Height = max(0.0, sheet_h - inset_bottom - inset_top) * safe_scale
        window.safeAreaRect.SetValue(left_property, inset_left * safe_scale)
        window.safeAreaRect.SetValue(top_property, inset_top * safe_scale)
        window.zoomText.Text = "{:.0f}%".format(factor * 100.0)
        if redraw:
            draw_preview()

    def on_zoom_in(sender, args):
        set_zoom(zoom_state["factor"] * 1.20)

    def on_zoom_out(sender, args):
        set_zoom(zoom_state["factor"] / 1.20)

    def on_zoom_fit(sender, args):
        try:
            available_w = max(260.0, window.previewScroll.ViewportWidth - 36.0)
            available_h = max(180.0, window.previewScroll.ViewportHeight - 36.0)
            fit_factor = min(available_w / canvas_base_width,
                             available_h / (canvas_base_width * sheet_h / sheet_w))
            set_zoom(fit_factor)
        except Exception:
            set_zoom(0.75)

    def sync_sheet_combo():
        required = page_count()
        while window.sheetCombo.Items.Count < required:
            window.sheetCombo.Items.Add("Prancha {}".format(window.sheetCombo.Items.Count + 1))
        while window.sheetCombo.Items.Count > required:
            window.sheetCombo.Items.RemoveAt(window.sheetCombo.Items.Count - 1)
        if window.sheetCombo.Items.Count == 0:
            window.sheetCombo.Items.Add("Prancha 1")
        if window.sheetCombo.SelectedIndex < 0 or window.sheetCombo.SelectedIndex >= window.sheetCombo.Items.Count:
            window.sheetCombo.SelectedIndex = 0

    def on_drop(sender, args):
        try:
            if not args.Data.GetDataPresent(DataFormats.StringFormat):
                return
            item_id = safe_text(args.Data.GetData(DataFormats.StringFormat))
            if item_id not in items_by_id:
                return
            point = args.GetPosition(window.previewCanvas)
            page_index = max(0, window.sheetCombo.SelectedIndex)
            center_x = min_x + point.X / scale_state["value"]
            center_y = max_y - point.Y / scale_state["value"]
            center = snap_item_center(items_by_id[item_id], (center_x, center_y))
            state["pages"][0]["placements"][item_id] = {
                "page": page_index, "center": center}
            state["unplaced"].discard(item_id)
            invalidate_proposal_cache("vista reposicionada")
            sync_sheet_combo()
            draw_preview()
            args.Handled = True
        except Exception as error:
            window.previewStatusText.Text = "Não foi possível posicionar a vista: {}".format(error)

    def on_drag_over(sender, args):
        try:
            if args.Data.GetDataPresent(DataFormats.StringFormat):
                args.Effects = DragDropEffects.Move
            else:
                args.Effects = getattr(DragDropEffects, "None")
            args.Handled = True
        except Exception:
            pass

    def on_drag_start(sender, args):
        try:
            drag_start["point"] = args.GetPosition(window.dragList)
        except Exception:
            drag_start["point"] = None

    def on_drag_move(sender, args):
        try:
            if Mouse.LeftButton != MouseButtonState.Pressed or drag_start["point"] is None:
                return
            selected = window.dragList.SelectedItem
            if selected is None:
                return
            point = args.GetPosition(window.dragList)
            start = drag_start["point"]
            if abs(point.X - start.X) < 4 and abs(point.Y - start.Y) < 4:
                return
            label = safe_text(selected)
            item_id = label_to_id.get(label)
            if item_id:
                data = DataObject()
                data.SetData(DataFormats.StringFormat, item_id)
                DragDrop.DoDragDrop(window.dragList, data, DragDropEffects.Move)
            drag_start["point"] = None
        except Exception:
            pass

    def on_auto_arrange(sender, args):
        if not layout_ui_state["suppress_selection"]:
            reset_automatic_layout_scales()
            timer = proposal_state.get("search_timer")
            if timer is not None:
                try:
                    timer.Stop()
                except Exception:
                    pass
            proposal_state["search"] = None
            proposal_state["search_timer"] = None
            proposal_state["searching"] = False
            proposal_state["search_paused"] = False
            proposal_state["search_complete"] = False
            proposal_state["candidates"] = []
            proposal_state["index"] = -1
            proposal_state["input_signature"] = None
            restore_search_controls()
        style = safe_text(window.layoutCombo.SelectedItem)
        if style == MANUAL_LAYOUT:
            window.previewStatusText.Text = "Escolha um modo automático ou arraste as vistas manualmente."
            return
        if setup.get("single_sheet") and setup.get("scale_mode") == SCALE_AUTO:
            try:
                unused_items, setup["scale_summary"] = fit_items_to_single_page(
                    items, bounds, setup["margin_ft"], setup["gap_ft"], style)
                state["items"] = items
            except Exception as error:
                window.previewStatusText.Text = safe_text(error)
                return
        multi = setup["mode"] == "create"
        requested_count = setup.get("requested_sheet_count")
        if requested_count:
            planned_pages, unplaced_ids = layout_fixed_page_count(
                items, bounds, setup["margin_ft"], setup["gap_ft"], style,
                requested_count)
        else:
            planned_pages, unplaced_ids = layout_pages(
                items, bounds, setup["margin_ft"], setup["gap_ft"], style,
                multi and not setup.get("single_sheet"))
        placements = {}
        for page_index, page in enumerate(planned_pages):
            for item_id, center in page["placements"].items():
                placements[item_id] = {"page": page_index, "center": center}
        state["pages"][0]["placements"].clear()
        state["pages"][0]["placements"].update(placements)
        state["page_count"] = max(1, len(planned_pages))
        state["unplaced"] = set(unplaced_ids)
        sync_sheet_combo()
        draw_preview()

    def restore_search_controls():
        controls = proposal_state.get("search_controls") or []
        for control, was_enabled in controls:
            try:
                control.IsEnabled = was_enabled
            except Exception:
                pass
        proposal_state["search_controls"] = None
        try:
            window.cancelSearchButton.Visibility = Visibility.Collapsed
            window.autoArrangeButton.Content = (
                "Continuar procura" if proposal_state.get("search_paused")
                else "Organizar automaticamente")
        except Exception:
            pass

    def invalidate_proposal_cache(reason):
        has_proposal_state = bool(
            proposal_state.get("search") is not None or
            proposal_state.get("searching") or
            proposal_state.get("search_paused") or
            proposal_state.get("search_complete") or
            proposal_state.get("candidates"))
        if not has_proposal_state:
            return
        timer = proposal_state.get("search_timer")
        if timer is not None:
            try:
                timer.Stop()
            except Exception:
                pass
        proposal_state["search"] = None
        proposal_state["search_timer"] = None
        proposal_state["searching"] = False
        proposal_state["search_paused"] = False
        proposal_state["search_complete"] = False
        proposal_state["displayed_best_signature"] = None
        proposal_state["candidates"] = []
        proposal_state["index"] = -1
        proposal_state["input_signature"] = None
        proposal_state["snapshot"] = None
        restore_search_controls()
        window.proposalInfoText.Text = (
            "A composição mudou ({}); as propostas anteriores foram descartadas. "
            "Organize novamente para gerar propostas compatíveis."
        ).format(reason)

    def set_search_controls_busy():
        controls = [
            window.autoArrangeButton, window.layoutCombo, window.chooseScaleButton,
            window.hierarchyCombo, window.previewCanvas, window.dragList,
            window.sheetCombo, window.addSheetButton, window.removeSheetButton,
            window.confirmButton, window.zoomInButton, window.zoomOutButton,
            window.zoomFitButton
        ]
        proposal_state["search_controls"] = [
            (control, bool(control.IsEnabled)) for control in controls]
        for control in controls:
            control.IsEnabled = False
        window.cancelSearchButton.Visibility = Visibility.Visible
        window.cancelSearchButton.IsEnabled = True
        window.autoArrangeButton.Content = "A procurar…"

    def apply_proposal_candidate(candidate, index=None, total=None, message=None):
        candidate_items_by_id = dict(
            (item["id"], item) for item in candidate["items"])
        for live_item in items:
            planned_item = candidate_items_by_id.get(live_item["id"])
            if planned_item is not None:
                live_item.update(planned_item)
        state["items"] = items
        state["pages"][0]["placements"].clear()
        for page_index, page in enumerate(candidate["pages"]):
            for item_id, center in page.get("placements", {}).items():
                state["pages"][0]["placements"][item_id] = {
                    "page": page_index, "center": center}
        state["page_count"] = max(1, len(candidate["pages"]))
        state["unplaced"] = set(candidate["unplaced"])
        if index is not None:
            proposal_state["index"] = index
        next_layout = candidate["style"]
        layout_ui_state["suppress_selection"] = True
        try:
            if next_layout in LAYOUTS:
                window.layoutCombo.SelectedItem = next_layout
        finally:
            layout_ui_state["suppress_selection"] = False
        sync_sheet_combo()
        window.sheetCombo.SelectedIndex = 0
        draw_preview()
        if message:
            window.proposalInfoText.Text = message
        elif index is not None:
            window.proposalInfoText.Text = (
                "Proposta {} de {} válidas e distintas — {} | comparação {}/100 | entrada v{}"
            ).format(index + 1, total or 1, candidate["name"],
                     candidate["score"], candidate.get("input_version", 0))

    def finish_proposal_search(cancelled=False, error=None):
        search = proposal_state.get("search")
        timer = proposal_state.get("search_timer")
        if timer is not None:
            try:
                timer.Stop()
            except Exception:
                pass
        proposal_state["search_timer"] = None
        proposal_state["searching"] = False
        if search is None:
            proposal_state["search_paused"] = False
            restore_search_controls()
            return
        candidates = search.final_candidates()
        for candidate in candidates:
            candidate["input_version"] = proposal_state["input_version"]
        proposal_state["candidates"] = candidates
        proposal_state["index"] = -1
        proposal_state["search_paused"] = bool(cancelled and error is None)
        proposal_state["search_complete"] = not proposal_state["search_paused"]
        if not proposal_state["search_paused"]:
            proposal_state["search"] = None
        restore_search_controls()
        if error:
            proposal_state["search_paused"] = False
            proposal_state["search_complete"] = True
            proposal_state["search"] = None
            message = "A procura foi interrompida por erro: {}".format(safe_text(error))
        elif proposal_state["search_paused"]:
            message = "Procura pausada após {} avaliação(ões): {} proposta(s) válida(s). " \
                      "Clique em Continuar procura para encontrar mais propostas; editar a composição descarta esta procura.".format(
                          search.evaluated, len(candidates))
        else:
            message = "Procura concluída: {} avaliação(ões), {} proposta(s) válida(s) e distintas.".format(
                search.evaluated, len(candidates))
        if candidates:
            proposal_state["index"] = 0
            apply_proposal_candidate(candidates[0], 0, len(candidates), message)
        else:
            window.proposalInfoText.Text = message
            if not error:
                window.previewStatusText.Text = (
                    "A procura não encontrou uma composição completa válida. "
                    "Reveja as escalas, a área útil ou a quantidade de folhas.")
        window.autoArrangeButton.Content = (
            "Continuar procura" if proposal_state["search_paused"]
            else "Organizar automaticamente")

    def on_proposal_search_tick(sender, args):
        search = proposal_state.get("search")
        if not proposal_state.get("searching") or search is None:
            return
        try:
            candidate = search.step()
            if candidate is not None:
                candidate["input_version"] = proposal_state["input_version"]
                proposal_state["candidates"] = search.candidates
                displayed = proposal_state.get("displayed_best_signature")
                current_best = next((entry for entry in search.candidates
                                     if entry["signature"] == displayed), None)
                if current_best is None or candidate["score"] > current_best["score"]:
                    proposal_state["displayed_best_signature"] = candidate["signature"]
                    progress = (
                        "A procurar: {} configurações avaliadas; {} composições válidas distintas. "
                        "Melhor até agora: {} ({}/100)."
                    ).format(search.evaluated, len(search.candidates),
                             candidate["name"], candidate["score"])
                    apply_proposal_candidate(candidate, message=progress)
            elif search.candidates:
                window.proposalInfoText.Text = (
                    "A procurar: {} configurações avaliadas; {} composições válidas distintas."
                ).format(search.evaluated, len(search.candidates))
            else:
                window.proposalInfoText.Text = (
                    "A procurar composições válidas… {} configurações avaliadas."
                ).format(search.evaluated)
            if search.done:
                finish_proposal_search()
        except Exception as error:
            finish_proposal_search(error=error)

    def start_proposal_search(snapshot):
        allow_multiple = (setup["mode"] == "create" and
                          not setup.get("single_sheet"))
        search = CompositionProposalSearch(
            snapshot["items"], snapshot["bounds"], snapshot["margin"],
            snapshot["gap"], allow_multiple,
            snapshot.get("requested_sheet_count"))
        proposal_state["search"] = search
        proposal_state["candidates"] = []
        proposal_state["index"] = -1
        proposal_state["searching"] = True
        proposal_state["search_paused"] = False
        proposal_state["search_complete"] = False
        proposal_state["displayed_best_signature"] = None
        set_search_controls_busy()
        window.proposalInfoText.Text = "A preparar a procura de composições…"
        timer = DispatcherTimer()
        timer.Interval = TimeSpan.FromMilliseconds(10)
        timer.Tick += on_proposal_search_tick
        proposal_state["search_timer"] = timer
        timer.Start()

    def pause_proposal_search(sender=None, args=None):
        if not proposal_state.get("searching"):
            return
        finish_proposal_search(cancelled=True)

    def start_or_resume_proposal_search():
        search = proposal_state.get("search")
        if search is None:
            start_proposal_search(refresh_composition_snapshot())
            return
        proposal_state["search_paused"] = False
        proposal_state["searching"] = True
        proposal_state["search_complete"] = False
        proposal_state["candidates"] = search.candidates
        proposal_state["index"] = -1
        set_search_controls_busy()
        window.proposalInfoText.Text = (
            "A continuar a procura: {} configurações avaliadas; {} composições válidas."
        ).format(search.evaluated, len(search.candidates))
        timer = DispatcherTimer()
        timer.Interval = TimeSpan.FromMilliseconds(10)
        timer.Tick += on_proposal_search_tick
        proposal_state["search_timer"] = timer
        timer.Start()

    def on_next_layout(sender, args):
        if proposal_state.get("searching"):
            return
        reset_automatic_layout_scales()
        snapshot = refresh_composition_snapshot()
        if proposal_state.get("search_paused"):
            proposal_state["search_paused"] = False
            proposal_state["candidates"] = proposal_state["search"].candidates
            proposal_state["index"] = -1
            start_or_resume_proposal_search()
            return
        if not proposal_state.get("search_complete"):
            start_proposal_search(snapshot)
            return
        candidates = proposal_state["candidates"]
        if not candidates:
            window.proposalInfoText.Text = (
                "A procura não encontrou uma composição completa válida. "
                "Reveja as escalas, a área útil ou a quantidade de folhas.")
            return
        if proposal_state["index"] < 0:
            current_pages, unused_outside, unused_overlaps, unused_unplaced = analyze_preview_state(state, setup)
            current_plan = [{"placements": page} for page in current_pages]
            current_signature = _proposal_signature(current_plan, items)
            next_index = 0
            for candidate_index, candidate in enumerate(candidates):
                if candidate["signature"] != current_signature:
                    next_index = candidate_index
                    break
        else:
            next_index = (proposal_state["index"] + 1) % len(candidates)
        candidate = candidates[next_index]
        apply_proposal_candidate(candidate, next_index, len(candidates))

    def on_layout_selection_changed(sender, args):
        if not layout_ui_state["suppress_selection"]:
            on_auto_arrange(sender, args)

    def on_add_sheet(sender, args):
        if setup["mode"] != "create":
            return
        current = page_count()
        for item_id, placement in list(state["pages"][0]["placements"].items()):
            if placement["page"] >= current:
                placement["page"] = current - 1
        state["page_count"] = current + 1
        sync_sheet_combo()
        window.sheetCombo.SelectedIndex = current
        invalidate_proposal_cache("quantidade de pranchas alterada")
        draw_preview()

    def on_remove_sheet(sender, args):
        page_index = max(0, window.sheetCombo.SelectedIndex)
        if page_index == 0:
            window.previewStatusText.Text = "A primeira prancha não pode ser removida."
            return
        if page_has_placement(page_index):
            window.previewStatusText.Text = "Mova ou remova as vistas desta prancha antes de removê-la."
            return
        for placement in state["pages"][0]["placements"].values():
            if placement["page"] > page_index:
                placement["page"] -= 1
        state["page_count"] = max(1, page_count() - 1)
        sync_sheet_combo()
        window.sheetCombo.SelectedIndex = max(0, page_index - 1)
        invalidate_proposal_cache("quantidade de pranchas alterada")
        draw_preview()

    def on_confirm(sender, args):
        by_page, outside, overlaps, unplaced = analyze_preview_state(state, setup)
        if setup.get("single_sheet") and unplaced:
            window.previewStatusText.Text = (
                "Modo uma prancha: posicione todas as vistas nesta folha antes de confirmar.")
            return
        if setup.get("requested_sheet_count") and unplaced:
            window.previewStatusText.Text = (
                "Não foi possível acomodar todas as vistas nas {} pranchas escolhidas. "
                "Ajuste as escalas, a composição ou aumente o número de pranchas."
                .format(setup["requested_sheet_count"]))
            return
        if setup.get("requested_sheet_count"):
            occupied = sum(1 for page in by_page if page)
            if occupied != setup["requested_sheet_count"]:
                window.previewStatusText.Text = (
                    "A opção escolhida requer {} pranchas com vistas. Distribui pelo menos uma vista por prancha."
                    .format(setup["requested_sheet_count"]))
                return
        if unplaced and not bool(window.ignoreUnplacedCheck.IsChecked):
            window.previewStatusText.Text = (
                "Há {} vista(s) sem prancha. Arraste-as para uma folha ou marque a opção de ignorar."
                .format(len(unplaced)))
            return
        if (outside or overlaps) and not bool(window.allowConflictsCheck.IsChecked):
            window.previewStatusText.Text = (
                "A prévia encontrou {} elemento(s) fora da margem e {} sobreposição(ões). "
                "Ajuste o layout ou marque a confirmação de avisos."
                .format(len(outside), len(overlaps)))
            return
        scale_changes = [item for item in items
                         if not item.get("is_schedule") and
                         not item.get("scale_locked") and
                         item.get("current_scale") and item.get("planned_scale") and
                         int(item["current_scale"]) != int(item["planned_scale"])]
        if scale_changes:
            lines = ["• {}: 1:{} → 1:{}".format(
                item["name"], item["current_scale"], item["planned_scale"])
                for item in scale_changes[:12]]
            if len(scale_changes) > 12:
                lines.append("• +{} alteração(ões) de escala.".format(
                    len(scale_changes) - 12))
            message = (
                "O Fólio vai alterar a escala destas vistas no modelo. A escala pertence à vista "
                "e também pode afetar outras folhas onde ela seja usada.\n\n{}\n\n"
                "Confirmar a alteração das escalas e continuar?"
            ).format("\n".join(lines))
            if not forms.alert(message, title="Confirmar alterações de escala",
                               yes=True, no=True, warn_icon=True):
                window.previewStatusText.Text = (
                    "Confirmação cancelada. As escalas do modelo continuam inalteradas.")
                return
        result.update({
            "accepted": True,
            "action": "confirm",
            "pages": by_page,
            "placements": state["pages"][0]["placements"].copy(),
            "ignored_ids": list(unplaced),
            "outside": outside,
            "overlaps": overlaps,
            "allow_conflicts": bool(window.allowConflictsCheck.IsChecked),
            "page_count": len(by_page)
        })
        window.Close()

    window.sheetCombo.Items.Add("Prancha 1")
    window.sheetCombo.SelectedIndex = 0
    window.sheetCombo.SelectionChanged += draw_preview
    window.layoutCombo.SelectionChanged += on_layout_selection_changed
    window.chooseScaleButton.Click += on_choose_scale
    window.hierarchyCombo.SelectionChanged += on_apply_hierarchy
    for index, item in enumerate(items):
        tile = getattr(window, "viewTile{}".format(index))
        handle = getattr(window, "viewResizeHandle{}".format(index))
        tile.MouseLeftButtonDown += (lambda sender, args, item_id=item["id"]:
                                     on_view_mouse_down(item_id, False, sender, args))
        tile.MouseMove += on_view_mouse_move
        tile.MouseLeftButtonUp += on_view_mouse_up
        handle.MouseLeftButtonDown += (lambda sender, args, item_id=item["id"]:
                                       on_view_mouse_down(item_id, True, sender, args))
        handle.MouseMove += on_view_mouse_move
        handle.MouseLeftButtonUp += on_view_mouse_up
    window.previewCanvas.Drop += on_drop
    window.previewCanvas.DragOver += on_drag_over
    window.dragList.SelectionChanged += on_drag_list_selection_changed
    window.dragList.PreviewMouseLeftButtonDown += on_drag_start
    window.dragList.PreviewMouseMove += on_drag_move
    window.autoArrangeButton.Click += on_next_layout
    window.cancelSearchButton.Click += pause_proposal_search
    window.zoomInButton.Click += on_zoom_in
    window.zoomOutButton.Click += on_zoom_out
    window.zoomFitButton.Click += on_zoom_fit
    window.addSheetButton.Click += on_add_sheet
    window.removeSheetButton.Click += on_remove_sheet
    def stop_search_on_close(sender, args):
        timer = proposal_state.get("search_timer")
        if timer is not None:
            try:
                timer.Stop()
            except Exception:
                pass
        proposal_state["search_timer"] = None
        proposal_state["searching"] = False

    def on_back(sender, args):
        result["action"] = "back"
        window.Close()

    window.Closed += stop_search_on_close
    window.backButton.Click += on_back
    window.confirmButton.Click += on_confirm
    sync_sheet_combo()
    draw_preview()
    window.ShowDialog()
    return result


def result_dialog(title, intro, created=None, moved=None, skipped=None,
                  warnings=None, errors=None):
    """Show an actionable operation summary in pyRevit's output panel."""
    created = created or []
    moved = moved or []
    skipped = skipped or []
    warnings = warnings or []
    errors = errors or []
    output = script.get_output()
    output.print_md("# {}".format(title))
    output.print_md(intro)
    output.print_md("**Pranchas criadas:** {}  |  **Vistas organizadas:** {}  |  **Ignoradas:** {}".format(
        len(created), len(moved), len(skipped)))
    if created:
        output.print_md("## Pranchas")
        for entry in created:
            output.print_md("- **{}** — {} — {} elemento(s) de composição".format(
                entry["number"], entry["name"], entry["count"]))
    if moved:
        output.print_md("## Vistas organizadas")
        for label in moved:
            output.print_md("- {}".format(label))
    if skipped:
        output.print_md("## Tarefas ignoradas ou não concluídas")
        for item in skipped:
            output.print_md("- {}".format(item))
    if warnings:
        output.print_md("## Avisos")
        for item in warnings:
            output.print_md("- {}".format(item))
    if errors:
        output.print_md("## Erros")
        for item in errors:
            output.print_md("- {}".format(item))
    summary = "{}\n\nPranchas criadas: {}\nVistas organizadas: {}\nItens ignorados: {}\nAvisos: {}\nErros: {}".format(
        intro, len(created), len(moved), len(skipped), len(warnings), len(errors))
    forms.alert(summary, title=TITLE, warn_icon=bool(errors or warnings))


def unique_sheet_number(prefix, start, used_numbers):
    number = int(start)
    while True:
        candidate = "{}{}".format(prefix, number)
        if candidate not in used_numbers:
            used_numbers.add(candidate)
            return candidate, number + 1
        number += 1


def label_for_item(item):
    return "{} | {}".format(item["name"], item["kind"])


class PreviewConflictError(Exception):
    pass


def apply_planned_scales(items):
    """Change only views whose planned scale differs; caller owns the Revit transaction."""
    changes = []
    for item in items:
        target = item.get("planned_scale")
        current = item.get("current_scale")
        if item.get("scale_locked") or not target or not current or int(target) == int(current):
            continue
        view = item["view"]
        try:
            actual_before = int(view.Scale)
        except Exception:
            actual_before = None
        if actual_before != int(current):
            raise Exception(
                "A escala de '{}' mudou depois da prévia (prevista 1:{}, atual 1:{}). "
                "Reabra a composição antes de confirmar."
                .format(item["name"], current, actual_before or "indisponível"))
        lock_reason = view_scale_lock_reason(view)
        if lock_reason:
            raise Exception("A escala de '{}' ficou bloqueada antes da execução: {}."
                            .format(item["name"], lock_reason))
        try:
            view.Scale = int(target)
        except Exception as error:
            raise Exception(
                "Não foi possível alterar a escala de '{}'. A vista pode ter a escala controlada por um modelo de vista. Detalhe: {}"
                .format(item["name"], safe_text(error)))
        changes.append("{}: 1:{} → 1:{}".format(item["name"], current, target))
    if changes:
        doc.Regenerate()
        for item in items:
            target = item.get("planned_scale")
            current = item.get("current_scale")
            if item.get("scale_locked") or not target or not current or int(target) == int(current):
                continue
            try:
                actual = int(item["view"].Scale)
            except Exception:
                actual = None
            if actual != int(target):
                raise Exception(
                    "A escala de '{}' não assumiu 1:{}; verifique se um modelo de vista controla esse parâmetro."
                    .format(item["name"], target))
    return changes


def execute_active_sheet(setup, preview_result):
    placements = preview_result.get("placements", {})
    items_by_id = dict((item["id"], item) for item in setup["items"])
    sheet = setup["active_sheet"]
    if sheet is None or not placements:
        raise Exception("A folha ativa ou as posições da prévia não estão disponíveis.")
    moved, skipped, warnings = [], [], []
    move_failures = []
    transaction = DB.Transaction(doc, "Tudo32f | Organizar composição da folha")
    try:
        status = transaction.Start()
        if status != DB.TransactionStatus.Started:
            raise Exception("O Revit não permitiu iniciar a transação de organização.")
        scale_changes = apply_planned_scales(setup["items"])
        for change in scale_changes:
            warnings.append("Escala ajustada: {}.".format(change))
        for item_id, placement in placements.items():
            item = items_by_id.get(item_id)
            if item is None:
                move_failures.append("Vista ID {} não encontrada na seleção.".format(item_id))
                continue
            element = (item.get("schedule_instance") if item.get("is_schedule")
                       else item.get("viewport"))
            if element is None:
                move_failures.append("{} não é um elemento editável da folha ativa.".format(item["name"]))
                continue
            try:
                center_x, center_y = placement["center"]
                refreshed = refresh_layout_item(item, element) or item
                move_layout_item(item, element, (center_x, center_y), refreshed)
                moved.append(label_for_item(item))
            except Exception as error:
                move_failures.append("{} — {}".format(item["name"], safe_text(error)))
        if move_failures:
            details = "\n".join("• {}".format(entry) for entry in move_failures[:12])
            if len(move_failures) > 12:
                details += "\n• +{} falha(s) adicionais.".format(len(move_failures) - 12)
            raise Exception(
                "A organização foi cancelada porque nem todos os elementos puderam ser movidos. "
                "Nenhuma alteração será mantida.\n\n{}".format(details))
        if len(moved) != len(placements):
            raise Exception("A organização foi cancelada: foram movidos {} de {} elementos."
                            .format(len(moved), len(placements)))
        doc.Regenerate()
        actual_items, actual_placements = {}, {}
        for item_id, placement in placements.items():
            item = items_by_id.get(item_id)
            element = ((item.get("schedule_instance") if item and item.get("is_schedule")
                        else item.get("viewport")) if item else None)
            if item is None or element is None:
                continue
            try:
                refreshed = refresh_layout_item(item, element)
                if not refreshed:
                    raise Exception("o Revit não forneceu limites reais após o movimento")
                actual_items[item_id] = refreshed
                actual_placements[item_id] = refreshed["current_center"]
            except Exception as error:
                move_failures.append("{} — validação final: {}".format(
                    item["name"], safe_text(error)))
        if move_failures:
            details = "\n".join("• {}".format(entry) for entry in move_failures[:12])
            raise Exception(
                "A validação final falhou; a organização foi revertida.\n\n{}".format(details))
        if len(actual_items) != len(placements):
            raise Exception("A validação final encontrou {} de {} elementos. A organização foi revertida."
                            .format(len(actual_items), len(placements)))
        outside, overlaps = page_diagnostics(actual_items, actual_placements,
                                             sheet_bounds(sheet), setup["margin_ft"])
        if (outside or overlaps) and not preview_result.get("allow_conflicts", False):
            raise Exception("A validação real encontrou {} elemento(s) fora da margem e {} sobreposição(ões). A transação foi revertida; ajuste a composição na prévia ou confirme os avisos.".format(
                len(outside), len(overlaps)))
        for item_id in outside:
            warnings.append("{} ficou fora da margem segura na folha ativa.".format(items_by_id[item_id]["name"]))
        for id_a, id_b in overlaps:
            warnings.append("Sobreposição entre {} e {} na folha ativa.".format(
                items_by_id[id_a]["name"], items_by_id[id_b]["name"]))
        commit_status = transaction.Commit()
        if commit_status != DB.TransactionStatus.Committed:
            raise Exception("O Revit não confirmou a organização (estado: {}).".format(commit_status))
    except Exception:
        try:
            if transaction.GetStatus() == DB.TransactionStatus.Started:
                transaction.RollBack()
        except Exception:
            pass
        raise
    return {"created": [], "moved": moved, "skipped": skipped, "warnings": warnings, "errors": []}


def correct_actual_page_layout(page_created, bounds, margin, gap, style,
                               force_single_page=False):
    """Correct positions from real Revit extents without changing confirmed scales."""
    notes = []
    min_x, min_y, max_x, max_y = bounds
    inset_left, inset_bottom, inset_right, inset_top = margin_insets(margin)
    left, bottom = min_x + inset_left, min_y + inset_bottom
    right, top = max_x - inset_right, max_y - inset_top
    usable_w, usable_h = max(0.0, right - left), max(0.0, top - bottom)

    def refresh_items():
        refreshed_items = {}
        missing = []
        for item, layout_element, unused_center in page_created:
            refreshed = refresh_layout_item(item, layout_element)
            if refreshed:
                refreshed_items[item["id"]] = refreshed
            else:
                missing.append(item["name"])
        if missing:
            raise PreviewConflictError(
                "O Revit não forneceu limites reais para: {}. A operação será revertida."
                .format(", ".join(missing[:8])))
        return refreshed_items

    def move_to_centers(items_by_id, centers):
        for item_id, center in centers.items():
            actual = items_by_id.get(item_id)
            if actual is None:
                continue
            layout_element = (actual.get("schedule_instance") if actual.get("is_schedule")
                              else actual.get("viewport"))
            if layout_element is None:
                continue
            move_layout_item(actual, layout_element, center, actual)

    for unused_attempt in range(9):
        doc.Regenerate()
        actual_items = refresh_items()
        actual_placements = dict((item_id, item["current_center"])
                                 for item_id, item in actual_items.items())
        outside, overlaps = page_diagnostics(actual_items, actual_placements, bounds, margin)
        if not outside and not overlaps:
            return notes

        if force_single_page:
            packed, unplaced = layout_pages(list(actual_items.values()), bounds,
                                            margin, gap, style, allow_multiple=True)
            packed_count = sum(len(page["placements"]) for page in packed)
            fits_one = (len(packed) == 1 and not unplaced and
                        packed_count == len(actual_items))
            if not fits_one:
                raise PreviewConflictError(
                    "As dimensões reais não cabem em uma única prancha com as escalas escolhidas. "
                    "Ajuste as escalas na prévia e confirme novamente; nenhuma escala será alterada "
                    "depois desta confirmação.")

        if outside:
            too_large = []
            for item_id, item in actual_items.items():
                if item["layout_width"] > usable_w or item["layout_height"] > usable_h:
                    too_large.append(item_id)
            if too_large:
                raise PreviewConflictError(
                    "Uma ou mais vistas excedem a área segura com as escalas confirmadas. "
                    "Ajuste a escala explicitamente na prévia ou escolha uma prancha maior.")
            for item_id, item in actual_items.items():
                center_x, center_y = item["current_center"]
                half_w, half_h = item["layout_width"] / 2.0, item["layout_height"] / 2.0
                target_x = min(max(center_x, left + half_w), right - half_w)
                target_y = min(max(center_y, bottom + half_h), top - half_h)
                if abs(target_x - center_x) > 1e-6 or abs(target_y - center_y) > 1e-6:
                    layout_element = (item.get("schedule_instance") if item.get("is_schedule")
                                      else item.get("viewport"))
                    if layout_element is not None:
                        move_layout_item(item, layout_element, (target_x, target_y), item)
            doc.Regenerate()
            continue

        if overlaps:
            packed, unplaced = layout_pages(list(actual_items.values()), bounds,
                                            margin, gap, style, allow_multiple=True)
            packed_count = sum(len(page["placements"]) for page in packed)
            if len(packed) == 1 and not unplaced and packed_count == len(actual_items):
                move_to_centers(actual_items, packed[0]["placements"])
                doc.Regenerate()
                continue
            raise PreviewConflictError(
                "As dimensões reais criaram sobreposições que não cabem numa única folha. "
                "Ajuste a composição na prévia e confirme novamente.")
    raise PreviewConflictError(
        "A validação das dimensões reais da viewport não conseguiu encaixar todas as vistas sem ultrapassar margens ou criar sobreposições. A operação foi revertida.")


def preflight_sheet_viewports(titleblock_id, page, items_by_id, page_number):
    """Probe the selected views on a temporary sheet, then always roll it back."""
    failures = []
    probe_scope = DB.SubTransaction(doc)
    if probe_scope.Start() != DB.TransactionStatus.Started:
        raise Exception("Não foi possível iniciar a pré-validação da página {}.".format(page_number))
    try:
        probe_sheet = DB.ViewSheet.Create(doc, titleblock_id)
        if probe_sheet is None:
            raise Exception("O Revit não criou a folha temporária de pré-validação.")
        doc.Regenerate()
        for item_id, unused_center in page.items():
            item = items_by_id.get(item_id)
            if item is None:
                failures.append("ID {}: elemento ausente da composição.".format(item_id))
                continue
            view = item["view"]
            try:
                if item.get("is_schedule"):
                    instance = DB.ScheduleSheetInstance.Create(
                        doc, probe_sheet.Id, view.Id, DB.XYZ(0.0, 0.0, 0.0))
                    if instance is None:
                        failures.append("{} [ID {}] — Revit não devolveu uma instância de tabela.".format(
                            item["name"], item["id"]))
                        continue
                    doc.Regenerate()
                    measured_item = make_schedule_item(view, instance)
                    if measured_item.get("current_center") is None:
                        failures.append("{} [ID {}] — a tabela foi criada, mas o Revit não forneceu limites reais.".format(
                            item["name"], item["id"]))
                    continue
                if not DB.Viewport.CanAddViewToSheet(doc, probe_sheet.Id, view.Id):
                    failures.append(
                        "{} [ID {} | {}] — Revit recusou a viewport (CanAddViewToSheet=False)."
                        .format(item["name"], item["id"], item["kind"]))
                    continue
                viewport = DB.Viewport.Create(
                    doc, probe_sheet.Id, view.Id, DB.XYZ(0.0, 0.0, 0.0))
                if viewport is None:
                    failures.append("{} [ID {} | {}] — Revit não devolveu uma viewport.".format(
                        item["name"], item["id"], item["kind"]))
                    continue
                doc.Regenerate()
            except Exception as error:
                failures.append("{} [ID {} | {}] — {}".format(
                    item["name"], item["id"], item["kind"], safe_text(error)))
        return failures
    finally:
        try:
            if probe_scope.GetStatus() == DB.TransactionStatus.Started:
                probe_scope.RollBack()
        except Exception:
            pass


def execute_sheet_creation(setup, preview_result):
    pages = preview_result.get("pages", [])
    items_by_id = dict((item["id"], item) for item in setup["items"])
    nonempty_pages = [(index, page) for index, page in enumerate(pages) if page]
    if not nonempty_pages:
        raise Exception("A composição não contém vistas posicionadas. Nenhuma prancha foi criada.")
    requested_count = setup.get("requested_sheet_count")
    if requested_count and len(nonempty_pages) != requested_count:
        raise Exception("A prévia não contém vistas em todas as {} pranchas escolhidas. Volte e distribua as vistas por todas as folhas.".format(
            requested_count))
    titleblock = setup.get("titleblock")
    if titleblock is None:
        titleblock = doc.GetElement(setup.get("titleblock_id"))
    if titleblock is None:
        raise Exception("O tipo de carimbo selecionado não existe mais no projeto.")
    used_numbers = set()
    for existing_sheet in DB.FilteredElementCollector(doc).OfClass(DB.ViewSheet):
        try:
            used_numbers.add(safe_text(existing_sheet.SheetNumber))
        except Exception:
            pass
    number_cursor = int(setup["number_start"])
    created, moved, skipped, warnings = [], [], [], []
    transaction = DB.Transaction(doc, "Tudo32f | Criar e organizar pranchas")
    try:
        status = transaction.Start()
        if status != DB.TransactionStatus.Started:
            raise Exception("O Revit não permitiu iniciar a transação de criação.")
        scale_changes = apply_planned_scales(setup["items"])
        for change in scale_changes:
            warnings.append("Escala ajustada: {}.".format(change))
        if not titleblock.IsActive:
            titleblock.Activate()
            doc.Regenerate()
        preflight_failures = []
        for page_number, page in nonempty_pages:
            page_failures = preflight_sheet_viewports(
                titleblock.Id, page, items_by_id, page_number + 1)
            for failure in page_failures:
                preflight_failures.append("Página {}: {}".format(page_number + 1, failure))
        if preflight_failures:
            details = "\n".join("• {}".format(entry) for entry in preflight_failures[:12])
            if len(preflight_failures) > 12:
                details += "\n• +{} vista(s) com falha.".format(len(preflight_failures) - 12)
            raise PreviewConflictError(
                "A pré-validação detetou vista(s) que o Revit não aceita nesta composição. "
                "Nenhuma folha será mantida e as escalas serão revertidas.\n\n{}\n\n"
                "Contingência: remova ou substitua as vistas indicadas e volte a confirmar. "
                "Se uma vista já estiver noutra folha, retire-a dessa folha primeiro."
                .format(details))
        for page_number, page in nonempty_pages:
            sheet_scope = DB.SubTransaction(doc)
            if sheet_scope.Start() != DB.TransactionStatus.Started:
                raise Exception("Não foi possível iniciar a transação auxiliar da folha {}.".format(page_number + 1))
            sheet = None
            page_created = []
            page_skipped_start = len(skipped)
            try:
                sheet = DB.ViewSheet.Create(doc, titleblock.Id)
                if sheet is None:
                    raise Exception("O Revit não criou a folha.")
                sheet_number, number_cursor = unique_sheet_number(
                    setup["prefix"], number_cursor, used_numbers)
                sheet.SheetNumber = sheet_number
                if len(nonempty_pages) == 1:
                    sheet.Name = setup["sheet_title"]
                else:
                    sheet.Name = "{} - {:02d}".format(setup["sheet_title"], len(created) + 1)
                doc.Regenerate()
                for item_id, center in page.items():
                    item = items_by_id.get(item_id)
                    if item is None:
                        skipped.append("Vista ID {} não encontrada na composição.".format(item_id))
                        continue
                    view = item["view"]
                    try:
                        if (not item.get("is_schedule") and
                                not DB.Viewport.CanAddViewToSheet(doc, sheet.Id, view.Id)):
                            skipped.append("{} [ID {} | {}] não pode ser colocada na folha {}: CanAddViewToSheet=False.".format(
                                item["name"], item["id"], item["kind"], sheet_number))
                            continue
                    except Exception as error:
                        skipped.append("{} [ID {} | {}] — validação de colocação: {}".format(
                            item["name"], item["id"], item["kind"], safe_text(error)))
                        continue
                    item_scope = DB.SubTransaction(doc)
                    if item_scope.Start() != DB.TransactionStatus.Started:
                        skipped.append("{} [ID {} | {}] na folha {} — não foi possível iniciar a operação de colocação.".format(
                            item["name"], item["id"], item["kind"], sheet_number))
                        continue
                    try:
                        if item.get("is_schedule"):
                            layout_element = DB.ScheduleSheetInstance.Create(
                                doc, sheet.Id, view.Id,
                                DB.XYZ(center[0], center[1], 0.0))
                            if layout_element is None:
                                raise Exception("O Revit não retornou uma instância de tabela.")
                            doc.Regenerate()
                            measured_item = make_schedule_item(view, layout_element)
                            move_layout_item(item, layout_element, center, measured_item)
                        else:
                            offset_x, offset_y = item.get("box_offset", (0.0, 0.0))
                            viewport_center = DB.XYZ(center[0] + offset_x,
                                                     center[1] + offset_y, 0.0)
                            layout_element = DB.Viewport.Create(
                                doc, sheet.Id, view.Id, viewport_center)
                            if layout_element is None:
                                raise Exception("O Revit não retornou uma viewport criada.")
                        doc.Regenerate()
                        if item_scope.Commit() != DB.TransactionStatus.Committed:
                            raise Exception("O Revit não confirmou a colocação do elemento.")
                        page_created.append((item, layout_element, center))
                    except Exception as error:
                        try:
                            if item_scope.GetStatus() == DB.TransactionStatus.Started:
                                item_scope.RollBack()
                        except Exception:
                            pass
                        skipped.append("{} [ID {} | {}] na folha {} — {}".format(
                            item["name"], item["id"], item["kind"], sheet_number, safe_text(error)))
                if len(page_created) != len(page):
                    page_failures = skipped[page_skipped_start:]
                    details = "\n".join("• {}".format(entry) for entry in page_failures[:12])
                    if len(page_failures) > 12:
                        details += "\n• +{} falha(s) adicionais.".format(len(page_failures) - 12)
                    raise PreviewConflictError(
                        "A página {} recebeu apenas {} de {} elemento(s). A criação foi cancelada "
                        "e nenhuma folha incompleta será mantida.\n\n{}"
                        .format(page_number + 1, len(page_created), len(page),
                                details or "O Revit não forneceu o motivo da rejeição."))
                if not page_created:
                    sheet_scope.RollBack()
                    warnings.append("A composição da página {} não gerou elementos; a folha vazia não foi mantida.".format(page_number + 1))
                    continue
                if sheet_scope.Commit() != DB.TransactionStatus.Committed:
                    raise Exception("O Revit não confirmou a criação desta folha.")
                doc.Regenerate()
                actual_items = {}
                actual_placements = {}
                for item, layout_element, center in page_created:
                    refreshed = refresh_layout_item(item, layout_element)
                    if refreshed:
                        actual_items[item["id"]] = refreshed
                        actual_placements[item["id"]] = refreshed["current_center"]
                if len(actual_items) != len(page_created):
                    raise PreviewConflictError(
                        "A validação real da página {} reconheceu apenas {} de {} elementos. "
                        "Toda a operação será revertida."
                        .format(page_number + 1, len(actual_items), len(page_created)))
                if actual_items:
                    single_page = (len(nonempty_pages) == 1 and
                                   len(page_created) == len(page))
                    fit_notes = correct_actual_page_layout(
                        page_created, sheet_bounds(sheet), setup["margin_ft"],
                        setup["gap_ft"], setup["layout"], single_page)
                    warnings.extend(fit_notes)
                    doc.Regenerate()
                    actual_items = {}
                    actual_placements = {}
                    for item, layout_element, center in page_created:
                        refreshed = refresh_layout_item(item, layout_element)
                        if refreshed:
                            actual_items[item["id"]] = refreshed
                            actual_placements[item["id"]] = refreshed["current_center"]
                    if len(actual_items) != len(page_created):
                        raise PreviewConflictError(
                            "Após a correção geométrica, a página {} não pôde ser validada por completo. "
                            "Toda a operação será revertida.".format(page_number + 1))
                actual_outside, actual_overlaps = page_diagnostics(
                    actual_items, actual_placements, sheet_bounds(sheet), setup["margin_ft"])
                if (actual_outside or actual_overlaps) and not preview_result.get("allow_conflicts", False):
                    outside_detail = describe_outside_items(
                        actual_items, actual_outside, sheet_bounds(sheet), setup["margin_ft"])
                    raise PreviewConflictError(
                        "A validação final da página {} encontrou {} item(ns) fora da margem e {} sobreposição(ões). A operação foi revertida. Detalhes: {}".format(
                            page_number + 1, len(actual_outside), len(actual_overlaps),
                            outside_detail or "Verifique as dimensões reais dos rótulos."))
                for item_id in actual_outside:
                    warnings.append("{} ultrapassa a margem segura em {} após considerar o rótulo real.".format(
                        items_by_id[item_id]["name"], sheet_number))
                for id_a, id_b in actual_overlaps:
                    warnings.append("Sobreposição entre {} e {} em {} após considerar os rótulos reais.".format(
                        items_by_id[id_a]["name"], items_by_id[id_b]["name"], sheet_number))
                created.append({"number": sheet_number, "name": safe_text(sheet.Name), "count": len(page_created)})
                for item, layout_element, center in page_created:
                    moved.append("{} → {}".format(item["name"], sheet_number))
            except PreviewConflictError:
                try:
                    if sheet_scope.GetStatus() == DB.TransactionStatus.Started:
                        sheet_scope.RollBack()
                except Exception:
                    pass
                raise
            except Exception as error:
                try:
                    if sheet_scope.GetStatus() == DB.TransactionStatus.Started:
                        sheet_scope.RollBack()
                except Exception:
                    pass
                raise PreviewConflictError(
                    "A página {} falhou durante a criação. Toda a operação foi revertida, "
                    "incluindo as folhas anteriores e as alterações de escala.\n\nDetalhe: {}"
                    .format(page_number + 1, safe_text(error)))
        if not created:
            transaction.RollBack()
            raise Exception("Nenhuma folha recebeu uma viewport válida. Nenhuma alteração foi mantida.")
        commit_status = transaction.Commit()
        if commit_status != DB.TransactionStatus.Committed:
            raise Exception("O Revit não confirmou a criação (estado: {}).".format(commit_status))
    except Exception:
        try:
            if transaction.GetStatus() == DB.TransactionStatus.Started:
                transaction.RollBack()
        except Exception:
            pass
        raise
    for item_id in preview_result.get("ignored_ids", []):
        item = items_by_id.get(item_id)
        if item:
            skipped.append("{} ficou sem prancha por opção do usuário.".format(item["name"]))
    return {"created": created, "moved": moved, "skipped": skipped,
            "warnings": warnings, "errors": []}


def make_setup_initial(setup):
    return {
        "mode": "Organizar a folha ativa" if setup["mode"] == "active" else
                "Criar pranchas e distribuir vistas",
        "layout": setup["layout"],
        "scale_mode": setup.get("scale_mode", SCALE_KEEP),
        "keep_scales": bool(setup.get("keep_scales", setup.get("scale_mode") == SCALE_KEEP)),
        "custom_scale": setup.get("custom_scale") or 100,
        "single_sheet": bool(setup.get("single_sheet", False)),
        "profile_name": setup.get("profile_name", "Personalizado"),
        "margin_cm": setup["margin_cm"],
        "requested_sheet_count": setup.get("requested_sheet_count") or "Automático",
        "plan_scale": setup.get("preferred_scales", {}).get("floorplan", 100),
        "section_scale": setup.get("preferred_scales", {}).get("section", 100),
        "detail_scale": setup.get("preferred_scales", {}).get("detail", 20),
        "gap_cm": setup["gap_cm"],
        "number_prefix": setup["prefix"],
        "number_start": setup["number_start"],
        "sheet_title": setup["sheet_title"],
        "selected_ids": setup["selected_ids"],
        "titleblock_id": id_value(setup["titleblock_id"]) if setup["titleblock_id"] else ""
    }


def main():
    SCHEDULE_METRICS_CACHE.clear()
    if doc is None or uidoc is None:
        forms.alert("Abra um projeto no Revit antes de usar o Fólio.", title=TITLE)
        return
    try:
        if doc.IsFamilyDocument:
            forms.alert("Esta ferramenta trabalha em documentos de projeto, não em famílias.", title=TITLE)
            return
        if doc.IsReadOnly:
            forms.alert("O documento está somente para leitura. Abra uma cópia editável antes de continuar.", title=TITLE)
            return
        if doc.IsModifiable:
            forms.alert("Finalize a transação ativa antes de abrir o Fólio.", title=TITLE)
            return
    except Exception as error:
        forms.alert("Não foi possível validar o documento: {}".format(safe_text(error)), title=TITLE, warn_icon=True)
        return
    active_view = doc.ActiveView
    active_sheet = active_view if isinstance(active_view, DB.ViewSheet) else None
    candidates, diagnostics = collect_view_candidates()
    active_items = active_sheet_viewports(active_sheet)
    titleblocks = titleblock_types()
    if not candidates and not active_items:
        forms.alert("Não foram encontradas vistas que possam ser organizadas. Verifique se existem vistas elegíveis e se alguma folha está ativa.",
                    title=TITLE, warn_icon=True)
        return
    if not titleblocks and active_sheet is None:
        forms.alert("O Fólio não encontrou uma família/tipo de carimbo no documento ativo.\n\nDiagnóstico: {}\n\nConfirme que a família foi carregada neste mesmo projeto do Revit (não apenas aberta na biblioteca ou noutro documento).".format(
                        titleblock_scan_summary()),
                    title=TITLE, warn_icon=True)
        return
    initial = None
    while True:
        try:
            setup = show_setup_window(active_sheet, candidates, active_items, titleblocks,
                                      diagnostics, initial)
        except Exception as error:
            forms.alert("Não foi possível abrir a janela inicial do Fólio.\n\nDetalhe: {}".format(
                safe_text(error)), title=TITLE, warn_icon=True)
            return
        if not setup:
            return
        phase = "preparar o tipo de carimbo carregado"
        try:
            if setup["mode"] == "create":
                setup["bounds"] = titleblock_bounds(setup["titleblock"])
            else:
                setup["bounds"] = sheet_bounds(active_sheet)
            setup["items"], setup["scale_summary"], setup["scale_locked_items"] = plan_scale_items(
                setup["items"], setup["bounds"], setup["margin_ft"],
                setup.get("scale_mode", SCALE_KEEP), setup.get("custom_scale"),
                setup.get("preferred_scales"))
            if (setup.get("mode") == "create" and setup.get("single_sheet") and
                    setup.get("scale_mode") == SCALE_AUTO):
                setup["items"], setup["scale_summary"] = fit_items_to_single_page(
                    setup["items"], setup["bounds"], setup["margin_ft"],
                    setup["gap_ft"], setup["layout"])
            insets = margin_insets(setup["margin_ft"])
            usable_width = max(0.0, setup["bounds"][2] - setup["bounds"][0] - insets[0] - insets[2])
            usable_height = max(0.0, setup["bounds"][3] - setup["bounds"][1] - insets[1] - insets[3])
            setup["scale_summary"] += "\nÁrea útil configurada: {:.1f} × {:.1f} cm.".format(
                ft_to_cm(usable_width), ft_to_cm(usable_height))
            setup["scale_summary"] += "\n" + summarize_annotation_diagnostics(setup["items"])
            state = initial_preview_state(setup)
            phase = "abrir a janela de prévia"
            preview_result = preview_window(setup, state)
            if not preview_result or preview_result.get("action") == "cancel":
                return
            if preview_result.get("action") == "back":
                initial = make_setup_initial(setup)
                continue
            if not preview_result.get("accepted"):
                return
            phase = "aplicar a composição no Revit"
            if setup["mode"] == "active":
                result = execute_active_sheet(setup, preview_result)
                result_dialog("Organização concluída", "As viewports selecionadas foram reorganizadas na folha ativa.",
                              result["created"], result["moved"], result["skipped"], result["warnings"], result["errors"])
            else:
                result = execute_sheet_creation(setup, preview_result)
                result_dialog("Montagem concluída", "O Fólio concluiu a criação e validou o posicionamento das vistas.",
                              result["created"], result["moved"], result["skipped"], result["warnings"], result["errors"])
            return
        except Exception as error:
            forms.alert("Falha ao {}. As alterações não confirmadas foram revertidas.\n\nDetalhe: {}".format(
                phase, safe_text(error)), title=TITLE, warn_icon=True)
            return


if __name__ == "__main__":
    main()
