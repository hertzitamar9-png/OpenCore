# Large image exports

Add 2K (2048 square), 4K (4096 square), and 8K (8192 square) to the shared
Media Studio/chat image form. Show exact dimensions. Preserve the existing
256/512/768/1024 options. Large exports render at 1024 square and resize locally
with Lanczos; label them as upscaled rather than native high-resolution inference.
Pass outputWidth/outputHeight separately so native job validation and GPU work
remain bounded. Both ComfyUI and Diffusers save the requested dimensions, report
the resizing stage, and retain output/render metadata in PNG files.

Managed installations must request a real inference verification before Ready.
Standard Hub image/text imports already prepare automatically after download.
Unknown architectures, incompatible adapters, and hardware limits remain explicit;
do not relabel a download as executable without a passing runtime test.

Implementation: regression tests first; shared controls and worker output helper;
Hub validation and installation invariant; packaged browser/worker checks; publish
the signed update and verify the installed user-facing form and real output.
