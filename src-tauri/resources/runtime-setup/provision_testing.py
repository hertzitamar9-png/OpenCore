"""Publisher tool provisioning; existing SDKs, VMs and host features are preserved."""
import ctypes
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import tempfile
import time
import uuid

ANDROID_TOOLS_URL = "https://dl.google.com/android/repository/commandlinetools-win-15859902_latest.zip"
ANDROID_TOOLS_SHA256 = "90ae805d20434428bffcb699c290860f19bb5f66a67e6b330067e3de801fb04a"
VBOX_URL = "https://download.virtualbox.org/virtualbox/7.2.20/VirtualBox-7.2.20-175154-Win.exe"
VBOX_SHA256 = "a81777d2b36380ce042a29e9c554cf032eb46a793f62e3cc82e7411e535c2c26"


def java_major(text):
    match = re.search(r'(?:openjdk|java) (?:version )?["\s]*(\d+)(?:\.(\d+))?', text)
    if not match:
        return None
    major = int(match.group(1))
    return int(match.group(2)) if major == 1 and match.group(2) else major


def existing_file(candidates):
    return next((str(Path(candidate).resolve()) for candidate in candidates if candidate and Path(candidate).is_file()), None)


def java_paths():
    return [Path(os.environ["JAVA_HOME"]) / "bin" / "java.exe" if os.environ.get("JAVA_HOME") else None,
            shutil.which("java"), Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Android/Android Studio/jbr/bin/java.exe"]


def vbox_path():
    return existing_file([shutil.which("VBoxManage"), Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Oracle/VirtualBox/VBoxManage.exe"])


def discover_tools(root, runner):
    root = Path(root)
    sdks = [root / "runtime-setup/android/sdk", Path(os.environ.get("LOCALAPPDATA", str(root))) / "Android/Sdk"]
    sdks.extend(Path(os.environ[name]) for name in ("ANDROID_HOME", "ANDROID_SDK_ROOT") if os.environ.get(name))
    android = []
    for sdk in dict.fromkeys(sdks):
        adb = existing_file([sdk / "platform-tools/adb.exe", sdk / "platform-tools/adb"])
        emulator = existing_file([sdk / "emulator/emulator.exe", sdk / "emulator/emulator"])
        if not adb and not emulator:
            continue
        item = {"sdkRoot": str(sdk), "adb": adb, "emulator": emulator, "managed": sdk.resolve().is_relative_to(root.resolve())}
        if adb:
            try:
                item["devices"] = runner.run([adb, "devices", "-l"], timeout=15).stdout
            except (OSError, RuntimeError) as error:
                item["error"] = str(error)
        if emulator:
            try:
                item["avds"] = runner.run([emulator, "-list-avds"], timeout=15).stdout.splitlines()
                acceleration = runner.run([emulator, "-accel-check"], timeout=15, check=False)
                item["accelerationUsable"] = acceleration.returncode == 0
                item["acceleration"] = acceleration.stdout
            except (OSError, RuntimeError) as error:
                item["error"] = str(error)
        android.append(item)
    java = []
    for candidate in java_paths():
        if not candidate or not Path(candidate).is_file():
            continue
        try:
            version = runner.run([candidate, "-version"], timeout=10).stdout
            java.append({"path": str(candidate), "version": version, "major": java_major(version)})
        except (OSError, RuntimeError):
            pass
    executable, virtualbox = vbox_path(), None
    if executable:
        try:
            virtualbox = {"executable": executable, "version": runner.run([executable, "--version"], timeout=15).stdout,
                          "vms": runner.run([executable, "list", "vms"], timeout=15).stdout}
        except (OSError, RuntimeError) as error:
            virtualbox = {"executable": executable, "error": str(error)}
    return {"android": android, "java": java, "virtualbox": virtualbox}


def require_windows_x64():
    if os.name != "nt" or platform.machine().lower() not in ("amd64", "x86_64"):
        raise RuntimeError("Automatic testing-tool provisioning is verified for Windows x64 only. Existing configured tools remain available on other hosts.")


def source_properties(path):
    try:
        return dict(line.split("=", 1) for line in Path(path).read_text(encoding="utf-8").splitlines() if "=" in line and not line.startswith("#"))
    except OSError:
        return {}


def provision_android(root, options, runner):
    from setup_manager import android_plan, download, extract_zip, WindowsJob
    require_windows_x64()
    plan = android_plan(root, options.get("acceptLicenses", False))
    home, sdk = Path(plan["sdkRoot"]).parent, Path(plan["sdkRoot"])
    # The complete SDK stays managed even if another Android Studio SDK exists.
    tools = sdk / "cmdline-tools" / "opencore-15859902"
    java = next((str(path) for path in java_paths() if path and Path(path).is_file()
                 and (java_major(runner.run([path, "-version"], timeout=10).stdout) or 0) >= 17), None)
    if not java:
        raise RuntimeError("Android setup needs Java 17 or later. Set JAVA_HOME or install Android Studio's bundled Java runtime, then retry. No SDK packages were downloaded.")
    if not (tools / "lib/sdkmanager-classpath.jar").is_file():
        archive = download(ANDROID_TOOLS_URL, home / "downloads/commandlinetools-15859902.zip", ANDROID_TOOLS_SHA256, runner)
        unpacked = home / "unpacked-15859902"
        extract_zip(archive, unpacked)
        tools.parent.mkdir(parents=True, exist_ok=True)
        if tools.exists():
            raise RuntimeError("A partial managed SDK tool folder exists. Retry after reviewing its setup diagnostics; existing files were preserved.")
        (unpacked / "cmdline-tools").rename(tools)
    env = dict(os.environ, ANDROID_HOME=str(sdk), ANDROID_SDK_ROOT=str(sdk),
               ANDROID_AVD_HOME=plan["avdHome"], ANDROID_USER_HOME=str(home / "user"))
    Path(plan["avdHome"]).mkdir(parents=True, exist_ok=True)
    Path(env["ANDROID_USER_HOME"]).mkdir(parents=True, exist_ok=True)
    sdkmanager = [java, f"-Dcom.android.sdklib.toolsdir={tools}", "-classpath", str(tools / "lib/sdkmanager-classpath.jar"), "com.android.sdklib.tool.sdkmanager.SdkManagerCli", f"--sdk_root={sdk}"]
    runner.reporter("accepting-licenses", detail="Applying the Android SDK licenses accepted in the setup form")
    runner.run([*sdkmanager, "--licenses"], env=env, input_text="y\n" * 100, timeout=180)
    runner.reporter("installing-sdk", detail="Installing stable Android API 36, emulator and platform tools")
    runner.run([*sdkmanager, "--channel=0", *plan["packages"]], env=env, input_text="y\n" * 100, timeout=3600)
    avd_ini = Path(plan["avdHome"]) / (plan["avdName"] + ".ini")
    if not avd_ini.is_file():
        runner.reporter("creating-avd", detail="Creating an isolated OpenCore Android virtual device")
        avdmanager = [java, "-classpath", str(tools / "lib/avdmanager-classpath.jar"), "com.android.sdklib.tool.AvdManagerCli"]
        runner.run([*avdmanager, *plan["createAvd"]], env=env, input_text="no\n", timeout=120)
    emulator, adb = sdk / "emulator/emulator.exe", sdk / "platform-tools/adb.exe"
    acceleration = runner.run([emulator, "-accel-check"], env=env, timeout=20, check=False)
    if acceleration.returncode:
        raise RuntimeError("Android emulator acceleration is unavailable. Enable the Windows Hypervisor Platform/firmware virtualization and restart Windows if required. SDK installation is retained. " + acceleration.stdout)
    runner.reporter("verifying-emulator", detail="Booting the managed AVD and waiting for Android's boot-completed property")
    device = subprocess.Popen([str(emulator), "-avd", plan["avdName"], "-port", "5580", "-no-window", "-no-audio", "-gpu", "auto"],
                              env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                              creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP)
    job = WindowsJob(device)
    started, boot = time.monotonic(), None
    try:
        while time.monotonic() - started < 240:
            runner.check_cancel()
            if device.poll() is not None:
                raise RuntimeError("Android emulator exited before its OS finished booting")
            status = runner.run([adb, "-s", "emulator-5580", "shell", "getprop", "sys.boot_completed"], env=env, timeout=10, check=False)
            if status.returncode == 0 and status.stdout.strip() == "1":
                boot = True
                break
            time.sleep(0.5)
        if not boot:
            raise RuntimeError("Managed Android AVD did not finish booting within 240 seconds")
        packages = {name: source_properties(sdk.joinpath(*name.split(";")) / "source.properties") for name in plan["packages"]}
        return {"executable": str(adb), "emulatorExecutable": str(emulator), "sdkRoot": str(sdk), "avdHome": plan["avdHome"],
                "avdName": plan["avdName"], "packageRevisions": packages, "commandLineToolsSha256": ANDROID_TOOLS_SHA256,
                "environmentVerified": True, "bootVerified": True, "bootMs": round((time.monotonic() - started) * 1000),
                "deviceState": "stopped-after-verification"}
    finally:
        job.stop()
        device.wait(timeout=15)
        job.close()


def provision_virtualbox(root, options, runner):
    from setup_manager import download
    require_windows_x64()
    executable = vbox_path()
    installed = False
    if not executable:
        if not options.get("allowAdministrator"):
            raise ValueError("VirtualBox host installation requires Windows administrator permission. Enable the administrator choice in setup to continue.")
        archive = download(VBOX_URL, Path(root) / "runtime-setup/downloads/VirtualBox-7.2.20-Win.exe", VBOX_SHA256, runner, max_bytes=250_000_000)
        runner.reporter("awaiting-administrator", detail="Windows will request permission to install the verified VirtualBox host drivers")
        if ctypes.windll.shell32.IsUserAnAdmin():
            result = runner.run([archive, "--silent", "-msiparams", "REBOOT=ReallySuppress"], timeout=900, check=False)
        else:
            env = dict(os.environ, OPENCORE_VERIFIED_INSTALLER=str(archive))
            script = "$p=Start-Process -FilePath $env:OPENCORE_VERIFIED_INSTALLER -Verb RunAs -ArgumentList @('--silent','-msiparams','REBOOT=ReallySuppress') -Wait -PassThru -WindowStyle Hidden; exit $p.ExitCode"
            result = runner.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script], env=env, timeout=900, check=False)
        if result.returncode not in (0, 3010):
            raise RuntimeError("Windows did not complete VirtualBox installation. Administrator permission, the Microsoft VC++ runtime or a pending Windows restart may be required. " + result.stdout)
        if result.returncode == 3010:
            raise RuntimeError("VirtualBox was installed, but Windows requires a restart before host readiness can be verified. Restart and retry setup.")
        executable, installed = vbox_path(), True
    if not executable:
        raise RuntimeError("VirtualBox installer finished without a usable VBoxManage executable")
    runner.reporter("verifying-tools", detail="Checking the actual VirtualBox command and host virtualization information")
    version = runner.run([executable, "--version"], timeout=20).stdout.strip()
    host = runner.run([executable, "list", "hostinfo"], timeout=30).stdout
    return {"executable": executable, "version": version, "hostInfo": host, "installedBySetup": installed,
            "installerSha256": VBOX_SHA256 if installed else None, "environmentVerified": True, "guestVerified": False}


def vm_inputs(root, options, environment):
    iso = Path(options.get("isoPath", ""))
    if not iso.is_absolute() or not iso.is_file() or iso.suffix.lower() != ".iso":
        raise ValueError("Choose an existing absolute path to a licensed operating-system ISO")
    password_env = options.get("passwordEnv") or "OPENCORE_TEST_GUEST_PASSWORD"
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,100}", password_env) or not environment.get(password_env):
        raise ValueError("Set the named guest password environment variable before unattended VM installation")
    user = options.get("guestUser") or "opencore"
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,30}", user):
        raise ValueError("Guest user must start with a letter and contain 1–31 letters, digits, dashes or underscores")
    identifier = str(uuid.uuid4())
    return {"id": identifier, "name": "OpenCore_Test_" + identifier[:8], "isoPath": str(iso.resolve()),
            "passwordEnv": password_env, "guestUser": user, "baseFolder": str(Path(root).resolve() / "runtime-setup/vms" / identifier)}


