from __future__ import annotations

from pathlib import Path

from misflix.core.subtitles.media import MediaInfo, StreamInfo
from misflix.infra import spanish_detection
from misflix.infra.spanish_detection import SpanishDetection, SpanishDetector, clip_command, frame_command


def test_clip_command_seeks_before_input_and_outputs_whisper_format(tmp_path):
    command = clip_command(Path("/v.mkv"), StreamInfo(2, "audio", "aac"), 120.0, tmp_path / "c.wav")

    joined = " ".join(command)
    assert command.index("-ss") < command.index("-i")  # busqueda directa: no lee todo el archivo
    assert "-map 0:2" in joined and "-ac 1 -ar 16000" in joined


def test_frame_command_crops_the_bottom_and_uses_hardware_decoding_when_available(tmp_path):
    command = frame_command(Path("/v.mkv"), 600.0, tmp_path / "f.png")

    joined = " ".join(command)
    assert "-hwaccel auto" in joined
    assert command.index("-ss") < command.index("-i")
    assert "crop=iw:ih*0.30:0:ih*0.70" in joined


def test_detector_without_tools_detects_nothing(monkeypatch, tmp_path):
    monkeypatch.setattr(spanish_detection.shutil, "which", lambda name: None)
    detector = SpanishDetector(tmp_path)
    info = MediaInfo(duration=100, streams=(StreamInfo(1, "audio", "aac"),))

    detection = detector.detect(Path("/v.mkv"), info)

    assert detector.tools == frozenset()
    assert detection == SpanishDetection(audio_languages={}, burned_language=None, tools=frozenset())


def test_detector_listens_to_every_track_and_reads_frames(monkeypatch, tmp_path):
    binaries = {"whisper-cli": "/usr/bin/whisper-cli", "tesseract": "/usr/bin/tesseract"}
    monkeypatch.setattr(spanish_detection.shutil, "which", lambda name: binaries.get(name))
    calls: list[list[str]] = []

    def fake_run(command):
        calls.append(command)
        if command[-1] == "--list-langs":
            return "List of available languages:\neng\nspa\n"
        if command[0].endswith("whisper-cli"):
            return "auto-detected language: es (p = 0.97)\n"
        if command[0].endswith("tesseract"):
            return "¿Qué estás haciendo aquí? No lo sé, pero está bien. Yo quiero que usted se vaya ahora.\n"
        if command[-1].endswith(".png"):
            Path(command[-1]).write_bytes(b"png")
        return ""

    monkeypatch.setattr(spanish_detection, "_run", fake_run)
    model = tmp_path / "whisper" / "ggml-base.bin"
    model.parent.mkdir(parents=True)
    model.write_bytes(b"0" * (101 * 1024 * 1024))
    info = MediaInfo(duration=1000, streams=(StreamInfo(1, "audio", "aac"), StreamInfo(2, "audio", "ac3")))

    detection = SpanishDetector(tmp_path).detect(Path("/v.mkv"), info)

    assert detection.tools == frozenset({"voz", "ocr"})
    assert detection.audio_languages == {1: "es", 2: "es"}
    assert detection.spanish_audio
    assert detection.burned_language == "es"
