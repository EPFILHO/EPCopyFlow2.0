# EPCopyFlow 2.0
# core/win_process.py
# Helpers Windows-específicos via ctypes.
#
# disable_power_throttling: opt-out do EcoQoS / Power Throttling do Windows
# para um processo. HIGH_PRIORITY_CLASS e EcoQoS são ortogonais — Windows
# pode marcar processo em background como "Eco" mesmo com prioridade alta,
# reduzindo CPU/IO. Isso afeta MT5 ao perder foco.
# Doc: https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/ns-processthreadsapi-process_power_throttling_state

import sys
import logging

logger = logging.getLogger(__name__)

# Windows constants
_PROCESS_SET_INFORMATION = 0x0200
_ProcessPowerThrottling = 4  # PROCESS_INFORMATION_CLASS::ProcessPowerThrottling
_PROCESS_POWER_THROTTLING_CURRENT_VERSION = 1
_PROCESS_POWER_THROTTLING_EXECUTION_SPEED = 0x1


def disable_power_throttling(pid: int) -> bool:
    """Desliga EcoQoS / Power Throttling para o processo `pid`.

    Retorna True em sucesso, False em qualquer falha (ambiente não-Windows,
    API indisponível em versão antiga do Windows, processo já morto, sem
    permissão, etc.). Nunca lança — falha silenciosa com log de warning.
    """
    if not sys.platform.startswith("win"):
        return False

    try:
        import ctypes
        from ctypes import wintypes

        class _PROCESS_POWER_THROTTLING_STATE(ctypes.Structure):
            _fields_ = [
                ("Version", wintypes.ULONG),
                ("ControlMask", wintypes.ULONG),
                ("StateMask", wintypes.ULONG),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        kernel32.SetProcessInformation.argtypes = [
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD
        ]
        kernel32.SetProcessInformation.restype = wintypes.BOOL

        handle = kernel32.OpenProcess(_PROCESS_SET_INFORMATION, False, pid)
        if not handle:
            err = ctypes.get_last_error()
            logger.warning(f"OpenProcess falhou para pid={pid} (err={err}).")
            return False

        try:
            state = _PROCESS_POWER_THROTTLING_STATE()
            state.Version = _PROCESS_POWER_THROTTLING_CURRENT_VERSION
            # ControlMask = bit que queremos controlar (execution speed throttling).
            # StateMask = 0 → desligado (sempre rodar em performance state).
            state.ControlMask = _PROCESS_POWER_THROTTLING_EXECUTION_SPEED
            state.StateMask = 0

            ok = kernel32.SetProcessInformation(
                handle,
                _ProcessPowerThrottling,
                ctypes.byref(state),
                ctypes.sizeof(state),
            )
            if not ok:
                err = ctypes.get_last_error()
                logger.warning(f"SetProcessInformation falhou para pid={pid} (err={err}).")
                return False
            return True
        finally:
            kernel32.CloseHandle(handle)
    except Exception as e:
        logger.warning(f"disable_power_throttling exceção pid={pid}: {e}")
        return False
