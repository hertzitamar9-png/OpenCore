// OpenCore's stdio entry point for the Apache-licensed upstream app-server.
// The agent loop, shell sandbox, patching, MCP, and session format come from
// the pinned source. This executable is built by OpenCore.
#![recursion_limit = "256"]

use codex_app_server::{
    AppServerExit, AppServerRuntimeOptions, AppServerTransport, PluginStartupTasks,
    RemoteControlStartupMode, run_main_with_transport_options,
};
use codex_arg0::arg0_dispatch_or_else;
use codex_config::LoaderOverrides;
use codex_protocol::protocol::SessionSource;
use codex_utils_cli::CliConfigOverrides;
use std::path::Path;

fn main() -> anyhow::Result<()> {
    let args: Vec<_> = std::env::args_os().skip(1).collect();
    if matches!(args.as_slice(), [version] if version == "--version" || version == "-V") {
        println!("OpenCore Agent {}", env!("CARGO_PKG_VERSION"));
        return Ok(());
    }

    // The desktop compatibility host passes the upstream app-server command.
    let stdio = matches!(args.as_slice(), [command, flag, address]
        if command == "app-server" && flag == "--listen" && address == "stdio://");
    if stdio {
        anyhow::ensure!(
            std::env::var_os("CODEX_HOME").is_some_and(|home| Path::new(&home).is_absolute()),
            "OpenCore must supply a private agent session directory"
        );
    } else {
        // The same executable is the upstream apply-patch and sandbox helper.
        // Let arg0 dispatch consume those invocations before running the server.
        anyhow::ensure!(
            args.first().is_some_and(|flag| {
                flag == "--codex-run-as-apply-patch"
                    || flag == "--__codex-windows-mxc"
                    || flag == "--codex-run-as-fs-helper"
                    || flag == "--run-as-windows-sandbox"
            }),
            "Start OpenCore Agent through the OpenCore desktop app"
        );
    }

    arg0_dispatch_or_else(move |paths| async move {
        anyhow::ensure!(stdio, "Unrecognized OpenCore agent helper invocation");
        let overrides = CliConfigOverrides {
            raw_overrides: [
                "analytics.enabled=false",
                "feedback.enabled=false",
                "check_for_update_on_startup=false",
                "cli_auth_credentials_store=\"file\"",
                "chatgpt_base_url=\"http://127.0.0.1:1/\"",
            ]
            .into_iter()
            .map(str::to_string)
            .collect(),
        };
        let exit = run_main_with_transport_options(
            paths,
            overrides,
            LoaderOverrides::default(),
            /*strict_config*/ false,
            /*default_analytics_enabled*/ false,
            AppServerTransport::Stdio,
            SessionSource::Custom("opencore".to_string()),
            Default::default(),
            AppServerRuntimeOptions {
                plugin_startup_tasks: PluginStartupTasks::Skip,
                remote_control_startup_mode: RemoteControlStartupMode::DisabledEphemeral,
                ..Default::default()
            },
        )
        .await?;
        if exit == AppServerExit::Forced {
            std::process::exit(0);
        }
        Ok(())
    })
}
