Unlimited OCR 2 publisher model code

Source: https://huggingface.co/baidu/Unlimited-OCR-2
Revision: 9cf077c4b206e0e2a31879df937b0b0bcba3661c

The Python modules were compared against the Hub's pinned Git blob hashes and
sizes. OpenCore changes the coordinate parser to `ast.literal_eval` and the
optional checkpoint loader to `torch.load(..., weights_only=True)`. OpenCore
loads these bundled files directly and does not execute Python from a model
download. The publisher's MIT license is included in LICENSE.baiduo.txt. The
DeepSeek-derived modules also retain their Apache-2.0 notices in their headers;
the Apache-2.0 license is included in LICENSE.apache-2.0.txt.
