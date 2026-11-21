"""The rigging engine's tunable knobs (pure Python, no Blender), shared by the engine and by JARVIS's dojo.

name: (default, low, high). The JUDGE (pose tests, skin validation, IK tests, export, round trip) has NO knobs on purpose:
an experiment may change how a character is fitted, never how the result is measured."""

PARAM_SPEC = {
    "arm_below_crotch": (0.03, -0.2, 0.4),
    "arm_floor_frac": (None, 0.1, 0.7),
    "arm_ext_min": (1.15, 1.0, 2.0),
    "arm_reg_lo": (1.3, 1.0, 2.0),
    "arm_reg_hi": (0.9, 0.5, 1.0),
    "crotch_plausible": (0.45, 0.3, 0.6),
    "crotch_proportion": (0.54, 0.4, 0.6),
    "elbow_frac": (0.48, 0.35, 0.6),
    "wrist_frac": (0.80, 0.6, 0.92),
    "prune_radius": (0.006, 0.002, 0.03),
    "prune_min_verts": (10, 3, 200),
    "proxy_faces": (25000, 5000, 80000),
    "weld_voxel_div": (150, 60, 400),
    "skin_skip": ([], None, None),
    "anat_voxels": (160, 64, 220),
    "anat_min_branch": (0.07, 0.03, 0.25),
    "anat_simplify": (0.6, 0.2, 2.5),
    "anat_wing_flat": (0.18, 0.05, 0.5),
    "anat_center_iters": (3, 0, 6),
    "anat_max_branches": (24, 4, 40),
    "anat_seg_len": (4.5, 2.0, 9.0),
    "anat_wing_share": (0.10, 0.02, 0.5),
    "anat_sheet_cut": (3, 1, 6),
}

PARAM_DOC = {
    "arm_below_crotch": "how far below the crotch (fraction of body height) the search for the hand/arm tip reaches; raise it for arms hanging down by the sides",
    "arm_floor_frac": "if set, the absolute lowest height (fraction of body height above the feet) the arm tip search may reach; overrides arm_below_crotch",
    "arm_ext_min": "an arm counts as visible when it sticks out this many torso half-widths; lower it for arms held close to the body",
    "arm_reg_lo": "inner edge (in torso half-widths) of the region used to fit the arm axis",
    "arm_reg_hi": "outer edge (fraction of the arm's max reach) of the region used to fit the arm axis",
    "crotch_plausible": "the crotch must be at least this fraction of neck height, else anatomical proportion is used (coats and skirts hide the leg gap)",
    "crotch_proportion": "fraction of neck height used for the crotch when the measured one is implausible",
    "elbow_frac": "where the elbow sits along the shoulder-to-hand line",
    "wrist_frac": "where the wrist sits along the shoulder-to-hand line (the hand bone starts here)",
    "prune_radius": "support radius (fraction of height) for keeping finger/toe bones; larger removes more fine bones",
    "prune_min_verts": "minimum mesh vertices near a finger/toe bone to keep it",
    "proxy_faces": "face count of the proxy mesh used for automatic weights on dense models",
    "weld_voxel_div": "resolution of the welded proxy (height / this); higher keeps thin parts like arms",
    "anat_voxels": "voxel resolution of the anatomy analysis (cells along the longest side); higher sees thinner parts but is slower",
    "anat_min_branch": "shortest limb/branch kept, as a fraction of the longest geodesic path; lower finds small appendages, higher ignores bumps",
    "anat_simplify": "joint simplification tolerance in local radii; lower = more joints following curves, higher = straighter bones",
    "anat_wing_flat": "a branch is a wing/fin when its cross-section flatness ratio is below this; raise it to treat chunkier flat parts as wings",
    "anat_center_iters": "how many times each limb's path is pulled to the centre of its tissue",
    "anat_max_branches": "maximum number of branches (limbs, wings, tails...) the analysis may extract",
    "anat_seg_len": "target bone length in local radii: lower = more bones per limb",
    "anat_wing_share": "a branch is a wing when at least this share of the tissue around it is thin membrane; lower it for small or sparse wings",
    "anat_sheet_cut": "tissue thinner than this many voxels (half-thickness) can be membrane (wing, fin, fan); raise it for chunkier wings",
    "skin_skip": "list of skin methods to skip: direct, proxy, weld (jump straight to a method that suits a dense or messy scan)",
}


def validate_params(d):
    """Return an error string, or None if every name is known and every value is inside its range."""
    if not isinstance(d, dict):
        return "params must be an object"
    for k, v in d.items():
        if k not in PARAM_SPEC:
            return f"unknown parameter {k}"
        default, lo, hi = PARAM_SPEC[k]
        if k == "skin_skip":
            if not isinstance(v, list) or any(x not in ("direct", "proxy", "weld") for x in v):
                return "skin_skip must be a list of direct/proxy/weld"
        elif v is None:
            if default is not None:
                return f"{k} cannot be None"
        elif not isinstance(v, (int, float)) or isinstance(v, bool) or not lo <= v <= hi:
            return f"{k}={v} outside [{lo}, {hi}]"
    return None
