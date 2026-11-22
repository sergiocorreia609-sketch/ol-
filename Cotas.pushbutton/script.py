# -*- coding: utf-8 -*-
"""Tudo32f | Cotas automáticas para Revit.

This version adds an explicit scope/configuration and review step, duplicate
protection, cancellation-safe transactions, and actionable diagnostics.
"""
from Autodesk.Revit.DB import *
from Autodesk.Revit.UI.Selection import ISelectionFilter, ObjectType
from Autodesk.Revit.Exceptions import OperationCanceledException
import clr
clr.AddReference("WindowsBase")
clr.AddReference("PresentationCore")
clr.AddReference("PresentationFramework")
from System.Collections.Generic import List
from pyrevit import revit, forms, script

doc = revit.doc
view = doc.ActiveView
CM = UnitTypeId.Centimeters
MM = UnitTypeId.Millimeters
ZERO = UnitUtils.ConvertToInternalUnits(0.5, CM)
MERGE = UnitUtils.ConvertToInternalUnits(20, CM)
INTERSECTION = UnitUtils.ConvertToInternalUnits(30, CM)
EXTENSION = UnitUtils.ConvertToInternalUnits(10, CM)
LINE_SPACING = UnitUtils.ConvertToInternalUnits(30, CM)
MARKER = "BIMCODER_AUTO_DIM_V2"


def ask_float(prompt, default, positive=True):
    raw = forms.ask_for_string(default=default, prompt=prompt,
                               title="Cotas automáticas Tudo32f")
    if raw is None:
        script.exit()
    try:
        value = float(raw.replace(',', '.'))
        if positive and value <= 0:
            raise ValueError()
        return value
    except Exception:
        forms.alert("Valor inválido. Informe um número maior que zero.",
                    exitscript=True)


def type_label(dt):
    try:
        family = dt.FamilyName or "Cota"
    except Exception:
        family = "Cota"
    try:
        name = dt.get_Parameter(BuiltInParameter.SYMBOL_NAME_PARAM).AsString()
    except Exception:
        name = dt.Name
    return "{} : {} [ID {}]".format(family, name or "Sem nome", dt.Id)


