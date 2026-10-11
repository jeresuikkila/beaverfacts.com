"""Build the Beaverfacts beaver in Blender from code.

Recreates the hand-drawn mascot (src/assets/Beaverhugs1.png) as a
stylized 3D model and exports it for the Three.js scene on the site.

    blender -b --factory-startup --python blender/build_beaver.py -- \
        --glb public/models/beaver.glb --blend blender/beaver.blend [--renders DIR]

Coordinates: Z up, the beaver faces -Y, feet rest on Z=0.
"""

import math
import sys

import bmesh
import bpy
import numpy as np
from mathutils import Matrix, Quaternion, Vector

# --------------------------------------------------------------------------
# Palette, sampled from the original drawing

BROWN = "#8E623E"
EAR_INNER = "#FBE6DA"
NOSE = "#5A5B55"
TOOTH = "#FFFCEB"
DARK_GREY = "#4A4A4A"
LEAF_RED = "#B4002B"
INK = "#111111"


def hex_rgba(h, a=1.0):
    h = h.lstrip("#")
    srgb = [int(h[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    lin = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in srgb]
    return (*lin, a)


# --------------------------------------------------------------------------
# Scene helpers


def reset():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def material(name, color, rough=0.65, image=None, emission=0.0):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = hex_rgba(color)
    bsdf.inputs["Roughness"].default_value = rough
    if image is not None:
        tex = mat.node_tree.nodes.new("ShaderNodeTexImage")
        tex.image = image
        mat.node_tree.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
    if emission:
        bsdf.inputs["Emission Color"].default_value = hex_rgba(color)
        bsdf.inputs["Emission Strength"].default_value = emission
    return mat


def image_from_array(name, rgba):
    """rgba: H x W x 4 float array of sRGB values, row 0 at the bottom."""
    h, w, _ = rgba.shape
    img = bpy.data.images.new(name, w, h, alpha=True)
    img.pixels.foreach_set(np.ascontiguousarray(rgba, dtype=np.float32).ravel())
    img.file_format = "PNG"
    img.pack()
    return img


ROOT = None


def add_object(name, data, mat=None, outline=True, smooth=True):
    ob = bpy.data.objects.new(name, data)
    bpy.context.scene.collection.objects.link(ob)
    if ROOT is not None:
        ob.parent = ROOT
    if mat is not None and hasattr(data, "materials"):
        data.materials.append(mat)
    if smooth and isinstance(data, bpy.types.Mesh):
        data.polygons.foreach_set("use_smooth", [True] * len(data.polygons))
    # Read by the site as userData.outline: draw a toon ink outline or not.
    ob["outline"] = 1 if outline else 0
    return ob


def bm_to_mesh(name, bm):
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    return me


def ellipsoid_mesh(name, radii, seg=24, rings=12):
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=seg, v_segments=rings, radius=1.0)
    for v in bm.verts:
        v.co = Vector((v.co.x * radii[0], v.co.y * radii[1], v.co.z * radii[2]))
    return bm_to_mesh(name, bm)


def ellipsoid(name, loc, radii, mat, rot=None, outline=True, seg=24, rings=12):
    ob = add_object(name, ellipsoid_mesh(name, radii, seg, rings), mat, outline)
    ob.location = loc
    if rot is not None:
        ob.rotation_mode = "QUATERNION"
        ob.rotation_quaternion = rot
    return ob


def curve_object(name, points, radius, mat, radii=None, outline=True, cyclic=False):
    """A round tube along a smooth bezier through `points`, converted to a mesh."""
    cu = bpy.data.curves.new(name + "_curve", "CURVE")
    cu.dimensions = "3D"
    cu.bevel_depth = radius
    cu.bevel_resolution = 4
    cu.resolution_u = 12
    cu.use_fill_caps = True
    sp = cu.splines.new("BEZIER")
    sp.bezier_points.add(len(points) - 1)
    sp.use_cyclic_u = cyclic
    for i, (bp, co) in enumerate(zip(sp.bezier_points, points)):
        bp.co = co
        bp.handle_left_type = bp.handle_right_type = "AUTO"
        if radii:
            bp.radius = radii[i]
    tmp = bpy.data.objects.new(name + "_tmp", cu)
    bpy.context.scene.collection.objects.link(tmp)
    ob = mesh_from(tmp, name, mat, outline)
    return ob


def mesh_from(src, name, mat, outline=True):
    """Bake an object (curve, modifiers) to a plain mesh object and drop the source."""
    dg = bpy.context.evaluated_depsgraph_get()
    me = bpy.data.meshes.new_from_object(src.evaluated_get(dg))
    me.name = name
    me.materials.clear()
    data = src.data
    bpy.data.objects.remove(src)
    if isinstance(data, bpy.types.Curve):
        bpy.data.curves.remove(data)
    return add_object(name, me, mat, outline)


def subsurf(ob, levels=1):
    m = ob.modifiers.new("Subdivision", "SUBSURF")
    m.levels = m.render_levels = levels
    return m


# --------------------------------------------------------------------------
# Signed distance fields (numpy, vectorized over N x 3 points)


def sd_ellipsoid(p, c, r):
    q = (p - np.asarray(c)) / np.asarray(r)
    k0 = np.linalg.norm(q, axis=1)
    k1 = np.linalg.norm(q / np.asarray(r), axis=1)
    return k0 * (k0 - 1.0) / np.maximum(k1, 1e-9)


def smin(a, b, k):
    h = np.clip(0.5 + 0.5 * (b - a) / k, 0.0, 1.0)
    return b * (1 - h) + a * h - k * h * (1 - h)


def quad_sphere(cuts):
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=2.0)
    bmesh.ops.subdivide_edges(bm, edges=bm.edges[:], cuts=cuts, use_grid_fill=True)
    for v in bm.verts:
        v.co.normalize()
    return bm