def parse_properties(text):
    return {key.strip(): value.strip().strip('"') for line in text.splitlines() if "=" in line for key, value in [line.split("=", 1)]}


def provision_vm(root, options, runner):
    from setup_manager import file_hash
    require_windows_x64()
    config = vm_inputs(root, options, os.environ)
    executable = vbox_path()
    if not executable:
        raise RuntimeError("Install the VirtualBox host tools with the setup action first")
    version = runner.run([executable, "--version"], timeout=20).stdout.strip()
    if tuple(int(value) for value in version.split("r")[0].split(".")[:2]) < (7, 1):
        raise RuntimeError("The managed unattended guest recipe requires VirtualBox 7.1 or later")
    detected = parse_properties(runner.run([executable, "unattended", "detect", "--iso=" + config["isoPath"], "--machine-readable"], timeout=120).stdout)
    os_type = detected.get("OSTypeId", "")
    if not os_type or detected.get("IsInstallSupported", "").lower() not in ("true", "yes", "1"):
        raise RuntimeError("VirtualBox does not support unattended installation for the selected ISO. No VM was created. " + str(detected))
    windows = os_type.lower().startswith("windows")
    base = Path(config["baseFolder"])
    base.mkdir(parents=True, exist_ok=False)
    disk = base / "system.vdi"
    secret = base / ".guest-password.tmp"
    created = False
    try:
        secret.write_text(os.environ[config["passwordEnv"]], encoding="utf-8")
        owner = os.environ.get("USERDOMAIN", "") + "\\" + os.environ.get("USERNAME", "")
        runner.run(["icacls", secret, "/inheritance:r", "/grant:r", owner + ":(R,W)"], timeout=15)
        runner.reporter("creating-vm", detail=f"Creating {config['name']} from the selected licensed ISO", vmId=config["id"])
        runner.run([executable, "createvm", "--name", config["name"], "--uuid", config["id"], "--ostype", os_type, "--basefolder", base, "--register"])
        created = True
        args = [executable, "modifyvm", config["id"], "--memory", "4096", "--cpus", "2", "--nic1", "nat", "--graphicscontroller", "vboxsvga" if windows else "vmsvga", "--firmware", "efi"]
        if os_type.lower().startswith("windows11"):
            args.extend(["--tpm-type", "2.0"])
        runner.run(args)
        runner.run([executable, "createmedium", "disk", "--filename", disk, "--size", "65536", "--format", "VDI"])
        runner.run([executable, "storagectl", config["id"], "--name", "SATA", "--add", "sata", "--controller", "IntelAhci"])
        runner.run([executable, "storageattach", config["id"], "--storagectl", "SATA", "--port", "0", "--device", "0", "--type", "hdd", "--medium", disk])
        runner.reporter("installing-guest", detail="Starting the publisher's unattended OS installer with Guest Additions")
        runner.run([executable, "unattended", "install", config["id"], "--iso=" + config["isoPath"], "--user=" + config["guestUser"],
                    "--user-password-file=" + str(secret), "--admin-password-file=" + str(secret), "--install-additions", "--hostname=opencore-test.local", "--start-vm=headless"], timeout=180)
        started, verified = time.monotonic(), False
        while time.monotonic() - started < 3600:
            runner.check_cancel()
            runner.reporter("waiting-for-guest", detail="Waiting for Guest Additions and a successful command inside the new guest")
            args = [executable, "guestcontrol", config["id"], "run", "--username", config["guestUser"], "--passwordfile", secret,
                    "--wait-stdout", "--timeout", "10000", "--exe", "C:\\Windows\\System32\\cmd.exe" if windows else "/bin/sh", "--"]
            args.extend(["cmd.exe", "/c", "echo OpenCoreGuestReady"] if windows else ["sh", "-c", "printf OpenCoreGuestReady"])
            result = runner.run(args, timeout=20, check=False)
            if result.returncode == 0 and "OpenCoreGuestReady" in result.stdout:
                verified = True
                break
            time.sleep(2)
        if not verified:
            raise RuntimeError("The new guest did not pass its guest command readiness check within one hour. The managed VM is retained for inspection.")
        return {"executable": executable, "vmId": config["id"], "vmName": config["name"], "guestUser": config["guestUser"],
                "passwordEnv": config["passwordEnv"], "isoPath": config["isoPath"], "isoSha256": file_hash(config["isoPath"]),
                "environmentVerified": True, "guestVerified": True, "guestInstallMs": round((time.monotonic() - started) * 1000)}
    finally:
        if created:
            # Only the UUID created in this operation can be stopped by cleanup.
            runner.run([executable, "controlvm", config["id"], "poweroff"], timeout=30, check=False, allow_cancel=False)
        secret.unlink(missing_ok=True)
