"""Registry of the skills that plug into the learning core. Adding a skill = one adapter class + one line here."""
import importlib

from core import learn

ADAPTERS = {"blender": ("core.learn_blender", "BlenderAdapter"), "python": ("core.learn_python", "PythonAdapter"),
            "blender_basics": ("core.learn_blender_basics", "BlenderBasicsAdapter")}


def adapter(skill):
    mod, cls = ADAPTERS[skill]
    return getattr(importlib.import_module(mod), cls)()


def engine(skill, **kw):
    return learn.Engine(adapter(skill), **kw)
