import bpy, json, os, math, subprocess, re, shutil, time, bmesh, tempfile
from bpy.props import CollectionProperty
from struct import unpack, pack
from bpy_extras.io_utils import ExportHelper, ImportHelper
from mathutils import Vector, Matrix
from .src.lib.helpers.material import MeshMaterial
from . import bl_info, TEMP_TEXTURES_PATH
from .src.lib.github_downloader import Github
from .src.lib.binary_reader import BinaryReader
from .src.lib.helpers.cryptography import generate_hash_from_directory
from .config import AddonSettings

TEMP_DIR = tempfile.gettempdir()
MESHPOINT_COLOR = (0.18039216101169586, 0.7686275243759155, 1.0)

ADDON_SETTINGS_FILE = os.path.join(os.environ["LOCALAPPDATA"], "sins2", "sins2-blender-extension", "settings.json")
CWD_PATH = os.path.dirname(os.path.abspath(__file__))
MESHBUILDER_EXE = os.path.join(
    CWD_PATH, "src", "lib", "tools", "meshbuilder", "meshbuilder.exe"
)
TEXCONV_EXE = os.path.join(CWD_PATH, "src", "lib", "tools", "texconv", "texconv.exe")

GAME_MATRIX = Matrix(((-1, 0, 0, 0), (0, 0, 1, 0), (0, 1, 0, 0), (0, 0, 0, 1)))
MESHPOINT_MATRIX = Matrix(((-1, 0, 0, 0), (0, 1, 0, 0), (0, 0, -1, 0), (0, 0, 0, 1)))

DUPLICATION_POSTFIX = r"(\-\d+)?"
MESHPOINTING_RULES = {
    "ability": rf"^ability(\.\d*)?{DUPLICATION_POSTFIX}$",
    "child": rf"^child\.(\w*)\.?(\d+)?{DUPLICATION_POSTFIX}$",
    "weapon": rf"^weapon\.\w+(\.\d+)?{DUPLICATION_POSTFIX}$",
    "hangar": rf"^hangar(\.\d*)?{DUPLICATION_POSTFIX}$",
    "bomb": rf"^bomb(\.\d+)?{DUPLICATION_POSTFIX}$",
    "exhaust": rf"^exhaust(\.\d*)?{DUPLICATION_POSTFIX}$",
    "aura": r"^aura$",
    "center": r"^center$",
    "above": r"^above$",
    "turret_muzzle": rf"^turret_muzzle(\.\d+)?{DUPLICATION_POSTFIX}$",
    "flair": rf"^flair(\.\w+)(\.?\d+)?{DUPLICATION_POSTFIX}$",
    "ship_build": r"^ship_build$",
    "extractor": r"^extractor$",
    # ---------------------------- from 2022  ---------------------------- #
    # - `exhaust` // ship exhaust effects                                  #
    # - `bomb` // planet bombing points                                    #
    # - `above` // for effects above                                       #
    # - `aura` // aura effects                                             #
    # - `center` // effects from center                                    #
    # - `extractor` // asteroid resource extractor attachment point        #
    # - `hangar` // strikecraft hangar position                            #
    # - `ship_build` // ship build effects                                 #
    # - `atmosphere_entry` // atmosphere entry effects                     # <-- no references?
    # - `build` // build effects                                           # <-- only sins 1 meshes reference this?
    # - `flair` // flair effects                                           #
    # - `ability` // ability effects                                       #
    # - `weapon`                                                           #
    # - `child`                                                            #
    # - `turret_muzzle`                                                    #
}

github = Github(TEMP_DIR)

# check for updates when extension activates
try:
    latest_version = github.fetch_latest_commit()
except:
    latest_version = None

settings = AddonSettings(ADDON_SETTINGS_FILE)
settings.init()

SETTINGS = settings.load_settings()

if "is_first_installation" in SETTINGS:
    SETTINGS["current_version"] = latest_version
    del SETTINGS["is_first_installation"]
    settings.save_settings()

has_update = SETTINGS["current_version"] != latest_version

class SINSII_Main_Panel:

    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Sins II Extension"


class SINSII_PT_Panel(SINSII_Main_Panel, bpy.types.Panel):
    bl_label = "Export"
    bl_order = 1

    def draw(self, context):
        col = self.layout.column(align=True)
        col.separator(factor=0.5)
        col.operator("sinsii.export_mesh", icon="MESH_CUBE", text="Export mesh")
        # col.separator(factor=1.5)
        # col.operator("sinsii.debug")
        col.separator(factor=0.5)
        box = col.box()
        box.operator("sinsii.import_mesh", icon="LOOP_FORWARDS", text="Import mesh")
        box.label(text="Primary, Secondary, Emissive")
        row = box.row()
        row.prop(context.scene.mesh_properties, "team_color_1")
        row.prop(context.scene.mesh_properties, "team_color_2")
        row.prop(context.scene.mesh_properties, "team_color_3")
        if SETTINGS["has_synchronized_meshpoint_color"] == False:
            col = col.column()
            col.separator(factor=1.0)
            col.operator("sinsii.sync_color", text="Synchronize Meshpoint Color")
        else:
            for theme in bpy.context.preferences.themes:
                if tuple(theme.view_3d.empty) != MESHPOINT_COLOR:
                    SETTINGS["has_synchronized_meshpoint_color"] = False
                    settings.save_settings()
                    break



class SINSII_PT_Mesh_Panel(SINSII_Main_Panel, bpy.types.Panel):
    bl_label = "Mesh"
    bl_options = {"DEFAULT_CLOSED"}
    bl_order = 3

    def draw(self, context):
        col = self.layout.column(align=True)
        mesh = get_selected_mesh()
        if not mesh or mesh.type != "MESH":
            col.label(text="Select a mesh...")
        else:
            col.label(text=f"Selected: {mesh.name}")
            col.operator("sinsii.spawn_shield", icon="MESH_CIRCLE")
            col.operator(
                "sinsii.create_buffs", icon="EMPTY_SINGLE_ARROW", text="Generate Buffs"
            )
            col.separator(factor=0.5)
            col.operator("sinsii.export_spatial", icon="META_BALL")