def show_setup_window(type_map, config, scope_options):
    """Single, branded setup dialog with saved defaults and inline validation."""
    labels = sorted(type_map.keys())
    saved_scopes = getattr(config, "scopes", [scope_options[0], scope_options[2], scope_options[3]])
    saved_type = str(getattr(config, "dimension_type_id", ""))
    default_type = next((label for label, dt in type_map.items()
                         if str(dt.Id.IntegerValue) == saved_type), None)
    xaml = '''<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="Tudo32f | Cotas Inteligentes" Width="520" Height="650"
        ResizeMode="NoResize" WindowStartupLocation="CenterOwner"
        ShowInTaskbar="False" Background="#F3F6FA" FontFamily="Segoe UI">
      <Grid Margin="0">
        <Grid.RowDefinitions>
          <RowDefinition Height="112"/><RowDefinition Height="*"/>
          <RowDefinition Height="76"/>
        </Grid.RowDefinitions>
        <Border Grid.Row="0" Background="#142B45" Padding="26,18">
          <StackPanel VerticalAlignment="Center">
            <TextBlock Text="Tudo32f" Foreground="#8DD7D0" FontSize="12" FontWeight="Bold"/>
            <TextBlock Text="Cotas Inteligentes" Foreground="White" FontSize="25" FontWeight="SemiBold" Margin="0,4,0,0"/>
            <TextBlock Text="Configure o escopo e o padrão desta execução" Foreground="#D7E2ED" FontSize="12" Margin="0,4,0,0"/>
          </StackPanel>
        </Border>
        <ScrollViewer Grid.Row="1" VerticalScrollBarVisibility="Auto">
          <StackPanel Margin="24,18,24,10">
            <TextBlock Text="O QUE COTAR" Foreground="#31506E" FontSize="11" FontWeight="Bold" Margin="0,0,0,8"/>
            <Border Background="White" BorderBrush="#D8E1EA" BorderThickness="1" CornerRadius="6" Padding="14">
              <StackPanel>
                <CheckBox x:Name="chkExterior" Content="Fachadas exteriores" Margin="0,2,0,8"/>
                <CheckBox x:Name="chkInterior" Content="Paredes interiores" Margin="0,2,0,8"/>
                <CheckBox x:Name="chkOpenings" Content="Portas e janelas hospedadas" Margin="0,2,0,8"/>
                <CheckBox x:Name="chkIntersections" Content="Intercepções de paredes" Margin="0,2,0,8"/>
                <CheckBox x:Name="chkGrids" Content="Eixos retos" Margin="0,2,0,0"/>
              </StackPanel>
            </Border>
            <TextBlock Text="PADRÃO GRÁFICO" Foreground="#31506E" FontSize="11" FontWeight="Bold" Margin="0,18,0,8"/>
            <Border Background="White" BorderBrush="#D8E1EA" BorderThickness="1" CornerRadius="6" Padding="14">
              <StackPanel>
                <TextBlock Text="Estilo de cota" Foreground="#52667A" Margin="0,0,0,5"/>
                <ComboBox x:Name="styleCombo" Height="30"/>
                <Grid Margin="0,14,0,0">
                  <Grid.ColumnDefinitions><ColumnDefinition/><ColumnDefinition/><ColumnDefinition/></Grid.ColumnDefinitions>
                  <StackPanel Grid.Column="0" Margin="0,0,8,0">
                    <TextBlock Text="Afastamento (cm)" Foreground="#52667A" FontSize="11"/>
                    <TextBox x:Name="gapInput" Height="29" Margin="0,4,0,0" Padding="6,4"/>
                  </StackPanel>
                  <StackPanel Grid.Column="1" Margin="4,0,4,0">
                    <TextBlock Text="Entre linhas (cm)" Foreground="#52667A" FontSize="11"/>
                    <TextBox x:Name="spacingInput" Height="29" Margin="0,4,0,0" Padding="6,4"/>
                  </StackPanel>
                  <StackPanel Grid.Column="2" Margin="8,0,0,0">
                    <TextBlock Text="Agrupamento (cm)" Foreground="#52667A" FontSize="11"/>
                    <TextBox x:Name="mergeInput" Height="29" Margin="0,4,0,0" Padding="6,4"/>
                  </StackPanel>
                </Grid>
              </StackPanel>
            </Border>
            <TextBlock x:Name="statusText" Text="As escolhas serão guardadas para a próxima execução." Foreground="#52667A" FontSize="11" TextWrapping="Wrap" Margin="2,12,2,0"/>
          </StackPanel>
        </ScrollViewer>
        <Border Grid.Row="2" Background="White" BorderBrush="#D8E1EA" BorderThickness="0,1,0,0" Padding="20,12">
          <Grid>
            <TextBlock Text="A prévia será apresentada antes de alterar o modelo." VerticalAlignment="Center" Foreground="#52667A" FontSize="11" Margin="0,0,190,0" TextWrapping="Wrap"/>
            <StackPanel Orientation="Horizontal" HorizontalAlignment="Right" VerticalAlignment="Center">
              <Button x:Name="cancelButton" Content="Cancelar" Width="84" Height="34" Margin="0,0,8,0" Background="White" BorderBrush="#C7D2DE"/>
              <Button x:Name="runButton" Content="Analisar" Width="104" Height="34" Background="#087E83" Foreground="White" FontWeight="SemiBold" BorderThickness="0"/>
            </StackPanel>
          </Grid>
        </Border>
      </Grid>
    </Window>'''
    window = forms.WPFWindow(xaml, literal_string=True, handle_esc=True, set_owner=True)
    for name, control in (("Fachadas exteriores", window.chkExterior),
                          ("Paredes interiores", window.chkInterior),
                          ("Cotar portas e janelas", window.chkOpenings),
                          ("Cotar intercepções", window.chkIntersections),
                          ("Cotar eixos retos", window.chkGrids)):
        control.IsChecked = name in saved_scopes
    for label in labels:
        window.styleCombo.Items.Add(label)
    if default_type in type_map:
        window.styleCombo.SelectedItem = default_type
    elif labels:
        window.styleCombo.SelectedIndex = 0
    window.gapInput.Text = str(getattr(config, "gap_cm", 50))
    window.spacingInput.Text = str(getattr(config, "spacing_cm", 30))
    window.mergeInput.Text = str(getattr(config, "merge_cm", 20))
    result = {"accepted": False}

    def read_positive(text, label):
        try:
            value = float(text.replace(",", "."))
            if value <= 0:
                raise ValueError()
            return value
        except Exception:
            raise ValueError("{} deve ser um número maior que zero.".format(label))

    def on_run(sender, args):
        scopes = []
        if window.chkExterior.IsChecked:
            scopes.append("Fachadas exteriores")
        if window.chkInterior.IsChecked:
            scopes.append("Paredes interiores")
        if window.chkOpenings.IsChecked:
            scopes.append("Cotar portas e janelas")
        if window.chkIntersections.IsChecked:
            scopes.append("Cotar intercepções")
        if window.chkGrids.IsChecked:
            scopes.append("Cotar eixos retos")
        if not ("Fachadas exteriores" in scopes or "Paredes interiores" in scopes or
                "Cotar eixos retos" in scopes):
            window.statusText.Text = "Selecione fachadas, paredes interiores ou eixos retos."
            return
        try:
            result.update({"accepted": True, "scopes": scopes,
                "choice": window.styleCombo.SelectedItem,
                "gap_cm": read_positive(window.gapInput.Text, "Afastamento"),
                "spacing_cm": read_positive(window.spacingInput.Text, "Espaçamento"),
                "merge_cm": read_positive(window.mergeInput.Text, "Agrupamento")})
        except Exception as error:
            window.statusText.Text = str(error)
            return
        window.Close()

    window.runButton.Click += on_run
    window.cancelButton.Click += lambda sender, args: window.Close()
    window.ShowDialog()
    if not result["accepted"]:
        return None
    result["include_openings"] = "Cotar portas e janelas" in result["scopes"]
    result["include_intersections"] = "Cotar intercepções" in result["scopes"]
    result["include_grids"] = "Cotar eixos retos" in result["scopes"]
    result["dimension_type"] = type_map[result["choice"]]
    return result


def show_legacy_setup(type_map, config, scope_options):
    """Accessible fallback if WPF cannot load on a particular Revit host."""
    saved = getattr(config, "scopes", [scope_options[0], scope_options[2], scope_options[3]])
    selected = forms.SelectFromList.show(scope_options,
        title="Cotas Inteligentes | Escopo", button_name="Continuar",
        multiselect=True, defaults=saved)
    if selected is None:
        return None
    saved_id = str(getattr(config, "dimension_type_id", ""))
    default_label = next((label for label, dt in type_map.items()
                          if str(dt.Id.IntegerValue) == saved_id), None)
    labels = sorted(type_map.keys())
    choice = forms.SelectFromList.show(labels,
        title="Cotas Inteligentes | Estilo", button_name="Usar estilo",
        multiselect=False, defaults=[default_label] if default_label else None)
    if not choice:
        return None
    return {"accepted": True, "scopes": selected, "choice": choice,
        "dimension_type": type_map[choice],
        "gap_cm": ask_float("Afastamento da primeira linha (cm)", str(getattr(config, "gap_cm", 50))),
        "spacing_cm": ask_float("Espaçamento entre cadeias (cm)", str(getattr(config, "spacing_cm", 30))),
        "merge_cm": ask_float("Tolerância para agrupar trechos (cm)", str(getattr(config, "merge_cm", 20))),
        "include_openings": "Cotar portas e janelas" in selected,
        "include_intersections": "Cotar intercepções" in selected,
        "include_grids": "Cotar eixos retos" in selected}


