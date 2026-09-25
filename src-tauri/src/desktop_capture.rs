//! Capture one selected window from the Windows compositor, even when another
//! application covers it. The whole-desktop path remains in windows_control.

use base64::Engine;
use std::sync::{Arc, Mutex, OnceLock};
use std::time::{Duration, Instant};
use windows_capture::capture::{CaptureControl, Context, GraphicsCaptureApiHandler};
use windows_capture::frame::Frame;
use windows_capture::graphics_capture_api::InternalCaptureControl;
use windows_capture::settings::{
    ColorFormat, CursorCaptureSettings, DirtyRegionSettings, DrawBorderSettings,
    MinimumUpdateIntervalSettings, SecondaryWindowSettings, Settings,
};
use windows_capture::window::Window;

#[derive(Clone)]
pub(crate) struct CapturedFrame {
    pub data_url: String,
    pub width: u32,
    pub height: u32,
}

type CaptureError = Box<dyn std::error::Error + Send + Sync>;

struct FrameHandler {
    latest: Arc<Mutex<Option<CapturedFrame>>>,
    last_encode: Instant,
}

impl GraphicsCaptureApiHandler for FrameHandler {
    type Flags = Arc<Mutex<Option<CapturedFrame>>>;
    type Error = CaptureError;

    fn new(context: Context<Self::Flags>) -> Result<Self, Self::Error> {
        Ok(Self { latest: context.flags, last_encode: Instant::now() - Duration::from_secs(1) })
    }

    fn on_frame_arrived(&mut self, frame: &mut Frame, _control: InternalCaptureControl) -> Result<(), Self::Error> {
        if self.last_encode.elapsed() < Duration::from_millis(120) { return Ok(()); }
        self.last_encode = Instant::now();
        let buffer = frame.buffer()?;
        let (width, height) = (buffer.width(), buffer.height());
        if width == 0 || height == 0 || width as u64 * height as u64 > 24_000_000 { return Ok(()); }
        let mut without_padding = Vec::new();
        let pixels = buffer.as_nopadding_buffer(&mut without_padding);
        let mut encoded = Vec::new();
        {
            let mut encoder = png::Encoder::new(&mut encoded, width, height);
            encoder.set_color(png::ColorType::Rgba);
            encoder.set_depth(png::BitDepth::Eight);
            encoder.write_header()?.write_image_data(pixels)?;
        }
        if encoded.len() > 12 * 1024 * 1024 { return Ok(()); }
        let data_url = format!("data:image/png;base64,{}", base64::engine::general_purpose::STANDARD.encode(encoded));
        if let Ok(mut latest) = self.latest.lock() { *latest = Some(CapturedFrame { data_url, width, height }); }
        Ok(())
    }
}

struct Session {
    window_id: isize,
    control: CaptureControl<FrameHandler, CaptureError>,
    latest: Arc<Mutex<Option<CapturedFrame>>>,
}

static SESSION: OnceLock<Mutex<Option<Session>>> = OnceLock::new();

/// Where the captured image's top-left pixel sits in the window rectangle that click
/// coordinates use. Windows 10 and 11 give normal windows an invisible resize border
/// (7-8 px) that GetWindowRect and UI Automation include but the compositor capture
/// does not, so image coordinates must be shifted by this origin before clicking.
pub(crate) fn frame_origin(window_id: isize) -> (i64, i64) {
    use windows::Win32::Foundation::{HWND, RECT};
    use windows::Win32::Graphics::Dwm::{DwmGetWindowAttribute, DWMWA_EXTENDED_FRAME_BOUNDS};
    use windows::Win32::UI::WindowsAndMessaging::GetWindowRect;
    let hwnd = HWND(window_id as *mut std::ffi::c_void);
    let (mut outer, mut visible) = (RECT::default(), RECT::default());
    let found = unsafe {
        GetWindowRect(hwnd, &mut outer).is_ok()
            && DwmGetWindowAttribute(hwnd, DWMWA_EXTENDED_FRAME_BOUNDS, (&mut visible as *mut RECT).cast(),
                                     std::mem::size_of::<RECT>() as u32).is_ok()
    };
    if found { (i64::from(visible.left - outer.left), i64::from(visible.top - outer.top)) } else { (0, 0) }
}

pub(crate) fn stop_capture() {
    let old = SESSION.get().and_then(|session| session.lock().ok().and_then(|mut session| session.take()));
    if let Some(old) = old { let _ = old.control.stop(); }
}

pub(crate) fn frame_for_window(window_id: isize) -> Result<CapturedFrame, String> {
    let result = capture_frame(window_id);
    // A screenshot is one operation, not a permanent capture session.
    stop_capture();
    result
}

fn capture_frame(window_id: isize) -> Result<CapturedFrame, String> {
    let latest = {
        let mut session = SESSION.get_or_init(|| Mutex::new(None)).lock().map_err(|error| error.to_string())?;
        if session.as_ref().is_some_and(|current| current.window_id != window_id || current.control.is_finished()) {
            if let Some(previous) = session.take() { let _ = previous.control.stop(); }
        }
        if session.is_none() {
            let latest = Arc::new(Mutex::new(None));
            let window = Window::from_raw_hwnd(window_id as *mut std::ffi::c_void);
            let settings = Settings::new(
                window,
                CursorCaptureSettings::WithoutCursor,
                DrawBorderSettings::WithoutBorder,
                SecondaryWindowSettings::Include,
                MinimumUpdateIntervalSettings::Custom(Duration::from_millis(100)),
                DirtyRegionSettings::Default,
                ColorFormat::Rgba8,
                latest.clone(),
            );
            let control = FrameHandler::start_free_threaded(settings).map_err(|error| error.to_string())?;
            *session = Some(Session { window_id, control, latest: latest.clone() });
        }
        session.as_ref().unwrap().latest.clone()
    };

    for _ in 0..24 {
        if let Some(frame) = latest.lock().map_err(|error| error.to_string())?.clone() { return Ok(frame); }
        std::thread::sleep(Duration::from_millis(50));
    }
    Err("The selected window has not produced a frame yet. Keep it open and try Refresh.".into())
}