class SINSII_OT_Format_Meshpoints(bpy.types.Operator):
    bl_idname = "sinsii.format_meshpoints"
    bl_label = "Format"

    @classmethod
    def poll(cls, context):
        mesh = get_selected_mesh()
        return mesh and mesh.type == "EMPTY"

    def execute(self, context):
        meshpoints = get_selected_meshes(type="EMPTY")
        mesh_props = context.scene.mesh_properties

        def meshpoint_format(name, idx):
            return f"{name}.0-{i}" if mesh_props.duplicate_meshpoint_toggle else f"{name}.{i}"

        for _ in range(2):
            for i, meshpoint in enumerate(meshpoints):
                name = mesh_props.meshpoint_name if mesh_props.meshpoint_type == "custom" else mesh_props.meshpoint_type
                meshpoint.name = re.sub(r"\.\d{3}", "", meshpoint_format(name, i))
        return {"FINISHED"}

class SINSII_PT_Mesh_Point_Panel(SINSII_Main_Panel, bpy.types.Panel):
    bl_label = "Meshpoints"
    bl_options = {"DEFAULT_CLOSED"}
    bl_order = 4

    def draw(self, context):
        col = self.layout.column(align=True)
        col.label(text="Name")
        row = col.row()
        row.prop(context.scene.mesh_properties, "meshpoint_name")
        row.prop(context.scene.mesh_properties, "meshpoint_type")
        col.separator(factor=1.0)
        row = col.row()
        row.operator("sinsii.spawn_meshpoint", icon="EMPTY_AXIS")
        row.operator("sinsii.format_meshpoints")
        col.separator(factor=1.0)
        col.prop(context.scene.mesh_properties, "duplicate_meshpoint_toggle")
        col.separator(factor=1.0)
        col.label(text=f"Selected meshpoints: {len(get_selected_meshes(type='EMPTY'))}")

class SINSII_PT_Meshpoint_Turret(SINSII_Main_Panel, bpy.types.Panel):
    bl_label = "Turret"
    bl_parent_id = "SINSII_PT_Meshpoint_Documentation"
    bl_options = {"DEFAULT_CLOSED"}
    bl_order = 7

    def draw(self, context):
        box = self.layout.box()
        box.label(text="Turret", icon="EMPTY_AXIS")
        col = box.column(align=True)
        col.label(text="Mount attachment point")
        box = col.box()
        box.label(text="child.<mount_name>")
        col.label(text="Barrel muzzle")
        box = col.box()
        box.label(text="turret_muzzle.[0-9]")


class SINSII_PT_Meshpoint_Miscellaneous(SINSII_Main_Panel, bpy.types.Panel):
    bl_label = "Miscellaneous"
    bl_parent_id = "SINSII_PT_Meshpoint_Documentation"
    bl_options = {"DEFAULT_CLOSED"}
    bl_order = 8

    def draw(self, context):
        box = self.layout.box()
        box.label(text="Miscellaneous", icon="EMPTY_AXIS")
        col = box.column(align=True)
        col.label(text="Ship building effects")
        box = col.box()
        box.label(text="ship_build")
        col.label(text="Asteroid resource extractor attachment point")
        box = col.box()
        box.label(text="extractor")


class SINSII_PT_Meshpoint(SINSII_Main_Panel, bpy.types.Panel):
    bl_label = "General"
    bl_parent_id = "SINSII_PT_Meshpoint_Documentation"
    bl_options = {"DEFAULT_CLOSED"}
    bl_order = 6

    def draw(self, context):
        col = self.layout.column(align=True).box()
        col.label(text="General", icon="EMPTY_AXIS")
        col.label(text="Orientation")
        col.operator(
            "wm.url_open", text="See meshpoint orientation here", icon="URL"
        ).url = "https://i.imgur.com/VluXLbg.png"
        col.label(text="Align your mesh towards wherever the Monkey Primitive points to")
        col.label(text="Note")
        col.label(
            text="If you add a dash (-) delimiter before a number the engine will ignore everything after it"
        )
        col.label(
            text="- Ex: ability.0-1, ability.0-2, they will be perceived as ability.0"
        )
        col.label(
            text="Additionally, when doing meshpoints you'll have to parent it to the host mesh"
        )
        col.label(text="Types", icon="EMPTY_DATA")
        col.label(text="Buffs")
        box = col.box()
        box.label(text="aura, center, above")
        col.label(text="Ability")
        box = col.box()
        box.label(text="ability.[0-9]")
        col.label(text="Weapon")
        box = col.box()
        box.label(text="child.<turret_name>_[0-9]")
        box.label(text="weapon.<weapon_name>")
        box.label(text="bomb")
        col.label(text="Exhaust")
        box = col.box()
        box.label(text="exhaust.[0-9]")
        col.label(text="Hangar")
        box = col.box()
        box.label(text="hangar.[0-9]")


class SINSII_PT_Meshpoint_Documentation(SINSII_Main_Panel, bpy.types.Panel):
    bl_label = "Meshpoints"
    bl_parent_id = "SINSII_PT_Documentation_Panel"
    bl_options = {"DEFAULT_CLOSED"}
    bl_order = 6

    def draw(self, context):
        col = self.layout.column(align=True)
        col.label(
            text="Here you will find all your need to know about meshpointing your ship"
        )


def flip_normals(mesh):
    try:
        bpy.context.view_layer.objects.active = mesh
        bpy.ops.object.editmode_toggle()
        bpy.ops.mesh.select_all(action="SELECT")
        bpy.ops.mesh.flip_normals()
        bpy.ops.object.editmode_toggle()
    except:
        pass

class SINSII_OT_Sync_Empty_Color(bpy.types.Operator):
    bl_label = "Synchronize Meshpoint Color"
    bl_description = "Changes Blender Empty color to a cyan-like blue"
    bl_idname = "sinsii.sync_color"

    def execute(self, context):
        for theme in bpy.context.preferences.themes:
            theme.view_3d.empty = MESHPOINT_COLOR
        SETTINGS["has_synchronized_meshpoint_color"] = True
        settings.save_settings()
        return {"FINISHED"}


class SINSII_PT_Documentation_Panel(SINSII_Main_Panel, bpy.types.Panel):
    bl_label = "Help" if not has_update else "Help ℹ"
    bl_options = {"DEFAULT_CLOSED"}
    bl_order = 5

    def draw(self, context):
        col = self.layout.column(align=True)
        col.label(text=f"Version: {'.'.join(map(str, bl_info['version']))} {'' if not has_update else '- new version avaliable.'}")
        col.separator(factor=1.0)
        col.operator("sinsii.updates", icon="URL", text="Check for updates" if not has_update else "Update now")


