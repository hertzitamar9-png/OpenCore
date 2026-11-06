using System;
using System.Drawing;
using System.Windows.Forms;

// A disposable target for the real upstream driver, never a user's document.
public sealed class CuaDriverWindow : Form {
    int count;
    public CuaDriverWindow() {
        Text = "OpenCore Cua Driver verification";
        ClientSize = new Size(480, 190);
        ShowInTaskbar = true;
        var label = new Label {Text = "Count: 0", AccessibleName = "Count: 0", Left = 24, Top = 24, Width = 300, Height = 30};
        var button = new Button {Text = "Increment counter", AccessibleName = "Increment counter", Left = 24, Top = 70, Width = 200, Height = 40};
        button.Click += delegate { count++; label.Text = "Count: " + count; label.AccessibleName = label.Text; };
        var finish = new Button {Text = "Finish verification", Left = 242, Top = 70, Width = 200, Height = 40};
        finish.Click += delegate { Close(); };
        Controls.Add(label); Controls.Add(button); Controls.Add(finish);
        var timeout = new Timer {Interval = 300000};
        timeout.Tick += delegate { Close(); };
        timeout.Start();
    }
    protected override bool ShowWithoutActivation { get { return true; } }
    [STAThread]
    public static void Main() {
        Application.EnableVisualStyles();
        Application.Run(new CuaDriverWindow());
    }
}
