# YuE2 Studio integration

`music_index.html` is adapted from the existing YuE2 Studio interface in the
YuE Apache 2.0 project: https://github.com/multimodal-art-projection/YuE.
The applicable license is included in `YuE-LICENSE.txt`.

OpenCore modifications: blue appearance, generic model status in the heading,
immediate cancellation feedback, and separate resource-cleanup state.
`music_host.py` loads the user's existing studio server and Python installation,
adds cancellation checks to file hashing, and serves this interface. It does
not copy model weights, environments, song outputs, or user source files.
