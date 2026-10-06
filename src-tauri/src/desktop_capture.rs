//! Capture one selected window from the Windows compositor, even when another
//! application covers it. The whole-desktop path remains in windows_control.

use base64::Engine;
use std::sync::{Arc, Mutex, OnceLock};
use std::time::{Duration, Instant};
use windows::Win32::Foundation::{HWND, RECT};
use windows::Win32::UI::HiDpi::{
    SetThreadDpiAwarenessContext, DPI_AWARENESS_CONTEXT,
    DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2,
};
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

/// Win32 rectangles and window placement must use the compositor's physical
/// pixels, regardless of the calling worker or main thread's current DPI mode.
/// Restore the caller's context on every exit; never change process DPI mode.
pub(crate) struct PhysicalDpiScope(DPI_AWARENESS_CONTEXT);

impl PhysicalDpiScope {
    pub(crate) fn new() -> Result<Self, String> {
        let previous = unsafe {
            SetThreadDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)
        };
        if previous.0.is_null() {
            return Err("Windows could not establish physical desktop coordinates. No desktop input was sent.".into());
        }
        Ok(Self(previous))
    }
}

impl Drop for PhysicalDpiScope {
    fn drop(&mut self) {
        unsafe { SetThreadDpiAwarenessContext(self.0); }
    }
}

pub(crate) fn physical_window_rect(window_id: isize) -> Result<RECT, String> {
    use windows::Win32::UI::WindowsAndMessaging::GetWindowRect;
    let _dpi = PhysicalDpiScope::new()?;
    let mut rect = RECT::default();
    unsafe { GetWindowRect(HWND(window_id as *mut std::ffi::c_void), &mut rect) }
        .map_err(|error| format!("Cannot read the selected window's physical bounds: {error}"))?;
    if rect.right <= rect.left || rect.bottom <= rect.top {
        return Err("The selected window has no available physical bounds.".into());
    }
    Ok(rect)
}

/// DWM reports the visible frame in physical screen pixels and excludes the
/// invisible resize edges included by GetWindowRect and UI Automation.
pub(crate) fn visible_window_rect(window_id: isize) -> Result<RECT, String> {
    use windows::Win32::Graphics::Dwm::{DwmGetWindowAttribute, DWMWA_EXTENDED_FRAME_BOUNDS};
    let mut rect = RECT::default();
    unsafe {
        DwmGetWindowAttribute(HWND(window_id as *mut std::ffi::c_void),
            DWMWA_EXTENDED_FRAME_BOUNDS, (&mut rect as *mut RECT).cast(),
            std::mem::size_of::<RECT>() as u32)
    }.map_err(|error| format!("Cannot read the selected window's visible frame: {error}"))?;
    if rect.right <= rect.left || rect.bottom <= rect.top {
        return Err("The selected window has no available visible frame.".into());
    }
    Ok(rect)
}

fn origin_from_bounds(outer: RECT, visible: RECT) -> (i64, i64) {
    (i64::from(visible.left) - i64::from(outer.left),
     i64::from(visible.top) - i64::from(outer.top))
}

/// Where the captured image's top-left pixel sits in the window rectangle that click
/// coordinates use. Windows 10 and 11 give normal windows an invisible resize border
/// (7-8 px) that GetWindowRect and UI Automation include but the compositor capture
/// does not, so image coordinates must be shifted by this origin before clicking.
pub(crate) fn frame_origin(window_id: isize) -> Result<(i64, i64), String> {
    Ok(origin_from_bounds(physical_window_rect(window_id)?, visible_window_rect(window_id)?))
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

#[cfg(test)]
mod tests {
    use super::*;
    use windows::Win32::Foundation::RECT;

    #[test]
    fn capture_origin_is_the_physical_crop_offset_without_dpi_scaling() {
        let outer = RECT { left: -1932, top: 132, right: -468, bottom: 984 };
        let visible = RECT { left: -1920, top: 132, right: -480, bottom: 972 };
        assert_eq!(origin_from_bounds(outer, visible), (12, 0));
        let outer = RECT { left: 148, top: 92, right: 1088, bottom: 732 };
        let visible = RECT { left: 158, top: 92, right: 1078, bottom: 722 };
        assert_eq!(origin_from_bounds(outer, visible), (10, 0));
        assert_eq!(origin_from_bounds(visible, visible), (0, 0));
    }
}