class SINSII_OT_Generate_Buffs(bpy.types.Operator):
    bl_idname = "sinsii.create_buffs"
    bl_label = "Generate Buffs"

    @classmethod
    def poll(cls, context):
        return context.mode == "OBJECT"

    def execute(self, context):
        has_center, has_above, has_aura = False, False, False
        mesh = get_selected_mesh()
        apply_transforms(mesh)

        if mesh:
            radius = get_bounding_box(mesh)[0]

            for empty in (e for e in mesh.children if e.type == "EMPTY"):
                if "center" in empty.name:
                    has_center = True
                if "above" in empty.name:
                    has_above = True
                if "aura" in empty.name:
                    has_aura = True

            if mesh.type == "MESH":
                if not has_center:
                    create_empty(mesh, radius, "center", (0, 0, 0), "PLAIN_AXES")
                if not has_above:
                    create_empty(mesh, radius, "above", (0, 0, radius), "PLAIN_AXES")
                if not has_aura:
                    create_empty(mesh, radius, "aura", (0, 0, -radius), "PLAIN_AXES")
        else:
            self.report({"WARNING"}, "Select the mesh before generating buffs")

        return {"FINISHED"}


class SINSII_OT_Export_Spatial_Information(bpy.types.Operator, ExportHelper):
    bl_idname = "sinsii.export_spatial"
    bl_label = "Export spatials"

    filename_ext = ".unit"
    filter_glob: bpy.props.StringProperty(default="*.unit", options={"HIDDEN"})

    @classmethod
    def poll(cls, context):
        return context.mode == "OBJECT"

    def execute(self, context):
        mesh = get_selected_mesh()

        if not mesh or not mesh.type == "MESH":
            self.report({"WARNING"}, "You need to select a mesh before exporting")
            return {"CANCELLED"}

        radius, extents, center = get_bounding_box(mesh=mesh)

        try:
            with open(self.filepath, "r+") as f:
                unit_contents = json.load(f)
                if "spatial" in unit_contents:
                    unit_contents["spatial"] = {
                        "radius": radius,
                        "box": {"center": tuple((center)), "extents": tuple((extents))},
                        "collision_rank": 1,
                    }
                    f.seek(0)
                    f.write(json.dumps(unit_contents, indent=4))
                    f.truncate()
                else:
                    self.report(
                        {"ERROR"}, "Cannot locate spatial object, try creating it"
                    )
                    return {"CANCELLED"}
        except Exception as e:
            self.report({"ERROR"}, f"Spatial export failed: {e}")
            return {"CANCELLED"}

        return {"FINISHED"}


def apply_transforms(mesh):
    if not frozen(mesh):
        bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)


class SINSII_OT_Spawn_Meshpoint(bpy.types.Operator):
    bl_idname = "sinsii.spawn_meshpoint"
    bl_label = "Spawn meshpoint"
    bl_description = "Spawns an empty on the selected face/vertex"

    def execute(self, context):
        if not bpy.context.mode == "EDIT_MESH":
            self.report({"WARNING"}, "Make sure you are in edit mode")
            return {"CANCELLED"}
        mesh = get_selected_mesh()
        if not mesh:
            self.report({"WARNING"}, "Please select a mesh first")
            return {"CANCELLED"}
        bpy.ops.object.editmode_toggle()
        apply_transforms(mesh)
        bpy.ops.object.editmode_toggle()

        radius = get_bounding_box(mesh)[0]
        bpy.ops.view3d.snap_cursor_to_selected()
        bpy.ops.object.editmode_toggle()
        create_empty(
            mesh=mesh,
            radius=radius / 2,
            name=context.scene.mesh_properties.meshpoint_name,
            empty_type="ARROWS",
            location=bpy.context.scene.cursor.location,
        )
        bpy.ops.view3d.snap_cursor_to_center()

        return {"FINISHED"}


def make_meshpoint_rules(mesh):
    invalid_meshpoints = []

    for meshpoint in mesh.children:
        name = meshpoint.name
        is_matched = False

        for key, regex in MESHPOINTING_RULES.items():
            if re.match(regex, name):
                is_matched = True
                break

        if not is_matched:
            invalid_meshpoints.append(name)

    return invalid_meshpoints


def get_file_list(directory):
    files = []
    for dirpath, dirname, filenames in os.walk(directory):
        if ".git" in dirpath:
            continue
        for name in dirname:
            if ".git" in name or "__pycache__" in name:
                continue
            files.append(os.path.relpath(os.path.join(dirpath, name), directory))
        for name in filenames:
            if "pyc" in name:
                continue
            files.append(os.path.relpath(os.path.join(dirpath, name), directory))
    return files


class SINSII_OT_Debug(bpy.types.Operator):
    bl_idname = "sinsii.debug"
    bl_label = "Debug"

    def execute(self, context):
        return {"FINISHED"}


class SINSII_OT_Check_For_Updates(bpy.types.Operator):
    bl_idname = "sinsii.updates"
    bl_label = "Check for updates"

    def execute(self, context):
        temp_path = github.temp
        github.fetch_latest_archive()

        current_files = set(get_file_list(CWD_PATH))
        temp_files = set(get_file_list(temp_path))

        for file in current_files.difference(temp_files):
            file_path = os.path.join(CWD_PATH, file)
            if os.path.isdir(file_path):
                shutil.rmtree(file_path)
            else:
                os.remove(file_path)

        curr_hash = generate_hash_from_directory(
            directory=CWD_PATH, file_list=get_file_list(CWD_PATH)
        )

        repo_hash = generate_hash_from_directory(
            directory=temp_path, file_list=get_file_list(temp_path)
        )

        if curr_hash == repo_hash:
            shutil.rmtree(temp_path)
            self.report({"INFO"}, "No updates found.")
        else:
            os.makedirs(os.path.join(CWD_PATH, "src"), exist_ok=True)
            for file in os.listdir(temp_path):
                if os.path.isdir(os.path.join(temp_path, file)):
                    shutil.copytree(
                        os.path.join(temp_path, file),
                        os.path.join(CWD_PATH, "src"),
                        dirs_exist_ok=True,
                    )
                else:
                    shutil.copy(os.path.join(temp_path, file), CWD_PATH)
            shutil.rmtree(temp_path)

            SETTINGS["current_version"] = latest_version
            settings.save_settings()

            self.report(
                {"INFO"},
                "Extension updated successfully, restart blender for it to take effect.",
            )
        return {"FINISHED"}


