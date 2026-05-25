from actions.file_controller import write_file
from actions.terminal_control import terminal_control

terminal_control({"command": "cd ~/Desktop/yeni_proje/counter"})
print(write_file(path="src/App.jsx", content="// test update"))