class CandidateWallSelectionFilter(ISelectionFilter):
    def __init__(self, wall_ids):
        self.wall_ids = set(wall_ids)

    def AllowElement(self, element):
        try:
            return isinstance(element, Wall) and element.Id.IntegerValue in self.wall_ids
        except Exception:
            return False

    def AllowReference(self, reference, point):
        return False


class CreatedWallFilter(ISelectionFilter):
    def __init__(self, wall_ids):
        self.wall_ids = set(wall_ids)
    def AllowElement(self, element):
        try:
            return isinstance(element, Wall) and element.Id.IntegerValue in self.wall_ids
        except Exception:
            return False
    def AllowReference(self, reference, point):
        return False


def review_created_dimension_sides(dimension_map):
    """Mirror created dimension chains around the clicked wall axis."""
    if not dimension_map:
        return 0
    wall_ids = set()
    for dim_id, ids, normal in dimension_map:
        wall_ids.update(ids)
    uidoc = revit.uidoc
    moved_count = 0
    failed_count = 0
    pick_filter = CreatedWallFilter(wall_ids)
    while True:
        try:
            picked = uidoc.Selection.PickObject(
                ObjectType.Element, pick_filter,
                "Clique numa parede cotada para passar as cotas para o lado oposto. Esc termina.")
        except OperationCanceledException:
            break
        except Exception:
            break
        wall = doc.GetElement(picked.ElementId)
        if not wall:
            continue
        wall_id = wall.Id.IntegerValue
        location = wall.Location
        wall_curve = location.Curve if isinstance(location, LocationCurve) else None
        if wall_curve is None:
            continue
        center = wall_curve.Evaluate(0.5, True)
        pending_moves = 0
        tx = Transaction(doc, "Cotas Inteligentes - mudar lado")
        tx.Start()
        try:
            for dim_id, ids, normal in dimension_map:
                if wall_id not in ids:
                    continue
                dim = doc.GetElement(dim_id)
                dimension_curve = dim.Curve if dim else None
                if not isinstance(dimension_curve, Line):
                    continue
                # Dimension.Curve is unbound in Revit: use its Origin instead
                # of GetEndPoint(), which throws for an unbound line.
                point = dimension_curve.Origin
                tangent = normal.CrossProduct(XYZ.BasisZ).Normalize()
                anchor = center + tangent * (point - center).DotProduct(tangent)
                vector = point - anchor
                if vector.GetLength() < 1e-8:
                    continue
                ElementTransformUtils.MoveElement(
                    doc, dim.Id, vector.Negate().Subtract(vector))
                pending_moves += 1
            tx.Commit()
            moved_count += pending_moves
        except Exception:
            if tx.HasStarted():
                tx.RollBack()
            failed_count += 1
    if failed_count:
        forms.alert("{} tentativa(s) de reposicionamento falharam; as alterações dessas tentativas foram revertidas.".format(failed_count), title="Cotas Inteligentes")
    return moved_count


def review_wall_selection(walls):
    """Let the user choose eligible walls directly in the active Revit view."""
    if not walls:
        return []
    uidoc = revit.uidoc
    by_id = dict((wall.Id.IntegerValue, wall) for wall in walls)
    try:
        preselected = List[Reference]()
        for wall in walls:
            preselected.Add(Reference(wall))
        picked = uidoc.Selection.PickObjects(
            ObjectType.Element,
            CandidateWallSelectionFilter(by_id.keys()),
            "Cotas Inteligentes: paredes identificadas realçadas. Ctrl+clique para retirar ou adicionar paredes; Enter conclui.",
            preselected)
    except Exception:
        return None
    selected_ids = set(reference.ElementId.IntegerValue for reference in picked)
    selected = [wall for wall_id, wall in by_id.items() if wall_id in selected_ids]
    if not selected:
        forms.alert("Nenhuma parede foi selecionada; não serão criadas cotas.",
                    title="Cotas Inteligentes")
        return None
    return selected


def stable(reference):
    try:
        return reference.ConvertToStableRepresentation(doc)
    except Exception:
        return None


def projection_range(bbox, direction):
    values = [XYZ(x, y, z).DotProduct(direction)
              for x in (bbox.Min.X, bbox.Max.X)
              for y in (bbox.Min.Y, bbox.Max.Y)
              for z in (bbox.Min.Z, bbox.Max.Z)]
    return min(values), max(values)


def compact(items):
    items.sort(key=lambda x: x[0])
    out, refs = [], set()
    for measure, reference, priority in items:
        key = stable(reference)
        if not key or key in refs:
            continue
        refs.add(key)
        if out and measure - out[-1][0] < ZERO:
            if priority > out[-1][2]:
                out[-1] = (measure, reference, priority)
            continue
        out.append((measure, reference, priority))
    return out


def get_dimension_types():
    return [x for x in FilteredElementCollector(doc).OfClass(DimensionType)
            if x.StyleType == DimensionStyleType.Linear]


def existing_signature(dimension):
    try:
        refs = [stable(r) for r in dimension.References]
        refs = [r for r in refs if r]
        return (dimension.DimensionType.Id.IntegerValue, tuple(sorted(refs)))
    except Exception:
        return None


def existing_dimensions():
    result = set()
    for dim in FilteredElementCollector(doc, view.Id).OfClass(Dimension):
        sig = existing_signature(dim)
        if sig:
            result.add(sig)
    return result


