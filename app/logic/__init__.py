# app/logic initialization
import importlib

_cloud_runtime = importlib.import_module('app.logic.openrouter_runtime_patch')
_cloud_runtime.apply_openrouter_cloud_registry()
