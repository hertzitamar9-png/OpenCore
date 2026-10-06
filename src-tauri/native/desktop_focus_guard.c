/* A bounded activation veto on one selected application's GUI thread.
 * No app startup, input hooks, keyboard/text capture, remote memory or timers.
 * The installing parent owns the hook and the two expiring window properties.
 */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#if !defined(_WIN64)
#error The desktop activation guard supports x64 targets only.
#endif

#define OC_LEASE_PROPERTY L"OpenCore.ManualActivationLease.v1"
#define OC_ACK_PROPERTY L"OpenCore.ManualActivationAck.v1"
#define OC_MAX_LEASE_MS 2500ULL

typedef struct OC_LEASE {
    HWND window;
    ULONGLONG deadline;
    ULONG_PTR token;
} OC_LEASE;

static BOOL CALLBACK find_live_lease(HWND window, LPARAM data) {
    OC_LEASE *lease = (OC_LEASE *)data;
    ULONG_PTR token = (ULONG_PTR)GetPropW(window, OC_LEASE_PROPERTY);
    /* Upper 48 bits contain the monotonic expiry, lower 16 bits distinguish
     * actions whose preflight starts in the same clock tick.
     */
    ULONGLONG deadline = (ULONGLONG)token >> 16;
    ULONGLONG now = GetTickCount64();
    if (deadline > now && deadline - now <= OC_MAX_LEASE_MS) {
        lease->window = window;
        lease->deadline = deadline;
        lease->token = token;
        return FALSE;
    }
    return TRUE;
}

__declspec(dllexport) UINT WINAPI OpenCoreDesktopHookAbi(void) {
    return 1U;
}

__declspec(dllexport) LRESULT CALLBACK OpenCoreDesktopCbtProc(int code, WPARAM wparam, LPARAM lparam) {
    if (code == HCBT_ACTIVATE || code == HCBT_QS) {
        OC_LEASE lease = { NULL, 0ULL, 0 };
        /* The hook is installed only on the exact selected GUI thread. Do not
         * inspect keyboard, mouse, focus, window creation or other hook data.
         */
        EnumThreadWindows(GetCurrentThreadId(), find_live_lease, (LPARAM)&lease);
        if (lease.window != NULL) {
            if (code == HCBT_QS) {
                /* A queued WM_QUEUESYNC proves this DLL is running in the
                 * target before the parent permits any actual mutation.
                 */
                if ((ULONG_PTR)GetPropW(lease.window, OC_LEASE_PROPERTY) == lease.token) {
                    SetPropW(lease.window, OC_ACK_PROPERTY, (HANDLE)lease.token);
                }
            } else {
                const CBTACTIVATESTRUCT *activation = (const CBTACTIVATESTRUCT *)lparam;
                ULONGLONG now = GetTickCount64();
                /* An ordinary user click can still activate the target. The
                 * parent observes the change and never restores user focus.
                 * Recheck expiry and identity at the veto, since a previous
                 * callback may still finish after its parent has unhooked.
                 */
                if (lease.deadline > now && lease.deadline - now <= OC_MAX_LEASE_MS
                    && (ULONG_PTR)GetPropW(lease.window, OC_LEASE_PROPERTY) == lease.token
                    && (activation == NULL || !activation->fMouse)) {
                    return 1;
                }
            }
        }
    }
    return CallNextHookEx(NULL, code, wparam, lparam);
}
