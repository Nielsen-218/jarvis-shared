# Licence audit for the character pipeline (2026-10-31)

Not legal advice. Read from the licence files actually on this PC; where a file is missing it says so.
Your own art (original characters, your avatar) is yours. These licences are about the TOOLS and MODELS JARVIS
runs on it, and what they allow you to do with what comes out.

| Component | Used for | Licence (evidence) | What it means for a game |
|---|---|---|---|
| **Hunyuan3D-2** (image -> 3D shape) | `image_to_character` | Tencent Hunyuan 3D 2.0 **Community** License (`Jarvis3D/Hunyuan3D-2/LICENSE`). Code headers still say "non-commercial"; the LICENSE file is what governs. | Commercial use is allowed **only inside the Territory = worldwide EXCEPT the EU, UK and South Korea**; using, distributing or displaying the Output outside the Territory is unlicensed (s.5c). Over 1M monthly active users needs a licence from Tencent (s.4). Outputs may not be used to train or improve any other AI model (s.5b). Tencent claims no rights in your Outputs (s.6d). **A game sold in the EU/UK/South Korea using Hunyuan-generated geometry is a risk.** |
| FLUX.2-klein-4B (text -> image) | concept images (not yet wired) | No licence file in the local cache (weights only) | **Unverified.** Check the model card before shipping anything made with it. |
| TRELLIS, TripoSG, TripoSR (image -> 3D) | cloned in `blender-avengers/02-image-to-3d`, not installed | MIT (code). Weights' model cards not checked | Candidate commercial-safe generators; confirm each weight licence before relying on them. |
| UniRig (auto rigger) | planned rigger candidate | MIT (code). Weights not checked | Fine for a game; confirm weights card. |
| stable-fast-3d | cloned | Stability AI Community License | Free below a revenue threshold; read before use. |
| Blender 5.2, Rigify, glTF/FBX exporters | all rigging, export | GPL (the tool). Files you make are yours | Nothing you export is GPL-encumbered. |
| Unreal Engine 5.8 | target engine | Epic EULA | Royalty terms above the free tier; assets you import are yours. |
| llama3.2 3B (brain), qwen3-vl 8B (vision) | chat / judging renders | Llama 3.2 Community License / Apache-2.0 | Not part of shipped assets. |
| **Meshy** (made the `Meshy_AI_*` files) | your existing characters | Depends on YOUR Meshy plan: free tier is CC BY 4.0 (attribution), paid tiers grant commercial ownership | **Check which plan made each file** (the files were made before JARVIS touched them). |

## Your characters
* Original art you made with ChatGPT is yours to use (OpenAI assigns output rights to the user). Caution, not a legal
  opinion: in the US, work with little human authorship can have weak copyright protection, so keep your prompts,
  sketches and edits as evidence of authorship.
* **Your avatar is a likeness of you.** JARVIS's pipeline (Hunyuan3D, Blender, rigging) runs fully **on your PC**; the
  image is not uploaded anywhere by JARVIS. Cloud tools (Meshy, ChatGPT) are outside JARVIS and have their own terms.

## What JARVIS now does about it
* Every `image_to_character` run writes `provenance.json` next to the model (generator, licence, restrictions, input
  image path) and says the restriction out loud.
* Rigging, decimation and animation steps are your own pipeline (Blender): outputs carry no extra licence.

## Decisions for the owner
1. Will Living Shadow World be sold in the EU, UK or South Korea? If yes, do not ship Hunyuan-generated geometry; use
   the MIT generators (needs install + a weights check) or hand-model/Meshy-paid meshes instead.
2. Which Meshy plan made the Vanguard / Neon Sentinel / Red Sweater / Pink Confidence files?
3. Is the FLUX.2-klein-4B card Apache-2.0 for the 4B model? (Check on the model page before using it commercially.)