def create_empty(mesh, radius, name, location, empty_type):
    bpy.ops.object.empty_add(type=empty_type)
    empty = bpy.context.object
    empty.empty_display_size = radius * 0.05
    empty.name = name
    empty.location = location
    empty.parent = mesh
    empty.rotation_euler = (math.radians(90), 0, 0)


def get_bounding_box(mesh):

    def calculate_center(l):
        return (max(l) + min(l)) / 2 if l else 0

    if mesh:
        mesh_box = [GAME_MATRIX @ Vector(axis) for axis in mesh.bound_box]

        bounds = [
            coord for vector in mesh_box for coord in (vector.x, vector.y, vector.z)
        ]

        bounds_x = bounds[::3]
        bounds_y = bounds[1::3]
        bounds_z = bounds[2::3]

        center_x = calculate_center(bounds_x)
        center_y = calculate_center(bounds_y)
        center_z = calculate_center(bounds_z)

        center = [center_x, center_y, -center_z]

        extents = [
            ((max(bounds_x) - min(bounds_x)) / 2),
            ((max(bounds_y) - min(bounds_y)) / 2),
            ((max(bounds_z) - min(bounds_z)) / 2),
        ]

        bounding_sphere_radius = max(
            (p - Vector([center_x, center_y, center_z])).length for p in mesh_box
        )

        return bounding_sphere_radius, extents, center


def get_active_material():
    mesh = get_selected_mesh()
    if mesh and mesh.active_material:
        return mesh.active_material


def get_selected_meshes(type="MESH"):
    selected_meshes = []
    for mesh in bpy.context.selected_objects:
        if mesh.type == type:
            selected_meshes.append(mesh)
    return selected_meshes


def get_selected_mesh():
    selected_objects = bpy.context.selected_objects
    if len(selected_objects) > 0:
        return selected_objects[0]


def run_texconv(texture, temp_dir):
    subprocess.run(
        [TEXCONV_EXE, "-m", "1", "-y", "-f", "BC7_UNORM", "-r", texture, "-o", temp_dir]
    )