def sdf_mesh(name, sdf, center, cuts=24, tmax=4.0):
    """Shrink-wrap a quad sphere onto the zero set of a star-shaped SDF."""
    bm = quad_sphere(cuts)
    center = np.asarray(center, dtype=float)
    dirs = np.array([v.co[:] for v in bm.verts])
    lo = np.zeros(len(dirs))
    hi = np.full(len(dirs), tmax)
    for _ in range(40):
        mid = (lo + hi) / 2
        inside = sdf(center + dirs * mid[:, None]) < 0
        lo = np.where(inside, mid, lo)
        hi = np.where(inside, hi, mid)
    pts = center + dirs * lo[:, None]
    for v, p in zip(bm.verts, pts):
        v.co = Vector(p)
    return bm_to_mesh(name, bm)


def surface_point(sdf, x, z, y_from=-3.0, y_to=0.0):
    """Where a ray along +Y at (x, z) first hits the surface, plus the normal."""
    lo, hi = y_from, y_to  # outside .. inside
    for _ in range(50):
        mid = (lo + hi) / 2
        if sdf(np.array([[x, mid, z]]))[0] < 0:
            hi = mid
        else:
            lo = mid
    p = np.array([x, hi, z])
    e = 1e-4
    n = np.array(
        [
            sdf(np.array([p + d]))[0] - sdf(np.array([p - d]))[0]
            for d in (np.array([e, 0, 0]), np.array([0, e, 0]), np.array([0, 0, e]))
        ]
    )
    return Vector(p), Vector(n).normalized()


def facing(normal, bias=0.5):
    """Rotation turning local -Y (an attachment's front) toward `normal`,
    pulled toward the viewer by `bias` so face details read clearly."""
    d = (normal * (1 - bias) + Vector((0, -1, 0)) * bias).normalized()
    return Vector((0, -1, 0)).rotation_difference(d)


# --------------------------------------------------------------------------
# Painted textures


