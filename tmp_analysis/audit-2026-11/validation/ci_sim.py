import sys
sys.dont_write_bytecode = True
for n in ("torch", "torchvision", "ultralytics", "dearpygui", "tensorrt", "kornia", "pycuda", "ids_peak", "ids_peak_ipl"):
    sys.modules[n] = None
import pytest
sys.exit(pytest.main(sys.argv[1:]))