def run_meshbuilder(file_path, dest_path):
    try:
        result = subprocess.run(
            [
                MESHBUILDER_EXE,
                f"--input_path={file_path}",
                f"--output_folder_path={dest_path}",
                "--mesh_output_format=binary",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout
    except subprocess.CalledProcessError as e:
        return e.stderr


def get_materials(mesh):
    materials = []
    if mesh.type == "MESH":
        for material in mesh.data.materials:
            if material is not None:
                materials.append(material.name.lower())
        if len(materials) == 0:
            return mesh.name
    return materials


def create_and_move_mesh_materials(file_path, mesh):
    materials = get_materials(mesh)
    unused_mats = get_unused_materials(mesh, materials)
    # create new ones
    for material in (material for material in materials if material not in unused_mats):
        # skip unused material
        material_name = f"{material}.mesh_material"
        mesh_materials_dir = normalize(file_path, "../mesh_materials")
        mesh_material = os.path.join(mesh_materials_dir, material_name)
        if os.path.exists(mesh_material):
            continue
        with open(os.path.join(file_path, material_name), "w") as f:
            mesh_material = MeshMaterial(
                clr=f"{material}_clr",
                nrm=f"{material}_nrm",
                msk=f"{material}_msk",
                orm=f"{material}_orm",
            ).json()
            f.write(json.dumps(mesh_material, indent=4))
            f.close()
        dest = mesh_materials_dir if os.path.exists(mesh_materials_dir) else file_path
        rename(path=file_path, dest=dest, filename=material_name)


def apply_meshpoint_transforms(mesh):
    transforms = []
    if len(mesh.children) >= 1:
        for empty in mesh.children:
            if empty is None and not empty.type == "EMPTY":
                continue
            transforms.append(empty.matrix_world.copy())
            empty.matrix_local = empty.matrix_basis @ MESHPOINT_MATRIX
    return transforms


def restore_meshpoint_transforms(children, original):
    if children and len(children) >= 1:
        for i, empty in enumerate(children):
            empty.matrix_local = original[i]


def clear_leftovers(export_dir, mesh_name):
    for leftover in os.listdir(export_dir):
        if any(e for e in [".mesh_material", ".bin", ".gltf"] if leftover.endswith(e)):
            try:
                os.remove(os.path.join(export_dir, leftover))
            except:
                raise Exception(f"Could not remove: {leftover}")


def normalize(file_path, args):
    return os.path.normpath(os.path.join(file_path, args))


def rename(path, dest, filename):
    os.replace(os.path.join(path, filename), os.path.join(dest, filename))


def purge_orphans():
    bpy.ops.outliner.orphans_purge()


class SINSII_OT_Spawn_Shield_Mesh(bpy.types.Operator):
    bl_idname = "sinsii.spawn_shield"
    bl_label = "Spawn shield mesh"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        return context.mode == "OBJECT"
    def execute(self, context):
        mesh = get_selected_mesh()

        if mesh:
            radius = get_bounding_box(mesh)[0]

            bpy.ops.mesh.primitive_uv_sphere_add(
                segments=32, radius=radius, align="WORLD", location=mesh.location
            )
            bpy.ops.object.shade_smooth()

            shield = bpy.context.active_object
            shield.name = f"{mesh.name}_shield"
            new_mat = bpy.data.materials.new(name=f"{mesh.name}_shield")
            shield.data.materials.append(new_mat)
            shield.select_set(False)

        # purge_orphans()

        return {"FINISHED"}


def get_unused_materials(mesh, materials):
    unused_mats = []
    for i, mat in enumerate(materials):
        tris = []
        for tri in mesh.data.polygons:
            if tri.material_index == i:
                tris.append(tri.material_index)
        if len(tris) == 0:
            unused_mats.append(mat)
    return unused_mats


def load_mesh_data(self, mesh_data, mesh_name, mesh):
    primitives = mesh_data["primitives"]
    materials = mesh_data["materials"]
    meshpoints = mesh_data["meshpoints"]

    vert_arr, normal_arr, uv_coords = [], [], {x: [] for x in ["uv0", "uv1"]}

    for i, vertex in enumerate(mesh_data["vertices"]):
        p = tuple(
            GAME_MATRIX @ Vector([vertex["p"][0], vertex["p"][1], -vertex["p"][2]])
        )
        vert_arr.append(p)

        n = tuple(
            GAME_MATRIX @ Vector([-vertex["n"][0], -vertex["n"][1], vertex["n"][2]])
        )
        normal_arr.append(n)

        uv0 = [vertex["uv0"][0], 1 - vertex["uv0"][1]]
        uv_coords["uv0"].append(uv0)

        if vertex["uv1"]:
            uv1 = [vertex["uv1"][0], 1 - vertex["uv1"][1]]
            uv_coords["uv1"].append(uv1)
        else:
            # failsafe
            uv_coords["uv1"].append([0, 0])

    for i in range(2):
        mesh.uv_layers.new(name=f"uv{i}")

    indices = mesh_data["indices"]
    loops = [indices[i : i + 3] for i in range(0, len(indices), 3)]

    mesh.from_pydata(vert_arr, [], loops)
    mesh.update()

    obj = bpy.data.objects.new(name=mesh_name, object_data=mesh)
    scene = bpy.context.scene
    scene.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)

    bm = bmesh.new()
    bm.from_mesh(mesh)

    for face in bm.faces:
        for loop in face.loops:
            loop[bm.loops.layers.uv["uv0"]].uv = uv_coords["uv0"][loop.vert.index]
            if uv_coords["uv1"]:
                loop[bm.loops.layers.uv["uv1"]].uv = uv_coords["uv1"][loop.vert.index]

    bm.to_mesh(mesh)
    bm.free()

    for material in materials:
        mesh_materials_path = normalize(self.filepath, "../../mesh_materials")
        textures_path = normalize(self.filepath, "../../textures")

        if not os.path.exists(mesh_materials_path):
            new_mat = bpy.data.materials.new(name=material)
        else:
            new_mat = create_shader_nodes(
                material,
                mesh_materials_path,
                textures_path,
                run_texconv,
            )
        mesh.materials.append(new_mat)

    for primitive in primitives:
        mat_idx = primitive["material_index"]
        start = primitive["vertex_index_start"]
        count = primitive["vertex_index_count"]
        end = start + count
        for i in range(start // 3, end // 3):
            mesh.polygons[i].material_index = mat_idx

    mesh.update()
    mesh.polygons.foreach_set("use_smooth", [True] * len(mesh.polygons))
    mesh.normals_split_custom_set_from_vertices(normal_arr)

    radius = get_bounding_box(obj)[0]

    for meshpoint in meshpoints:
        name = meshpoint["name"]
        pos = meshpoint["position"]
        rot = meshpoint["rotation"]

        bpy.ops.object.empty_add(type="ARROWS")
        empty = bpy.context.object
        empty.empty_display_size = radius * 0.05
        empty.name = name
        empty.location = GAME_MATRIX @ Vector((pos[0], pos[1], -pos[2]))
        empty.parent = obj
        empty.rotation_euler = (
            GAME_MATRIX.transposed()
            @ Matrix(
                (
                    (rot[0], rot[1], rot[2]),
                    (rot[3], rot[4], rot[5]),
                    (rot[6], rot[7], rot[8]),
                )
            ).to_4x4()
            @ MESHPOINT_MATRIX
        ).to_euler()

    flip_normals(obj)

    # purge_orphans()

    return obj, radius


def import_mesh(self, file_path):
    mesh_name = file_path.rsplit("\\", 1)[1].split(".mesh")[0]
    mesh = bpy.data.meshes.new(name=mesh_name)
    try:

        #  _____ _____ _   _  _____     _____
        # /  ___|_   _| \ | |/  ___|   / __  \
        # \ `--.  | | |  \| |\ `--.    `' / /'
        #  `--. \ | | | . ` | `--. \     / /
        # /\__/ /_| |_| |\  |/\__/ /   ./ /___
        # \____/ \___/\_| \_/\____/    \_____/

        buffer = BinaryReader.open(file_path)
        reader = BinaryReader.initialize_from(buffer)
    except Exception as e:
        self.report({"ERROR"}, f"Mesh import failed.: {e}")
        return {"CANCELLED"}

    return load_mesh_data(self, reader.mesh_data, mesh_name, mesh)


class SINSII_OT_Import_Mesh(bpy.types.Operator, ImportHelper):
    bl_idname = "sinsii.import_mesh"
    bl_label = "Import mesh"
    bl_description = "You might encounter normal issues on certain models"
    bl_options = {"REGISTER"}

    filename_ext = ".mesh"
    filter_glob: bpy.props.StringProperty(default="*.mesh", options={"HIDDEN"})

    files: CollectionProperty(type=bpy.types.PropertyGroup)

    @classmethod
    def poll(cls, context):
        return context.mode == "OBJECT"

    def execute(self, context):
        os.makedirs(TEMP_TEXTURES_PATH, exist_ok=True)
        radius_arr = []
        offset = 0
        for i, file in enumerate(self.files):
            mesh, radius = import_mesh(
                self, os.path.join(os.path.dirname(self.filepath), file.name)
            )
            radius_arr.append(radius)
            if i > 0:
                offset += radius_arr[i - 1] + radius_arr[i]

            mesh.location = (offset, 0, 0)

        self.report({"INFO"}, f"Imported meshes: {[file.name for file in self.files]}")
        return {"FINISHED"}


def original_transforms(mesh):
    original_transform = mesh.matrix_world.copy()
    mesh.matrix_world = GAME_MATRIX @ mesh.matrix_world
    original_meshpoint_transforms = apply_meshpoint_transforms(mesh=mesh)
    return original_transform, original_meshpoint_transforms


def frozen(mesh):
    if mesh.type == "MESH":
        if (
            not all(vec == 1 for vec in mesh.scale)
            or not all(vec == 0 for vec in mesh.rotation_euler)
            or not all(vec == 0 for vec in mesh.location)
        ):
            return False

    return True


def get_avaliable_sorted_materials(mesh):
    materials = get_materials(mesh)
    unused_mats = get_unused_materials(mesh, materials)
    return sorted(
        material for material in set(materials) if material not in unused_mats
    )


def clean_gltf_document(file_path):
    with open(f"{file_path}.gltf", "r+") as f:
        gltf_document = json.load(f)
        for material in gltf_document["materials"]:
            try:
                del material["doubleSided"]
            except:
                pass
        f.seek(0)
        f.write(json.dumps(gltf_document))
        f.truncate()


def restore_mesh_transforms(transforms, meshes):
    for i, mesh in enumerate(meshes):
        mt, mpt = transforms[i]
        mesh.matrix_world = mt
        restore_meshpoint_transforms(children=mesh.children, original=mpt)


def export_gltf_document(file_path):
    bpy.ops.export_scene.gltf(
        filepath=file_path,
        export_format="GLTF_SEPARATE",
        export_yup=False,
        use_selection=True,
        export_apply=False,
        export_image_format="NONE",
    )
    clean_gltf_document(file_path)


def join_meshes(meshes):
    bpy.ops.object.select_all(action="DESELECT")
    for mesh in sorted(meshes, key=lambda mesh: mesh.name.lower()):
        mesh.select_set(True)
    if len(meshes) > 1:
        bpy.ops.object.join()
    return bpy.context.view_layer.objects.active


def export_mesh(self, mesh_name, export_dir):
    now = time.time()

    original_transforms_arr = []

    if not get_selected_meshes():
        self.report({"WARNING"}, f"You need to select a mesh before exporting")
        return

    mesh = get_selected_mesh()

    invalid_meshpoints = make_meshpoint_rules(mesh)
    if invalid_meshpoints:
        self.report(
            {"ERROR"},
            f'Invalid meshpoints: [ {", ".join(meshpoint for meshpoint in invalid_meshpoints)} ]',
        )
        return

    materials = get_materials(mesh)
    if type(materials) is str:
        self.report(
            {"ERROR"},
            'Cannot export "{0}" without any materials'.format(materials),
        )
        return

    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.separate(type="MATERIAL")
    bpy.ops.object.mode_set(mode="OBJECT")
    meshes = get_selected_meshes()

    for mesh in meshes:
        apply_transforms(mesh)
        original_transform, original_meshpoint_transforms = original_transforms(mesh)
        original_transforms_arr.append(
            (original_transform, original_meshpoint_transforms)
        )
        for meshpoint in mesh.children:
            meshpoint.select_set(True)

    if "-" in mesh_name:
        mesh_name = mesh_name.replace("-", "_")

    full_mesh_path = os.path.join(export_dir, mesh_name)

    export_gltf_document(full_mesh_path)
    restore_mesh_transforms(original_transforms_arr, meshes)

    meshbuilder_err = run_meshbuilder(
        file_path=f"{full_mesh_path}.gltf", dest_path=export_dir
    )

    mesh = join_meshes(meshes)

    if meshbuilder_err and not meshbuilder_err.strip().endswith("not found"):
        self.report({"ERROR"}, meshbuilder_err)
        return
    else:
        print(meshbuilder_err)

    buffer = BinaryReader.open(os.path.join(export_dir, f"{mesh_name}.mesh"))
    reader = BinaryReader.initialize_from(buffer)

    curr_offset = reader.meshpoint_offset_start
    new_buffer = bytearray(buffer)
    for meshpoint in mesh.children:
        if meshpoint.hide_get():
            continue
        name_length_offset = curr_offset
        name_length = reader.u32_at_offset(name_length_offset)

        start = 4 + name_length_offset
        end = start + name_length
        new_name = re.sub("\\b\-\d+\\b", "", meshpoint.name).encode("utf-8")
        new_buffer[start:end] = pack(f"{len(meshpoint.name)}s", new_name)
        curr_offset += 4 + name_length + 50

    curr_mat_offset = reader.materials_offset_start
    buffer_end = len(new_buffer)

    material_bytes = bytearray(new_buffer[:curr_mat_offset])

    # consume prefixes
    for material in get_avaliable_sorted_materials(mesh):
        mat_length_offset = curr_mat_offset
        old_name_length = reader.u32_at_offset(mat_length_offset)

        material_name = material.encode("utf-8")

        material_bytes.extend(pack("I", len(material_name)))
        material_bytes.extend(material_name)

        curr_mat_offset += 4 + old_name_length
    material_bytes.extend(new_buffer[curr_mat_offset:buffer_end])

    new_buffer = material_bytes

    with open(os.path.join(export_dir, f"{mesh_name}.mesh"), "wb") as f:
        f.write(new_buffer)

    post_export_operations(export_dir, mesh_name, mesh)

    self.report(
        {"INFO"},
        "Mesh exported successfully to: {} - Finished in: {:.6f}s".format(
            self.filepath, time.time() - now
        ),
    )


def post_export_operations(export_dir, mesh_name, mesh):
    clear_leftovers(export_dir, mesh_name)
    create_and_move_mesh_materials(export_dir, mesh)


class SINSII_OT_Export_Mesh(bpy.types.Operator, ExportHelper):
    bl_idname = "sinsii.export_mesh"
    bl_label = "Export mesh"
    bl_options = {"REGISTER"}

    filename_ext = ""

    filter_glob: bpy.props.StringProperty(default="*.mesh", options={"HIDDEN"})

    def invoke(self, context, event):
        try:
            self.filepath = get_selected_mesh().name
        except:
            pass
        return super().invoke(context, event)

    @classmethod
    def poll(cls, context):
        return context.mode == "OBJECT"

    def execute(self, context):
        __ = self.filepath.rsplit("\\", 1)
        EXPORT_DIR = __[0]
        MESH_NAME = __[1].lower()

        try:
            export_mesh(self, MESH_NAME, EXPORT_DIR)
        except Exception as e:
            self.report({"ERROR"}, f"Could not export the model: {e}")
            return {"CANCELLED"}

        # purge_orphans()

        return {"FINISHED"}


def set_node_position(node, x, y):
    node.location = (x * 100, y * -100)


def add_driver(node_id, node, name, data_path):
    is_emissive = 2 if name == "Emissive" else 1
    for i, driver in enumerate(
        node_id.driver_add(
            f'nodes["{node.name}"].color_ramp.elements[{is_emissive}].color'
        )
    ):
        driver = driver.driver
        driver.type = "SUM"
        var = driver.variables.new()
        var.name = name
        var.targets[0].id_type = "SCENE"
        var.targets[0].id = bpy.context.scene
        var.targets[0].data_path = f"{data_path}[{i}]"


def load_texture(node, texture, run_texconv):
    try:
        # convert to usable formats for blender
        tex_file = os.path.basename(texture)
        tmp_texture_path = os.path.join(TEMP_TEXTURES_PATH, tex_file)

        if os.path.exists(tmp_texture_path):
            node.image = bpy.data.images[tex_file]
        else:
            run_texconv(texture, TEMP_TEXTURES_PATH)
            image = bpy.data.images.load(tmp_texture_path)
            node.image = image
    except:
        image = bpy.ops.image.new(name=node.label, width=1, height=1)
        node.image = bpy.data.images[node.label]

    if node.image and node.label != "_clr":
        node.image.colorspace_settings.name = "Non-Color"


def load_mesh_material(name, filepath, textures_path):
    mesh_material = os.path.join(filepath, f"{name}.mesh_material")

    if not os.path.exists(mesh_material):
        return ["", "", "", ""]

    contents = json.load(open(mesh_material, "r"))
    return [
        (
            os.path.join(textures_path, f"{contents.get(key)}.dds")
            if contents.get(key)
            else " "
        )
        for key in [
            "base_color_texture",
            "occlusion_roughness_metallic_texture",
            "mask_texture",
            "normal_texture",
        ]
    ]


def create_shader_nodes(material_name, mesh_materials_path, textures_path, run_texconv):
    textures = load_mesh_material(material_name, mesh_materials_path, textures_path)

    material = bpy.data.materials.new(name=material_name)
    material.use_nodes = True
    node_id = material.node_tree
    nodes = material.node_tree.nodes

    principled_node = next(node for node in nodes if node.type == "BSDF_PRINCIPLED")
    set_node_position(principled_node, 0, 0)

    _clr = nodes.new(type="ShaderNodeTexImage")
    set_node_position(_clr, -16, 0)
    _clr.label = "_clr"
    load_texture(_clr, textures[0], run_texconv)

    _orm = nodes.new(type="ShaderNodeTexImage")
    set_node_position(_orm, -16, 2)
    _orm.label = "_orm"
    load_texture(_orm, textures[1], run_texconv)

    _msk = nodes.new(type="ShaderNodeTexImage")
    set_node_position(_msk, -16, 4)
    _msk.label = "_msk"
    load_texture(_msk, textures[2], run_texconv)

    _nrm = nodes.new(type="ShaderNodeTexImage")
    set_node_position(_nrm, -16, 6)
    _nrm.label = "_nrm"
    load_texture(_nrm, textures[3], run_texconv)

    mapping_node = nodes.new(type="ShaderNodeMapping")
    set_node_position(mapping_node, -19, 0)

    tex_coord_node = nodes.new(type="ShaderNodeTexCoord")
    set_node_position(tex_coord_node, -21, 0)

    mix_node_1 = nodes.new(type="ShaderNodeMix")
    set_node_position(mix_node_1, -4, -2)
    mix_node_1.data_type = "RGBA"
    mix_node_1.blend_type = "MIX"

    mix_node_2 = nodes.new(type="ShaderNodeMix")
    set_node_position(mix_node_2, -8, -2)
    mix_node_2.data_type = "RGBA"
    mix_node_2.blend_type = "VALUE"

    clamp_node = nodes.new(type="ShaderNodeClamp")
    clamp_node.inputs[1].default_value = 0.135
    clamp_node.inputs[2].default_value = 0.350
    clamp_node.clamp_type = "RANGE"
    set_node_position(clamp_node, -10, 1)

    separate_color_node = nodes.new(type="ShaderNodeSeparateColor")
    set_node_position(separate_color_node, -13, 2)
    separate_color_node_2 = nodes.new(type="ShaderNodeSeparateColor")
    set_node_position(separate_color_node_2, -13, 4)

    color_ramp = nodes.new(type="ShaderNodeValToRGB")
    set_node_position(color_ramp, -7, 3)
    color_ramp.color_ramp.interpolation = "EASE"
    color_ramp.color_ramp.elements[0].position = 0.445
    color_ramp.color_ramp.elements[1].position = 0.560

    color_ramp_2 = nodes.new(type="ShaderNodeValToRGB")
    set_node_position(color_ramp_2, -7, 5)
    color_ramp_2.color_ramp.elements[0].color = (0, 0, 0, 1)
    add_driver(node_id, color_ramp_2, "Team Color - 1", "mesh_properties.team_color_1")

    color_ramp_3 = nodes.new(type="ShaderNodeValToRGB")
    set_node_position(color_ramp_3, -7, 7)
    color_ramp_3.color_ramp.elements[0].color = (0, 0, 0, 1)
    add_driver(node_id, color_ramp_3, "Team Color - 2", "mesh_properties.team_color_2")

    color_ramp_4 = nodes.new(type="ShaderNodeValToRGB")
    set_node_position(color_ramp_4, -7, 9)
    color_ramp_4.color_ramp.elements[0].color = (0, 0, 0, 1)
    color_ramp_4.color_ramp.elements.new(position=0.5)
    color_ramp_4.color_ramp.elements[1].color = (0, 0, 0, 1)
    add_driver(node_id, color_ramp_4, "Emissive", "mesh_properties.team_color_3")

    separate_color_node_3 = nodes.new(type="ShaderNodeSeparateColor")
    set_node_position(separate_color_node_3, -13, 6)

    multiply_node = nodes.new(type="ShaderNodeMath")
    multiply_node.operation = "MULTIPLY"
    multiply_node.inputs[1].default_value = 1
    set_node_position(multiply_node, -10, 12)

    multiply_node_2 = nodes.new(type="ShaderNodeMath")
    multiply_node_2.operation = "MULTIPLY"
    set_node_position(multiply_node_2, -4, 13)
    multiply_node_2.inputs[1].default_value = 2.0

    multiply_node_3 = nodes.new(type="ShaderNodeMath")
    multiply_node_3.operation = "MULTIPLY"
    set_node_position(multiply_node_3, -4, 11)
    multiply_node_3.inputs[1].default_value = 2.0

    subtract_node = nodes.new(type="ShaderNodeMath")
    subtract_node.operation = "SUBTRACT"
    set_node_position(subtract_node, -2, 11)
    subtract_node.inputs[1].default_value = 1.0

    subtract_node_2 = nodes.new(type="ShaderNodeMath")
    subtract_node_2.operation = "SUBTRACT"
    set_node_position(subtract_node_2, -2, 13)
    subtract_node_2.inputs[1].default_value = 1.0

    combine_xyz_node = nodes.new(type="ShaderNodeCombineXYZ")
    set_node_position(combine_xyz_node, 0, 12)

    dot_product_node = nodes.new(type="ShaderNodeVectorMath")
    set_node_position(dot_product_node, 2, 12)
    dot_product_node.operation = "DOT_PRODUCT"

    color_invert_node = nodes.new(type="ShaderNodeInvert")
    set_node_position(color_invert_node, 4, 12)

    clamp_node_2 = nodes.new(type="ShaderNodeClamp")
    clamp_node_2.inputs[1].default_value = 0
    clamp_node_2.inputs[2].default_value = 1
    set_node_position(clamp_node_2, 6, 12)

    square_root_node = nodes.new(type="ShaderNodeMath")
    square_root_node.operation = "SQRT"
    set_node_position(square_root_node, 8, 12)

    combine_xyz_node_2 = nodes.new(type="ShaderNodeCombineXYZ")
    set_node_position(combine_xyz_node_2, 10, 10)

    normal_map_node = nodes.new(type="ShaderNodeNormalMap")
    set_node_position(normal_map_node, 12, 10)

    principled_node.inputs[27].default_value = 10

    links = material.node_tree.links
    links.new(mix_node_1.outputs["Result"], principled_node.inputs["Base Color"])
    links.new(mix_node_2.outputs["Result"], mix_node_1.inputs["B"])
    links.new(mix_node_2.outputs["Result"], mix_node_1.inputs["B"])
    links.new(_clr.outputs["Color"], mix_node_2.inputs["B"])
    links.new(multiply_node.outputs["Value"], multiply_node_3.inputs["Value"])
    links.new(subtract_node.outputs["Value"], combine_xyz_node.inputs["X"])
    links.new(subtract_node_2.outputs["Value"], combine_xyz_node.inputs["Y"])
    links.new(_orm.outputs["Color"], separate_color_node.inputs["Color"])
    links.new(separate_color_node.outputs["Green"], clamp_node.inputs["Value"])
    links.new(multiply_node_3.outputs["Value"], subtract_node.inputs["Value"])
    links.new(combine_xyz_node.outputs["Vector"], dot_product_node.inputs[0])
    links.new(combine_xyz_node.outputs["Vector"], dot_product_node.inputs[1])
    links.new(dot_product_node.outputs["Value"], color_invert_node.inputs["Color"])
    links.new(multiply_node_2.outputs["Value"], subtract_node_2.inputs["Value"])
    links.new(square_root_node.outputs["Value"], combine_xyz_node_2.inputs["Z"])
    links.new(combine_xyz_node_2.outputs["Vector"], normal_map_node.inputs["Color"])
    links.new(normal_map_node.outputs["Normal"], principled_node.inputs["Normal"])
    links.new(separate_color_node_3.outputs["Red"], combine_xyz_node_2.inputs["X"])
    links.new(multiply_node.outputs["Value"], combine_xyz_node_2.inputs["Y"])
    links.new(clamp_node.outputs["Result"], principled_node.inputs["Roughness"])
    links.new(separate_color_node_3.outputs["Green"], multiply_node_2.inputs["Value"])
    links.new(color_invert_node.outputs["Color"], clamp_node_2.inputs["Value"])
    links.new(clamp_node_2.outputs["Result"], square_root_node.inputs["Value"])
    links.new(separate_color_node.outputs["Blue"], color_ramp.inputs["Fac"])
    links.new(color_ramp.outputs["Color"], principled_node.inputs["Metallic"])
    links.new(color_ramp_2.outputs["Color"], mix_node_2.inputs["A"])
    links.new(separate_color_node_3.outputs["Green"], multiply_node.inputs["Value"])
    links.new(color_ramp_3.outputs["Color"], mix_node_1.inputs["A"])
    links.new(color_ramp_4.outputs["Color"], principled_node.inputs[26])
    links.new(_nrm.outputs["Color"], separate_color_node_3.inputs["Color"])
    links.new(_msk.outputs["Color"], separate_color_node_2.inputs["Color"])
    links.new(separate_color_node_2.outputs["Red"], color_ramp_2.inputs["Fac"])
    links.new(separate_color_node_2.outputs["Green"], color_ramp_3.inputs["Fac"])
    links.new(separate_color_node_2.outputs["Blue"], color_ramp_4.inputs["Fac"])
    links.new(tex_coord_node.outputs["UV"], mapping_node.inputs["Vector"])
    links.new(mapping_node.outputs["Vector"], _clr.inputs["Vector"])
    links.new(mapping_node.outputs["Vector"], _nrm.inputs["Vector"])
    links.new(mapping_node.outputs["Vector"], _msk.inputs["Vector"])
    links.new(mapping_node.outputs["Vector"], _orm.inputs["Vector"])
    links.new(_clr.outputs["Alpha"], principled_node.inputs["Alpha"])

    return material


classes = (
    SINSII_OT_Import_Mesh,
    SINSII_OT_Export_Mesh,
    SINSII_OT_Generate_Buffs,
    SINSII_OT_Check_For_Updates,
    SINSII_OT_Debug,
    SINSII_OT_Sync_Empty_Color,
    SINSII_OT_Spawn_Meshpoint,
    SINSII_OT_Spawn_Shield_Mesh,
    SINSII_OT_Export_Spatial_Information,
    SINSII_PT_Panel,
    SINSII_OT_Format_Meshpoints,
    SINSII_PT_Mesh_Point_Panel,
    SINSII_PT_Mesh_Panel,
    SINSII_PT_Documentation_Panel,
    SINSII_PT_Meshpoint_Documentation,
    SINSII_PT_Meshpoint_Miscellaneous,
    SINSII_PT_Meshpoint_Turret,
    SINSII_PT_Meshpoint,
)


def register():
    for Class in classes:
        bpy.utils.register_class(Class)


def unregister():
    for Class in classes:
        bpy.utils.unregister_class(Class)