def is_managed_dimension(dimension):
    try:
        p = dimension.get_Parameter(BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
        return bool(p and p.HasValue and p.AsString() == MARKER)
    except Exception:
        return False


def dimension_text_size(dimension):
    try:
        p = dimension.DimensionType.get_Parameter(BuiltInParameter.DIM_TEXT_SIZE)
        if p and p.HasValue and p.AsDouble() > 0:
            return p.AsDouble() * view.Scale
    except Exception:
        pass
    return UnitUtils.ConvertToInternalUnits(2.5, MM) * view.Scale


def label_overlaps(a, apos, b, bpos):
    delta = apos - bpos
    return (abs(delta.DotProduct(view.RightDirection)) < a["w"] + b["w"] + a["margin"] and
            abs(delta.DotProduct(view.UpDirection)) < a["h"] + b["h"] + a["margin"])


def resolve_text_collisions(dimensions):
    labels = []
    for dim, tangent, normal in dimensions:
        segments = []
        try:
            segments = list(dim.Segments)
        except Exception:
            pass
        targets = segments if segments else [dim]
        for target in targets:
            try:
                if not target.IsTextPositionAdjustable():
                    continue
                pos = target.TextPosition
                anchor = target.Origin
                text = target.ValueString or ""
                if pos is None:
                    continue
                size = dimension_text_size(dim)
                labels.append({"dim": dim, "target": target, "pos": pos,
                    "anchor": anchor, "normal": normal,
                    "w": max(size * .8, len(text) * size * .32),
                    "h": size * .65, "size": size, "margin": size * .2})
            except Exception:
                continue
    moved = unresolved = 0
    for i, label in enumerate(labels):
        if not any(label_overlaps(label, label["pos"], other, other["pos"])
                   for j, other in enumerate(labels) if i != j):
            continue
        original = label["pos"]
        candidate_found = None
        for step in range(1, 7):
            for sign in (1, -1):
                candidate = original + label["normal"] * (label["size"] * .9 * step * sign)
                if not any(label_overlaps(label, candidate, other, other["pos"])
                           for j, other in enumerate(labels) if i != j):
                    candidate_found = candidate
                    break
            if candidate_found:
                break
        if candidate_found is None:
            unresolved += 1
            continue
        try:
            had_leader = bool(label["dim"].HasLeader)
            label["dim"].HasLeader = True
            label["target"].TextPosition = candidate_found
            label["target"].LeaderEndPosition = label["anchor"]
            label["pos"] = candidate_found
            moved += 1
        except Exception:
            try:
                label["target"].TextPosition = original
                label["dim"].HasLeader = had_leader
            except Exception:
                pass
            unresolved += 1
    return moved, unresolved


def build_grid_plan():
    grids = []
    curved = 0
    for grid in FilteredElementCollector(doc, view.Id).OfClass(Grid):
        try:
            curve = grid.Curve
            line = curve if isinstance(curve, Line) else None
            if line is None:
                curved += 1
                continue
            p0, p1 = line.GetEndPoint(0), line.GetEndPoint(1)
            direction = XYZ(p1.X-p0.X, p1.Y-p0.Y, 0)
            if direction.GetLength() < 1e-9:
                continue
            direction = direction.Normalize()
            if direction.X < -1e-9 or (abs(direction.X) <= 1e-9 and direction.Y < 0):
                direction = direction.Negate()
            across = XYZ(-direction.Y, direction.X, 0)
            measure = (p0.DotProduct(across) + p1.DotProduct(across)) * .5
            along = [p0.DotProduct(direction), p1.DotProduct(direction)]
            grids.append({"grid": grid, "direction": direction,
                "across": across, "measure": measure,
                "lo": min(along), "hi": max(along)})
        except Exception:
            continue
    groups = []
    for item in grids:
        group = next((g for g in groups
            if abs(g["direction"].DotProduct(item["direction"])) > .999), None)
        if group is None:
            group = {"direction": item["direction"], "across": item["across"],
                     "items": [], "lo": item["lo"], "hi": item["hi"]}
            groups.append(group)
        group["items"].append(item)
        group["lo"] = min(group["lo"], item["lo"])
        group["hi"] = max(group["hi"], item["hi"])
    plans = []
    for group in groups:
        chain = []
        for item in group["items"]:
            try:
                chain.append((item["measure"], Reference(item["grid"]), 2))
            except Exception:
                continue
        chain.sort(key=lambda x: x[0])
        deduped = []
        seen = set()
        for item in chain:
            key = stable(item[1])
            if key and key not in seen:
                seen.add(key)
                deduped.append(item)
        if len(deduped) < 2:
            continue
        # Dimension axis crosses the grids; the line is placed beyond their ends.
        run = {"normal": group["direction"], "tangent": group["across"],
               "plane": group["hi"], "side_sign": 1}
        plans.append({"run": run, "ends": deduped,
                      "sets": [("Eixos", deduped)], "kind": "grid"})
    return plans, curved


def build_plan(walls, include_openings, include_intersections, wall_side):
    faces, runs, seen = [], [], set()
    diagnostics = {"sem_face": 0, "curvas": 0, "aberturas_sem_refs": 0,
                   "trechos_invalidos": 0}
    for wall in walls:
        try:
            side_refs = HostObjectUtils.GetSideFaces(wall, wall_side)
        except Exception:
            diagnostics["sem_face"] += 1
            continue
        for reference in side_refs:
            try:
                face = wall.GetGeometryObjectFromReference(reference)
                key = stable(reference)
                if not isinstance(face, PlanarFace):
                    diagnostics["curvas"] += 1
                    continue
                if not key or key in seen:
                    continue
                face_normal = XYZ(face.FaceNormal.X, face.FaceNormal.Y, 0)
                if face_normal.GetLength() < 1e-9:
                    continue
                face_normal = face_normal.Normalize()
                normal = face_normal
                # Give parallel faces a consistent orientation so both sides
                # of interior walls are grouped predictably.
                if normal.X < -1e-9 or (abs(normal.X) <= 1e-9 and normal.Y < 0):
                    normal = normal.Negate()
                tangent = XYZ(-normal.Y, normal.X, 0)
                vertices = list(face.Triangulate().Vertices)
                if not vertices:
                    continue
                seen.add(key)
                sample = face.Origin
                tv = [p.DotProduct(tangent) for p in vertices]
                nv = [p.DotProduct(normal) for p in vertices]
                entry = {"wall": wall, "ref": reference, "normal": normal,
                         "side_sign": 1 if face_normal.DotProduct(normal) >= 0 else -1,
                         "tangent": tangent, "sample": sample,
                         "plane": sample.DotProduct(normal),
                         "lo": min(tv), "hi": max(tv), "vertices": vertices}
                faces.append(entry)
                run = None
                for candidate in runs:
                    same_wall = wall.Id.IntegerValue in candidate["wall_ids"]
                    touches = entry["lo"] <= candidate["hi"] + MERGE and entry["hi"] >= candidate["lo"] - MERGE
                    if (candidate["normal"].DotProduct(normal) > .999 and
                        candidate["side_sign"] == entry["side_sign"] and
                        abs(candidate["plane"] - entry["plane"]) <= ZERO and
                        (same_wall or touches)):
                        run = candidate
                        break
                if run is None:
                    run = {"normal": normal, "tangent": tangent,
                           "plane": entry["plane"], "lo": entry["lo"],
                           "hi": entry["hi"], "side_sign": entry["side_sign"],
                           "wall_ids": set(), "faces": []}
                    runs.append(run)
                run["lo"] = min(run["lo"], entry["lo"])
                run["hi"] = max(run["hi"], entry["hi"])
                run["wall_ids"].add(wall.Id.IntegerValue)
                run["faces"].append(entry)
            except Exception:
                diagnostics["sem_face"] += 1

    if not runs:
        return [], diagnostics

    # Wall runs often terminate at walls outside the chosen dimension scope.
    # Use their real side-face references as endpoint references, without
    # adding those neighboring walls to the set of walls being dimensioned.
    endpoint_faces = list(faces)
    endpoint_keys = set(stable(face_item["ref"]) for face_item in endpoint_faces)
    endpoint_wall_ids = set()
    geometry_options = Options()
    geometry_options.ComputeReferences = True
    geometry_options.DetailLevel = ViewDetailLevel.Fine
    for endpoint_wall in FilteredElementCollector(doc, view.Id).OfClass(Wall):
        endpoint_wall_id = endpoint_wall.Id.IntegerValue
        if endpoint_wall_id in endpoint_wall_ids:
            continue
        endpoint_wall_ids.add(endpoint_wall_id)
        for endpoint_side in (ShellLayerType.Interior, ShellLayerType.Exterior):
            try:
                endpoint_refs = HostObjectUtils.GetSideFaces(endpoint_wall, endpoint_side)
            except Exception:
                continue
            for endpoint_ref in endpoint_refs:
                try:
                    endpoint_key = stable(endpoint_ref)
                    if not endpoint_key or endpoint_key in endpoint_keys:
                        continue
                    endpoint_face = endpoint_wall.GetGeometryObjectFromReference(endpoint_ref)
                    if not isinstance(endpoint_face, PlanarFace):
                        continue
                    endpoint_normal = XYZ(endpoint_face.FaceNormal.X,
                                          endpoint_face.FaceNormal.Y, 0)
                    if endpoint_normal.GetLength() < 1e-9:
                        continue
                    endpoint_normal = endpoint_normal.Normalize()
                    if (endpoint_normal.X < -1e-9 or
                            (abs(endpoint_normal.X) <= 1e-9 and endpoint_normal.Y < 0)):
                        endpoint_normal = endpoint_normal.Negate()
                    endpoint_vertices = list(endpoint_face.Triangulate().Vertices)
                    if not endpoint_vertices:
                        continue
                    endpoint_faces.append({"ref": endpoint_ref,
                        "normal": endpoint_normal, "sample": endpoint_face.Origin,
                        "vertices": endpoint_vertices})
                    endpoint_keys.add(endpoint_key)
                except Exception:
                    continue
        # GetSideFaces exposes the compound-structure finish faces, but not
        # every end cap created by joins, profiles, or edited wall geometry.
        # Extract original element geometry with references enabled so these
        # real planar faces can serve as chain endpoints too.
        try:
            geometry = endpoint_wall.get_Geometry(geometry_options)
            for geometry_object in geometry or []:
                if not isinstance(geometry_object, Solid) or geometry_object.Faces.Size == 0:
                    continue
                for geometry_face in geometry_object.Faces:
                    if not isinstance(geometry_face, PlanarFace):
                        continue
                    geometry_ref = geometry_face.Reference
                    geometry_key = stable(geometry_ref)
                    if not geometry_key or geometry_key in endpoint_keys:
                        continue
                    raw_normal = geometry_face.FaceNormal
                    face_normal = XYZ(raw_normal.X, raw_normal.Y, 0)
                    if face_normal.GetLength() < 1e-9:
                        continue
                    face_normal = face_normal.Normalize()
                    if (face_normal.X < -1e-9 or
                            (abs(face_normal.X) <= 1e-9 and face_normal.Y < 0)):
                        face_normal = face_normal.Negate()
                    vertices = list(geometry_face.Triangulate().Vertices)
                    if not vertices:
                        continue
                    endpoint_faces.append({"ref": geometry_ref,
                        "normal": face_normal, "sample": geometry_face.Origin,
                        "vertices": vertices})
                    endpoint_keys.add(geometry_key)
        except Exception:
            continue

    intersection_faces = []
    if include_intersections:
        for wall in FilteredElementCollector(doc, view.Id).OfClass(Wall):
            for side in (ShellLayerType.Interior, ShellLayerType.Exterior):
                try:
                    refs = HostObjectUtils.GetSideFaces(wall, side)
                except Exception:
                    continue
                for ref in refs:
                    try:
                        face = wall.GetGeometryObjectFromReference(ref)
                        if not isinstance(face, PlanarFace):
                            continue
                        n = XYZ(face.FaceNormal.X, face.FaceNormal.Y, 0)
                        verts = list(face.Triangulate().Vertices)
                        if n.GetLength() > 1e-9 and verts:
                            n = n.Normalize()
                            if n.X < -1e-9 or (abs(n.X) <= 1e-9 and n.Y < 0):
                                n = n.Negate()
                            intersection_faces.append({"ref": ref,
                                "normal": n, "sample": face.Origin,
                                "vertices": verts,
                                "wall_id": wall.Id.IntegerValue,
                                "side": side})
                    except Exception:
                        continue

    openings_by_wall = {}
    if include_openings:
        for category, param in ((BuiltInCategory.OST_Doors, BuiltInParameter.DOOR_WIDTH),
                                (BuiltInCategory.OST_Windows, BuiltInParameter.WINDOW_WIDTH)):
            for inst in FilteredElementCollector(doc, view.Id).OfCategory(category).WhereElementIsNotElementType():
                try:
                    host = inst.Host
                    if host and host.Id.IntegerValue in set(w.Id.IntegerValue for w in walls):
                        openings_by_wall.setdefault(host.Id.IntegerValue, []).append((inst, param))
                except Exception:
                    continue

    plan = []
    for run in runs:
        n, t = run["normal"], run["tangent"]
        ends = []
        for measure in (run["lo"], run["hi"]):
            candidates = []
            # A run's own exterior finish faces are parallel to its length.
            # The end references usually belong to perpendicular wall faces,
            # so search all visible exterior faces, not only this run.
            for f in endpoint_faces:
                if abs(f["normal"].DotProduct(t)) < .999:
                    continue
                m = f["sample"].DotProduct(t)
                if abs(m - measure) <= INTERSECTION:
                    nv = [v.DotProduct(n) for v in f["vertices"]]
                    if not (min(nv) - INTERSECTION <= run["plane"] <=
                            max(nv) + INTERSECTION):
                        continue
                    candidates.append((abs(m-measure), m, f["ref"], 2))
            if candidates:
                candidates.sort(key=lambda x: x[0])
                ends.append((candidates[0][1], candidates[0][2], 2))
        ends = compact(ends)
        if len(ends) < 2:
            diagnostics["trechos_invalidos"] += 1
            continue
        sets = [("Total", ends)]
        if include_intersections:
            chain = list(ends)
            for f in intersection_faces:
                if abs(f["normal"].DotProduct(t)) < .999:
                    continue
                m = f["sample"].DotProduct(t)
                if m <= ends[0][0] + ZERO or m >= ends[-1][0] - ZERO:
                    continue
                nv = [v.DotProduct(n) for v in f["vertices"]]
                if min(nv)-INTERSECTION <= run["plane"] <= max(nv)+INTERSECTION:
                    chain.append((m, f["ref"], 2))
            chain = compact(chain)
            if len(chain) > len(ends):
                sets.append(("Intercepções", chain))
        if include_openings:
            chain = list(ends)
            for wid in run["wall_ids"]:
                for inst, width_pid in openings_by_wall.get(wid, []):
                    try:
                        left = list(inst.GetReferences(FamilyInstanceReferenceType.Left) or [])
                        right = list(inst.GetReferences(FamilyInstanceReferenceType.Right) or [])
                        if not left or not right:
                            diagnostics["aberturas_sem_refs"] += 1
                            continue
                        loc = inst.Location
                        center = loc.Point if isinstance(loc, LocationPoint) else None
                        bbox = inst.get_BoundingBox(view)
                        if center is None:
                            if not bbox:
                                diagnostics["aberturas_sem_refs"] += 1
                                continue
                            center = (bbox.Min + bbox.Max) * .5
                        hand = XYZ(inst.HandOrientation.X, inst.HandOrientation.Y, 0)
                        align = hand.DotProduct(t)
                        p = inst.get_Parameter(width_pid)
                        width = p.AsDouble() if p and p.HasValue else 0
                        if width > 0 and abs(align) > .5:
                            half = width * abs(align) / 2
                            lo, hi = center.DotProduct(t)-half, center.DotProduct(t)+half
                        elif bbox:
                            lo, hi = projection_range(bbox, t)
                        else:
                            diagnostics["aberturas_sem_refs"] += 1
                            continue
                        lmeasure, rmeasure = (lo, hi) if align >= 0 else (hi, lo)
                        chain.extend([(lmeasure, r, 1) for r in left])
                        chain.extend([(rmeasure, r, 1) for r in right])
                    except Exception:
                        diagnostics["aberturas_sem_refs"] += 1
            chain = compact(chain)
            if len(chain) > len(ends):
                sets.append(("Vãos", chain))
        plan.append({"run": run, "ends": ends, "sets": sets})
    return plan, diagnostics


if not isinstance(view, ViewPlan) or view.GenLevel is None:
    forms.alert("Abra uma planta associada a um nível.", exitscript=True)

config = script.get_config()
types = get_dimension_types()
if not types:
    forms.alert("Não existem estilos de cota linear carregados.", exitscript=True)
type_map = {type_label(x): x for x in types}
scope_options = ["Fachadas exteriores", "Paredes interiores",
                 "Cotar portas e janelas", "Cotar intercepções", "Cotar eixos retos"]
try:
    setup = show_setup_window(type_map, config, scope_options)
except Exception as ui_error:
    forms.alert("A janela principal não pôde ser carregada. Vou abrir os controles "
                "compatíveis do pyRevit.\n\nDetalhe: {}".format(ui_error),
                title="Cotas Inteligentes | Compatibilidade")
    setup = show_legacy_setup(type_map, config, scope_options)
if setup is None:
    script.exit()
if not setup:
    script.exit()
selected = setup["scopes"]
include_openings = setup["include_openings"]
include_intersections = setup["include_intersections"]
dim_type = setup["dimension_type"]
choice = setup["choice"]
gap_cm, spacing_cm, merge_cm_value = setup["gap_cm"], setup["spacing_cm"], setup["merge_cm"]
gap = UnitUtils.ConvertToInternalUnits(gap_cm, CM)
spacing = UnitUtils.ConvertToInternalUnits(spacing_cm, CM)
MERGE = UnitUtils.ConvertToInternalUnits(merge_cm_value, CM)
config.scopes = list(selected)
config.dimension_type_id = str(dim_type.Id.IntegerValue)
config.gap_cm = gap_cm
config.spacing_cm = spacing_cm
config.merge_cm = merge_cm_value
script.save_config()

all_walls = list(FilteredElementCollector(doc, view.Id).OfClass(Wall))
exterior_walls, interior_walls, unclassified_walls = [], [], []
for wall in all_walls:
    try:
        wt = doc.GetElement(wall.GetTypeId())
        if not wt:
            unclassified_walls.append(wall)
            continue
        if wt.Function == WallFunction.Exterior:
            exterior_walls.append(wall)
        elif wt.Function == WallFunction.Interior:
            interior_walls.append(wall)
        else:
            unclassified_walls.append(wall)
    except Exception:
        unclassified_walls.append(wall)

# Walls whose type function cannot be read are still offered for review and
# face-reference extraction. Assign them to the requested wall-side scope.
if "Fachadas exteriores" in selected:
    exterior_walls.extend(unclassified_walls)
elif "Paredes interiores" in selected:
    interior_walls.extend(unclassified_walls)

candidate_walls = []
if "Fachadas exteriores" in selected:
    candidate_walls.extend(exterior_walls)
if "Paredes interiores" in selected:
    candidate_walls.extend(interior_walls)
reviewed_walls = review_wall_selection(candidate_walls)
if reviewed_walls is None:
    script.exit()
reviewed_ids = set([wall.Id.IntegerValue for wall in reviewed_walls])
exterior_walls = [wall for wall in exterior_walls
                  if wall.Id.IntegerValue in reviewed_ids]
interior_walls = [wall for wall in interior_walls
                  if wall.Id.IntegerValue in reviewed_ids]

plan, diagnostic = [], {"sem_face": 0, "curvas": 0,
    "aberturas_sem_refs": 0, "trechos_invalidos": 0}
scopes = []
if "Fachadas exteriores" in selected and exterior_walls:
    p, d = build_plan(exterior_walls, include_openings, include_intersections,
                      ShellLayerType.Exterior)
    plan.extend(p)
    scopes.append("fachadas exteriores ({})".format(len(exterior_walls)))
    for k in diagnostic:
        diagnostic[k] += d.get(k, 0)
if "Paredes interiores" in selected and interior_walls:
    p, d = build_plan(interior_walls, include_openings, include_intersections,
                      ShellLayerType.Interior)
    plan.extend(p)
    scopes.append("paredes interiores ({})".format(len(interior_walls)))
    for k in diagnostic:
        diagnostic[k] += d.get(k, 0)
curved_grids = 0
if "Cotar eixos retos" in selected:
    grid_plan, curved_grids = build_grid_plan()
    plan.extend(grid_plan)
    scopes.append("eixos retos ({})".format(len(grid_plan)))
wall_warnings = []
planned_wall_ids = set()
for planned_item in plan:
    planned_wall_ids.update(planned_item.get("run", {}).get("wall_ids", set()))
unplanned_walls = [wall for wall in reviewed_walls
                   if wall.Id.IntegerValue not in planned_wall_ids]
if unplanned_walls:
    labels = []
    for wall in unplanned_walls[:20]:
        try:
            wall_type = doc.GetElement(wall.GetTypeId())
            type_name = wall_type.Name if wall_type else "tipo desconhecido"
            function_name = str(wall_type.Function) if wall_type else "função desconhecida"
        except Exception:
            type_name, function_name = "tipo desconhecido", "função desconhecida"
        labels.append("ID {} | {} | {}".format(wall.Id.IntegerValue,
                                               function_name, type_name))
    wall_warnings.append(
        "{} de {} parede(s) selecionada(s) não produziram referências dimensionáveis: {}. "
        "Verifique faces, junções, partes e tipo de parede.".format(
            len(unplanned_walls), len(reviewed_walls), "; ".join(labels)))
if not plan:
    forms.alert("A análise terminou sem cadeias cotáveis.\n\n"
    "Paredes exteriores encontradas: {}\nParedes não exteriores encontradas: {}\n"
        "Trechos sem referências de extremidade: {}\nFaces sem leitura: {}\n\n"
        "Confira a função do tipo, vínculos/partes/junções e faces de acabamento das paredes."
        .format(len(exterior_walls), len(interior_walls), diagnostic["trechos_invalidos"],
                diagnostic["sem_face"]), exitscript=True)

preview = ["Tudo32f  |  PRÉVIA DE EXECUÇÃO", "",
    "Escopo: {}".format(", ".join(scopes) or "nenhuma parede encontrada"),
    "{} trecho(s); até {} linha(s) de cota.".format(
    len(plan), sum(len(p["sets"]) for p in plan)),
    "Estilo: {}".format(choice), "Afastamento inicial: {} cm".format(gap_cm),
    "Espaçamento entre cadeias: {} cm".format(spacing_cm),
    "Vãos: {}; intercepções: {}".format("sim" if include_openings else "não",
        "sim" if include_intersections else "não"),
    "Eixos retos: {}".format("sim" if "Cotar eixos retos" in selected else "não"),
    "A criação não altera nem apaga cotas existentes.",
    "Confirmar a criação?"]
if not forms.alert("\n".join(preview), yes=True, no=True):
    script.exit()

managed_dimensions = [d for d in FilteredElementCollector(doc, view.Id).OfClass(Dimension)
                      if is_managed_dimension(d)]
replace_managed = False
if managed_dimensions:
    replace_managed = forms.alert(
        "Encontrei {} cota(s) Tudo32f nesta vista.\n\n"
        "Deseja atualizá-las para refletir a geometria e as configurações atuais?\n"
        "As posições manuais dos textos dessas cotas serão recriadas.".format(len(managed_dimensions)),
        yes=True, no=True)

known = existing_dimensions()
if replace_managed:
    for old_dim in managed_dimensions:
        sig = existing_signature(old_dim)
        if sig in known:
            known.remove(sig)
created, skipped_duplicates, errors = 0, 0, []
new_dimensions = []
dimension_wall_map = []
transaction = Transaction(doc, "Tudo32f - cotas automáticas")
transaction.Start()
try:
    if replace_managed:
        doc.Delete(List[ElementId]([d.Id for d in managed_dimensions]))
    for item in plan:
        run, ends, sets = item["run"], item["ends"], item["sets"]
        n, t = run["normal"], run["tangent"]
        start, end = ends[0][0] - EXTENSION, ends[-1][0] + EXTENSION
        for idx, (chain_name, items) in enumerate(sets):
            refs = ReferenceArray()
            for measure, ref, priority in items:
                refs.Append(ref)
            sig = (dim_type.Id.IntegerValue, tuple(sorted(stable(ref) for _, ref, _ in items if stable(ref))))
            if sig in known:
                skipped_duplicates += 1
                continue
            offset = run["plane"] + run["side_sign"] * (gap + spacing * (len(sets)-idx-1))
            line = Line.CreateBound(
                XYZ(t.X*start+n.X*offset, t.Y*start+n.Y*offset, view.GenLevel.Elevation),
                XYZ(t.X*end+n.X*offset, t.Y*end+n.Y*offset, view.GenLevel.Elevation))
            try:
                dim = doc.Create.NewDimension(view, line, refs, dim_type)
                if dim:
                    new_dimensions.append((dim, t, n))
                    dimension_wall_map.append((dim.Id, set(run.get("wall_ids", set())), n))
                    # Store a marker in Comments when the dimension exposes it.
                    try:
                        p = dim.get_Parameter(BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
                        if p and not p.IsReadOnly:
                            p.Set(MARKER)
                    except Exception:
                        pass
                    known.add(sig)
                    created += 1
            except Exception as ex:
                errors.append("{}: {}".format(chain_name, ex))
    moved_texts = unresolved_texts = 0
    if created:
        doc.Regenerate()
        moved_texts, unresolved_texts = resolve_text_collisions(new_dimensions)
    if replace_managed and not created:
        transaction.RollBack()
        forms.alert("Nenhuma cota nova foi criada. As cotas anteriores foram preservadas.",
                    exitscript=True)
    transaction.Commit()
except Exception as ex:
    if transaction.HasStarted():
        transaction.RollBack()
    forms.alert("A transação foi revertida. Erro: {}".format(ex), exitscript=True)

side_moves = review_created_dimension_sides(dimension_wall_map) if created else 0
messages = ["Resultado: {} linha(s) criada(s); {} duplicata(s) ignorada(s).".format(created, skipped_duplicates)]
if created:
    messages.append("Revisão de lados concluída; {} cota(s) reposicionada(s).".format(side_moves))
actions = []
actions.extend(wall_warnings)
if moved_texts:
    messages.append("{} texto(s) reposicionado(s) com linha de chamada.".format(moved_texts))
if unresolved_texts:
    actions.append("{} possível(is) colisão(ões) de texto não resolvida(s); ajuste manualmente a posição.".format(unresolved_texts))
if diagnostic["trechos_invalidos"]:
    actions.append("{} trecho(s) sem referências de extremidade: revisar junções/continuidade das paredes.".format(diagnostic["trechos_invalidos"]))
if diagnostic["aberturas_sem_refs"]:
    actions.append("{} vão(s) sem referências Left/Right: revisar a família ou habilitar referências fortes.".format(diagnostic["aberturas_sem_refs"]))
if diagnostic["curvas"]:
    actions.append("{} face(s) curva(s)/não plana(s) ignoradas.".format(diagnostic["curvas"]))
if curved_grids:
    actions.append("{} eixo(s) curvo(s) ignorado(s); só eixos retos são cotados automaticamente.".format(curved_grids))
if diagnostic["sem_face"]:
    actions.append("{} face(s) não puderam ser lidas: verificar geometria editada/partes/joins.".format(diagnostic["sem_face"]))
if errors:
    actions.append("Falhas de criação (primeiras 5): " + " | ".join(errors[:5]))
if not created and skipped_duplicates:
    actions.append("As cotas propostas já existem. Nenhuma alteração foi feita.")
if actions:
    messages.append("Pendências e ações:\n- " + "\n- ".join(actions))
if not created and not skipped_duplicates:
    messages.append("Nenhuma cota foi criada; confira estilo, referências e compatibilidade da vista.")
forms.alert("\n".join(messages), title="Cotas automáticas - relatório")


