# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec file for GoPro Backend
Bundles FastAPI backend with all dependencies into a standalone executable
"""

block_cipher = None

# All Python modules that need to be included
hiddenimports = [
    # FastAPI and Uvicorn
    'uvicorn',
    'uvicorn.logging',
    'uvicorn.loops',
    'uvicorn.loops.auto',
    'uvicorn.protocols',
    'uvicorn.protocols.http',
    'uvicorn.protocols.http.auto',
    'uvicorn.protocols.websockets',
    'uvicorn.protocols.websockets.auto',
    'uvicorn.lifespan',
    'uvicorn.lifespan.on',

    # FastAPI dependencies
    'fastapi',
    'starlette',
    'starlette.routing',
    'starlette.middleware',
    'starlette.middleware.cors',
    'pydantic',
    'pydantic_core',

    # HTTP clients
    'httpx',
    'httpx._transports',
    'httpx._transports.default',
    'requests',

    # GoPro SDK
    'open_gopro',
    'open_gopro.gopro_wireless',
    'open_gopro.gopro_wired',
    'open_gopro.ble',
    'open_gopro.wifi',
    'open_gopro.constants',
    'open_gopro.responses',

    # Bluetooth (bleak)
    'bleak',
    'bleak.backends',
    'bleak.backends.corebluetooth',

    # WebSockets
    'websockets',
    'websockets.legacy',
    'websockets.legacy.server',

    # Other dependencies
    'pathlib',
    'asyncio',
    'logging',
    'json',
    'datetime',
    'zipfile',
    'tempfile',
]

a = Analysis(
    ['main.py'],  # Entry point
    pathex=[],
    binaries=[],
    datas=[
        # Include any data files if needed
        # ('templates', 'templates'),
    ],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Exclude unnecessary modules to reduce size
        'tkinter',
        'matplotlib',
        'numpy',
        'pandas',
        'PIL',
        'scipy',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='gopro-backend',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,  # Keep console for logging
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='gopro-backend',
)
