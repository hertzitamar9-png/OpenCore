#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    if let Some(code) = opencore_control_center_lib::desktop_helper::run_if_requested() {
        std::process::exit(code);
    }
    use opencore_control_center_lib::startup_desktop::{self, StartupDisposition};
    match startup_desktop::prepare() {
        Ok(StartupDisposition::StartHere) => opencore_control_center_lib::run(),
        Ok(StartupDisposition::Relaunched) => {}
        Err(error) => {
            startup_desktop::show_error(&error);
            std::process::exit(1);
        }
    }
}
