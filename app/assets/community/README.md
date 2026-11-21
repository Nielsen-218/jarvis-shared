# Community character artwork

All artwork and fitting/rig data in this directory are **CC0-1.0**. The full
dedication is in `LICENSE-CC0.md`. No MakeHuman or MPFB application code is copied.

## Sources and credits

- Official [MakeHuman system asset pack license list](https://github.com/makehumancommunity/makehuman-static-website/blob/master/content/Assets/AssetPacks/makehuman_system_assets.md).
- [CC0 system pack](https://files2.makehumancommunity.org/asset_packs/makehuman_system_assets/makehuman_system_assets_cc0.zip): male/female casual suit 01, male elegant suit 01, shoes 01, braid 01, short hair 04, afro 01, ponytail 01, high-poly eyes, eyelashes 01, eyebrow 001, brown/light-brown/green/deep-blue/grey eye textures, and young dark-skinned male/female diffuse textures.
- Copyright notices identify MakeHuman Community, Data Collection AB, Joel Palmius and Jonas Hauquier. Each selected MHCLO file explicitly records the September 2020 release to CC0. Exact notices, paths and SHA-256 hashes are retained in `manifest.json`.
- [MPFB game-engine weights](https://github.com/makehumancommunity/mpfb2/blob/3af0cd5a2ffba072e93a34e5f6f494fdc4813ddf/src/mpfb/data/rigs/standard/weights.game_engine.json), pinned revision `3af0cd5a2ffba072e93a34e5f6f494fdc4813ddf`; that data file explicitly declares CC0. The earlier arm-only export is retained for compatibility alongside the full rig below.
- The same revision's [game-engine skeleton](https://github.com/makehumancommunity/mpfb2/blob/3af0cd5a2ffba072e93a34e5f6f494fdc4813ddf/src/mpfb/data/rigs/standard/rig.game_engine.json) and complete weights now also supply the 53-bone rig in `rig.json`. Four strongest normalized influences are retained per body vertex; garment and newly clipped vertices interpolate those weights. Browser-authored poses/idle use this rig; no external motion capture is claimed.
- Additional [CC0 shirts](https://github.com/makehumancommunity/makehuman-static-website/blob/master/content/Assets/AssetPacks/shirts01.md): **Namuhekam**, male polo with diffuse/OpenGL normal/roughness/height maps; **Margaret Toigo**, keyhole tank with authored fabric. Individual MHCLO headers explicitly declare CC0. Paths, author notices and source hashes are in each manifest entry.
- Additional [CC0 hair](https://github.com/makehumancommunity/makehuman-static-website/blob/master/content/Assets/AssetPacks/hair01.md): **Cortu Johnstone**, short messy hair; **Margaret Toigo**, curled-under bob with normal map. Source diffuse maps were desaturated to support selectable hair colors; Cortu's object-space normal map was intentionally not treated as a tangent-space normal map.
- Skin surface detail: **Katsukagi / 3DTextures.me**, [Skin Human 002](https://3dtextures.me/2019/01/24/skin-human-002/), with [explicit CC0 redistribution permission](https://3dtextures.me/about/). The public linked normal map is resized to 1024px WebP and used at low intensity with repeat wrapping. It adds surface shading, not a different skin-color/photo identity. Original SHA-256 and public download link are retained in `surface_detail` / `source_hashes`.
- The separately credited CC0 base human/morphs are in `../avatar/`. Rendering uses separately licensed MIT Three.js in `../../vendor/three/`.

## Transformations and reproduction

`scripts/community_assets.py` lists ZIP entries and downloads selected source files
using verified HTTP ranges. It refuses full-pack responses. Raw artwork is stored
only in ignored `data/community-source/`; the entire 268 MiB pack is unnecessary.
`scripts/export_community_assets.py` converts those files to browser JSON, retaining
UVs, triangles, body-fitting bindings, body deletion masks and source hashes.
Diffuse/normal textures are resized to 1024px WebP; desktop skin uses 2048px, touch
skin uses 1024px. Thumbnails are capped at 160px. Arm weights are aggregated from
the published rig data. The complete browser community bundle is about 14.7 MB
uncompressed, including selected garment-finishing data; the actual app static
mount compresses JSON responses and preserves conditional caching. A local level-4
gzip measurement reduced the body/community JSON from 13.1 MB to 3.6 MB (72%).
This is a byte-count measurement, not a physical-phone speed benchmark.

The renderer fits the artwork to existing body morphs, applies the relaxed arm
pose, and clips hidden waist/boot boundaries while interpolating UV coordinates.
Six hairstyle assets, three garment ensembles and two individual tops are curated previews, not a
complete fashion catalog. Preview choices do not add products to owned inventory.
Skin selections are visual controls; no medical/genetic inference is made.
Textures contain their original authored branding (including the MakeHuman tee).
These assets do not imply endorsement by MakeHuman or the depicted identity of
either household member. No personal photographs or measurements are included.

High-poly eyes include an alpha-masked corneal region: its source alpha must be
respected or it covers the iris with opaque dark geometry. The renderer preserves
that alpha and uses the authored iris variants rather than painting over sclera.
Older hair-card assets, especially afro 01, can expose roots/planes in close-ups;
more options are not a claim that every asset meets modern game-hair quality.

The polo, tank and bob have one Catmull-Clark finishing step applied to their
authored polygon cages. Sparse control-point recipes preserve live body fitting,
skin weights and face-varying UV seams. This smooths collars/cuffs/contours without
replacing artist-made garments with procedural clothing. Source shapes and terms
remain credited; generated mesh counts and transformations are in the manifest.
The CC-BY hair02 pack was inspected but is **not used** because its versioned
license metadata was not resolved. Its source metadata stays ignored/local.
