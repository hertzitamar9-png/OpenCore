; The per-user updater must reopen the app even if the shell COM launcher
; cannot start it. All files and registry entries are complete at this hook.
; OpenCore's entry point moves itself to the desktop shell context before
; opening app data. Its single-instance handler also absorbs a later launch
; from Tauri's normal .onInstSuccess handler.
!macro NSIS_HOOK_POSTINSTALL
  !if "${INSTALLMODE}" == "currentUser"
    ${If} ${Silent}
    ${OrIf} $PassiveMode = 1
      Push $R0
      Push $R1
      ClearErrors
      ${GetOptions} $CMDLINE "/R" $R0
      ${IfNot} ${Errors}
        StrCpy $R1 ""
        ClearErrors
        ${GetOptions} $CMDLINE "/ARGS" $R1
        ${If} ${Errors}
          StrCpy $R1 ""
        ${EndIf}
        ClearErrors
        Exec '$"$INSTDIR\${MAINBINARYNAME}.exe$" $R1'
        ${If} ${Errors}
          DetailPrint "OpenCore was updated. Open it from your shortcut if it does not reopen."
        ${Else}
          DetailPrint "OpenCore relaunch requested."
        ${EndIf}
      ${EndIf}
      Pop $R1
      Pop $R0
      ClearErrors
    ${EndIf}
  !endif
!macroend
