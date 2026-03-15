import pythoncom  # type: ignore
import win32com.client  # type: ignore

try:
    pythoncom.CoInitialize()
    shell = win32com.client.Dispatch("Shell.Application")
    names = []
    for window in shell.Windows():
        names.append(window.Name)
    print("Open windows in Shell.Application:", names)
except Exception as e:
    print(e)
finally:
    pythoncom.CoUninitialize()
