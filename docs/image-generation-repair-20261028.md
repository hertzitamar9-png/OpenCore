# Image generation in OpenCore

Open Conversations and select **Images** above the message composer, or open
**Media Studio → Images**. Both use the same form and durable PC-owned job queue.
The image picker lists downloaded generators and connected external generators;
LoRAs are listed separately under a compatible base. Image preparation runs
automatically when Generate is pressed, including an actual model execution test.
The prompt is submitted after preparation succeeds. Errors keep the prompt for
retry. Regular chat displays generation progress, cancellation, and the generated
image in the same conversation. Game Dev Studio retains its advanced settings and
saved presets.

## Repaired release contract

The preserved native executable contains its own runtime recipes and Python worker
resources. Updating only the loose installation files left the native verifier and
smoke worker on older versions. Packaging now updates these declared data slots
alongside the frontend, validates each original slot against the baseline, verifies
compression round trips, and checks that bytes outside the declared data ranges
remain unchanged. Python resources are compressed into their existing slots;
native machine code and executable length remain unchanged.

MOSS and Qwen ComfyUI recipes use safetensors 0.8.0, satisfying the pinned
Transformers version. Windows extended paths are normalized before containment
checks, job directories exist before ComfyUI startup, and the readiness check
recognizes the listener owned by a managed Python launcher's child. Owned child
processes are stopped after generation. The Qwen loader bundle is included in
installers, with upstream revision, license and the RAM-copy change documented in
its OPENCORE-NOTICE.txt. Published output previews omit internal duplicate copies.

## Verification

On the installed application with an RTX 4070, MOSS INT8 ConvRot and Qwen Image 2.1
Uncensored NVFP4 both passed native model preparation and generated 512×512 images
from the regular chat form with their corresponding downloaded LoRAs. The MOSS
runtime attached 252 LoRA patches; Qwen attached 192. Both jobs completed without
errors in the same conversation, and their PNGs were decoded and displayed inline.
The generated files are local test evidence, not release assets.

Automated coverage checks the native data boundaries, executable Python resource
wrappers, Windows path normalization, launcher-child readiness, downloaded-only
selection, LoRA job arguments, automatic verified preparation, failed preparation,
same-chat previews without duplicate internal images, and phone-width layout.
These results qualify the tested MOSS and Qwen packages; they do not assert that
an arbitrary image architecture or LoRA is compatible.
