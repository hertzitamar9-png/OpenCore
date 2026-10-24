using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Net;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;

[assembly: AssemblyTitle("OpenCore Setup")]
[assembly: AssemblyProduct("OpenCore")]
[assembly: AssemblyCompany("OpenCore")]
[assembly: AssemblyFileVersion("@@VERSION@@.0")]

internal static class Setup
{
    internal const string Repository = "hertzitamar9-png/OpenCore";
    internal const string PublicKey = "@@PUBLIC_KEY@@";
    internal const string VerifierHash = "@@VERIFIER_HASH@@";
    internal static readonly string CacheRoot = Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "OpenCoreSetup", "packages");

    [STAThread]
    private static int Main(string[] args)
    {
        ServicePointManager.SecurityProtocol = SecurityProtocolType.Tls12;
        if (!Environment.Is64BitOperatingSystem) {
            MessageBox.Show("OpenCore requires 64-bit Windows.", "OpenCore Setup");
            return 2;
        }
        if (args.Length == 4 && args[0] == "--verify-only") {
            try { Verify(args[1], args[2], args[3], null); return 0; }
            catch (Exception error) { Console.Error.WriteLine(error.Message); return 1; }
        }
        string report = null;
        if (args.Length == 2 && args[0] == "--download-only") report = Path.GetFullPath(args[1]);
        else if (args.Length != 0) return 2;
        bool first;
        using (Mutex instance = new Mutex(true, @"Local\OpenCoreSetup.Downloader", out first)) {
            if (!first) {
                MessageBox.Show("OpenCore Setup is already open. Continue in its existing window.", "OpenCore Setup");
                return 3;
            }
            try {
                Application.EnableVisualStyles();
                Application.SetCompatibleTextRenderingDefault(false);
                using (SetupWindow window = new SetupWindow(report)) Application.Run(window);
                return Environment.ExitCode;
            }
            finally { instance.ReleaseMutex(); }
        }
    }

    internal static string Quote(string value)
    {
        if (value.IndexOf('"') >= 0 || value.IndexOf('\0') >= 0) throw new InvalidDataException("Invalid file path.");
        return "\"" + value + "\"";
    }

    internal static string Hash(string path, Action check)
    {
        using (SHA256 sha = SHA256.Create())
        using (FileStream source = File.OpenRead(path)) {
            byte[] buffer = new byte[1024 * 1024];
            int count;
            while ((count = source.Read(buffer, 0, buffer.Length)) != 0) {
                if (check != null) check();
                sha.TransformBlock(buffer, 0, count, buffer, 0);
            }
            sha.TransformFinalBlock(new byte[0], 0, 0);
            return BitConverter.ToString(sha.Hash).Replace("-", "").ToLowerInvariant();
        }
    }

    internal static void Verify(string package, string signature, string expectedHash, Action check)
    {
        if (!Regex.IsMatch(expectedHash, "^[a-fA-F0-9]{64}$") ||
            !String.Equals(Hash(package, check), expectedHash, StringComparison.OrdinalIgnoreCase))
            throw new InvalidDataException("The download checksum did not match. OpenCore Setup did not run the package.");
        string parent = Path.GetFullPath(Path.Combine(Path.GetTempPath(), "OpenCoreSetup"));
        string temporary = Path.GetFullPath(Path.Combine(parent, Guid.NewGuid().ToString("N")));
        if (!temporary.StartsWith(parent + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase))
            throw new InvalidDataException("Invalid verification folder.");
        Directory.CreateDirectory(temporary);
        try {
            string verifier = Path.Combine(temporary, "minisign.exe");
            using (Stream resource = Assembly.GetExecutingAssembly().GetManifestResourceStream("OpenCore.Minisign.exe"))
            using (FileStream destination = File.Create(verifier)) resource.CopyTo(destination);
            if (Hash(verifier, check) != VerifierHash) throw new InvalidDataException("The bundled signature verifier changed.");
            string text = File.ReadAllText(signature).Trim();
            if (!text.StartsWith("untrusted comment:", StringComparison.Ordinal))
                text = Encoding.UTF8.GetString(Convert.FromBase64String(text));
            string decoded = Path.Combine(temporary, "package.minisig");
            File.WriteAllText(decoded, text, new UTF8Encoding(false));
            ProcessStartInfo start = new ProcessStartInfo(verifier,
                "-V -Q -m " + Quote(Path.GetFullPath(package)) + " -x " + Quote(decoded) + " -P " + Quote(PublicKey));
            start.UseShellExecute = false; start.CreateNoWindow = true;
            start.RedirectStandardOutput = true; start.RedirectStandardError = true;
            using (Process child = Process.Start(start)) {
                Task<string> output = child.StandardOutput.ReadToEndAsync();
                Task<string> error = child.StandardError.ReadToEndAsync();
                while (!child.WaitForExit(100)) {
                    try { if (check != null) check(); }
                    catch { child.Kill(); child.WaitForExit(); throw; }
                }
                if (child.ExitCode != 0)
                    throw new InvalidDataException("OpenCore's update signature was not valid. The package was not started.");
                Task.WaitAll(output, error);
            }
        }
        finally { Directory.Delete(temporary, true); }
    }
}