def grid(size):
    v, u = np.mgrid[0:size, 0:size]
    return (u + 0.5) / size, (v + 0.5) / size


def disc(u, v, cx, cy, r, soft=0.006):
    d = np.hypot(u - cx, v - cy)
    return np.clip((r - d) / soft * 0.5 + 0.5, 0, 1)


def hex_srgba(h):
    h = h.lstrip("#")
    return (*[int(h[i : i + 2], 16) / 255 for i in (0, 2, 4)], 1.0)


def paint(base, mask, color):
    c = np.asarray(hex_srgba(color))
    return base * (1 - mask[..., None]) + c * mask[..., None]


def eye_image(name, inner):
    """Cartoon eye: big brown iris, huge pupil, two catch lights.
    `inner` is +1 when the nose is toward +u, -1 otherwise."""
    u, v = grid(256)
    img = np.ones((256, 256, 4))
    ic = (0.5 + 0.07 * inner, 0.47)
    img = paint(img, disc(u, v, *ic, 0.42), INK)
    img = paint(img, disc(u, v, *ic, 0.385), "#5C3300")
    pc = (0.5 + 0.1 * inner, 0.51)
    img = paint(img, disc(u, v, *pc, 0.28), "#000000")
    img = paint(img, disc(u, v, pc[0] + 0.1, pc[1] + 0.12, 0.085), "#FFFFFF")
    img = paint(img, disc(u, v, pc[0] - 0.1, pc[1] - 0.13, 0.035), "#FFFFFF")
    return image_from_array(name, img)


def tail_image(name):
    """Scaly crosshatch of the paddle tail: two families of gently curved lines."""
    size = 512
    u, v = grid(size)
    img = np.ones((size, size, 4)) * np.asarray(hex_srgba(DARK_GREY))
    x, y = u - 0.5, v - 0.5
    ink = np.zeros_like(u)
    for sign in (1, -1):
        s = (y + sign * 0.55 * x) + 0.35 * x * x * sign
        f = 0.5 - np.abs(((s * 5.0) % 1.0) - 0.5)  # distance to nearest line, in cells
        ink = np.maximum(ink, np.clip((0.045 - f) / 0.015 + 0.5, 0, 1))
    # Fade the lines out before the rim, where the flat projection smears.
    t = np.clip(y * 2.5 / 1.2, -1, 1)
    taper = 1 - 0.32 * np.clip(-t, 0, 1) ** 1.5
    rim = (x / (0.49 * taper)) ** 2 + (y / 0.48) ** 2
    ink *= np.clip((0.8 - rim) / 0.12, 0, 1)
    img = paint(img, ink, "#1A1A1A")
    return image_from_array(name, img)


# --------------------------------------------------------------------------
# The beaver


def body_sdf(p):
    zc, rz = 1.2, 1.12
    t = np.clip((p[:, 2] - zc) / rz, -1, 1)
    taper = 1 - 0.12 * t  # egg: narrower toward the top of the head
    q = p.copy()
    q[:, 0] /= taper
    q[:, 1] /= taper
    d = sd_ellipsoid(q, (0, 0, zc), (0.86, 0.76, rz))
    for sx in (-1, 1):  # big jowly cheeks
        d = smin(d, sd_ellipsoid(p, (sx * 0.4, -0.5, 1.36), (0.42, 0.34, 0.3)), 0.12)
    d = smin(d, sd_ellipsoid(p, (0, -0.22, 0.62), (0.76, 0.62, 0.6)), 0.2)  # belly
    return d


def foot_sdf(p):
    d = sd_ellipsoid(p, (0, 0.05, 0), (0.24, 0.3, 0.085))
    for i, x in enumerate((-0.14, 0.0, 0.14)):
        d = smin(d, sd_ellipsoid(p, (x, -0.26 - 0.03 * (i == 1), 0), (0.085, 0.11, 0.075)), 0.06)
    return d


