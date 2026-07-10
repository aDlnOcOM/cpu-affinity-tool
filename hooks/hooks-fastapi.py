from PyInstaller.utils.hooks import collect_submodules, collect_data_files, copy_metadata

hiddenimports = collect_submodules('fastapi')
datas = collect_data_files('fastapi')
datas += collect_data_files('starlette')
datas += copy_metadata('fastapi')
datas += copy_metadata('pydantic')