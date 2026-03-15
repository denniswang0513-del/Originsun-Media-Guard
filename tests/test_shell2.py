import pythoncom  # type: ignore
import win32com.client  # type: ignore

try:
    pythoncom.CoInitialize()
    shell = win32com.client.Dispatch("Shell.Application")
    for i, window in enumerate(shell.Windows()):
        try:
            name = window.Name
            sel = window.Document.SelectedItems()
            print(f"Window {i} ({name}): {sel.Count} selected items")
            for j in range(sel.Count):
                print("  ->", sel.Item(j).Path)
        except Exception as e:
            print(f"Window {i} ({getattr(window, 'Name', 'Unknown')}) has no SelectedItems: {e}")
except Exception as e:
    print(e)
finally:
    pythoncom.CoUninitialize()