def tail_sdf(p):
    hz = 1.2
    t = np.clip(p[:, 2] / hz, -1, 1)
    taper = 1 - 0.32 * np.clip(-t, 0, 1) ** 1.5  # narrower where it meets the body
    q = p.copy()
    q[:, 0] /= taper
    return sd_ellipsoid(q, (0, 0, 0), (0.78, 0.1, hz))


MAPLE_LEAF = [  # right half, top tip first; y up, unit-ish size
    (0.00, 1.00), (0.12, 0.76), (0.28, 0.84), (0.23, 0.46), (0.47, 0.66),
    (0.54, 0.53), (0.82, 0.60), (0.71, 0.37), (0.87, 0.29), (0.52, 0.03),
    (0.58, -0.09), (0.22, -0.02), (0.05, -0.13),
]


def build():
    global ROOT
    reset()
    ROOT = bpy.data.objects.new("Beaver", None)
    bpy.context.scene.collection.objects.link(ROOT)

    brown = material("Fur", BROWN)
    ear_in = material("EarInner", EAR_INNER)
    nose_m = material("Nose", NOSE, rough=0.3)
    tooth_m = material("Tooth", TOOTH, rough=0.35)
    grey = material("Feet", DARK_GREY)
    leaf_m = material("Leaf", LEAF_RED, rough=0.25)
    ink = material("Ink", INK)
    shine = material("Shine", "#FFFFFF", emission=1.0)

    # Body: head and torso are one egg, like the drawing.
    body = add_object("Body", sdf_mesh("Body", body_sdf, (0, -0.05, 1.2), cuts=20), brown)
    subsurf(body)

    # Ears with pale inner
    for side, sx in (("L", -1), ("R", 1)):
        rot = Quaternion((0, 1, 0), math.radians(-24 * sx)) @ Quaternion((1, 0, 0), math.radians(8))
        loc = Vector((sx * 0.56, 0.02, 2.1))
        ellipsoid(f"Ear_{side}", loc, (0.21, 0.1, 0.25), brown, rot)
        inner = rot @ Vector((0, -0.065, -0.02))
        ellipsoid(f"EarInner_{side}", loc + inner, (0.14, 0.05, 0.18), ear_in, rot, outline=False)

    # Eyes: textured domes set into the face, with lashes at the outer corner
    for side, sx in (("L", -1), ("R", 1)):
        p, n = surface_point(body_sdf, sx * 0.3, 1.8)
        rot = facing(n, 0.3) @ Quaternion((0, 0, 1), math.radians(-6 * sx))
        rx, ry, rz = 0.215, 0.13, 0.275
        me = ellipsoid_mesh(f"Eye_{side}", (rx, ry, rz), seg=32, rings=16)
        uv = me.uv_layers.new(name="UVMap")
        for loop in me.loops:
            co = me.vertices[loop.vertex_index].co
            uv.data[loop.index].uv = (co.x / rx * 0.5 + 0.5, co.z / rz * 0.5 + 0.5)
        mat = material(f"Eye_{side}", "#FFFFFF", rough=0.15, image=eye_image(f"eye_{side}", -sx))
        eye = add_object(f"Eye_{side}", me, mat)
        eye.rotation_mode = "QUATERNION"
        eye.rotation_quaternion = rot
        eye.location = p + n * 0.01

        lash_pts = []
        for k, (a, l) in enumerate(((0.35, 0.09), (0.05, 0.1))):
            base = Vector((sx * rx * 0.92, -ry * 0.35, rz * a))
            tip = base + Vector((sx * l, -0.02, l * (0.55 - 0.5 * k)))
            mid = (base + tip) / 2 + Vector((0, 0, 0.02))
            lash_pts.append([base, mid, tip])
        for k, pts in enumerate(lash_pts):
            world = [eye.location + rot @ q for q in pts]
            curve_object(f"Lash_{side}{k}", world, 0.013, ink, radii=[1.0, 0.8, 0.3], outline=False)

    # Nose with a glint
    p, n = surface_point(body_sdf, 0, 1.5)
    rot = facing(n, 0.6)
    nose = ellipsoid("Nose", p - n * 0.02, (0.15, 0.08, 0.085), nose_m, rot)
    ellipsoid("NoseGlint", nose.location + rot @ Vector((0.02, -0.07, 0.04)),
              (0.04, 0.015, 0.02), shine, rot, outline=False, seg=16, rings=8)

    # Big buck teeth hanging from between the cheeks
    for side, sx in (("L", -1), ("R", 1)):
        p, n = surface_point(body_sdf, sx * 0.105, 1.12)
        bm = bmesh.new()
        bmesh.ops.create_cube(bm, size=1.0)
        for v in bm.verts:
            v.co = Vector((v.co.x * 0.2, v.co.y * 0.07, v.co.z * 0.26))
        tooth = add_object(f"Tooth_{side}", bm_to_mesh(f"Tooth_{side}", bm), tooth_m)
        tooth.location = Vector((p.x, p.y + 0.015, p.z))
        tooth.rotation_euler = (math.radians(-8), 0, math.radians(-4 * sx))
        bev = tooth.modifiers.new("Bevel", "BEVEL")
        bev.width = 0.03
        bev.segments = 4

    # Feet: webbed paddles with three toes
    for side, sx in (("L", -1), ("R", 1)):
        foot = add_object(f"Foot_{side}", sdf_mesh(f"Foot_{side}", foot_sdf, (0, 0, 0), cuts=10, tmax=1.0), grey)
        foot.location = (sx * 0.42, -0.5, 0.085)
        foot.rotation_euler = (0, 0, math.radians(18 * sx))
        subsurf(foot)

    # Tail: a big scaly paddle rising up behind, leaning out to the left
    me = sdf_mesh("Tail", tail_sdf, (0, 0, 0), cuts=14, tmax=2.0)
    uv = me.uv_layers.new(name="UVMap")
    for loop in me.loops:
        co = me.vertices[loop.vertex_index].co
        uv.data[loop.index].uv = (co.x / 1.6 + 0.5, co.z / 2.5 + 0.5)
    tail = add_object("Tail", me, material("Tail", DARK_GREY, rough=0.5, image=tail_image("tail")))
    subsurf(tail)
    tail.rotation_mode = "XYZ"
    tail.rotation_euler = (math.radians(-22), math.radians(-44), 0)
    base_world = Vector((-0.2, 0.5, 0.3))
    tail.location = base_world - tail.rotation_euler.to_matrix() @ Vector((0, 0, -1.12))
    tail["pivot"] = list(base_world)

    # Maple leaf: a chunky, gummy cut-out held against the belly
    pts = MAPLE_LEAF + [(-x, y) for x, y in reversed(MAPLE_LEAF[1:])]
    cu = bpy.data.curves.new("Leaf_curve", "CURVE")
    cu.dimensions = "2D"
    cu.fill_mode = "BOTH"
    cu.extrude = 0.03
    cu.bevel_depth = 0.035
    cu.bevel_resolution = 3
    cu.resolution_u = 6
    sp = cu.splines.new("BEZIER")
    sp.bezier_points.add(len(pts) - 1)
    sp.use_cyclic_u = True
    for bp, (x, y) in zip(sp.bezier_points, pts):
        bp.co = (x, y, 0)
        bp.handle_left_type = bp.handle_right_type = "AUTO"
    tmp = bpy.data.objects.new("Leaf_tmp", cu)
    bpy.context.scene.collection.objects.link(tmp)
    leaf = mesh_from(tmp, "Leaf", leaf_m)
    stem = curve_object("LeafStem", [Vector((0.02, 0, -0.08)), Vector((0.06, 0, -0.3)),
                                     Vector((0.16, 0, -0.52))], 0.05, leaf_m, radii=[1.0, 0.9, 0.75])
    for ob in (leaf, stem):
        ob.parent = None
        ob.matrix_world = Matrix.Identity(4)
    leaf_root = bpy.data.objects.new("LeafGroup", None)
    bpy.context.scene.collection.objects.link(leaf_root)
    leaf_root.parent = ROOT
    for ob in (leaf, stem):
        ob.parent = leaf_root
    # Stand the leaf up facing the viewer, tipped back a touch and turned
    leaf_root.scale = (0.56, 0.56, 0.56)
    leaf_root.rotation_euler = (math.radians(90 - 10), math.radians(14), math.radians(4))
    leaf_root.location = (0.04, -0.95, 0.56)

    # Arms wrapping around the leaf, little paws on top
    for side, sx in (("L", -1), ("R", 1)):
        shoulder = Vector((sx * 0.66, -0.4, 1.02))
        elbow = Vector((sx * 0.74, -0.7, 0.72))
        paw = Vector((sx * 0.4, -1.04, 0.62))
        curve_object(f"Arm_{side}", [shoulder, elbow, paw], 0.105, brown, radii=[1.0, 1.0, 0.85])
        ellipsoid(f"Paw_{side}", paw + Vector((-sx * 0.03, -0.01, 0)), (0.12, 0.09, 0.1), brown)
        for k in range(3):
            ellipsoid(f"Finger_{side}{k}", paw + Vector((-sx * 0.12, -0.04, 0.06 - k * 0.06)),
                      (0.045, 0.04, 0.032), brown, seg=16, rings=8)

    return ROOT


