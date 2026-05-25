import sys
from PyQt6.QtWidgets import QApplication
from ui import ProjectMonitorWindow

app = QApplication(sys.argv)
pm = ProjectMonitorWindow()
pm.show()
print("Success")
