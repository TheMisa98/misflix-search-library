from __future__ import annotations

from pathlib import Path

import pytest

from misflix.core.subtitles.detection import LanguageGuess
from misflix.core.subtitles.media import MediaInfo, StreamInfo
from misflix.infra import spanish_detection, whisper_lid
from misflix.infra.spanish_detection import (
    SpanishDetection,
    SpanishDetector,
    clip_command,
    confident_words,
    frame_command,
    parse_lid_output,
)

TSV_HEADER = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"


def tsv_row(word: str, conf: float) -> str:
    return f"5\t1\t1\t1\t1\t1\t0\t0\t10\t10\t{conf}\t{word}"


def test_clip_command_seeks_before_input_and_outputs_whisper_format(tmp_path):
    command = clip_command(Path("/v.mkv"), StreamInfo(2, "audio", "aac"), 120.0, tmp_path / "c.wav")

    joined = " ".join(command)
    assert command.index("-ss") < command.index("-i")  # busqueda directa: no lee todo el archivo
    assert "-map 0:2" in joined and "-ac 1 -ar 16000" in joined


def test_frame_command_crops_the_bottom_center_and_uses_hardware_decoding_when_available(tmp_path):
    command = frame_command(Path("/v.mkv"), 600.0, tmp_path / "f.png")

    joined = " ".join(command)
    assert "-hwaccel auto" in joined
    assert command.index("-ss") < command.index("-i")
    assert "crop=iw*0.8:ih*0.30:iw*0.1:ih*0.70" in joined


def test_parse_lid_output_skips_whisper_logs():
    output = (
        "whisper_init_from_file_with_params_no_state: loading model\n"
        '{"language": "es", "probability": 0.934}\n'
        "basura\n"
        '{"language": "en", "probability": 0.5}\n'
    )

    assert parse_lid_output(output) == [LanguageGuess("es", 0.934), LanguageGuess("en", 0.5)]


def test_confident_words_drops_low_confidence_noise():
    tsv = "\n".join(
        [
            TSV_HEADER,
            tsv_row("Me", 91.0),
            tsv_row("da", 88.5),
            tsv_row("Ea", 12.0),
            tsv_row("", 95),
            tsv_row("gusto.", 77),
        ]
    )

    assert confident_words(tsv) == "Me da gusto."


@pytest.mark.parametrize(
    ("stream", "listened"),
    [
        (StreamInfo(1, "audio", "aac"), True),  # The Aviator: sin idioma ni titulo
        (StreamInfo(1, "audio", "dts", title="Surround"), True),  # House: `und` "Surround"
        (StreamInfo(1, "audio", "aac", language="jpn"), False),  # Kimetsu: se le cree al tag
        (StreamInfo(1, "audio", "aac", title="[AAC | 2.0 ENG]"), False),
        (StreamInfo(1, "audio", "aac", title="Latino"), False),
    ],
)
def test_only_untagged_tracks_are_listened_to(stream, listened):
    assert spanish_detection._needs_listening(stream) is listened


def test_detector_without_tools_detects_nothing(monkeypatch, tmp_path):
    monkeypatch.setattr(whisper_lid, "find_library", lambda: None)
    monkeypatch.setattr(spanish_detection.shutil, "which", lambda name: None)
    detector = SpanishDetector(tmp_path)
    info = MediaInfo(duration=100, streams=(StreamInfo(1, "audio", "aac"),))

    detection = detector.detect(Path("/v.mkv"), info)

    assert detector.tools == frozenset()
    assert detection == SpanishDetection(audio_languages={}, burned_language=None, tools=frozenset())


@pytest.fixture
def fake_tools(monkeypatch, tmp_path):
    """Whisper y Tesseract "instalados", con respuestas programables."""
    monkeypatch.setattr(whisper_lid, "find_library", lambda: "/usr/lib64/libwhisper.so.1")
    monkeypatch.setattr(spanish_detection.shutil, "which", lambda name: f"/usr/bin/{name}")
    model = tmp_path / "whisper" / "ggml-base.bin"
    model.parent.mkdir(parents=True)
    model.write_bytes(b"0" * (101 * 1024 * 1024))

    class Tools:
        lid_answers: list[list[str]] = []
        ocr_text = ""
        calls: list[list[str]] = []
        envs: list[dict | None] = []

    def fake_run(command, env=None, timeout=300):
        Tools.calls.append(command)
        Tools.envs.append(env)
        if command[-1] == "--list-langs":
            return "List of available languages:\neng\nspa\n"
        if "misflix.infra.whisper_lid" in command:
            languages = Tools.lid_answers.pop(0)
            return "\n".join(f'{{"language": "{lang}", "probability": 0.95}}' for lang in languages)
        if command[0].endswith("tesseract"):
            return "\n".join([TSV_HEADER, *(tsv_row(word, 90) for word in Tools.ocr_text.split())])
        Path(command[-1]).write_bytes(b"x")  # ffmpeg: fragmento o fotograma
        return ""

    monkeypatch.setattr(spanish_detection, "_run", fake_run)
    return Tools


def test_untagged_dub_is_detected_with_two_clips_in_a_cpu_only_process(fake_tools, tmp_path):
    fake_tools.lid_answers = [["es", "es"]]
    info = MediaInfo(
        duration=1000, streams=(StreamInfo(1, "audio", "aac"), StreamInfo(2, "audio", "ac3", language="eng"))
    )

    detection = SpanishDetector(tmp_path).detect(Path("/v.mkv"), info)

    assert detection.audio_languages == {1: "es"}  # la pista `eng` no se escucha
    assert detection.spanish_audio
    lid_runs = [i for i, c in enumerate(fake_tools.calls) if "misflix.infra.whisper_lid" in c]
    assert len(lid_runs) == 1
    assert fake_tools.envs[lid_runs[0]]["HIP_VISIBLE_DEVICES"] == "-1"  # la GPU AMD se cuelga con ROCm


def test_more_clips_are_added_one_at_a_time_until_there_is_a_decision(fake_tools, tmp_path):
    fake_tools.lid_answers = [["ja", "en"], ["es"], ["es"]]
    info = MediaInfo(duration=1000, streams=(StreamInfo(1, "audio", "aac"),))

    detection = SpanishDetector(tmp_path).detect(Path("/v.mkv"), info)

    assert detection.audio_languages == {1: "es"}
    assert fake_tools.lid_answers == []  # 4 fragmentos: 2 + 1 + 1, y paro


def test_burned_spanish_subtitles_are_read_and_reading_stops_early(fake_tools, tmp_path):
    fake_tools.ocr_text = "¿Qué estás haciendo aquí? No lo sé, pero está bien. Yo quiero que usted se vaya ahora."
    info = MediaInfo(duration=1000, streams=(StreamInfo(1, "audio", "aac", language="jpn"),))

    detection = SpanishDetector(tmp_path).detect(Path("/v.mkv"), info)

    assert detection.burned_language == "es"
    assert detection.audio_languages == {}
    frames = [c for c in fake_tools.calls if c[0].endswith("tesseract") and c[-1] == "tsv"]
    assert 1 < len(frames) < 20  # corta apenas tiene texto suficiente


def test_whisper_lid_main_prints_one_json_line_per_clip(monkeypatch, capsys):
    monkeypatch.setattr(whisper_lid, "detect", lambda model, clips, threads: [("es", 0.97), ("es", 0.91)])

    assert whisper_lid.main(["modelo.bin", "a.wav", "b.wav"]) == 0
    assert parse_lid_output(capsys.readouterr().out) == [LanguageGuess("es", 0.97), LanguageGuess("es", 0.91)]
    assert whisper_lid.main([]) == 2