# --------------------------------------------------------------------------
# Preview renders, export


def add_preview_rig():
    scene = bpy.context.scene
    world = bpy.data.worlds.new("World")
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs["Color"].default_value = hex_rgba("#DEB887")
    world.node_tree.nodes["Background"].inputs["Strength"].default_value = 1.0
    scene.world = world

    sun = bpy.data.objects.new("Sun", bpy.data.lights.new("Sun", "SUN"))
    sun.data.energy = 3.0
    sun.rotation_euler = (math.radians(50), math.radians(10), math.radians(-35))
    scene.collection.objects.link(sun)

    cam = bpy.data.objects.new("Camera", bpy.data.cameras.new("Camera"))
    cam.data.lens = 60
    scene.collection.objects.link(cam)
    scene.camera = cam

    try:
        scene.render.engine = "BLENDER_EEVEE"
    except TypeError:
        scene.render.engine = "BLENDER_EEVEE_NEXT"
    scene.render.resolution_x = scene.render.resolution_y = 900
    scene.view_settings.view_transform = "Standard"
    return cam


def aim(cam, loc, target=(0, 0, 1.1)):
    cam.location = loc
    d = Vector(target) - Vector(loc)
    cam.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()


def main():
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    args = dict(zip(argv[::2], argv[1::2]))

    build()

    if "--glb" in args:
        bpy.ops.export_scene.gltf(
            filepath=args["--glb"],
            export_format="GLB",
            export_apply=True,
            export_extras=True,
            export_yup=True,
            export_cameras=False,
            export_lights=False,
        )

    cam = add_preview_rig()
    aim(cam, (-2.2, -6.0, 1.9))

    if "--blend" in args:
        bpy.ops.wm.save_as_mainfile(filepath=args["--blend"], compress=True)

    if "--renders" in args:
        out = args["--renders"].rstrip("\\/")
        views = {
            "front": (0, -6.5, 1.4),
            "three_quarter": (-2.2, -6.0, 1.9),
            "side": (-6.5, -0.6, 1.4),
            "back": (-2.5, 6.0, 2.0),
        }
        for name, loc in views.items():
            aim(cam, loc)
            bpy.context.scene.render.filepath = f"{out}\\{name}.png"
            bpy.ops.render.render(write_still=True)


main()
