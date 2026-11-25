"""Pinned local derivation and CUDA profile for Nanbeige Extreme.

No arbitrary executable paths, remote model code, or lower-precision target.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import time
import uuid

IDENTITY = "hf-6e616e626569676565787472"
REVISION = "527d4a61bdbac641176cd1677d269c7eb4a50e72"
REPO = "local/Nanbeige-Extreme"
TARGET = "Nanbeige4.2-3B-SAT-Q8_0.gguf"
DRAFT = "Nanbeige4.2-3B-DSpark-Q8_0.gguf"
ENGINE = "nanbeige-extreme-cuda12-v1"
LIBS = "nanbeige-extreme-cuda12-libs-v1"
STOCK_REPO = "bartowski/Nanbeige_Nanbeige4.2-3B-GGUF"
STOCK_REV = "17562eefe9752007209148d5f7a6e275fc8d8077"
STOCK_NAME = "Nanbeige_Nanbeige4.2-3B-Q8_0.gguf"
DRAFT_REPO = "Anbeeld/Nanbeige4.2-3B-DSpark-GGUF"
DRAFT_REV = "afd4c12e499cdb8de25d9e5791ac8da12601999e"
STOCK_SHA = "837ba713ef3a3b5c9aee82e5dcba07600ea7db36ae392419f982b3bfaec04ef2"
TARGET_SHA = "2db77c24159bb323bc0b933ccb03d5f036c68714f79040846ac9ff0d212e2eec"
DRAFT_SHA = "65a11de7f6d63dd0b8cf00d0386997b0dfae5a93be6e5e82c58ea7434ea2aedd"
STOCK_BYTES, TARGET_BYTES, DRAFT_BYTES = 4434787488,4434787552,905017600

def _pin(repo, rev, name, size, digest, role):
    return dict(repository=repo,revision=rev,filename=name,bytes=size,sha256=digest,
                gitBlobSha1=None,role=role,pinned=True)

def catalog_entry():
    files=[_pin(REPO,REVISION,TARGET,TARGET_BYTES,TARGET_SHA,"model"),
           _pin(REPO,REVISION,DRAFT,DRAFT_BYTES,DRAFT_SHA,"support")]
    modes=[dict(id=m,targetContextTokens=t,contextTokens=8192,supported=True)
           for m,t in (("native",1048576),("echo",32768))]
    return dict(id=IDENTITY,repository=REPO,revision=REVISION,variantKey="local:nanbeige-extreme-q8",
        format="gguf",quantization="Q8_0",subdirectory="",category="text",files=files,
        bytes=TARGET_BYTES+DRAFT_BYTES,installable=True,status="not-installed",
        runtime=dict(adapter="gguf",available=False,inferenceVerified=False,
                     reason="Requires NVIDIA CUDA; install and verify actual generation."),
        familyId="local-nanbeige-extreme",familyLabel="Nanbeige Extreme",name="Nanbeige Extreme",
        builtIn=True,nativeModelIds=[],libraryCategory="text",origin="nanbeige-extreme",
        managedEngine=ENGINE,modelType="nanbeige",localDerived=True,
        builtInDescription="Pinned local Q8 derivative of Nanbeige4.2 with Q8 DSpark and prompt lookup.",
        description="8K context, CUDA-only. Speculative decoding verifies draft tokens against the target.",
        originalRepository="Nanbeige/Nanbeige4.2-3B",sourceUrl="https://huggingface.co/Nanbeige/Nanbeige4.2-3B",
        license="apache-2.0",supportsTextChat=True,supportsTextGeneration=True,outputsText=True,
        inputModalities=["text"],outputModalities=["text"],contextTokens=8192,supportedContextTokens=8192,
        operatingMode="native",operatingModes=modes,modeAvailability=[dict(x,scope="text") for x in modes],
        runtimePreference="cuda")

def check_entry(entry):
    expected=catalog_entry()
    for key in ("id","repository","revision","files","bytes"):
        if entry.get(key)!=expected[key]:raise ValueError("Nanbeige Extreme package pin differs")

def _digest(path, pause):
    from hf_download import Downloader
    h=hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda:f.read(8<<20),b""):
            Downloader._pause(pause)
            h.update(chunk)
    return h.hexdigest()

def reconstruct(source, target, pause):
    """Build the same GGUF byte for byte; reject any unpinned input/output."""
    from hf_download import Downloader
    source,target=Path(source),Path(target)
    if source.resolve()==target.resolve():raise ValueError("Never edit the original checkpoint")
    if source.stat().st_size!=STOCK_BYTES or _digest(source,pause)!=STOCK_SHA:
        raise ValueError("Nanbeige base checksum mismatch")
    spec=json.loads(Path(__file__).with_name("nanbeige-extreme-patch.json").read_text(encoding="utf8"))
    if (spec["schema"],spec["source_sha256"],spec["target_sha256"])!=(1,STOCK_SHA,TARGET_SHA):
        raise ValueError("Invalid Nanbeige patch")
    prefix=base64.b64decode(spec["header"],validate=True)
    temp=target.with_name(target.name+"."+uuid.uuid4().hex+".tmp")
    try:
        with source.open("rb") as a,temp.open("xb") as b:
            a.seek(24)
            b.write(prefix)
            b.write(a.read(spec["source_metadata_end"]-24))
            b.write(bytes(spec["target_data_start"]-b.tell()))
            a.seek(spec["source_data_start"])
            for block in iter(lambda:a.read(8<<20),b""):
                Downloader._pause(pause)
                b.write(block)
            for row in spec["rows"]:
                offset=int(row["offset"])
                data=base64.b64decode(row["data"],validate=True)
                if not spec["target_data_start"]<=offset<=TARGET_BYTES-len(data):
                    raise ValueError("Invalid patch row")
                b.seek(offset);b.write(data)
        if temp.stat().st_size!=TARGET_BYTES or _digest(temp,pause)!=TARGET_SHA:
            raise ValueError("Reconstructed Q8 checkpoint does not match the benchmarked model")
        temp.replace(target)
    finally:temp.unlink(missing_ok=True)

def _source_entry(repo,rev,name,size,digest):
    from hf_catalog import stable_id
    return dict(id=stable_id(repo,"gguf:"+name),repository=repo,revision=rev,
                format="gguf",subdirectory="",installable=True,bytes=size,
                files=[_pin(repo,rev,name,size,digest,"model")])

def sources():
    return (_source_entry(STOCK_REPO,STOCK_REV,STOCK_NAME,STOCK_BYTES,STOCK_SHA),
            _source_entry(DRAFT_REPO,DRAFT_REV,DRAFT,DRAFT_BYTES,DRAFT_SHA))

def install(entry,downloader,pause,report,parallel_ranges=True):
    from hf_download import atomic_json,managed_path,Downloader
    check_entry(entry)
    folder=downloader.directory(entry)
    folder.mkdir(parents=True,exist_ok=True)
    targets=[managed_path(folder,pin["filename"]) for pin in entry["files"]]
    with downloader.owned_targets(targets,pause,report):
        done=0
        for pin,target,upstream in zip(entry["files"],targets,sources()):
            if downloader._verify(target,pin,pause) is None:
                def progress(v):
                    report({**v,"bytesDone":min(entry["bytes"],done+v.get("bytesDone",0)),
                            "bytesTotal":entry["bytes"]})
                receipt=downloader.install(upstream,pause,progress,parallel_ranges=parallel_ranges)
                source=Path(receipt["files"][0]["localPath"])
                Downloader._pause(pause)
                if target.name==TARGET:
                    if shutil.disk_usage(folder).free<TARGET_BYTES+downloader.disk_reserve:
                        raise ValueError("Additional free space is needed to build the Q8 derivative")
                    report(dict(stage="verifying",detail="Reconstructing the exact pinned Q8 derivative"))
                    reconstruct(source,target,pause)
                else:
                    tmp=target.with_name(target.name+"."+uuid.uuid4().hex+".tmp")
                    try:
                        try:os.link(source,tmp)
                        except OSError:
                            with source.open("rb") as a,tmp.open("xb") as b:
                                for chunk in iter(lambda:a.read(8<<20),b""):
                                    Downloader._pause(pause);b.write(chunk)
                        if _digest(tmp,pause)!=DRAFT_SHA:raise ValueError("Draft checksum mismatch")
                        tmp.replace(target)
                    finally:tmp.unlink(missing_ok=True)
            done+=pin["bytes"]
        receipt_path=managed_path(folder,"install-receipt.json")
        receipt=dict(schema=1,id=IDENTITY,repository=REPO,revision=REVISION,bytes=entry["bytes"],
            files=[dict(pin,localPath=str(target),actualSha256=pin["sha256"]) for pin,target in zip(entry["files"],targets)],
            installedAt=time.time(),inferenceVerified=False,localDirectory=str(folder),receiptPath=str(receipt_path))
        Downloader._pause(pause)
        atomic_json(receipt_path,receipt)
        report(dict(stage="installed",bytesDone=entry["bytes"],bytesTotal=entry["bytes"]))
        return receipt

def plan(entry,root,prefer_gpu=True,override=None):
    from hf_runtime import _weight_folder
    from managed_engines import executable,cuda_driver_version
    from model_modes import mode_context,NATIVE_TARGET,ECHO_TARGET
    check_entry(entry)
    if override not in (None,"auto","cuda","managed-cuda") or not prefer_gpu or entry.get("runtimePreference")=="cpu":
        raise ValueError("Nanbeige Extreme requires NVIDIA CUDA")
    if cuda_driver_version()<12040:
        raise ValueError("Nanbeige Extreme requires a CUDA 12.4-compatible NVIDIA driver")
    folder=_weight_folder(entry,root)
    ctx=mode_context(entry)
    if ctx!=8192:raise ValueError("Nanbeige Extreme is verified at 8192 context tokens")
    mode=entry.get("operatingMode","native")
    cmd=[str(executable(root,ENGINE)),"-m",str(folder/TARGET),"-a",IDENTITY,"-ngl","99","-fa","on",
         "-c",str(ctx),"-np","1","--host","127.0.0.1","--port","{port}","--api-key-file","{keyfile}",
         "--no-webui","--no-cache-prompt","--cache-ram","0","--spec-type","ngram-simple,draft-dspark",
         "-md",str(folder/DRAFT),"-ngld","99","--spec-draft-n-max","7","--spec-draft-p-min","0",
         "--spec-ngram-simple-size-n","8","--spec-ngram-simple-size-m","48","-t","4","-tb","4",
         "--backend-sampling","--no-context-shift","--jinja","--offline","-lv","4","--fit","off"]
    return dict(command=cmd,context=ctx,operatingMode=mode,
        requestedContextTokens=NATIVE_TARGET if mode=="native" else ECHO_TARGET,format="gguf",
        folder=str(folder),runtimeBackend="cuda",publisherEngine=ENGINE,engineFamily=ENGINE,cudaLibraryId=LIBS)
