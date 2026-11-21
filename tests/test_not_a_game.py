"""The stores' background programs are not games (2026-11-09: the Epic launcher's EOSOverlayRenderer kept JARVIS in game mode
all morning, unloading his big model with the graphics card at 6 %)."""
from types import SimpleNamespace

import psutil
import pytest

from core import acquire


def _procs(*rows):
    return lambda attrs: [SimpleNamespace(info={"name": n, "exe": e}) for n, e in rows]


@pytest.mark.parametrize("name, exe", [
    ("EOSOverlayRenderer-Win64-Shipping.exe", r"C:\Program Files (x86)\Epic Games\Epic Online Services\managedArtifacts\98bc\EOSOverlayRenderer-Win64-Shipping.exe"),
    ("EpicGamesLauncher.exe", r"C:\Program Files\Epic Games\Launcher\Portal\Binaries\Win64\EpicGamesLauncher.exe"),
    ("EpicOnlineServicesUserHelper.exe", r"C:\Program Files (x86)\Epic Games\Epic Online Services\EpicOnlineServicesUserHelper.exe"),
    ("UnrealCEFSubProcess.exe", r"C:\Program Files\Epic Games\Launcher\Engine\Binaries\Win64\UnrealCEFSubProcess.exe"),
])
def test_store_background_programs_are_not_games(monkeypatch, name, exe):
    monkeypatch.setattr(psutil, "process_iter", _procs((name, exe)))
    assert acquire.running_game() == ""


def test_a_real_epic_or_steam_game_still_counts(monkeypatch):
    monkeypatch.setattr(psutil, "process_iter", _procs(
        ("EOSOverlayRenderer-Win64-Shipping.exe", r"C:\Program Files (x86)\Epic Games\Epic Online Services\x\EOSOverlayRenderer-Win64-Shipping.exe"),
        ("FortniteClient-Win64-Shipping.exe", r"C:\Program Files\Epic Games\Fortnite\FortniteGame\Binaries\Win64\FortniteClient-Win64-Shipping.exe")))
    assert acquire.running_game() == "FortniteClient-Win64-Shipping.exe"
    monkeypatch.setattr(psutil, "process_iter", _procs(("Hades2.exe", r"D:\SteamLibrary\steamapps\common\Hades II\Ship\Hades2.exe")))
    assert acquire.running_game() == "Hades2.exe"


@pytest.mark.parametrize("name, exe", [
    ("dotnet.exe", r"C:\Program Files\Epic Games\UE_5.8\Engine\Binaries\ThirdParty\DotNet\10.0\win-x64\dotnet.exe"),
    ("UnrealEditor.exe", r"C:\Program Files\Epic Games\UE_5.8\Engine\Binaries\Win64\UnrealEditor.exe"),
    ("ShaderCompileWorker.exe", r"C:\Program Files\Epic Games\UE_5.8\Engine\Binaries\Win64\ShaderCompileWorker.exe"),
])
def test_the_unreal_engine_is_not_a_game(monkeypatch, name, exe):
    # live 2026-11-09: building Shadow World put the engine's dotnet.exe in game mode
    monkeypatch.setattr(psutil, "process_iter", _procs((name, exe)))
    assert acquire.running_game() == ""