internal sealed class SetupWindow : Form
{
    private readonly Label stage = new Label();
    private readonly Label detail = new Label();
    private readonly ProgressBar progress = new ProgressBar();
    private readonly Button action = new Button();
    private readonly string report;
    private volatile bool cancelled;
    private volatile bool running;
    private HttpWebRequest activeRequest;
    private readonly JavaScriptSerializer json = new JavaScriptSerializer();

    internal SetupWindow(string reportPath)
    {
        report = reportPath; Text = "OpenCore Setup";
        Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath);
        ClientSize = new Size(580, 290); MinimumSize = new Size(590, 320);
        FormBorderStyle = FormBorderStyle.FixedDialog; MaximizeBox = false;
        StartPosition = FormStartPosition.CenterScreen; Font = new Font("Segoe UI", 10);
        BackColor = Color.FromArgb(13, 17, 23); ForeColor = Color.FromArgb(214, 223, 234);
        PictureBox logo = new PictureBox();
        logo.SetBounds(26, 24, 64, 64); logo.SizeMode = PictureBoxSizeMode.Zoom;
        using (Stream stream = Assembly.GetExecutingAssembly().GetManifestResourceStream("OpenCore.Logo.png"))
        using (Image image = Image.FromStream(stream)) logo.Image = new Bitmap(image);
        Label title = new Label(); title.Text = "OpenCore Setup"; title.Font = new Font("Segoe UI", 20, FontStyle.Bold);
        title.SetBounds(106, 25, 440, 38);
        Label subtitle = new Label(); subtitle.Text = "Downloads OpenCore and its core dependencies";
        subtitle.SetBounds(108, 67, 440, 28); subtitle.ForeColor = Color.FromArgb(174, 185, 199);
        stage.SetBounds(28, 114, 524, 29); stage.Text = "Checking the latest OpenCore release…";
        stage.AccessibleName = "Setup status";
        progress.SetBounds(28, 153, 524, 18); progress.Maximum = 1000;
        progress.AccessibleName = "OpenCore download progress";
        detail.SetBounds(28, 180, 524, 45); detail.ForeColor = subtitle.ForeColor;
        action.SetBounds(387, 235, 165, 34); action.Text = "Pause download";
        action.FlatStyle = FlatStyle.Flat; action.BackColor = Color.FromArgb(38, 91, 157);
        action.ForeColor = Color.White;
        action.Click += delegate {
            if (running) { cancelled = true; if (activeRequest != null) activeRequest.Abort(); action.Enabled = false; }
            else BeginDownload();
        };
        LinkLabel notices = new LinkLabel(); notices.Text = "Third-party licenses";
        notices.SetBounds(28, 244, 240, 25); notices.LinkColor = subtitle.ForeColor;
        notices.LinkClicked += delegate {
            using (Stream resource = Assembly.GetExecutingAssembly().GetManifestResourceStream("OpenCore.Notices.txt"))
            using (StreamReader reader = new StreamReader(resource))
                MessageBox.Show(this, reader.ReadToEnd(), "OpenCore licenses");
        };
        Controls.AddRange(new Control[] {logo, title, subtitle, stage, progress, detail, action, notices});
        Shown += delegate { BeginDownload(); };
        FormClosing += delegate { cancelled = true; if (activeRequest != null) activeRequest.Abort(); };
    }

    private void CheckCancelled() { if (cancelled) throw new OperationCanceledException(); }

    private void Update(string text, string more, int value, bool marquee)
    {
        if (IsDisposed || Disposing) return;
        BeginInvoke((Action)delegate {
            stage.Text = text; detail.Text = more;
            progress.Style = marquee ? ProgressBarStyle.Marquee : ProgressBarStyle.Continuous;
            if (!marquee) progress.Value = Math.Max(0, Math.Min(1000, value));
        });
    }

    private HttpWebRequest Request(string url)
    {
        HttpWebRequest request = (HttpWebRequest)WebRequest.Create(url);
        request.UserAgent = "OpenCore-Setup"; request.Accept = "application/vnd.github+json";
        request.Timeout = 30000; request.ReadWriteTimeout = 30000;
        request.AllowAutoRedirect = true; activeRequest = request; CheckCancelled();
        return request;
    }

    private string ReadText(string url, int limit)
    {
        using (WebResponse response = Request(url).GetResponse())
        using (Stream source = response.GetResponseStream())
        using (MemoryStream result = new MemoryStream()) {
            byte[] buffer = new byte[8192]; int count;
            while ((count = source.Read(buffer, 0, buffer.Length)) > 0) {
                CheckCancelled();
                if (result.Length + count > limit) throw new InvalidDataException("Release metadata was too large.");
                result.Write(buffer, 0, count);
            }
            return Encoding.UTF8.GetString(result.ToArray());
        }
    }

    private static Dictionary<string, object> Asset(Dictionary<string, object> release, string name)
    {
        foreach (object value in (System.Collections.IEnumerable)release["assets"]) {
            Dictionary<string, object> asset = (Dictionary<string, object>)value;
            if ((string)asset["name"] == name && (string)asset["state"] == "uploaded") return asset;
        }
        throw new InvalidDataException("The verified Windows package is not available in this release. Try again shortly.");
    }

    private static string AssetUrl(Dictionary<string, object> asset, string tag)
    {
        string expected = "https://github.com/" + Setup.Repository + "/releases/download/" + tag + "/" +
            Uri.EscapeDataString((string)asset["name"]);
        if ((string)asset["browser_download_url"] != expected) throw new InvalidDataException("Unexpected release download location.");
        return expected;
    }

    private void Download(string url, string path, long expectedBytes)
    {
        long offset = File.Exists(path) ? new FileInfo(path).Length : 0;
        if (offset > expectedBytes) { File.Delete(path); offset = 0; }
        if (offset == expectedBytes) return;
        HttpWebRequest request = Request(url);
        if (offset > 0) request.AddRange(offset);
        using (HttpWebResponse response = (HttpWebResponse)request.GetResponse()) {
            if (response.StatusCode != HttpStatusCode.PartialContent) offset = 0;
            else if (!response.Headers["Content-Range"].StartsWith("bytes " + offset + "-", StringComparison.Ordinal))
                throw new InvalidDataException("The server returned an unexpected download range.");
            using (Stream source = response.GetResponseStream())
            using (FileStream destination = new FileStream(path, offset == 0 ? FileMode.Create : FileMode.Append, FileAccess.Write, FileShare.Read)) {
                long received = offset; Stopwatch speed = Stopwatch.StartNew(), paint = Stopwatch.StartNew();
                byte[] buffer = new byte[256 * 1024]; int count;
                while ((count = source.Read(buffer, 0, buffer.Length)) > 0) {
                    CheckCancelled(); received += count;
                    if (received > expectedBytes) throw new InvalidDataException("The package exceeded its declared size.");
                    destination.Write(buffer, 0, count);
                    if (paint.ElapsedMilliseconds > 100) {
                        Update("Downloading OpenCore…",
                            String.Format("{0:F1} / {1:F1} MB  ·  {2:F1} MB/s", received / 1e6, expectedBytes / 1e6,
                                (received - offset) / 1e6 / Math.Max(0.1, speed.Elapsed.TotalSeconds)),
                            (int)(received * 1000 / expectedBytes), false);
                        paint.Restart();
                    }
                }
                if (received != expectedBytes) throw new IOException("The download ended early. Resume to continue.");
            }
        }
    }

    private async void BeginDownload()
    {
        if (running) return;
        running = true; cancelled = false; action.Text = "Pause download"; action.Enabled = true;
        try {
            await Task.Run((Action)delegate {
                Update("Checking the latest OpenCore release…", "Connecting to OpenCore's official GitHub repository", 0, true);
                Dictionary<string, object> release = json.Deserialize<Dictionary<string, object>>(
                    ReadText("https://api.github.com/repos/" + Setup.Repository + "/releases/latest", 2 * 1024 * 1024));
                if ((bool)release["draft"] || (bool)release["prerelease"]) throw new InvalidDataException("The release is not published.");
                string tag = (string)release["tag_name"];
                if (!Regex.IsMatch(tag, @"^app-v\d+\.\d+\.\d+$")) throw new InvalidDataException("Invalid OpenCore release identity.");
                Dictionary<string, object> package = Asset(release, "OpenCore.Package.exe");
                Dictionary<string, object> signature = Asset(release, "OpenCore.Package.exe.sig");
                Match digest = Regex.Match(package["digest"] as string ?? "", "^sha256:([a-f0-9]{64})$");
                if (!digest.Success) throw new InvalidDataException("GitHub did not provide a verified package checksum.");
                string expectedHash = digest.Groups[1].Value;
                long bytes = Convert.ToInt64(package["size"]);
                if (bytes < 2000000 || bytes > 8L * 1024 * 1024 * 1024) throw new InvalidDataException("Invalid OpenCore package size.");
                string folder = Path.Combine(Setup.CacheRoot, tag); Directory.CreateDirectory(folder);
                string target = Path.Combine(folder, "OpenCore.Package.exe");
                string signed = Path.Combine(folder, "OpenCore.Package.exe.sig");
                File.WriteAllText(signed, ReadText(AssetUrl(signature, tag), 65536), new UTF8Encoding(false));
                if (!File.Exists(target) || new FileInfo(target).Length != bytes || Setup.Hash(target, CheckCancelled) != expectedHash) {
                    string partial = target + ".partial";
                    Download(AssetUrl(package, tag), partial, bytes);
                    if (Setup.Hash(partial, CheckCancelled) != expectedHash) {
                        File.Delete(partial);
                        throw new InvalidDataException("The download checksum failed. Choose Retry to download a clean copy.");
                    }
                    if (File.Exists(target)) File.Delete(target);
                    File.Move(partial, target);
                }
                Update("Verifying OpenCore…", "Checking the package checksum and OpenCore's update signature", 1000, true);
                Setup.Verify(target, signed, expectedHash, CheckCancelled); CheckCancelled();
                if (report != null) {
                    File.WriteAllText(report, json.Serialize(new {passed = true, version = tag.Substring(5),
                        package = target, bytes = bytes, sha256 = expectedHash, signatureVerified = true, installerStarted = false}));
                } else {
                    Update("Starting OpenCore Setup…", "The verified package is ready to install.", 1000, false);
                    ProcessStartInfo start = new ProcessStartInfo(target); start.UseShellExecute = true;
                    using (Process child = Process.Start(start)) { }
                }
            });
            Environment.ExitCode = 0; running = false; Close();
        }
        catch (Exception error) {
            running = false; Environment.ExitCode = 1;
            Update(cancelled ? "Download paused" : "Setup could not finish",
                cancelled ? "Open OpenCore Setup again or choose Resume to continue this download." : error.Message, progress.Value, false);
            action.Text = cancelled ? "Resume download" : "Retry"; action.Enabled = true;
        }
    }
}
