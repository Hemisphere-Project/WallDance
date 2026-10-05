import re
from PyInstaller.archive.readers import CArchiveReader, ZlibArchiveReader
car = CArchiveReader("/data/WallDance/launcher/release/WallDanceLauncher.exe")
print([n for n in car.toc if re.match(r'python3\d+\.dll', n)])
print([n for n in car.toc if 'dulwich' in n.lower()][:10])
z = ZlibArchiveReader("pyz.bin")
raw = z.extract("dulwich", raw=True)
i = raw.find(b"__version__")
print(raw[max(0,i-200):i+50])
raw = z.extract("git_manager", raw=True)
print(raw[:4000])
