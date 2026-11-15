# YuE2 Studio integration

`music_index.html` is adapted from the existing YuE2 Studio interface in the
YuE Apache 2.0 project: https://github.com/multimodal-art-projection/YuE.
The applicable license is included in `YuE-LICENSE.txt`.

OpenCore modifications: blue appearance, generic model status in the heading,
immediate cancellation feedback, and separate resource-cleanup state.
`music_host.py` loads the studio server and Python installation in the YuE
folder, adds cancellation checks to file hashing, and serves this interface. It
does not copy model weights, environments, song outputs, or user source files.

## Music runtime installer

`runtime-setup/music-runtime/` lets OpenCore install that runtime on request.
It downloads the YuE inference code (`yue2` package and license) from a pinned
commit of https://github.com/multimodal-art-projection/YuE under the Apache
License 2.0, verifying each file's SHA-256, and installs PyTorch and the other
Python packages listed in `music-runtime.json` from PyPI and
download.pytorch.org under their own licenses. `studio_server.py` is written by
OpenCore and installed as `studio/server.py`. The YuE2 model weights are not
part of this installer; they are downloaded separately and are licensed
CC-BY-NC-4.0 (non-commercial use).
