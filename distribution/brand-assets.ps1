param([Parameter(Mandatory=$true)][string]$Logo, [Parameter(Mandatory=$true)][string]$Output)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
[IO.Directory]::CreateDirectory($Output) | Out-Null
$brandImage = [Drawing.Image]::FromFile((Resolve-Path -LiteralPath $Logo).Path)
try {
    foreach ($brandSpec in @(@{Name='header.bmp';Width=150;Height=57;LogoSize=48;X=98;Y=4}, @{Name='welcome.bmp';Width=164;Height=314;LogoSize=112;X=26;Y=52})) {
        $brandBitmap = [Drawing.Bitmap]::new($brandSpec.Width,$brandSpec.Height,[Drawing.Imaging.PixelFormat]::Format24bppRgb)
        $brandGraphics = [Drawing.Graphics]::FromImage($brandBitmap)
        try {
            $brandGraphics.Clear([Drawing.Color]::White)
            $brandGraphics.InterpolationMode = [Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
            $brandGraphics.DrawImage($brandImage,$brandSpec.X,$brandSpec.Y,$brandSpec.LogoSize,$brandSpec.LogoSize)
            if ($brandSpec.Name -eq 'welcome.bmp') {
                $brandFont = [Drawing.Font]::new('Segoe UI',15,[Drawing.FontStyle]::Bold)
                $brandBrush = [Drawing.SolidBrush]::new([Drawing.Color]::FromArgb(23,35,55))
                try { $brandGraphics.DrawString('OpenCore',$brandFont,$brandBrush,[single]26,[single]181) }
                finally { $brandFont.Dispose(); $brandBrush.Dispose() }
            }
            $brandBitmap.Save((Join-Path $Output $brandSpec.Name),[Drawing.Imaging.ImageFormat]::Bmp)
        } finally { $brandGraphics.Dispose(); $brandBitmap.Dispose() }
    }
} finally { $brandImage.Dispose() }
